from __future__ import annotations
import contextlib
import copy
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import date
__all__ = ['OrganizerStore', 'OrganizerConflict', 'OrganizerBusy', 'DEFAULT_MAX_FACTS']
log = logging.getLogger('companion_organizer')
DEFAULT_MAX_FACTS = 200
_MIN_MAX_FACTS = 1
_MAX_MAX_FACTS = 2000
_MAX_MEMORY_CHARS = 20000
_MAX_TEXT_CHARS = 2000
_MAX_NAME_CHARS = 120
_MAX_ACTION_CHARS = 40
_PUBLIC_PROMISE_STATUSES = ('open', 'overdue', 'completed', 'archived')
_ASSIGNABLE_PROMISE_STATUSES = ('open', 'completed', 'archived')
_FACT_STATUSES = ('active', 'archived', 'expired')
_ASSIGNABLE_FACT_STATUSES = ('active', 'archived')
_ACTIONS = ('memory_save', 'fact_upsert', 'fact_status', 'promise_update', 'tidy')
_DATE_RE = re.compile('^\\d{4}-\\d{2}-\\d{2}$')
_WS_RE = re.compile('\\s+')
_LOCKS_GUARD = threading.Lock()
_LOCKS = {}

def _normalized_path(path):
    return os.path.normcase(os.path.abspath(str(path)))

def _path_lock(path):
    key = _normalized_path(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock

def _unlink_quietly(path):
    try:
        os.unlink(path)
    except OSError:
        pass

@contextlib.contextmanager
def _file_lock(path, timeout=10.0, stale_after=60.0):
    lock_path = str(path) + '.lock'
    parent = os.path.dirname(os.path.abspath(lock_path)) or '.'
    os.makedirs(parent, exist_ok=True)
    deadline = time.monotonic() + max(0.1, float(timeout))
    fd = None
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock_path) > stale_after:
                    _unlink_quietly(lock_path)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise OrganizerBusy()
            time.sleep(0.01)
        except OSError:
            break
    try:
        if fd is not None:
            try:
                os.write(fd, str(os.getpid()).encode('ascii', 'ignore'))
            except OSError:
                pass
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        _unlink_quietly(lock_path)

def _clean_text(value, max_len=None):
    if value is None:
        return ''
    text = value if isinstance(value, str) else str(value)
    if max_len is not None:
        text = text[:max_len]
    return text

def _clean_chat(value):
    return _clean_text(value).strip()[:_MAX_NAME_CHARS]

def _norm_text(value):
    return _WS_RE.sub(' ', _clean_text(value)).strip()

def _scope(record):
    return _clean_text(record.get('chat')).strip()

def _is_legacy(record):
    if not _scope(record):
        return True
    return record.get('legacy') is True

def _parse_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None

def _valid_date_string(value):
    if not value:
        return True
    return bool(_DATE_RE.match(value)) and _parse_date(value) is not None

def _date_before(value, today):
    parsed = _parse_date(value)
    base = _parse_date(today)
    if parsed is None or base is None:
        return False
    return parsed < base

def _memory_hash(text):
    return hashlib.sha1(_clean_text(text).encode('utf-8', 'ignore')).hexdigest()[:12]

def _revision_token(meta, memory_text='', wildcard_memory=False):
    try:
        base = 'rv%d' % int(meta.get('revision') or 0)
    except (TypeError, ValueError):
        base = 'rv0'
    return base + ('.*' if wildcard_memory else '.' + _memory_hash(memory_text))

def _parse_revision(token):
    token = _clean_text(token).strip()
    if not token.startswith('rv'):
        return None
    body = token[2:]
    if '.' in body:
        head, _, memory_part = body.partition('.')
    else:
        head, memory_part = (body, '*')
    try:
        revision = int(head)
    except (TypeError, ValueError):
        return None
    return (revision, memory_part or '*')

def _canonical_id(record):
    try:
        raw = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = repr(record)
    return hashlib.sha1(raw.encode('utf-8', 'ignore')).hexdigest()[:16]

def _public_fact(record):
    status = _clean_text(record.get('status')).strip().lower()
    if status not in _FACT_STATUSES:
        status = 'active'
    try:
        created = int(record.get('createdAt'))
    except (TypeError, ValueError):
        created = 0
    return {'id': _clean_text(record.get('id')) or 'ft-anon-' + _canonical_id(record), 'chat': _scope(record), 'personId': _clean_text(record.get('personId')), 'personName': _clean_text(record.get('personName')), 'text': _clean_text(record.get('text')), 'status': status, 'expiresAt': _clean_text(record.get('expiresAt')), 'createdAt': created}

def _public_promise(record, today=''):
    status = _clean_text(record.get('status')).strip().lower()
    if status not in _PUBLIC_PROMISE_STATUSES:
        status = 'open'
    due = _clean_text(record.get('due'))
    if status == 'open' and due and _date_before(due, today):
        status = 'overdue'
    return {'id': _clean_text(record.get('id')) or 'pr-anon-' + _canonical_id(record), 'chat': _scope(record), 'personId': _clean_text(record.get('personId')), 'personName': _clean_text(record.get('personName')), 'promise': _clean_text(record.get('promise')), 'due': due, 'status': status, 'made': _clean_text(record.get('made')), 'legacy': _is_legacy(record)}

def _error(status, code, message):
    return (status, {'ok': False, 'error': code, 'message': message})

class OrganizerConflict(Exception):
    code = 'revision_mismatch'

    def __init__(self, current_revision='', expected_revision=None, message=None):
        self.current_revision = _clean_text(current_revision)
        self.expected_revision = expected_revision
        self.message = message or '数据已被修改，请刷新后重试'
        super().__init__(self.message)

    def payload(self):
        return {'ok': False, 'error': self.code, 'message': self.message, 'revision': self.current_revision}

class OrganizerBusy(Exception):
    code = 'busy'

class _BadRequest(Exception):

    def __init__(self, message='请求参数不正确', status=400, code='invalid_input'):
        self.message = message
        self.status = status
        self.code = code
        super().__init__(message)

class _NotFound(Exception):

    def __init__(self, message='没有找到对应的记录'):
        self.message = message
        super().__init__(message)

class _TooLarge(_BadRequest):

    def __init__(self, message='内容过长'):
        super().__init__(message, status=413, code='too_large')

class OrganizerStore:

    def __init__(self, path, memory_get, memory_set, promises_load, promises_save, backup_root, cancel_reminders=None, today_provider=None, clock=None, max_facts=DEFAULT_MAX_FACTS, chats_provider=None):
        if not isinstance(path, str) or not path.strip():
            raise ValueError('path must be a non-empty string')
        for name, callback in (('memory_get', memory_get), ('memory_set', memory_set), ('promises_load', promises_load), ('promises_save', promises_save)):
            if not callable(callback):
                raise TypeError('%s must be callable' % name)
        if not isinstance(backup_root, str) or not backup_root.strip():
            raise ValueError('backup_root must be a non-empty directory path')
        self.path = path
        self.memory_get = memory_get
        self.memory_set = memory_set
        self.promises_load = promises_load
        self.promises_save = promises_save
        self.backup_root = backup_root
        self.cancel_reminders = cancel_reminders if callable(cancel_reminders) else None
        self.today_provider = today_provider
        self.clock = clock or time.time
        self.chats_provider = chats_provider if callable(chats_provider) else None
        try:
            max_facts = int(max_facts)
        except (TypeError, ValueError):
            max_facts = DEFAULT_MAX_FACTS
        self.max_facts = min(_MAX_MAX_FACTS, max(_MIN_MAX_FACTS, max_facts))
        self._lock = _path_lock(self.path)
        self._local = threading.local()

    @property
    def facts_path(self):
        base = self.path
        if base.lower().endswith('.json'):
            return base[:-5] + '.facts.json'
        return base + '.facts.json'

    def __repr__(self):
        return '<OrganizerStore %r>' % self.path

    @property
    def revision(self):
        with self._exclusive():
            meta = self._read_meta()
            if not meta.get('migrated'):
                meta = self._migrate(meta)
            return _revision_token(meta, wildcard_memory=True)

    def _now(self):
        try:
            return float(self.clock())
        except Exception:
            return time.time()

    def _today(self):
        if callable(self.today_provider):
            try:
                value = self.today_provider()
                if isinstance(value, str) and _DATE_RE.match(value.strip()[:10]):
                    return value.strip()[:10]
            except Exception:
                log.warning('today_provider failed; using local date', exc_info=True)
        return time.strftime('%Y-%m-%d', time.localtime(self._now()))

    @contextlib.contextmanager
    def _exclusive(self):
        depth = getattr(self._local, 'depth', 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield
            finally:
                self._local.depth = depth
            return
        with self._lock:
            with _file_lock(self.path):
                self._local.depth = 1
                try:
                    yield
                finally:
                    self._local.depth = 0

    @contextlib.contextmanager
    def exclusive(self):
        with self._exclusive():
            yield self

    @staticmethod
    def _default_meta():
        return {'version': 1, 'revision': 0, 'migrated': False, 'migration': None, 'memoryRevisions': {}, 'updatedAt': 0}

    def _read_meta(self):
        try:
            with open(self.path, encoding='utf-8') as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return self._default_meta()
        except Exception:
            log.warning('organizer meta unreadable; starting from defaults', exc_info=True)
            return self._default_meta()
        meta = self._default_meta()
        if isinstance(raw, dict):
            meta.update(raw)
        try:
            meta['revision'] = int(meta.get('revision') or 0)
        except (TypeError, ValueError):
            meta['revision'] = 0
        if not isinstance(meta.get('memoryRevisions'), dict):
            meta['memoryRevisions'] = {}
        meta['migrated'] = bool(meta.get('migrated'))
        return meta

    def _write_meta(self, meta):
        self._atomic_write_json(self.path, meta)

    def _atomic_write_json(self, path, data):
        parent = os.path.dirname(os.path.abspath(path)) or '.'
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + '.', suffix='.tmp', dir=parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            _unlink_quietly(tmp)

    def _atomic_write_bytes(self, path, payload):
        parent = os.path.dirname(os.path.abspath(path)) or '.'
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + '.', suffix='.tmp', dir=parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            _unlink_quietly(tmp)

    def _load_promises(self):
        try:
            raw = self.promises_load()
        except Exception:
            log.warning('promises_load failed; treating as empty', exc_info=True)
            return []
        if not isinstance(raw, list):
            return []
        try:
            return copy.deepcopy(raw)
        except Exception:
            return [dict(item) if isinstance(item, dict) else item for item in raw]

    def _save_promises(self, promises):
        self.promises_save(promises)

    def _load_facts(self):
        try:
            with open(self.facts_path, encoding='utf-8') as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return []
        except Exception:
            log.warning('facts store unreadable; treating as empty', exc_info=True)
            return []
        if isinstance(raw, dict):
            items = raw.get('facts')
        else:
            items = raw
        return items if isinstance(items, list) else []

    def _write_facts(self, facts):
        self._atomic_write_json(self.facts_path, {'version': 1, 'facts': facts})

    def _memory_text(self, chat):
        try:
            return _clean_text(self.memory_get(chat))
        except Exception:
            log.warning('memory_get failed', exc_info=True)
            return ''

    def _memory_revision(self, meta, chat, memory_text):
        revisions = meta.get('memoryRevisions') or {}
        try:
            value = int(revisions.get(chat) or 0)
        except (TypeError, ValueError):
            value = 0
        return 'mrv%d.%s' % (value, _memory_hash(memory_text))

    def _backup_target(self, kind):
        os.makedirs(self.backup_root, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(self._now()))
        name = '%s-%s-%s.json' % (kind, stamp, uuid.uuid4().hex[:6])
        return os.path.join(self.backup_root, name)

    @staticmethod
    def _json_bytes(data):
        return json.dumps(data, ensure_ascii=False, indent=1, default=str).encode('utf-8')

    def _write_backup(self, kind, payload):
        target = self._backup_target(kind)
        self._atomic_write_bytes(target, payload)
        if not os.path.isfile(target) or os.path.getsize(target) != len(payload):
            raise RuntimeError('backup write failed: %s' % target)
        return target

    def _backup_snapshot(self, kind, data):
        return self._write_backup(kind, self._json_bytes(data))

    def _backup_file_or_snapshot(self, kind, path, snapshot):
        if path and os.path.isfile(path):
            with open(path, 'rb') as handle:
                payload = handle.read()
        else:
            payload = self._json_bytes(snapshot)
        return self._write_backup(kind, payload)

    def _backup_promises(self, promises_before):
        return self._backup_file_or_snapshot('promises', self._promises_source_path(), promises_before)

    def _backup_facts(self, facts_before):
        return self._backup_file_or_snapshot('facts', self.facts_path, facts_before)

    def _backup_memory(self, chat, text):
        return self._backup_snapshot('memory', {'chat': chat, 'text': text})

    def _backup_meta(self):
        if os.path.isfile(self.path):
            with open(self.path, 'rb') as handle:
                payload = handle.read()
            return self._write_backup('meta', payload)
        return None

    def _promises_source_path(self):
        for callback in (self.promises_load, self.promises_save):
            candidates = (callback, getattr(callback, '__func__', None), getattr(callback, '__self__', None))
            for candidate in candidates:
                if candidate is None:
                    continue
                for attr in ('promises_path', '_promises_path'):
                    value = getattr(candidate, attr, None)
                    if callable(value):
                        try:
                            value = value()
                        except Exception:
                            continue
                    if isinstance(value, str) and value and os.path.isfile(value):
                        return os.path.abspath(value)
        return None

    def _backup_legacy(self, promises):
        os.makedirs(self.backup_root, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(self._now()))
        name = 'promises-%s-%s.json' % (stamp, uuid.uuid4().hex[:6])
        target = os.path.join(self.backup_root, name)
        source = self._promises_source_path()
        if source:
            with open(source, 'rb') as handle:
                payload = handle.read()
            mode = 'file'
        else:
            payload = self._json_bytes(promises)
            mode = 'content'
        self._atomic_write_bytes(target, payload)
        info = {'file': os.path.abspath(target), 'mode': mode, 'source': source, 'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload), 'records': len(promises), 'at': int(self._now())}
        if not os.path.isfile(target) or os.path.getsize(target) != len(payload):
            raise RuntimeError('legacy promises backup failed')
        log.info('legacy promises backed up to %s (%s)', target, mode)
        return info

    def _migrate(self, meta):
        promises = self._load_promises()
        backup = None
        if promises:
            backup = self._backup_legacy(promises)
        changed = False
        for record in promises:
            if not isinstance(record, dict):
                continue
            if not _clean_text(record.get('id')).strip():
                record['id'] = self._new_promise_id()
                changed = True
            if not _scope(record):
                if record.get('legacy') is not True:
                    record['legacy'] = True
                    changed = True
            elif 'legacy' not in record:
                record['legacy'] = False
                changed = True
        if changed:
            self._save_promises(promises)
        meta = dict(meta)
        meta['migrated'] = True
        meta['migration'] = {'at': int(self._now()), 'backup': backup}
        if backup or changed:
            meta['revision'] = int(meta.get('revision') or 0) + 1
        meta['updatedAt'] = int(self._now())
        self._backup_meta()
        self._write_meta(meta)
        log.info('organizer migrated: %d promise records', len(promises))
        return meta

    @staticmethod
    def _new_promise_id():
        return 'pr_' + uuid.uuid4().hex[:16]

    @staticmethod
    def _new_fact_id():
        return 'ft_' + uuid.uuid4().hex[:16]

    def _normalize_for_write(self, promises):
        changed = False
        for record in promises:
            if not isinstance(record, dict):
                continue
            if not _clean_text(record.get('id')).strip():
                record['id'] = self._new_promise_id()
                changed = True
            if not _scope(record):
                if record.get('legacy') is not True:
                    record['legacy'] = True
                    changed = True
        return changed

    def _conflict(self, meta, chat):
        return OrganizerConflict(current_revision=_revision_token(meta, self._memory_text(chat)))

    def _check_revision(self, revision, meta, chat, memory_text, enforce_memory=False):
        if revision is None:
            return
        parsed = _parse_revision(revision)
        if parsed is None:
            raise self._conflict(meta, chat)
        expected_global, expected_memory = parsed
        try:
            current_global = int(meta.get('revision') or 0)
        except (TypeError, ValueError):
            current_global = 0
        if expected_global != current_global:
            raise self._conflict(meta, chat)
        if enforce_memory and expected_memory != '*' and (expected_memory != _memory_hash(memory_text)):
            raise self._conflict(meta, chat)

    def _chats(self):
        if not self.chats_provider:
            return []
        try:
            items = self.chats_provider() or []
        except Exception:
            log.warning('chats_provider failed', exc_info=True)
            return []
        result = []
        seen = set()
        for item in items:
            if isinstance(item, dict):
                name = _clean_text(item.get('name') or item.get('id')).strip()
                chat_id = _clean_text(item.get('id') or name).strip()
                kind = _clean_text(item.get('kind')).strip().lower()
                if kind not in ('private', 'group'):
                    kind = 'private'
            elif isinstance(item, str):
                name = item.strip()
                chat_id = name
                kind = 'private'
            else:
                continue
            if not chat_id or chat_id in seen:
                continue
            seen.add(chat_id)
            result.append({'id': chat_id, 'name': name or chat_id, 'kind': kind})
        result.sort(key=lambda entry: entry['id'])
        return result

    def _configured_chat_ids(self):
        if self.chats_provider is None:
            return None
        ids = set()
        for entry in self._chats():
            ids.add(entry['id'])
            ids.add(entry['name'])
        return ids

    def _payload(self, chat, meta, promises, facts):
        chat = _clean_chat(chat)
        today = self._today()
        memory_text = self._memory_text(chat)
        if chat:
            view_promises = [item for item in promises if isinstance(item, dict) and _scope(item) == chat]
            view_facts = [item for item in facts if isinstance(item, dict) and _scope(item) == chat]
        else:
            view_promises = [item for item in promises if isinstance(item, dict) and (not _scope(item))]
            view_facts = [item for item in facts if isinstance(item, dict) and (not _scope(item))]
        public_promises = [_public_promise(item, today) for item in view_promises]
        public_facts = [_public_fact(item) for item in view_facts]
        counts = {'open': sum((1 for item in public_promises if item['status'] == 'open')), 'overdue': sum((1 for item in public_promises if item['status'] == 'overdue')), 'completed': sum((1 for item in public_promises if item['status'] == 'completed')), 'legacy': sum((1 for item in public_promises if item['legacy']))}
        return {'ok': True, 'revision': _revision_token(meta, memory_text), 'chat': chat, 'chats': self._chats(), 'memory': {'text': memory_text, 'revision': self._memory_revision(meta, chat, memory_text)}, 'facts': public_facts, 'promises': public_promises, 'counts': counts}

    def facts_for(self, chat, today=None):
        chat = _clean_chat(chat)
        if not chat:
            return []
        today = _clean_text(today) or self._today()
        with self._exclusive():
            facts = self._load_facts()
        result = []
        for record in facts:
            if not isinstance(record, dict) or _scope(record) != chat:
                continue
            status = _clean_text(record.get('status')).strip().lower() or 'active'
            if status != 'active':
                continue
            expires = _clean_text(record.get('expiresAt')).strip()
            if expires and _date_before(expires, today):
                continue
            result.append(_public_fact(record))
        return result

    def promises_for(self, chat, today=None):
        chat = _clean_chat(chat)
        if not chat:
            return []
        today = _clean_text(today) or self._today()
        with self._exclusive():
            promises = self._load_promises()
        result = []
        for record in promises:
            if not isinstance(record, dict) or _is_legacy(record):
                continue
            if _scope(record) != chat:
                continue
            if _clean_text(record.get('status')).strip().lower() not in ('open', 'overdue'):
                continue
            due = _clean_text(record.get('due')).strip()
            if due and _date_before(due, today):
                continue
            result.append(_public_promise(record, today))
        return result

    def get(self, chat=''):
        chat = _clean_chat(chat)
        try:
            with self._exclusive():
                meta = self._read_meta()
                if not meta.get('migrated'):
                    meta = self._migrate(meta)
                promises = self._load_promises()
                facts = self._load_facts()
                return self._payload(chat, meta, promises, facts)
        except OrganizerBusy:
            return {'ok': False, 'error': 'busy', 'message': '数据正在保存，请稍后重试'}

    def post(self, action, chat='', revision=None, **fields):
        action = _clean_text(action).strip().lower()[:_MAX_ACTION_CHARS]
        chat = _clean_text(chat).strip()
        if action not in _ACTIONS:
            return _error(400, 'invalid_input', '不支持的操作')
        if len(chat) > _MAX_NAME_CHARS:
            return _error(400, 'invalid_input', '会话名称过长')
        chat = _clean_chat(chat)
        try:
            with self._exclusive():
                meta = self._read_meta()
                if not meta.get('migrated'):
                    meta = self._migrate(meta)
                memory_text = self._memory_text(chat)
                self._check_revision(revision, meta, chat, memory_text, enforce_memory=action == 'memory_save')
                promises = self._load_promises()
                facts = self._load_facts()
                promises_before = copy.deepcopy(promises)
                facts_before = copy.deepcopy(facts)
                memory_before = memory_text
                changed_promises = self._normalize_for_write(promises)
                changed_facts = False
                memory_changed = False
                new_memory_text = None
                cancellations = []
                today = self._today()
                now = int(self._now())
                if action == 'memory_save':
                    if not chat:
                        raise _BadRequest('请选择会话')
                    text = fields.get('text')
                    if text is None:
                        text = ''
                    if not isinstance(text, str):
                        raise _BadRequest()
                    if len(text) > _MAX_MEMORY_CHARS:
                        raise _TooLarge()
                    if text != memory_text:
                        recheck = self._memory_text(chat)
                        if recheck != memory_text:
                            raise self._conflict(meta, chat)
                        new_memory_text = text
                        memory_changed = True
                elif action == 'fact_upsert':
                    fact_id = _clean_text(fields.get('id')).strip()
                    text = _required_text(fields, 'text', _MAX_TEXT_CHARS)
                    person_id = _optional_text(fields, 'personId', _MAX_NAME_CHARS)
                    person_name = _optional_text(fields, 'personName', _MAX_NAME_CHARS)
                    expires = _optional_text(fields, 'expiresAt', 10)
                    if not _valid_date_string(expires):
                        raise _BadRequest('到期日期格式不正确')
                    if fact_id:
                        index = _find_by_id_and_scope(facts, fact_id, chat)
                        if index is None:
                            raise _NotFound()
                        record = facts[index]
                        record['text'] = text
                        record['personId'] = person_id
                        record['personName'] = person_name
                        record['expiresAt'] = expires
                        record['updatedAt'] = now
                    else:
                        if not chat:
                            raise _BadRequest('请选择会话')
                        facts.append({'id': self._new_fact_id(), 'chat': chat, 'personId': person_id, 'personName': person_name, 'text': text, 'status': 'active', 'expiresAt': expires, 'createdAt': now, 'updatedAt': now})
                    self._cap_facts(facts, now, chat)
                    changed_facts = True
                elif action == 'fact_status':
                    fact_id = _required_id(fields, 'id')
                    status = _clean_text(fields.get('status')).strip().lower()
                    if status not in _ASSIGNABLE_FACT_STATUSES:
                        raise _BadRequest()
                    index = _find_by_id_and_scope(facts, fact_id, chat)
                    if index is None:
                        raise _NotFound()
                    if facts[index].get('status') != status:
                        facts[index]['status'] = status
                        facts[index]['updatedAt'] = now
                        changed_facts = True
                elif action == 'promise_update':
                    promise_id = _required_id(fields, 'id')
                    status = fields.get('status')
                    target_chat = fields.get('targetChat')
                    if status is not None:
                        status = _clean_text(status).strip().lower()
                        if status not in _ASSIGNABLE_PROMISE_STATUSES:
                            raise _BadRequest()
                    if target_chat is not None:
                        target_chat = _validated_target_chat(target_chat, self._configured_chat_ids())
                    index = _find_by_id_and_scope(promises, promise_id, chat)
                    if index is None:
                        raise _NotFound()
                    record = promises[index]
                    dirty = False
                    if status is not None and record.get('status') != status:
                        record['status'] = status
                        dirty = True
                        if status in ('completed', 'archived'):
                            cancellations.append(copy.deepcopy(record))
                    if target_chat is not None:
                        if _scope(record) != target_chat:
                            record['chat'] = target_chat
                            record['legacy'] = False
                            dirty = True
                    if dirty:
                        record['updatedAt'] = now
                        changed_promises = True
                elif action == 'tidy':
                    if self._tidy_promises(promises, chat, today, now, cancellations):
                        changed_promises = True
                    if self._tidy_facts(facts, chat, today, now):
                        changed_facts = True
                changed = changed_promises or changed_facts or memory_changed
                if changed:
                    if changed_promises:
                        self._backup_promises(promises_before)
                    if changed_facts:
                        self._backup_facts(facts_before)
                    if memory_changed:
                        self._backup_memory(chat, memory_before)
                    self._backup_meta()
                    if changed_promises:
                        self._save_promises(promises)
                    if changed_facts:
                        self._write_facts(facts)
                    if memory_changed:
                        self.memory_set(chat, new_memory_text)
                        revisions = meta.get('memoryRevisions') or {}
                        revisions[chat] = int(revisions.get(chat) or 0) + 1
                        meta['memoryRevisions'] = revisions
                    meta['revision'] = int(meta.get('revision') or 0) + 1
                    meta['updatedAt'] = now
                    self._write_meta(meta)
                payload = self._payload(chat, meta, promises, facts)
            for promise in cancellations:
                self._notify_cancel(promise)
            return (200, payload)
        except OrganizerConflict:
            raise
        except OrganizerBusy:
            return _error(409, 'busy', '数据正在保存，请稍后重试')
        except _BadRequest as exc:
            return _error(exc.status, exc.code, exc.message)
        except _NotFound as exc:
            return _error(404, 'not_found', exc.message)
        except Exception:
            log.exception('organizer write failed')
            return _error(500, 'storage_failed', '保存失败，请稍后重试')

    def tidy(self, chat='', revision=None):
        status, payload = self.post('tidy', chat, revision)
        return payload

    def register_promise(self, chat, promise, due='', made=None, person_id='', person_name='', revision=None):
        chat = _clean_text(chat).strip()
        if not chat or len(chat) > _MAX_NAME_CHARS:
            return _error(400, 'invalid_input', '请选择会话')
        chat = _clean_chat(chat)
        try:
            text = _required_text({'promise': promise}, 'promise', _MAX_TEXT_CHARS)
            person_id = _optional_text({'personId': person_id}, 'personId', _MAX_NAME_CHARS)
            person_name = _optional_text({'personName': person_name}, 'personName', _MAX_NAME_CHARS)
            due = _optional_text({'due': due}, 'due', 10)
            if not _valid_date_string(due):
                raise _BadRequest('到期日期格式不正确')
            made = _clean_text(made).strip() or self._today()
            if len(made) > 10:
                raise _BadRequest('日期格式不正确')
            with self._exclusive():
                meta = self._read_meta()
                if not meta.get('migrated'):
                    meta = self._migrate(meta)
                memory_text = self._memory_text(chat)
                self._check_revision(revision, meta, chat, memory_text)
                promises = self._load_promises()
                promises_before = copy.deepcopy(promises)
                now = int(self._now())
                promises.append({'id': self._new_promise_id(), 'chat': chat, 'personId': person_id, 'personName': person_name, 'promise': text, 'due': due, 'status': 'open', 'made': made, 'legacy': False, 'createdAt': now, 'updatedAt': now})
                self._backup_memory(chat, memory_text)
                self._backup_promises(promises_before)
                self._backup_meta()
                self._save_promises(promises)
                meta['revision'] = int(meta.get('revision') or 0) + 1
                meta['updatedAt'] = now
                self._write_meta(meta)
                facts = self._load_facts()
                payload = self._payload(chat, meta, promises, facts)
            return (200, payload)
        except OrganizerConflict:
            raise
        except OrganizerBusy:
            return _error(409, 'busy', '数据正在保存，请稍后重试')
        except _BadRequest as exc:
            return _error(exc.status, exc.code, exc.message)
        except Exception:
            log.exception('organizer promise registration failed')
            return _error(500, 'storage_failed', '保存失败，请稍后重试')

    def _notify_cancel(self, promise):
        if not self.cancel_reminders:
            return
        try:
            self.cancel_reminders(copy.deepcopy(promise))
        except Exception:
            log.warning('cancel_reminders callback failed', exc_info=True)

    def _tidy_promises(self, promises, chat, today, now, cancellations):
        changed = False
        scoped = [record for record in promises if isinstance(record, dict) and _scope(record) == chat]
        for record in scoped:
            if _clean_text(record.get('status')).strip().lower() != 'open':
                continue
            due = _clean_text(record.get('due')).strip()
            if due and _date_before(due, today):
                record['status'] = 'overdue'
                record['updatedAt'] = now
                changed = True
        groups = {}
        for index, record in enumerate(scoped):
            text = _norm_text(record.get('promise'))
            if not text:
                continue
            key = (text, _clean_text(record.get('due')).strip(), _clean_text(record.get('personId')).strip())
            groups.setdefault(key, []).append((index, record))
        for items in groups.values():
            if len(items) < 2:
                continue
            winner = None
            for _, record in items:
                if _clean_text(record.get('status')).strip().lower() == 'completed':
                    winner = record
                    break
            if winner is None:
                winner = items[0][1]
            for _, record in items:
                if record is winner:
                    continue
                if _clean_text(record.get('status')).strip().lower() in ('open', 'overdue'):
                    record['status'] = 'archived'
                    record['updatedAt'] = now
                    cancellations.append(copy.deepcopy(record))
                    changed = True
        return changed

    def _tidy_facts(self, facts, chat, today, now):
        changed = False
        scoped = [record for record in facts if isinstance(record, dict) and _scope(record) == chat]
        for record in scoped:
            status = _clean_text(record.get('status')).strip().lower()
            expires = _clean_text(record.get('expiresAt')).strip()
            if status == 'active' and expires and _date_before(expires, today):
                record['status'] = 'expired'
                record['updatedAt'] = now
                changed = True
        groups = {}
        for index, record in enumerate(scoped):
            if _clean_text(record.get('status')).strip().lower() != 'active':
                continue
            key = (_norm_text(record.get('text')), _clean_text(record.get('personId')).strip(), _clean_text(record.get('expiresAt')).strip())
            groups.setdefault(key, []).append((index, record))
        for items in groups.values():
            for _, record in items[1:]:
                record['status'] = 'archived'
                record['updatedAt'] = now
                changed = True
        if self._cap_facts(facts, now, chat):
            changed = True
        return changed

    def _cap_facts(self, facts, now, scope):
        active = [(index, record) for index, record in enumerate(facts) if isinstance(record, dict) and _scope(record) == scope and (_clean_text(record.get('status')).strip().lower() == 'active')]
        overflow = len(active) - self.max_facts
        if overflow <= 0:
            return False
        for _, record in active[:overflow]:
            record['status'] = 'archived'
            record['updatedAt'] = now
        return True

def _required_text(fields, key, max_len):
    value = fields.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _BadRequest()
    text = value.strip()
    if len(text) > max_len:
        raise _TooLarge()
    return text

def _optional_text(fields, key, max_len):
    value = fields.get(key)
    if value is None:
        return ''
    if not isinstance(value, str):
        raise _BadRequest()
    text = value.strip()
    if len(text) > max_len:
        raise _BadRequest()
    return text

def _required_id(fields, key):
    value = fields.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _BadRequest()
    return value.strip()

def _find_by_id_and_scope(records, record_id, chat):
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        if _clean_text(record.get('id')) != record_id:
            continue
        if _scope(record) != chat:
            continue
        return index
    return None

def _validated_target_chat(value, allowed):
    if not isinstance(value, str):
        raise _BadRequest()
    target = value.strip()
    if not target or len(target) > _MAX_NAME_CHARS:
        raise _BadRequest('目标会话不能为空')
    if allowed is not None and target not in allowed:
        raise _BadRequest('目标会话不存在')
    return target

import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid
__all__ = ['DiagnosticsStore', 'STATUSES', 'STAGES', 'STEP_STATUSES']
STATUSES = ('received', 'queued', 'skipped', 'generating', 'sending', 'sent', 'failed')
STAGES = ('received', 'trigger', 'model', 'send')
STEP_STATUSES = ('running', 'ok', 'skipped', 'failed')
_COUNT_KEYS = ('received', 'sent', 'skipped', 'failed')
_SAFE_CODE = re.compile('^[A-Za-z0-9_.:-]{0,64}$')
_MAX_STEPS = 64
_MAX_TRACE_IDS = 32
_MAX_PREVIEW = 160
_MAX_REASON = 200
_MAX_MESSAGE = 200
_MAX_CHAT = 120
_MAX_SENDER = 80
_MAX_TRACE_ID = 128

def _text(value, limit, collapse=False):
    if value is None:
        return ''
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return ''
    if collapse:
        value = ' '.join(value.split())
    else:
        value = ''.join((ch for ch in value if ch in ('\n', '\t') or ord(ch) >= 32))
    value = value.strip()
    if len(value) > limit:
        value = value[:limit - 1] + '…'
    return value

def _code(value):
    if not isinstance(value, str):
        return ''
    value = value.strip()
    return value if _SAFE_CODE.match(value) else ''

def _number(value, default=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not math.isfinite(float(value)):
        return default
    return float(value)

def _atomic_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=str(target.parent), prefix=target.name + '.', suffix='.tmp', delete=False) as out:
            temporary = out.name
            json.dump(value, out, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass

class DiagnosticsStore:

    def __init__(self, path=None, limit=200, clock=time.time, chats=None, chat_provider=None):
        self._path = str(path) if path else None
        self._limit = self._clean_limit(limit)
        self._clock = clock if callable(clock) else time.time
        self._chat_source = chat_provider if callable(chat_provider) else chats
        self._lock = threading.RLock()
        self._messages = self._load()

    @staticmethod
    def _clean_limit(limit):
        try:
            number = int(limit)
        except (TypeError, ValueError):
            return 200
        return max(1, min(number, 5000))

    def _now(self):
        try:
            return _number(self._clock(), time.time())
        except Exception:
            return time.time()

    def _save(self):
        if not self._path:
            return
        try:
            _atomic_json(self._path, {'version': 1, 'messages': self._messages})
        except (OSError, TypeError, ValueError):
            pass

    def _trim(self):
        if len(self._messages) > self._limit:
            del self._messages[self._limit:]

    def _find(self, diag_id):
        diag_id = str(diag_id or '')
        if not diag_id:
            return None
        for record in self._messages:
            if record['id'] == diag_id:
                return record
        for record in self._messages:
            if diag_id in record['traceIds']:
                return record
        return None

    def _load(self):
        if not self._path:
            return []
        try:
            raw = json.loads(Path(self._path).read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return []
        items = raw.get('messages') if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        loaded = []
        seen = set()
        for item in items:
            record = self._normalize_record(item)
            if record is None or record['id'] in seen:
                continue
            loaded.append(record)
            seen.add(record['id'])
            if len(loaded) >= self._limit:
                break
        return loaded

    @staticmethod
    def _normalize_record(item):
        if not isinstance(item, dict):
            return None
        diag_id = item.get('id')
        if not isinstance(diag_id, str) or not diag_id.strip():
            return None
        status = item.get('status')
        status = status if status in STATUSES else 'received'
        steps = []
        raw_steps = item.get('steps')
        if isinstance(raw_steps, list):
            for raw_step in raw_steps[-_MAX_STEPS:]:
                if not isinstance(raw_step, dict):
                    continue
                stage = raw_step.get('stage')
                step_status = raw_step.get('status')
                if stage not in STAGES or step_status not in STEP_STATUSES:
                    continue
                steps.append({'stage': stage, 'at': _number(raw_step.get('at')), 'status': step_status, 'reasonCode': _code(raw_step.get('reasonCode')), 'message': _text(raw_step.get('message'), _MAX_MESSAGE)})
        trace_ids = []
        raw_traces = item.get('traceIds')
        if isinstance(raw_traces, list):
            for raw_trace in raw_traces[:_MAX_TRACE_IDS]:
                trace = _text(raw_trace, _MAX_TRACE_ID)
                if trace and trace not in trace_ids:
                    trace_ids.append(trace)
        return {'id': diag_id.strip(), 'chat': _text(item.get('chat'), _MAX_CHAT), 'sender': _text(item.get('sender'), _MAX_SENDER), 'receivedAt': _number(item.get('receivedAt')), 'preview': _text(item.get('preview'), _MAX_PREVIEW, collapse=True), 'status': status, 'reasonCode': _code(item.get('reasonCode')), 'reason': _text(item.get('reason'), _MAX_REASON), 'steps': steps, 'traceIds': trace_ids}

    def start(self, chat, sender='', preview='', status='received', reason_code='', reason=''):
        if status not in STATUSES:
            raise ValueError('未知诊断状态：' + str(status))
        with self._lock:
            record = {'id': uuid.uuid4().hex, 'chat': _text(chat, _MAX_CHAT), 'sender': _text(sender, _MAX_SENDER), 'receivedAt': self._now(), 'preview': _text(preview, _MAX_PREVIEW, collapse=True), 'status': status, 'reasonCode': _code(reason_code), 'reason': _text(reason, _MAX_REASON), 'steps': [], 'traceIds': []}
            self._messages.insert(0, record)
            self._trim()
            self._save()
            return record['id']

    def update(self, diag_id, status=None, reason_code=None, reason=None, chat=None, sender=None, preview=None):
        if status is not None and status not in STATUSES:
            raise ValueError('未知诊断状态：' + str(status))
        with self._lock:
            record = self._find(diag_id)
            if record is None:
                return False
            if status is not None:
                record['status'] = status
            if reason_code is not None:
                record['reasonCode'] = _code(reason_code)
            if reason is not None:
                record['reason'] = _text(reason, _MAX_REASON)
            if chat is not None:
                record['chat'] = _text(chat, _MAX_CHAT)
            if sender is not None:
                record['sender'] = _text(sender, _MAX_SENDER)
            if preview is not None:
                record['preview'] = _text(preview, _MAX_PREVIEW, collapse=True)
            self._save()
            return True

    def step(self, diag_id, stage, status, reason_code='', message=''):
        if stage not in STAGES:
            raise ValueError('未知诊断阶段：' + str(stage))
        if status not in STEP_STATUSES:
            raise ValueError('未知步骤状态：' + str(status))
        with self._lock:
            record = self._find(diag_id)
            if record is None:
                return False
            record['steps'].append({'stage': stage, 'at': self._now(), 'status': status, 'reasonCode': _code(reason_code), 'message': _text(message, _MAX_MESSAGE)})
            if len(record['steps']) > _MAX_STEPS:
                del record['steps'][:-_MAX_STEPS]
            self._save()
            return True

    def trace(self, diag_id, trace_id):
        trace_id = _text(trace_id, _MAX_TRACE_ID)
        if not trace_id:
            return False
        with self._lock:
            record = self._find(diag_id)
            if record is None:
                return False
            if trace_id not in record['traceIds']:
                record['traceIds'].append(trace_id)
                if len(record['traceIds']) > _MAX_TRACE_IDS:
                    del record['traceIds'][:-_MAX_TRACE_IDS]
                self._save()
            return True

    def merge(self, ids, status=None, reason_code='', reason='', preview=None, chat=None, sender=None):
        if status is not None and status not in STATUSES:
            raise ValueError('未知诊断状态：' + str(status))
        if isinstance(ids, str):
            ids = [ids]
        try:
            ids = [str(item) for item in ids or []]
        except TypeError:
            return None
        with self._lock:
            records = []
            positions = []
            seen = set()
            for diag_id in ids:
                record = self._find(diag_id)
                if record is None or record['id'] in seen:
                    continue
                seen.add(record['id'])
                records.append(record)
                positions.append(self._messages.index(record))
            if not records:
                return None
            if len(records) == 1 and (not any((value is not None for value in (status, preview, chat, sender)))) and (not reason_code) and (not reason):
                return records[0]['id']
            merged_steps = []
            step_keys = set()
            for record in records:
                for step_item in record['steps']:
                    key = (step_item['stage'], step_item['at'], step_item['status'], step_item['reasonCode'], step_item['message'])
                    if key not in step_keys:
                        step_keys.add(key)
                        merged_steps.append(step_item)
            merged_steps.sort(key=lambda item: item['at'])
            trace_ids = []
            for record in records:
                for trace_id in record['traceIds'] + [record['id']]:
                    if trace_id not in trace_ids:
                        trace_ids.append(trace_id)
            for diag_id in ids:
                if diag_id and diag_id not in trace_ids:
                    trace_ids.append(diag_id)
            trace_ids = trace_ids[:_MAX_TRACE_IDS]
            first = records[0]
            merged = {'id': uuid.uuid4().hex, 'chat': _text(chat if chat is not None else first['chat'], _MAX_CHAT), 'sender': _text(sender if sender is not None else first['sender'], _MAX_SENDER), 'receivedAt': min((record['receivedAt'] for record in records)), 'preview': _text(preview if preview is not None else first['preview'], _MAX_PREVIEW, collapse=True), 'status': status if status is not None else first['status'], 'reasonCode': _code(reason_code) or next((record['reasonCode'] for record in records if record['reasonCode']), ''), 'reason': _text(reason, _MAX_REASON) or next((record['reason'] for record in records if record['reason']), ''), 'steps': merged_steps[-_MAX_STEPS:], 'traceIds': trace_ids}
            position = min(positions)
            removed = {record['id'] for record in records}
            self._messages = [item for item in self._messages if item['id'] not in removed]
            self._messages.insert(min(position, len(self._messages)), merged)
            self._trim()
            self._save()
            return merged['id']

    def send(self, diag_id, ok, how=''):
        success = ok is True
        how = _text(how, 40, collapse=True)
        with self._lock:
            record = self._find(diag_id)
            if record is None:
                return False
            if success:
                record['status'] = 'sent'
                record['reasonCode'] = ''
                record['reason'] = '发送成功'
                message = '发送成功' + ('（' + how + '）' if how else '')
                step_status = 'ok'
            else:
                lowered = how.lower()
                timeout = 'timeout' in lowered or '超时' in how
                record['status'] = 'failed'
                record['reasonCode'] = 'send_timeout' if timeout else 'send_failed'
                record['reason'] = '发送超时' if timeout else '发送失败'
                message = ('发送超时' if timeout else '发送失败') + ('（' + how + '）' if how else '')
                step_status = 'failed'
            record['steps'].append({'stage': 'send', 'at': self._now(), 'status': step_status, 'reasonCode': record['reasonCode'], 'message': message})
            if len(record['steps']) > _MAX_STEPS:
                del record['steps'][:-_MAX_STEPS]
            self._save()
            return True

    def clear(self):
        with self._lock:
            self._messages = []
            self._save()

    def get(self, diag_id):
        with self._lock:
            record = self._find(diag_id)
            return copy.deepcopy(record) if record is not None else None

    def _chats(self):
        source = self._chat_source
        if callable(source):
            try:
                source = source()
            except Exception:
                source = []
        if source is None:
            source = []
        if not isinstance(source, (list, tuple)):
            return []
        entries = []
        seen = set()
        for item in source:
            if isinstance(item, str):
                name, kind, mode = (item, 'private', '')
            elif isinstance(item, dict):
                name = item.get('name') or item.get('id') or ''
                kind = str(item.get('kind') or '').strip().lower()
                mode = str(item.get('mode') or '').strip().lower()
            else:
                continue
            name = _text(name, _MAX_CHAT)
            if not name:
                continue
            if kind not in ('private', 'group'):
                kind = 'group' if mode == 'wake' else 'private'
            if name in seen:
                continue
            seen.add(name)
            entries.append({'id': name, 'name': name, 'kind': kind})
        return entries

    def list(self, limit=50, chat=None):
        with self._lock:
            now = self._now()
            records = self._messages
            if chat is not None:
                target = str(chat)
                records = [record for record in records if record['chat'] == target]
            counts = {key: 0 for key in _COUNT_KEYS}
            counts['received'] = len(records)
            for record in records:
                if record['status'] in ('sent', 'skipped', 'failed'):
                    counts[record['status']] += 1
            try:
                size = int(limit)
            except (TypeError, ValueError):
                size = 50
            size = max(1, min(size, 1000))
            messages = copy.deepcopy(records[:size])
            return {'ok': True, 'now': now, 'messages': messages, 'counts': counts, 'chats': self._chats()}

    def __len__(self):
        with self._lock:
            return len(self._messages)

    def __bool__(self):
        return True

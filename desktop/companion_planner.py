import copy
import datetime
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
_VERSION_ID_RE = re.compile('^[a-f0-9]{16,64}$')
_SAFE_MSG = {'invalid_date': '只能修改今天的日程', 'invalid_input': '输入无效', 'revision': '日程版本已变化，请刷新后重试', 'busy': '已有日程任务正在进行', 'missing': '找不到对应日程或版本', 'bad_timeline': '重排结果不完整，已保留旧日程', 'locked': '该时间段已锁定，无法覆盖', 'generation_failed': '日程重排失败，已保留旧日程'}

def _atomic_json(path, data):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = '%s.tmp-%d-%d' % (path, os.getpid(), time.time_ns())
    try:
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

def _read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except Exception:
        return default

def _minutes(value):
    text = str(value or '')
    match = re.match('^(\\d{2}):(\\d{2})$', text)
    if not match:
        return None
    hour, minute = (int(match.group(1)), int(match.group(2)))
    if minute > 59 or hour > 24:
        return None
    total = hour * 60 + minute
    if total < 0 or total > 1440:
        return None
    if hour == 24 and minute != 0:
        return None
    return total

def _segment_id(date, index, segment):
    raw = '%s|%s|%s|%s|%s|%s' % (date, segment.get('from'), segment.get('to'), segment.get('activity'), segment.get('place'), segment.get('detail'))
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:20]

def _copy_segment(segment):
    return copy.deepcopy(segment)

def _valid_timeline(timeline):
    if not isinstance(timeline, list) or not timeline:
        return False
    if not all((isinstance(segment, dict) for segment in timeline)):
        return False
    if str(timeline[0].get('from')) != '00:00':
        return False
    if str(timeline[-1].get('to')) != '24:00':
        return False
    prev = 0
    for segment in timeline:
        start, end = (_minutes(segment.get('from')), _minutes(segment.get('to')))
        if start is None or end is None or start != prev or (end <= start):
            return False
        prev = end
    return prev == 1440

def merge_timeline(old_day, candidate_day, now_minutes, locked_ids):
    old = list((old_day or {}).get('timeline') or [])
    candidate = list((candidate_day or {}).get('timeline') or [])
    if old and (not all((isinstance(x, dict) for x in old))):
        raise RuntimeError('bad_timeline')
    if not old:
        if not _valid_timeline(candidate):
            raise RuntimeError('bad_timeline')
        return (dict(candidate_day or {}), [])
    preserved = []
    for index, segment in enumerate(old):
        sid = _segment_id(old_day.get('date') or candidate_day.get('date'), index, segment)
        start = _minutes(segment.get('from'))
        end = _minutes(segment.get('to'))
        if start is None or end is None:
            raise RuntimeError('bad_timeline')
        if start <= int(now_minutes) or sid in set(locked_ids):
            preserved.append((start, end, _copy_segment(segment), sid))
    preserved.sort(key=lambda item: item[0])
    for prev, cur in zip(preserved, preserved[1:]):
        if prev[1] > cur[0]:
            raise RuntimeError('bad_timeline')
    if not _valid_timeline(candidate):
        raise RuntimeError('bad_timeline')
    futures = []
    cursor = 0
    for start, end, segment, sid in preserved:
        if start > cursor:
            _fill_gap(candidate, cursor, start, futures)
        futures.append(segment)
        cursor = end
    if cursor < 1440:
        _fill_gap(candidate, cursor, 1440, futures)
    merged = list(futures)
    if not _valid_timeline(merged):
        raise RuntimeError('bad_timeline')
    day = dict(old_day or candidate_day or {})
    day['timeline'] = merged
    for key in ('chats', 'videos'):
        if isinstance(candidate_day.get(key), list):

            def protected(minute):
                return any((a <= minute < b for a, b, _, _ in preserved))
            slots = []
            for value in old_day.get(key) or []:
                minute = _minutes(value)
                if minute is not None and (minute <= now_minutes or protected(minute)):
                    slots.append(value)
            for value in candidate_day[key]:
                minute = _minutes(value)
                if minute is not None and now_minutes < minute < 1440 and (not protected(minute)):
                    slots.append(value)
            day[key] = sorted(set(slots), key=_minutes)
    if isinstance(candidate_day.get('state_out'), dict):
        day['state_out'] = copy.deepcopy(candidate_day['state_out'])
    return (day, [item[3] for item in preserved])

def _fill_gap(candidate, gap_start, gap_end, out):
    covered = gap_start
    for segment in candidate:
        start = _minutes(segment.get('from'))
        end = _minutes(segment.get('to'))
        if start is None or end is None:
            continue
        if end <= gap_start or start >= gap_end:
            continue
        lo, hi = (max(start, gap_start), min(end, gap_end))
        if lo != covered:
            continue
        if lo >= hi:
            continue
        piece = _copy_segment(segment)
        piece['from'] = '%02d:%02d' % (lo // 60, lo % 60)
        piece['to'] = '%02d:%02d' % (hi // 60, hi % 60)
        out.append(piece)
        covered = hi
    if covered != gap_end:
        raise RuntimeError('bad_timeline')

class CompanionPlanner:

    def __init__(self, manager, load_day, save_day, generate_day, day_path, data_dir, today_provider, backup_dir, extra_provider=None, apply_state=None, clock=time.time, log=None):
        self.manager = manager
        self._load_day = load_day
        self._save_day = save_day
        self._generate_day = generate_day
        self._day_path = day_path
        self._today = today_provider
        self._extra = extra_provider or (lambda: '')
        self._apply_state = apply_state or (lambda: None)
        self._backup_dir = backup_dir
        self._clock = clock
        self._log = log or (lambda *a: None)
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.state_path = os.path.join(data_dir, 'planner.json')
        self.versions_dir = os.path.join(data_dir, 'versions')
        os.makedirs(self.versions_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._state = self._load_state()
        self.manager._on_success = self._on_manager_success
        with self._lock:
            today = self._today()
            if not any((v.get('date') == today for v in self._state.get('versions') or [])):
                day = self._day()
                if day and day.get('timeline'):
                    self._record_snapshot_locked(day, '初始版本')
                    self._bump()
                    self._save_state()
                    self._prune_versions_locked()

    def _load_state(self):
        raw = _read_json(self.state_path, {})
        if not isinstance(raw, dict):
            raw = {}
        raw.setdefault('revision', 1)
        raw.setdefault('locks', {})
        raw.setdefault('versions', [])
        return raw

    def _save_state(self):
        _atomic_json(self.state_path, self._state)

    def _revision(self):
        return str(self._state.get('revision', 1))

    def _bump(self):
        self._state['revision'] = int(self._state.get('revision', 1)) + 1

    def _day(self):
        try:
            return self._load_day(self._today()) or {}
        except Exception:
            return {}

    def _timeline(self, date, day):
        timeline = list((day or {}).get('timeline') or [])
        locks = set((self._state.get('locks') or {}).get(date) or [])
        now = time.localtime()
        now_minutes = now.tm_hour * 60 + now.tm_min
        out = []
        for index, segment in enumerate(timeline):
            if not isinstance(segment, dict):
                continue
            sid = _segment_id(date, index, segment)
            item = _copy_segment(segment)
            item['id'] = sid
            item['locked'] = sid in locks
            start = _minutes(segment.get('from'))
            item['elapsed'] = start is not None and start <= now_minutes
            out.append(item)
        return out

    def get(self):
        date = self._today()
        with self.manager._lock:
            with self._lock:
                day = self._day()
                versions = [{'id': v.get('id'), 'createdAt': v.get('createdAt'), 'label': v.get('label')} for v in self._state.get('versions') or [] if v.get('date') == date]
                return {'ok': True, 'date': date, 'now': int(self._clock()), 'revision': self._revision(), 'timeline': self._timeline(date, day), 'versions': versions, 'generation': self.manager.public(date)}

    def has_locks(self, date=None):
        date = date or self._today()
        with self.manager._lock:
            with self._lock:
                day = self._load_day(date) or {}
                current_ids = {_segment_id(date, i, s) for i, s in enumerate(day.get('timeline') or []) if isinstance(s, dict)}
                return bool(set((self._state.get('locks') or {}).get(date) or []) & current_ids)

    def _conflict(self, revision):
        if revision is not None and str(revision) != self._revision():
            return {'ok': False, 'error': 'revision', 'message': _SAFE_MSG['revision'], 'revision': self._revision()}
        return None

    def _error(self, code, status=400, message=None):
        return (status, {'ok': False, 'error': code, 'message': message or _SAFE_MSG.get(code, '请求无效'), 'revision': self._revision()})

    def post(self, body):
        if not isinstance(body, dict):
            return self._error('invalid_input')
        action = str(body.get('action') or '')
        date = str(body.get('date') or self._today())
        if date != self._today():
            return self._error('invalid_date')
        if action == 'lock':
            return self._lock_action(body, date)
        if action == 'replan_remaining':
            return self._replan_action(body, date)
        if action == 'restore':
            return self._restore_action(body, date)
        return self._error('invalid_input')

    def _lock_action(self, body, date):
        with self.manager._lock:
            with self._lock:
                if self.manager.public(date).get('state') == 'running':
                    return (409, {'ok': False, 'error': 'busy', 'message': _SAFE_MSG['busy']})
                conflict = self._conflict(body.get('revision'))
                if conflict:
                    return (409, conflict)
                ids = body.get('segmentIds')
                if not isinstance(ids, list) or not all((isinstance(x, str) for x in ids)):
                    return self._error('invalid_input')
                locked = body.get('locked')
                if not isinstance(locked, bool):
                    return self._error('invalid_input')
                day = self._day()
                current = {item['id'] for item in self._timeline(date, day)}
                for sid in ids:
                    if sid not in current:
                        return self._error('invalid_input')
                locks = list((self._state.get('locks') or {}).get(date) or [])
                for sid in ids:
                    if locked and sid not in locks:
                        locks.append(sid)
                    if not locked and sid in locks:
                        locks.remove(sid)
                self._state.setdefault('locks', {})[date] = locks
                self._bump()
                self._save_state()
        return (200, {'ok': True, 'planner': self.get()})

    def _replan_action(self, body, date):
        rev = body.get('revision')
        code = None
        payload = None
        with self.manager._lock:
            with self._lock:
                conflict = self._conflict(rev)
                if conflict:
                    return (409, conflict)
                state = self.manager.public(date)
                if state.get('state') == 'running':
                    return (409, {'ok': False, 'error': 'busy', 'message': _SAFE_MSG['busy'], 'generation': state})
                locks = list((self._state.get('locks') or {}).get(date) or [])
                old_day = self._day()

                def worker(job):
                    if self._today() != date:
                        raise RuntimeError('invalid_date')
                    if self._day() != old_day:
                        raise RuntimeError('revision')
                    extra = ''
                    try:
                        extra = self._extra()
                    except Exception:
                        extra = ''
                    candidate = self._generate_day(date, extra=extra, persist=False, not_before=time.strftime('%H:%M'))
                    with self.manager._lock:
                        with self._lock:
                            if self._today() != date:
                                raise RuntimeError('invalid_date')
                            if self._day() != old_day:
                                raise RuntimeError('revision')
                            now = time.localtime()
                            merged, _ = merge_timeline(old_day, candidate, now.tm_hour * 60 + now.tm_min, locks)
                            if not _valid_timeline(merged.get('timeline')):
                                raise RuntimeError('bad_timeline')
                            self._save_day(merged)
                            try:
                                self._apply_state()
                            except Exception:
                                pass
                            return merged
                code, payload = self.manager.request_custom(date, 'manual', worker, backup=True)
        if code == 202:
            payload['planner'] = self.get()
        return (code, payload)

    def _restore_action(self, body, date):
        rev = body.get('revision')
        vid = str(body.get('versionId') or '')
        if not _VERSION_ID_RE.match(vid):
            return self._error('invalid_input')
        with self.manager._lock:
            with self._lock:
                conflict = self._conflict(rev)
                if conflict:
                    return (409, conflict)
                if self.manager.public(date).get('state') == 'running':
                    return (409, {'ok': False, 'error': 'busy', 'message': _SAFE_MSG['busy']})
                versions = self._state.get('versions') or []
                entry = next((v for v in versions if v.get('id') == vid and v.get('date') == date), None)
                if not entry:
                    return self._error('missing', 404)
                snapshot = _read_json(os.path.join(self.versions_dir, vid + '.json'))
                day = snapshot.get('day') if isinstance(snapshot, dict) else None
                if not isinstance(day, dict) or snapshot.get('date') != date or day.get('date') != date or (not _valid_timeline(day.get('timeline'))):
                    return self._error('missing', 404)
                self._record_current_locked('恢复前的当前版本')
                self._bump()
                self._save_state()
                try:
                    self._backup_current()
                except Exception:
                    return self._error('missing', 500, '无法备份当前日程，已取消恢复')
                self._save_day(day)
                try:
                    self._apply_state()
                except Exception:
                    pass
                self._record_snapshot_locked(day, '恢复到 ' + str(entry.get('label') or '旧版本'))
                self._bump()
                self._save_state()
                self._prune_versions_locked()
        return (200, {'ok': True, 'planner': self.get()})

    def _backup_current(self):
        path = self._day_path(self._today())
        if not os.path.isfile(path):
            return
        if not self._backup_dir:
            raise RuntimeError('backup unavailable')
        os.makedirs(self._backup_dir, exist_ok=True)
        dest = os.path.join(self._backup_dir, 'planner-%s-%s.json' % (self._today(), int(self._clock())))
        shutil.copy2(path, dest)

    def _on_manager_success(self, day, previous=None):
        try:
            with self._lock:
                if isinstance(previous, dict) and previous.get('date') == self._today() and previous.get('timeline'):
                    self._record_snapshot_locked(previous, '替换前版本')
                self._record_snapshot_locked(day, '日程已更新')
                self._bump()
                self._save_state()
                self._prune_versions_locked()
        except Exception:
            raise

    def _record_current_locked(self, label):
        day = self._day()
        if day and day.get('timeline'):
            self._record_snapshot_locked(day, label)

    def _record_snapshot_locked(self, day, label):
        if not isinstance(day, dict) or not day.get('timeline'):
            return
        vid = uuid.uuid4().hex
        _atomic_json(os.path.join(self.versions_dir, vid + '.json'), {'date': self._today(), 'day': day, 'createdAt': int(self._clock()), 'label': str(label or '')[:60]})
        self._state.setdefault('versions', []).append({'id': vid, 'createdAt': int(self._clock()), 'label': str(label or '')[:60], 'date': self._today()})
        self._state['versions'] = self._state['versions'][-20:]

    def _prune_versions_locked(self):
        keep = {v['id'] for v in self._state.get('versions') or []}
        try:
            for name in os.listdir(self.versions_dir):
                if name.endswith('.json') and name[:-5] not in keep:
                    os.remove(os.path.join(self.versions_dir, name))
        except OSError:
            pass

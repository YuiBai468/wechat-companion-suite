import json
import math
import os
import re
import shutil
import threading
import time
import uuid
COOLDOWN_SECONDS = 300
MAX_REASON_LEN = 48
REFUSAL_CODES = {'refusal', 'vendor_risk_refusal', 'refusal_cooldown'}
KNOWN_REASON_CODES = {'refusal', 'vendor_risk_refusal', 'refusal_cooldown', 'http_401', 'http_403', 'http_429', 'http_5xx', 'http_error', 'network_error', 'timeout', 'invalid_json', 'invalid_response', 'empty_output', 'incomplete_output', 'response_failed', 'response_error', 'generation_failed', 'bad_timeline'}
MESSAGES = {'idle': '暂无生成任务', 'running': '正在生成今日日程', 'succeeded': '今日日程已更新', 'failed': '日程生成失败，请稍后重试', 'cooldown': '日程生成被拒绝，请等待冷却后重试', 'refused': '日程生成被模型拒绝，请稍后重试'}
_REASON_RE = re.compile('life planner aux model failed:\\s*([a-z0-9_]{1,%d})' % MAX_REASON_LEN)

def idle_generation(date):
    return {'state': 'idle', 'jobId': None, 'date': date, 'source': None, 'startedAt': None, 'endedAt': None, 'message': MESSAGES['idle'], 'reasonCode': None, 'retryAfterSeconds': 0}

def _safe_reason_code(exc):
    code = getattr(exc, 'reason_code', None)
    if isinstance(code, str) and code in KNOWN_REASON_CODES:
        return code
    try:
        match = _REASON_RE.search(str(exc))
    except Exception:
        match = None
    if match and match.group(1) in KNOWN_REASON_CODES:
        return match.group(1)
    return 'generation_failed'

class LifeGenerationManager:

    def __init__(self, today_provider, load_day, generate_day, day_path, extra_provider=None, apply_state=None, backup_dir=None, clock=time.time, monotonic=time.monotonic, thread_factory=None, log=None, on_success=None):
        self._today = today_provider
        self._load_day = load_day
        self._generate = generate_day
        self._day_path = day_path
        self._extra = extra_provider or (lambda: '')
        self._apply_state = apply_state or (lambda: None)
        self._backup_dir = backup_dir
        self._clock = clock
        self._monotonic = monotonic
        self._thread_factory = thread_factory or (lambda target, name: threading.Thread(target=target, name=name, daemon=True))
        self._log = log or (lambda *a: None)
        self._on_success = on_success
        self._lock = threading.RLock()
        self._job = None
        self._last_by_date = {}
        self._cooldown = {}

    def today(self):
        return str(self._today())

    def public(self, date=None):
        date = str(date or self.today())
        with self._lock:
            return self._public_locked(date)

    def _public_locked(self, date):
        now = self._monotonic()
        job = self._job
        if job and job.get('state') in ('running', 'starting'):
            return self._public_shape(job, 0)
        cooldown = self._active_cooldown_locked(date, now)
        if cooldown:
            base = self._last_by_date.get(date) or idle_generation(date)
            out = self._public_shape(base, self._retry_after(cooldown, now))
            out['state'] = 'cooldown'
            out['message'] = MESSAGES['cooldown']
            out['reasonCode'] = cooldown.get('reasonCode')
            out['jobId'] = cooldown.get('jobId') or out.get('jobId')
            return out
        last = self._last_by_date.get(date)
        if last and last.get('state') in ('succeeded', 'failed'):
            return self._public_shape(last, 0)
        return idle_generation(date)

    @staticmethod
    def _public_shape(job, retry_after):
        return {'state': job.get('state') or 'idle', 'jobId': job.get('jobId'), 'date': job.get('date'), 'source': job.get('source'), 'startedAt': job.get('startedAt'), 'endedAt': job.get('endedAt'), 'message': job.get('message') or MESSAGES['idle'], 'reasonCode': job.get('reasonCode'), 'retryAfterSeconds': max(0, int(retry_after or 0))}

    def _retry_after(self, cooldown, now):
        return max(0, int(math.ceil(float(cooldown.get('until', 0)) - now)))

    def _active_cooldown_locked(self, date, now):
        item = self._cooldown.get(date)
        if not item:
            return None
        if float(item.get('until', 0)) <= now:
            self._cooldown.pop(date, None)
            return None
        return item

    def _set_cooldown_locked(self, date, reason_code, job):
        self._cooldown[date] = {'until': self._monotonic() + COOLDOWN_SECONDS, 'reasonCode': reason_code, 'jobId': job.get('jobId'), 'startedAt': job.get('startedAt'), 'endedAt': job.get('endedAt')}

    def request_manual(self, date=None, guard=None):
        today = self.today()
        if date is None:
            target = today
        else:
            target = str(date)
            if len(target) != 10 or target[4] != '-' or target[7] != '-':
                return self._error(400, 'invalid_date', '日期格式无效', today)
            try:
                import datetime
                datetime.date.fromisoformat(target)
            except Exception:
                return self._error(400, 'invalid_date', '日期无效', today)
            if target != today:
                return self._error(400, 'unsupported_date', '只能重新生成今天的日程', today)
        with self._lock:
            if guard is not None:
                try:
                    allowed = bool(guard())
                except Exception:
                    allowed = False
                if not allowed:
                    return (409, {'ok': False, 'error': 'locked', 'message': '今天有锁定的时间段，请使用局部重排或先解锁', 'generation': self._public_locked(today)})
            job = self._job
            if job and job.get('state') in ('running', 'starting'):
                return (200, {'ok': True, 'accepted': False, 'alreadyRunning': True, 'generation': self._public_shape(job, 0)})
            cooldown = self._active_cooldown_locked(target, self._monotonic())
            if cooldown:
                gen = self._public_locked(today)
                return (429, {'ok': False, 'error': 'cooldown', 'message': MESSAGES['cooldown'], 'retryAfterSeconds': gen['retryAfterSeconds'], 'generation': gen})
            job = self._new_job_locked(target, 'manual')
            self._job = job
            self._last_by_date[target] = job
            running = self._public_shape(job, 0)
        try:
            thread = self._thread_factory(lambda: self._run_job(job, backup=True), 'life-regenerate')
            thread.start()
        except Exception:
            with self._lock:
                job.update(state='failed', endedAt=int(self._clock()), message=MESSAGES['failed'], reasonCode='generation_failed')
                self._last_by_date[target] = dict(job)
            return self._error(500, 'generation_unavailable', '无法启动生成任务，请稍后重试', target)
        return (202, {'ok': True, 'accepted': True, 'generation': running})

    def _error(self, status, code, message, date):
        gen = self.public(date)
        return (status, {'ok': False, 'error': code, 'message': message, 'generation': gen})

    def request_custom(self, date, source, worker, backup=True):
        target = str(date)
        with self._lock:
            job = self._job
            if job and job.get('state') in ('running', 'starting'):
                return (409, {'ok': False, 'error': 'busy', 'message': '已有日程任务正在进行', 'generation': self._public_shape(job, 0)})
            cooldown = self._active_cooldown_locked(target, self._monotonic())
            if cooldown:
                return (429, {'ok': False, 'error': 'cooldown', 'message': MESSAGES['cooldown'], 'generation': self._public_locked(target)})
            job = self._new_job_locked(target, source)
            self._job = job
            self._last_by_date[target] = job
            running = self._public_shape(job, 0)
        try:
            thread = self._thread_factory(lambda: self._run_custom(job, worker, backup), 'life-planner')
            thread.start()
        except Exception:
            with self._lock:
                job.update(state='failed', endedAt=int(self._clock()), message=MESSAGES['failed'], reasonCode='generation_failed')
            return (500, {'ok': False, 'error': 'generation_unavailable', 'message': '无法启动日程任务，请稍后重试', 'generation': self.public(target)})
        return (202, {'ok': True, 'accepted': True, 'generation': running})

    def _run_custom(self, job, worker, backup):
        try:
            previous = job.get('_previous')
            if backup:
                self._backup_existing(job)
            day = worker(job)
            if not isinstance(day, dict) or not day:
                raise RuntimeError('generation_failed')
            if not isinstance(self._load_day(job['date']), dict):
                raise RuntimeError('generation_failed')
            with self._lock:
                if self._on_success is not None:
                    self._on_success(day, previous)
                job['state'] = 'succeeded'
                job['endedAt'] = int(self._clock())
                job['message'] = MESSAGES['succeeded']
                job['reasonCode'] = None
                self._last_by_date[job['date']] = dict(job)
                self._cooldown.pop(job['date'], None)
        except Exception as exc:
            code = _safe_reason_code(exc)
            self._restore_backup(job)
            with self._lock:
                job['state'] = 'failed'
                job['endedAt'] = int(self._clock())
                job['reasonCode'] = code
                job['message'] = MESSAGES['refused'] if code in REFUSAL_CODES else MESSAGES['failed']
                if code in REFUSAL_CODES:
                    self._set_cooldown_locked(job['date'], code, job)
                self._last_by_date[job['date']] = dict(job)
            self._log('life generation failed: %s' % code)

    def ensure_automatic(self, date, only_if_missing=True):
        target = str(date)
        with self._lock:
            job = self._job
            if job and job.get('state') in ('running', 'starting'):
                return {'started': False, 'reason': 'busy'}
            cooldown = self._active_cooldown_locked(target, self._monotonic())
            if cooldown:
                return {'started': False, 'reason': 'cooldown', 'generation': self._public_locked(target)}
            if only_if_missing:
                try:
                    if self._load_day(target):
                        return {'started': False, 'reason': 'exists'}
                except Exception:
                    pass
            job = self._new_job_locked(target, 'automatic')
            job['state'] = 'starting'
            self._job = job
            self._last_by_date[target] = job
        try:
            self._backup_existing(job)
        except Exception as exc:
            with self._lock:
                job['state'] = 'failed'
                job['reasonCode'] = _safe_reason_code(exc)
                job['message'] = MESSAGES['failed']
            return {'started': False, 'reason': 'failed', 'generation': self.public(target)}
        with self._lock:
            job['state'] = 'running'
        self._run_job(job, backup=False)
        with self._lock:
            return {'started': True, 'generation': self._public_shape(job, 0)}

    def _new_job_locked(self, date, source):
        try:
            previous = self._load_day(date)
        except Exception:
            previous = None
        return {'state': 'running', 'jobId': uuid.uuid4().hex, 'date': date, 'source': source, 'startedAt': int(self._clock()), 'endedAt': None, 'message': MESSAGES['running'], 'reasonCode': None, 'retryAfterSeconds': 0, '_previous': previous}

    def _backup_existing(self, job):
        path = self._day_path(job['date'])
        if not os.path.exists(path):
            return
        if not self._backup_dir:
            raise RuntimeError('backup unavailable')
        os.makedirs(self._backup_dir, exist_ok=True)
        name = '%s-%s-%s.json' % (job['date'], job['jobId'][:8], int(self._clock()))
        dest = os.path.join(self._backup_dir, name)
        shutil.copy2(path, dest)
        job['_backup'] = dest

    def _restore_backup(self, job):
        path = job.get('_backup')
        if not path or not os.path.isfile(path):
            return
        try:
            shutil.copy2(path, self._day_path(job['date']))
        except Exception:
            self._log('failed to restore day backup: %s' % job['date'])

    def _run_job(self, job, backup):
        try:
            previous = job.get('_previous')
            if backup:
                self._backup_existing(job)
            extra = ''
            try:
                extra = self._extra()
            except Exception:
                extra = ''
            day = self._generate(job['date'], extra)
            if not isinstance(day, dict) or not day:
                raise RuntimeError('generation_failed')
            if not isinstance(self._load_day(job['date']), dict):
                raise RuntimeError('generation_failed')
            try:
                self._apply_state()
            except Exception:
                pass
            with self._lock:
                if self._on_success is not None:
                    self._on_success(day, previous)
                job['state'] = 'succeeded'
                job['endedAt'] = int(self._clock())
                job['message'] = MESSAGES['succeeded']
                job['reasonCode'] = None
                self._last_by_date[job['date']] = dict(job)
                self._cooldown.pop(job['date'], None)
        except Exception as exc:
            code = _safe_reason_code(exc)
            self._restore_backup(job)
            with self._lock:
                job['state'] = 'failed'
                job['endedAt'] = int(self._clock())
                job['reasonCode'] = code
                job['message'] = MESSAGES['refused'] if code in REFUSAL_CODES else MESSAGES['failed']
                if code in REFUSAL_CODES:
                    self._set_cooldown_locked(job['date'], code, job)
                self._last_by_date[job['date']] = dict(job)
            self._log('life generation failed: %s' % code)

    def _reset_for_tests(self):
        with self._lock:
            self._job = None
            self._last_by_date.clear()
            self._cooldown.clear()

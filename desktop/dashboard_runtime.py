import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import types
import uuid
from datetime import datetime, timedelta, timezone as _timezone
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo('Asia/Shanghai')
except Exception:
    TZ = _timezone(timedelta(hours=8), 'Asia/Shanghai')
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(os.environ.get('DATA_DIR') or os.path.join(_ROOT, 'data'), 'dashboard')
TRACE_MAX_CALLS = 300
TRACE_MAX_BYTES = 64 * 1024 * 1024
LIST_MAX_LIMIT = 200
METRIC_LABELS = {'hr': ('心率', 'bpm'), 'resting_hr': ('静息心率', 'bpm'), 'stress': ('压力', ''), 'spo2': ('血氧', '%'), 'steps': ('步数', '步'), 'calories': ('热量', 'kcal'), 'distance': ('距离', 'km'), 'active_minutes': ('活动时长', 'min'), 'sleep_minutes': ('睡眠', 'min'), 'sleep_deep': ('深睡', 'min'), 'sleep_light': ('浅睡', 'min'), 'sleep_rem': ('REM', 'min'), 'sleep_awake': ('清醒', 'min'), 'sample_intensity': ('强度', ''), 'sample_energy': ('能量', '')}
_DATA_URL_RE = re.compile('^data:([\\w.+-]+/[\\w.+-]+)?(;base64)?,', re.I)

def _now():
    return time.time()

def _json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))

def _json_loads(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default

def local_date(ts):
    return datetime.fromtimestamp(float(ts), TZ).date().isoformat()

def day_bounds(date_str):
    d = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=TZ)
    start = d.timestamp()
    return (start, (d + timedelta(days=1)).timestamp())

def normalize_ts(value):
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    if ts > 100000000000.0:
        ts /= 1000.0
    return ts

def _media_kind(mime):
    mime = (mime or '').lower()
    if mime.startswith('image/'):
        return 'image'
    if mime.startswith('audio/'):
        return 'audio'
    if mime.startswith('video/'):
        return 'video'
    return 'file'
_SENSITIVE_KEYS = frozenset({'authorization', 'proxy-authorization', 'api_key', 'api-key', 'apikey', 'x-token', 'x_token', 'xtoken', 'token', 'access_token', 'refresh_token', 'api_token', 'password', 'passwd', 'secret', 'client_secret', 'credentials', 'cookie', 'set-cookie', 'private_key', 'auth', 'bearer'})
_HEADER_KEYS = frozenset({'headers', 'header'})
_BEARER_RE = re.compile('(?i)^\\s*(bearer|basic)\\s+\\S+\\s*$')
_SECRET_STRING_RE = re.compile('(?i)^\\s*(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,})\\s*$')

def _sanitize_payload(value, media, depth=0):
    if depth > 20:
        return '<max-depth>'
    if isinstance(value, str):
        match = _DATA_URL_RE.match(value)
        if match and (match.group(2) or len(value) > 4096):
            mime = match.group(1) or 'application/octet-stream'
            try:
                payload = value.split(',', 1)[1]
                raw = __import__('base64').b64decode(payload, validate=False)
            except Exception:
                raw = b''
            item = {'kind': _media_kind(mime), 'mime': mime, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest() if raw else '', 'note': 'binary payload replaced by metadata'}
            media.append(item)
            return '<media %s %s %dB sha256=%s>' % (item['kind'], mime, item['bytes'], item['sha256'][:16])
        if _BEARER_RE.match(value) or _SECRET_STRING_RE.match(value):
            return '<redacted:secret>'
        return value
    if isinstance(value, list):
        return [_sanitize_payload(item, media, depth + 1) for item in value]
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            lowered = str(key).casefold()
            if lowered in _SENSITIVE_KEYS:
                clean[str(key)] = '<redacted:secret>'
            elif lowered in _HEADER_KEYS:
                if isinstance(item, dict):
                    clean[str(key)] = {str(name): '<redacted:header>' for name in item.keys()}
                elif isinstance(item, list):
                    clean[str(key)] = ['<redacted:header>' for _ in item]
                else:
                    clean[str(key)] = '<redacted:header>'
            else:
                clean[str(key)] = _sanitize_payload(item, media, depth + 1)
        return clean
    return value

def _sanitize_media(value, media, depth=0):
    return _sanitize_payload(value, media, depth)

def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                ptype = part.get('type')
                if ptype in ('text', 'input_text', 'output_text') or (ptype is None and 'text' in part):
                    parts.append(str(part.get('text') or ''))
                elif ptype == 'image_url':
                    parts.append('<image>')
                elif ptype == 'input_audio':
                    parts.append('<audio>')
                elif 'audio' in part:
                    parts.append(str(part.get('audio') or '<audio>'))
                elif 'image' in part:
                    parts.append(str(part.get('image') or '<image>'))
                else:
                    parts.append('<%s>' % ptype)
            else:
                parts.append(str(part))
        return ''.join(parts)
    if isinstance(content, dict):
        return str(content.get('text') or content.get('content') or '')
    return '' if content is None else str(content)

def build_input_text(request):
    if not isinstance(request, dict):
        return ''
    messages = request.get('messages')
    if messages is None and isinstance(request.get('input'), dict):
        messages = request['input'].get('messages')
    if messages is None and isinstance(request.get('input'), list):
        messages = request['input']
    if messages is None:
        if 'prompt' in request:
            return str(request.get('prompt') or '')
        return ''
    lines = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get('role') or 'user')
        lines.append('【%s】\n%s' % (role, _content_text(item.get('content'))))
    return '\n\n'.join(lines)

def _preview(input_text, limit=200):
    text = re.sub('\\s+', ' ', str(input_text or '')).strip()
    return text[:limit]

class TraceStore:

    def __init__(self, path, max_calls=TRACE_MAX_CALLS, max_bytes=TRACE_MAX_BYTES):
        self.path = path
        self.max_calls = int(max_calls)
        self.max_bytes = int(max_bytes)
        self._local = threading.local()
        self._lock = threading.RLock()
        self._all_conns = []
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self._init_schema()
        self.mark_interrupted()

    def _conn(self):
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=15)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('PRAGMA synchronous=NORMAL')
            except sqlite3.DatabaseError:
                pass
            self._local.conn = conn
            with self._lock:
                self._all_conns.append(conn)
        return conn

    def close(self):
        with self._lock:
            conns, self._all_conns = (self._all_conns, [])
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        self._local = threading.local()

    def _init_schema(self):
        with self._lock:
            conn = self._conn()
            conn.executescript('\n                CREATE TABLE IF NOT EXISTS calls (\n                    id TEXT PRIMARY KEY,\n                    provider TEXT NOT NULL,\n                    model TEXT NOT NULL,\n                    purpose TEXT NOT NULL,\n                    status TEXT NOT NULL,\n                    started_at REAL NOT NULL,\n                    ended_at REAL,\n                    ms INTEGER,\n                    in_tokens INTEGER,\n                    out_tokens INTEGER,\n                    cached_tokens INTEGER,\n                    has_reasoning INTEGER DEFAULT 0,\n                    preview TEXT,\n                    request_json TEXT,\n                    input_text TEXT,\n                    reasoning TEXT,\n                    output TEXT,\n                    raw_response TEXT,\n                    error_json TEXT,\n                    media_json TEXT,\n                    chat TEXT\n                );\n                CREATE INDEX IF NOT EXISTS idx_calls_started ON calls(started_at DESC);\n                CREATE INDEX IF NOT EXISTS idx_calls_models ON calls(provider, model);\n            ')
            try:
                cols = {row['name'] for row in conn.execute('PRAGMA table_info(calls)').fetchall()}
                if 'chat' not in cols:
                    conn.execute('ALTER TABLE calls ADD COLUMN chat TEXT')
                    conn.execute('CREATE INDEX IF NOT EXISTS idx_calls_chat ON calls(chat, started_at DESC)')
            except Exception:
                pass
            conn.commit()

    def mark_interrupted(self):
        with self._lock:
            conn = self._conn()
            conn.execute("UPDATE calls SET status='interrupted', ended_at=COALESCE(ended_at, ?), error_json=? WHERE status='running'", (_now(), _json_dumps({'type': 'interrupted', 'reason': 'bot restarted before call finished'})))
            conn.commit()

    def start_call(self, provider, model, purpose, request, input_text=None, chat=None):
        call_id = uuid.uuid4().hex
        media = []
        clean_request = _sanitize_media(request, media)
        text = input_text if input_text is not None else build_input_text(clean_request)
        started = _now()
        with self._lock:
            conn = self._conn()
            conn.execute('INSERT INTO calls (id, provider, model, purpose, status, started_at, preview, request_json, input_text, media_json, chat) VALUES (?,?,?,?,?,?,?,?,?,?,?)', (call_id, str(provider), str(model), str(purpose), 'running', started, _preview(text), _json_dumps(clean_request), str(text or ''), _json_dumps(media), str(chat or '')))
            conn.commit()
            self._prune()
        return call_id

    def finish_call(self, call_id, response=None, output=None, reasoning=None, usage=None, ms=None, status='complete'):
        if not call_id:
            return
        media = []
        clean_response = _sanitize_media(response, media) if response is not None else None
        usage = usage or {}
        in_tokens = int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0)
        out_tokens = int(usage.get('completion_tokens') or usage.get('output_tokens') or 0)
        cached = int((usage.get('prompt_tokens_details') or {}).get('cached_tokens') or (usage.get('input_tokens_details') or {}).get('cached_tokens') or 0)
        if output is None:
            output = _extract_output(clean_response)
        if reasoning is None:
            reasoning = _extract_reasoning(clean_response)
        output = '' if output is None else str(output)
        reasoning = '' if reasoning is None else str(reasoning)
        with self._lock:
            conn = self._conn()
            row = conn.execute('SELECT media_json, request_json, input_text FROM calls WHERE id=?', (call_id,)).fetchone()
            if row is None:
                return
            media_all = _json_loads(row['media_json'], [])
            existing = {(m.get('sha256'), m.get('bytes')) for m in media_all if isinstance(m, dict)}
            for item in media:
                key = (item.get('sha256'), item.get('bytes'))
                if key not in existing:
                    media_all.append(item)
            conn.execute('UPDATE calls SET status=?, ended_at=?, ms=?, in_tokens=?, out_tokens=?, cached_tokens=?, has_reasoning=?, reasoning=?, output=?, raw_response=?, media_json=?, error_json=NULL WHERE id=?', (status, _now(), int(ms) if ms is not None else None, in_tokens, out_tokens, cached, 1 if reasoning else 0, reasoning, output, _json_dumps(clean_response) if clean_response is not None else None, _json_dumps(media_all), call_id))
            conn.commit()
            self._prune()

    def fail_call(self, call_id, error_type, http_status=None, *, reason_code=None, retryable=None, output=None, reasoning=None, raw_response=None, usage=None, ms=None):
        if not call_id:
            return
        code = str(reason_code) if reason_code in _SAFE_REASON_MESSAGES else None
        error = {'type': str(error_type or 'error')}
        if http_status is not None:
            try:
                error['httpStatus'] = int(http_status)
            except (TypeError, ValueError):
                pass
        if code:
            error['reasonCode'] = code
        if retryable is not None:
            error['retryable'] = bool(retryable)
        error['message'] = _error_message(error['type'], http_status, code)
        clean_raw = _sanitize_media(raw_response, []) if raw_response is not None else None
        if usage is None:
            in_tokens = out_tokens = cached_tokens = None
        else:
            usage = usage or {}
            in_tokens = int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0)
            out_tokens = int(usage.get('completion_tokens') or usage.get('output_tokens') or 0)
            cached_tokens = int((usage.get('prompt_tokens_details') or {}).get('cached_tokens') or (usage.get('input_tokens_details') or {}).get('cached_tokens') or 0)
        has_reasoning = None
        if reasoning is not None:
            has_reasoning = 1 if str(reasoning).strip() else 0
        with self._lock:
            conn = self._conn()
            conn.execute("UPDATE calls SET status='error', ended_at=?, error_json=?, output=COALESCE(?, output), reasoning=COALESCE(?, reasoning), raw_response=COALESCE(?, raw_response), in_tokens=COALESCE(?, in_tokens), out_tokens=COALESCE(?, out_tokens), cached_tokens=COALESCE(?, cached_tokens), has_reasoning=COALESCE(?, has_reasoning), ms=COALESCE(?, ms) WHERE id=?", (_now(), _json_dumps(error), None if output is None else str(output), None if reasoning is None else str(reasoning), None if raw_response is None else _json_dumps(clean_raw), in_tokens, out_tokens, cached_tokens, has_reasoning, ms, call_id))
            conn.commit()
            self._prune()

    def _prune(self):
        try:
            conn = self._conn()
            rows = conn.execute("SELECT id, LENGTH(COALESCE(request_json,'')) + LENGTH(COALESCE(input_text,'')) + LENGTH(COALESCE(output,'')) + LENGTH(COALESCE(reasoning,'')) + LENGTH(COALESCE(raw_response,'')) AS sz FROM calls ORDER BY started_at DESC").fetchall()
            keep = []
            total = 0
            for index, row in enumerate(rows):
                size = int(row['sz'] or 0)
                if index >= self.max_calls or total + size > self.max_bytes:
                    continue
                keep.append(row['id'])
                total += size
            if len(keep) == len(rows):
                return
            placeholders = ','.join(('?' for _ in keep))
            if keep:
                conn.execute('DELETE FROM calls WHERE id NOT IN (%s)' % placeholders, keep)
            else:
                conn.execute('DELETE FROM calls')
            conn.commit()
        except Exception:
            pass

    def list_calls(self, limit=100, model=None, provider=None, date=None, range_key=None, chat=None):
        limit = max(1, min(int(limit or 100), LIST_MAX_LIMIT))
        clauses = []
        params = []
        if provider:
            clauses.append('provider=?')
            params.append(str(provider))
        if model:
            clauses.append('model=?')
            params.append(str(model))
        if date:
            range_key = range_key or 'day'
            start, end = _range_bounds(date, range_key)
            clauses.append('started_at >= ? AND started_at < ?')
            params.extend([start, end])
        if chat:
            clauses.append('chat=?')
            params.append(str(chat))
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        conn = self._conn()
        rows = conn.execute('SELECT id, provider, model, purpose, status, started_at, ended_at, ms, in_tokens, out_tokens, cached_tokens, has_reasoning, preview, chat FROM calls' + where + ' ORDER BY started_at DESC LIMIT ?', params + [limit]).fetchall()
        total = conn.execute('SELECT COUNT(*) AS n FROM calls' + where, params).fetchone()['n']
        models = conn.execute('SELECT provider, model, COUNT(*) AS n FROM calls' + where + ' GROUP BY provider, model ORDER BY n DESC', params).fetchall()
        calls = [{'id': row['id'], 'provider': row['provider'], 'model': row['model'], 'purpose': row['purpose'], 'status': row['status'], 'startedAt': row['started_at'], 'endedAt': row['ended_at'], 'ms': row['ms'], 'inTokens': row['in_tokens'] or 0, 'outTokens': row['out_tokens'] or 0, 'cachedTokens': row['cached_tokens'] or 0, 'hasReasoning': bool(row['has_reasoning']), 'chat': row['chat'] or '', 'preview': row['preview'] or ''} for row in rows]
        return {'ok': True, 'calls': calls, 'models': [{'provider': row['provider'], 'model': row['model'], 'count': row['n']} for row in models], 'total': total}

    def get_call(self, call_id):
        conn = self._conn()
        row = conn.execute('SELECT * FROM calls WHERE id=?', (str(call_id),)).fetchone()
        if row is None:
            return None
        return {'id': row['id'], 'provider': row['provider'], 'model': row['model'], 'purpose': row['purpose'], 'status': row['status'], 'startedAt': row['started_at'], 'endedAt': row['ended_at'], 'ms': row['ms'], 'inTokens': row['in_tokens'] or 0, 'outTokens': row['out_tokens'] or 0, 'cachedTokens': row['cached_tokens'] or 0, 'hasReasoning': bool(row['has_reasoning']), 'chat': row['chat'] or '', 'request': _json_loads(row['request_json'], {}), 'inputText': row['input_text'] or '', 'reasoning': row['reasoning'] or '', 'output': row['output'] or '', 'rawResponse': _json_loads(row['raw_response'], None), 'error': _json_loads(row['error_json'], None), 'media': _json_loads(row['media_json'], [])}

    def stats_rows(self, limit=2000):
        limit = max(1, min(int(limit or 2000), 5000))
        conn = self._conn()
        rows = conn.execute('SELECT id, provider, model, purpose, status, started_at, ended_at, ms, in_tokens, out_tokens, cached_tokens, has_reasoning, request_json, chat FROM calls ORDER BY started_at DESC LIMIT ?', (limit,)).fetchall()
        return [{'id': row['id'], 'provider': row['provider'], 'model': row['model'], 'purpose': row['purpose'], 'status': row['status'], 'startedAt': row['started_at'], 'endedAt': row['ended_at'], 'ms': row['ms'], 'inTokens': row['in_tokens'] or 0, 'outTokens': row['out_tokens'] or 0, 'cachedTokens': row['cached_tokens'] or 0, 'hasReasoning': bool(row['has_reasoning']), 'request': _json_loads(row['request_json'], {}), 'chat': row['chat'] or ''} for row in rows]

    def summary(self):
        conn = self._conn()
        total = conn.execute('SELECT COUNT(*) AS n FROM calls').fetchone()['n']
        running = conn.execute("SELECT COUNT(*) AS n FROM calls WHERE status='running'").fetchone()['n']
        models = conn.execute('SELECT provider, model, COUNT(*) AS n FROM calls GROUP BY provider, model ORDER BY n DESC').fetchall()
        return {'total': total, 'running': running, 'models': [{'provider': r['provider'], 'model': r['model'], 'count': r['n']} for r in models]}

def _extract_output(response):
    if not isinstance(response, dict):
        return ''
    choices = response.get('choices')
    if choices:
        return _content_text((choices[0].get('message') or {}).get('content'))
    output = response.get('output')
    if isinstance(output, list):
        texts = []
        for item in output:
            if isinstance(item, dict) and item.get('type') == 'message':
                for part in item.get('content') or []:
                    if isinstance(part, dict) and part.get('type') in ('output_text', 'text'):
                        texts.append(str(part.get('text') or ''))
        if texts:
            return ''.join(texts)
    return str(response.get('output_text') or '')

def _extract_reasoning(response):
    if not isinstance(response, dict):
        return ''
    choices = response.get('choices')
    if choices:
        message = choices[0].get('message') or {}
        return _content_text(message.get('reasoning_content') or message.get('reasoning') or '')
    output = response.get('output')
    if isinstance(output, list):
        texts = []
        for item in output:
            if isinstance(item, dict) and item.get('type') == 'reasoning':
                for part in item.get('content') or item.get('summary') or []:
                    if isinstance(part, dict) and part.get('type') in ('reasoning_text', 'summary_text', 'text'):
                        texts.append(str(part.get('text') or ''))
        return ''.join(texts)
    return ''
_SAFE_REASON_MESSAGES = {'http_401': 'authentication failed (HTTP 401)', 'http_403': 'access denied (HTTP 403)', 'http_429': 'rate limited (HTTP 429)', 'http_5xx': 'provider server error', 'http_error': 'provider request failed', 'network_error': 'provider network error', 'timeout': 'provider request timed out', 'invalid_json': 'provider returned invalid JSON', 'invalid_response': 'provider returned an invalid response', 'empty_output': 'provider returned no usable output', 'incomplete_output': 'provider response was incomplete', 'response_failed': 'provider response failed', 'response_error': 'provider response reported an error', 'refusal': 'provider refused the request', 'vendor_risk_refusal': 'provider refused the request as high risk', 'refusal_cooldown': 'provider recently refused the same request'}

def _error_message(error_type, http_status=None, reason_code=None):
    if reason_code in _SAFE_REASON_MESSAGES:
        return _SAFE_REASON_MESSAGES[reason_code]
    if http_status is not None:
        return 'request failed with HTTP %s' % http_status
    return 'request failed (%s)' % (error_type or 'error')

def _range_bounds(date_str, range_key):
    start, end = day_bounds(date_str)
    key = (range_key or 'day').lower()
    days = {'day': 1, 'week': 7, 'month': 30}.get(key, 1)
    return (end - 86400.0 * days, end)

class HealthStore:

    def __init__(self, path, goals=None):
        self.path = path
        self.goals = {'stepsGoal': 8000, 'caloriesGoal': 400, 'activeGoal': 30}
        if goals:
            self.goals.update(goals)
        self._local = threading.local()
        self._lock = threading.RLock()
        self._all_conns = []
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self._init_schema()

    def _conn(self):
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=15)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('PRAGMA synchronous=NORMAL')
            except sqlite3.DatabaseError:
                pass
            self._local.conn = conn
            with self._lock:
                self._all_conns.append(conn)
        return conn

    def close(self):
        with self._lock:
            conns, self._all_conns = (self._all_conns, [])
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        self._local = threading.local()

    def _init_schema(self):
        with self._lock:
            conn = self._conn()
            conn.executescript('\n                CREATE TABLE IF NOT EXISTS samples (\n                    metric TEXT NOT NULL,\n                    ts REAL NOT NULL,\n                    value REAL,\n                    text TEXT,\n                    received_at REAL NOT NULL,\n                    source TEXT,\n                    dedupe TEXT PRIMARY KEY\n                );\n                CREATE INDEX IF NOT EXISTS idx_samples_metric_ts ON samples(metric, ts);\n                CREATE TABLE IF NOT EXISTS packets (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    received_at REAL NOT NULL,\n                    kind TEXT\n                );\n                CREATE TABLE IF NOT EXISTS devices (\n                    device_key TEXT PRIMARY KEY,\n                    status TEXT,\n                    name TEXT,\n                    model TEXT,\n                    battery REAL,\n                    charging INTEGER,\n                    status_at REAL,\n                    received_at REAL,\n                    raw TEXT\n                );\n                CREATE TABLE IF NOT EXISTS daily (\n                    date TEXT PRIMARY KEY,\n                    steps REAL, calories REAL, active_minutes REAL, distance_km REAL,\n                    sleep_minutes REAL, hr_avg REAL, resting_hr REAL, hr_min REAL,\n                    hr_max REAL, stress_avg REAL, stress_min REAL, stress_max REAL,\n                    spo2_avg REAL, spo2_min REAL, spo2_max REAL, standing REAL,\n                    training_load_day REAL, training_load_week REAL,\n                    training_load_level REAL, updated_at REAL\n                );\n                CREATE TABLE IF NOT EXISTS sleep (\n                    date TEXT PRIMARY KEY,\n                    minutes REAL, deep REAL, light REAL, rem REAL, awake REAL,\n                    bed REAL, wake REAL, stages TEXT, updated_at REAL\n                );\n            ')
            conn.commit()

    def _insert_sample(self, metric, ts, value, text, received_at, source, device=None):
        ts = normalize_ts(ts)
        if ts is None or not metric:
            return False
        key = '|'.join([str(metric), '%.3f' % float(ts), '' if value is None else str(value), str(text or '')])
        dedupe = hashlib.sha1(key.encode('utf-8')).hexdigest()
        with self._lock:
            conn = self._conn()
            try:
                conn.execute('INSERT OR IGNORE INTO samples (metric, ts, value, text, received_at, source, dedupe) VALUES (?,?,?,?,?,?,?)', (str(metric), float(ts), None if value is None else float(value), str(text or ''), float(received_at), str(source or ''), dedupe))
                conn.commit()
                return True
            except Exception:
                return False

    def _packet(self, received_at, kind):
        with self._lock:
            conn = self._conn()
            conn.execute('INSERT INTO packets (received_at, kind) VALUES (?,?)', (float(received_at), str(kind or '')))
            conn.commit()

    def _device(self, status=None, name=None, model=None, battery=None, charging=None, status_at=None, received_at=None, raw=None):
        with self._lock:
            conn = self._conn()
            row = conn.execute('SELECT * FROM devices ORDER BY status_at DESC LIMIT 1').fetchone()
            existing = dict(row) if row else {}
            new = {'device_key': existing.get('device_key') or 'default', 'status': status if status is not None else existing.get('status'), 'name': name if name is not None else existing.get('name'), 'model': model if model is not None else existing.get('model'), 'battery': battery if battery is not None else existing.get('battery'), 'charging': existing.get('charging') if charging is None else 1 if charging else 0, 'status_at': float(status_at) if status_at is not None else existing.get('status_at'), 'received_at': float(received_at or _now()), 'raw': _json_dumps(raw) if raw is not None else existing.get('raw')}
            old_at = existing.get('status_at')
            if status is not None and old_at is not None and (new['status_at'] is not None) and (float(new['status_at']) < float(old_at)):
                return
            conn.execute('INSERT INTO devices (device_key,status,name,model,battery,charging,status_at,received_at,raw) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(device_key) DO UPDATE SET status=excluded.status,name=excluded.name,model=excluded.model,battery=excluded.battery,charging=excluded.charging,status_at=excluded.status_at,received_at=excluded.received_at,raw=excluded.raw', (new['device_key'], new['status'], new['name'], new['model'], new['battery'], new['charging'], new['status_at'], new['received_at'], new['raw']))
            conn.commit()

    def ingest_packet(self, data, received_at=None):
        if not isinstance(data, dict):
            return False
        received = float(received_at or _now())
        source = str(data.get('source') or 'gadgetbridge')
        metric = str(data.get('metric') or '').strip().lower()
        text = str(data.get('text') or '')
        raw_ts = data.get('ts')
        ts = normalize_ts(raw_ts) or received
        device_meta = data.get('device') if isinstance(data.get('device'), dict) else {}
        device_status = data.get('device_status')
        if device_status is None and metric == 'device_status':
            device_status = data.get('text') or data
        name = device_meta.get('name') or data.get('device_name')
        model = device_meta.get('model') or data.get('device_model')
        battery = device_meta.get('battery', data.get('battery'))
        charging = device_meta.get('charging', data.get('charging'))
        if device_status is not None or device_meta:
            status = None
            if isinstance(device_status, dict):
                status = str(device_status.get('status') or '')
                if not status:
                    connected = device_status.get('connected')
                    if connected is not None:
                        status = 'connected' if connected else 'disconnected'
                    elif device_status.get('state'):
                        state = str(device_status['state']).upper()
                        status = 'connected' if 'CONNECT' in state and 'DISCONNECT' not in state else 'disconnected' if 'NOT_CONNECTED' in state else 'unknown'
                name = device_status.get('name', name)
                model = device_status.get('model', model)
                battery = device_status.get('battery', battery)
                charging = device_status.get('charging', charging)
                status_at = device_status.get('statusAt') or device_status.get('ts') or ts
            elif isinstance(device_status, str):
                low = device_status.strip().lower()
                status = 'connected' if low in ('connected', 'on', '1', 'true') else 'disconnected' if low in ('disconnected', 'off', '0', 'false') else 'unknown'
                status_at = ts
            else:
                status_at = None
            if status is not None and status not in ('connected', 'disconnected', 'unknown'):
                status = 'unknown'
            self._device(status=status, name=name, model=model, battery=battery, charging=charging, status_at=status_at, received_at=received, raw={'device': device_meta, 'device_status': device_status} if device_meta or device_status else None)
            self._packet(received, 'device_status')
        if metric == 'device_status':
            pass
        elif metric == 'samples' and text:
            self._ingest_samples(text, received, source, ts)
        elif metric == 'sleep_summary':
            self._ingest_sleep(text, received, ts)
        elif metric == 'daily_summary':
            self._ingest_daily(text, received, ts)
        elif metric == 'manual':
            self._ingest_manual(text, received, ts)
        elif metric:
            value = _number(data.get('value'))
            canonical, converted = _canonical_metric(metric, value, text)
            self._insert_sample(canonical, ts, converted, text, received, source)
            self._packet(received, canonical)
            if canonical in ('hr', 'stress', 'spo2', 'steps', 'calories', 'distance', 'sleep_minutes', 'active_minutes'):
                self._recompute_daily(local_date(ts))
        return True

    def _ingest_samples(self, text, received, source, fallback_ts):
        try:
            samples = json.loads(text)
        except Exception:
            return
        if not isinstance(samples, list):
            return
        dates = set()
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            ts = normalize_ts(sample.get('t')) or fallback_ts
            dates.add(local_date(ts))
            for key, value in sample.items():
                if key == 't':
                    continue
                num = _number(value)
                if key in ('hr', 'st', 'sp', 'step', 'cal', 'dist', 'int', 'en'):
                    canonical, converted = _canonical_sample_key(key, num, sample)
                    self._insert_sample(canonical, ts, converted, '', received, source)
                elif key == 'k':
                    kind = str(value or '')
                    upper = kind.upper()
                    if any((tag in upper for tag in ('SLEEP', 'AWAKE'))):
                        stage = 'deep' if 'DEEP' in upper else 'light' if 'LIGHT' in upper else 'rem' if 'REM' in upper else 'awake'
                        self._insert_sample('sleep_stage', ts, {'deep': 4, 'light': 2, 'rem': 3, 'awake': 1}[stage], kind, received, source)
                    elif kind:
                        self._insert_sample('activity_kind', ts, None, kind, received, source)
                elif num is not None:
                    self._insert_sample('sample_' + str(key), ts, num, '', received, source)
                    dates.add(local_date(ts))
        for date_str in dates:
            self._recompute_daily(date_str)

    def _ingest_sleep(self, text, received, ts):
        fields = _parse_kv(text)
        total = _number(fields.get('total'))
        if total is None or total <= 0:
            return
        wake = normalize_ts(fields.get('wake')) or ts
        date_str = local_date(wake)
        bedtime = normalize_ts(fields.get('bed'))
        with self._lock:
            conn = self._conn()
            conn.execute('INSERT INTO sleep (date,minutes,deep,light,rem,awake,bed,wake,stages,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET minutes=excluded.minutes,deep=excluded.deep,light=excluded.light,rem=excluded.rem,awake=excluded.awake,bed=excluded.bed,wake=excluded.wake,updated_at=excluded.updated_at', (date_str, total, _number(fields.get('deep')), _number(fields.get('light')), _number(fields.get('rem')), _number(fields.get('awake')), bedtime, wake, None, _now()))
            conn.commit()
        self._insert_sample('sleep_minutes', wake, total, '', received, 'sleep_summary')
        for name in ('deep', 'light', 'rem', 'awake'):
            val = _number(fields.get(name))
            if val is not None:
                self._insert_sample('sleep_' + name, wake, val, '', received, 'sleep_summary')
        self._recompute_daily(date_str)

    def _ingest_daily(self, text, received, ts):
        fields = _parse_kv(text)
        src_ts = normalize_ts(fields.get('ts')) or ts
        date_str = local_date(src_ts)
        mapping = {'hrRest': 'resting_hr', 'hrMin': 'hr_min', 'hrMax': 'hr_max', 'hrAvg': 'hr_avg', 'stressAvg': 'stress_avg', 'stressMin': 'stress_min', 'stressMax': 'stress_max', 'spo2Avg': 'spo2_avg', 'spo2Min': 'spo2_min', 'spo2Max': 'spo2_max', 'steps': 'steps', 'cal': 'calories', 'standing': 'standing', 'loadDay': 'training_load_day', 'loadWeek': 'training_load_week', 'loadLevel': 'training_load_level'}
        updates = {col: _number(fields.get(key)) for key, col in mapping.items()}
        if not any((v is not None for v in updates.values())):
            return
        with self._lock:
            conn = self._conn()
            conn.execute('INSERT OR IGNORE INTO daily (date, updated_at) VALUES (?,?)', (date_str, _now()))
            for col, value in updates.items():
                if value is not None:
                    conn.execute('UPDATE daily SET %s=?, updated_at=? WHERE date=?' % col, (value, _now(), date_str))
            conn.commit()
        self._recompute_daily(date_str)

    def _ingest_manual(self, text, received, ts):
        fields = _parse_kv(text)
        mtype = fields.get('type') or 'unknown'
        sample_ts = normalize_ts(fields.get('ts')) or ts
        value = _number(fields.get('value'))
        if value is None:
            return
        self._insert_sample('manual_%s' % mtype, sample_ts, value, text, received, 'manual')
        self._recompute_daily(local_date(sample_ts))

    def _recompute_daily(self, date_str):
        start, end = day_bounds(date_str)
        with self._lock:
            conn = self._conn()
            row = conn.execute("SELECT SUM(value) AS steps FROM samples WHERE metric='steps' AND ts>=? AND ts<?", (start, end)).fetchone()
            steps = row['steps'] if row and row['steps'] is not None else None
            row = conn.execute("SELECT SUM(value) AS v FROM samples WHERE metric='calories' AND ts>=? AND ts<?", (start, end)).fetchone()
            calories = row['v'] if row and row['v'] is not None else None
            row = conn.execute("SELECT SUM(value) AS v FROM samples WHERE metric='distance' AND ts>=? AND ts<?", (start, end)).fetchone()
            distance = row['v'] if row and row['v'] is not None else None
            row = conn.execute("SELECT AVG(value) AS v FROM samples WHERE metric='hr' AND value>0 AND ts>=? AND ts<?", (start, end)).fetchone()
            hr_avg = row['v'] if row and row['v'] is not None else None
            row = conn.execute("SELECT AVG(value) AS v FROM samples WHERE metric='stress' AND value>0 AND ts>=? AND ts<?", (start, end)).fetchone()
            stress_avg = row['v'] if row and row['v'] is not None else None
            row = conn.execute("SELECT AVG(value) AS v FROM samples WHERE metric='spo2' AND value>0 AND ts>=? AND ts<?", (start, end)).fetchone()
            spo2_avg = row['v'] if row and row['v'] is not None else None
            sleep_row = conn.execute('SELECT minutes FROM sleep WHERE date=?', (date_str,)).fetchone()
            sleep_minutes = sleep_row['minutes'] if sleep_row else None
            conn.execute('INSERT OR IGNORE INTO daily (date, updated_at) VALUES (?,?)', (date_str, _now()))
            updates = {'steps': steps, 'calories': calories, 'distance_km': distance, 'hr_avg': hr_avg, 'stress_avg': stress_avg, 'spo2_avg': spo2_avg, 'sleep_minutes': sleep_minutes}
            for column, value in updates.items():
                if value is not None:
                    conn.execute('UPDATE daily SET %s=?, updated_at=? WHERE date=?' % column, (value, _now(), date_str))
            conn.commit()

    def ingest_health(self, metric, value, text='', ts=None, received_at=None, device=None):
        packet = {'metric': str(metric), 'value': value, 'text': text, 'ts': ts if ts is not None else _now(), 'source': 'migration'}
        if device:
            packet['device'] = device
        return self.ingest_packet(packet, received_at=received_at)

    def _latest(self, metric, start=None, end=None):
        conn = self._conn()
        sql = 'SELECT ts, value, received_at, text FROM samples WHERE metric=?'
        params = [metric]
        if metric in ('hr', 'resting_hr', 'stress', 'spo2'):
            sql += ' AND value>0'
        if start is not None:
            sql += ' AND ts>=? AND ts<?'
            params += [start, end]
        sql += ' ORDER BY ts DESC LIMIT 1'
        return conn.execute(sql, params).fetchone()

    def snapshot(self, date=None, range_key='day'):
        now = _now()
        range_key = (range_key or 'day').lower()
        if range_key not in ('day', 'week', 'month'):
            raise ValueError('range must be day|week|month')
        if not date:
            date = local_date(now)
        start, end = day_bounds(date)
        range_start, range_end = _range_bounds(date, range_key)
        conn = self._conn()
        last_sample_row = conn.execute("SELECT MAX(received_at) AS t FROM samples WHERE metric!='device_status'").fetchone()
        last_sample_at = last_sample_row['t'] if last_sample_row else None
        last_packet_row = conn.execute('SELECT MAX(received_at) AS t FROM packets').fetchone()
        last_packet_at = last_packet_row['t'] if last_packet_row else None
        stale_after, offline_after = (180, 900)
        if last_sample_at is None:
            data_status = 'never'
        elif now - last_sample_at <= stale_after:
            data_status = 'live'
        elif now - last_sample_at <= offline_after:
            data_status = 'stale'
        else:
            data_status = 'offline'
        device_row = conn.execute('SELECT * FROM devices ORDER BY status_at DESC LIMIT 1').fetchone()
        device_status, device_reason = ('unknown', '')
        if device_row is not None and device_row['status_at'] is not None:
            age = now - float(device_row['status_at'])
            raw_status = str(device_row['status'] or 'unknown').lower()
            if age > offline_after:
                device_status = 'unknown'
                device_reason = 'device status expired'
            elif raw_status in ('connected', 'disconnected'):
                device_status = raw_status
                device_reason = 'device reported %s' % raw_status
            else:
                device_status = 'unknown'
                device_reason = 'device status unknown'
        else:
            device_reason = 'no device status received'

        def _series(metric):
            rows = conn.execute('SELECT ts, value FROM samples WHERE metric=? AND ts>=? AND ts<? ORDER BY ts ASC LIMIT 6000', (metric, range_start, range_end)).fetchall()
            return [{'t': row['ts'], 'v': row['value']} for row in rows if row['value'] is not None and (metric not in ('hr', 'stress', 'spo2') or row['value'] > 0)]
        daily_row = conn.execute('SELECT * FROM daily WHERE date=?', (date,)).fetchone()
        sleep_row = conn.execute('SELECT * FROM sleep WHERE date=?', (date,)).fetchone()

        def daily_value(row, name):
            if row is None:
                return None
            try:
                value = row[name]
            except (IndexError, KeyError):
                return None
            if (name.startswith(('hr_', 'stress_', 'spo2_')) or name == 'resting_hr') and value is not None and (value <= 0):
                return None
            return value
        latest_hr = self._latest('hr', start, end)
        latest_stress = self._latest('stress', start, end)
        latest_spo2 = self._latest('spo2', start, end)
        summary = {'steps': daily_value(daily_row, 'steps'), 'stepsGoal': self.goals['stepsGoal'], 'calories': daily_value(daily_row, 'calories'), 'caloriesGoal': self.goals['caloriesGoal'], 'activeMinutes': daily_value(daily_row, 'active_minutes'), 'activeGoal': self.goals['activeGoal'], 'distanceKm': daily_value(daily_row, 'distance_km'), 'heartRate': latest_hr['value'] if latest_hr else None, 'restingHeartRate': daily_value(daily_row, 'resting_hr'), 'stress': latest_stress['value'] if latest_stress else None, 'spo2': latest_spo2['value'] if latest_spo2 else None, 'sleepMinutes': sleep_row['minutes'] if sleep_row else daily_value(daily_row, 'sleep_minutes'), 'sleepScore': None}
        daily = []
        day_count = {'day': 1, 'week': 7, 'month': 30}[range_key]
        base = datetime.strptime(date, '%Y-%m-%d').date()
        for offset in range(day_count - 1, -1, -1):
            day = (base - timedelta(days=offset)).isoformat()
            row = conn.execute('SELECT * FROM daily WHERE date=?', (day,)).fetchone()
            daily.append({'date': day, 'steps': daily_value(row, 'steps'), 'calories': daily_value(row, 'calories'), 'activeMinutes': daily_value(row, 'active_minutes'), 'sleepMinutes': daily_value(row, 'sleep_minutes'), 'hrAvg': daily_value(row, 'hr_avg'), 'stressAvg': daily_value(row, 'stress_avg'), 'spo2Avg': daily_value(row, 'spo2_avg')})
        metrics = []
        known = ['hr', 'resting_hr', 'stress', 'spo2', 'steps', 'calories', 'distance', 'active_minutes', 'sleep_minutes', 'sleep_deep', 'sleep_light', 'sleep_rem', 'sleep_awake']
        seen = set()
        for metric in known:
            row = self._latest(metric, start, end)
            if row is None:
                continue
            label, unit = METRIC_LABELS.get(metric, (metric, ''))
            metrics.append({'key': metric, 'label': label, 'value': row['value'] if row['value'] is not None else None, 'unit': unit, 'observedAt': row['ts'], 'receivedAt': row['received_at']})
            seen.add(metric)
        rows = conn.execute("SELECT metric, MAX(ts) AS ts FROM samples WHERE metric!='device_status' AND ts>=? AND ts<? GROUP BY metric", (start, end)).fetchall()
        for row in rows:
            metric = row['metric']
            if metric in seen or metric in known:
                continue
            latest = self._latest(metric, start, end)
            label, unit = METRIC_LABELS.get(metric, (metric, ''))
            metrics.append({'key': metric, 'label': label, 'value': latest['value'] if latest else None, 'unit': unit, 'observedAt': latest['ts'] if latest else row['ts'], 'receivedAt': latest['received_at'] if latest else None})
        stages = self._sleep_stages(date)
        return {'ok': True, 'now': now, 'date': date, 'range': range_key, 'connection': {'dataStatus': data_status, 'lastPacketAt': last_packet_at, 'lastSampleAt': last_sample_at, 'staleAfterSeconds': stale_after, 'offlineAfterSeconds': offline_after, 'deviceStatus': device_status, 'deviceName': device_row['name'] if device_row else None, 'deviceModel': device_row['model'] if device_row else None, 'battery': device_row['battery'] if device_row else None, 'charging': bool(device_row['charging']) if device_row and device_row['charging'] is not None else None, 'statusAt': device_row['status_at'] if device_row else None, 'bridgeSource': 'Gadgetbridge', 'reason': device_reason}, 'summary': summary, 'series': {'hr': _series('hr'), 'stress': _series('stress'), 'spo2': _series('spo2'), 'steps': _series('steps')}, 'sleep': {'minutes': sleep_row['minutes'] if sleep_row else None, 'deep': sleep_row['deep'] if sleep_row else None, 'light': sleep_row['light'] if sleep_row else None, 'rem': sleep_row['rem'] if sleep_row else None, 'awake': sleep_row['awake'] if sleep_row else None, 'stages': stages}, 'daily': daily, 'metrics': metrics}

    def _sleep_stages(self, date_str):
        date_start, _ = day_bounds(date_str)
        start = date_start - 12 * 3600
        end = date_start + 12 * 3600
        rows = self._conn().execute("SELECT ts, value, text FROM samples WHERE metric='sleep_stage' AND ts>=? AND ts<? ORDER BY ts ASC", (start, end)).fetchall()
        stages = []
        for row in rows:
            name = str(row['text'] or '').upper()
            stage = 'deep' if 'DEEP' in name else 'light' if 'LIGHT' in name else 'rem' if 'REM' in name else 'awake' if 'AWAKE' in name else None
            if stage is None:
                continue
            ts = float(row['ts'])
            if stages and stages[-1]['stage'] == stage:
                stages[-1]['to'] = ts
            else:
                if stages and stages[-1].get('to') is None:
                    stages[-1]['to'] = ts
                stages.append({'from': ts, 'to': ts, 'stage': stage})
        return stages

    def connection_brief(self):
        now = _now()
        conn = self._conn()
        last = conn.execute("SELECT MAX(received_at) AS t FROM samples WHERE metric!='device_status'").fetchone()
        last_sample_at = last['t'] if last else None
        device_row = conn.execute('SELECT * FROM devices ORDER BY status_at DESC LIMIT 1').fetchone()
        if last_sample_at is None:
            data_status = 'never'
        elif now - last_sample_at <= 180:
            data_status = 'live'
        elif now - last_sample_at <= 900:
            data_status = 'stale'
        else:
            data_status = 'offline'
        device_status = 'unknown'
        if device_row is not None and device_row['status_at'] is not None:
            if now - float(device_row['status_at']) <= 900:
                if str(device_row['status']).lower() in ('connected', 'disconnected'):
                    device_status = str(device_row['status']).lower()
        return {'dataStatus': data_status, 'deviceStatus': device_status, 'lastSampleAt': last_sample_at, 'battery': device_row['battery'] if device_row else None, 'charging': bool(device_row['charging']) if device_row and device_row['charging'] is not None else None}

def _number(value):
    try:
        if value is None or value == '':
            return None
        return float(value)
    except (TypeError, ValueError):
        return None

def _canonical_metric(metric, value, text):
    metric = str(metric).strip().lower()
    if metric == 'calories':
        return ('calories', value)
    if metric == 'distance':
        return ('distance', value / 1000.0 if value is not None else None)
    return (metric, value)

def _canonical_sample_key(key, value, sample):
    if key == 'hr':
        return ('hr', value)
    if key == 'st':
        return ('stress', value)
    if key == 'sp':
        return ('spo2', value)
    if key == 'step':
        return ('steps', value)
    if key == 'cal':
        return ('calories', value)
    if key == 'dist':
        return ('distance', value / 100000.0 if value is not None else None)
    if key == 'int':
        return ('sample_intensity', value)
    if key == 'en':
        return ('sample_energy', value)
    return ('sample_' + str(key), value)

def _parse_kv(text):
    fields = {}
    for part in str(text or '').split(','):
        if '=' not in part:
            continue
        key, value = part.split('=', 1)
        fields[key.strip()] = value.strip()
    return fields

class DashboardRuntime:

    def __init__(self):
        self.data_dir = DATA_DIR
        self.traces = None
        self.health = None
        self._lock = threading.RLock()
        self._trace_hook = None

    def configure(self, data_dir=None, trace_db=None, health_db=None):
        with self._lock:
            self.data_dir = data_dir or self.data_dir
            trace_path = trace_db or os.path.join(self.data_dir, 'traces.db')
            health_path = health_db or os.path.join(self.data_dir, 'health.db')
            self.traces = TraceStore(trace_path)
            self.health = HealthStore(health_path)
        return self

    def close(self):
        for store in (self.traces, self.health):
            if store is not None:
                try:
                    store.close()
                except Exception:
                    pass

    def install_trace_hooks(self):
        hook = self.trace_event
        with self._lock:
            self._trace_hook = hook
        try:
            import aux_runtime
            aux_runtime.set_trace_hook(hook)
        except Exception:
            pass
        try:
            import qwen_runtime
            qwen_runtime.set_trace_hook(hook)
        except Exception:
            pass

    def trace_event(self, event):
        try:
            if not isinstance(event, dict) or self.traces is None:
                return None
            kind = event.get('kind')
            if kind == 'start':
                return self.traces.start_call(event.get('provider'), event.get('model'), event.get('purpose'), event.get('request') or {}, input_text=event.get('input_text'), chat=event.get('chat'))
            if kind == 'finish':
                self.traces.finish_call(event.get('call_id'), response=event.get('response'), output=event.get('output'), reasoning=event.get('reasoning'), usage=event.get('usage'), ms=event.get('ms'))
            elif kind == 'error':
                self.traces.fail_call(event.get('call_id'), event.get('error_type') or 'error', http_status=event.get('status'), reason_code=event.get('reason_code'), retryable=event.get('retryable'), output=event.get('output'), reasoning=event.get('reasoning'), raw_response=event.get('raw_response'), usage=event.get('usage'), ms=event.get('ms'))
        except Exception:
            pass
        return None

    def ingest_packet(self, data, received_at=None):
        try:
            if self.health is None:
                self.configure()
            return self.health.ingest_packet(data, received_at=received_at)
        except Exception:
            return False

    def ingest_health(self, *args, **kwargs):
        try:
            if self.health is None:
                self.configure()
            return self.health.ingest_health(*args, **kwargs)
        except Exception:
            return False

    def health_snapshot(self, date=None, range_key='day'):
        if self.health is None:
            self.configure()
        return self.health.snapshot(date=date, range_key=range_key)

    def handle_get(self, path, query):
        if self.traces is None:
            self.configure()
        if path == '/api/dashboard/traces':
            call_id = query.get('id')
            if call_id:
                call = self.traces.get_call(call_id)
                if call is None:
                    return (404, {'ok': False, 'error': 'trace not found'})
                return (200, {'ok': True, 'call': call})
            try:
                limit = int(query.get('limit') or 100)
            except (TypeError, ValueError):
                return (400, {'ok': False, 'error': 'limit must be an integer'})
            if limit < 1 or limit > LIST_MAX_LIMIT:
                return (400, {'ok': False, 'error': 'limit must be between 1 and %d' % LIST_MAX_LIMIT})
            model = query.get('model') or None
            if model is not None and len(str(model)) > 200:
                return (400, {'ok': False, 'error': 'model is too long'})
            provider = query.get('provider') or None
            date = query.get('date') or None
            range_key = query.get('range') or 'day'
            if date:
                try:
                    datetime.strptime(str(date), '%Y-%m-%d')
                except ValueError:
                    return (400, {'ok': False, 'error': 'date must be YYYY-MM-DD'})
            if str(range_key).lower() not in ('day', 'week', 'month'):
                return (400, {'ok': False, 'error': 'range must be day|week|month'})
            try:
                return (200, self.traces.list_calls(limit=limit, model=model, provider=provider, date=date, range_key=range_key))
            except Exception as exc:
                return (500, {'ok': False, 'error': type(exc).__name__})
        if path == '/api/dashboard/health':
            date = query.get('date') or None
            range_key = (query.get('range') or 'day').lower()
            if date:
                try:
                    datetime.strptime(str(date), '%Y-%m-%d')
                except ValueError:
                    return (400, {'ok': False, 'error': 'date must be YYYY-MM-DD'})
            if range_key not in ('day', 'week', 'month'):
                return (400, {'ok': False, 'error': 'range must be day|week|month'})
            try:
                return (200, self.health_snapshot(date=date, range_key=range_key))
            except ValueError as exc:
                return (400, {'ok': False, 'error': str(exc)})
            except Exception as exc:
                return (500, {'ok': False, 'error': type(exc).__name__})
        return None

    def state_fields(self):
        fields = {'traces': {'total': 0, 'running': 0, 'models': []}, 'wearable': {}}
        try:
            if self.traces is not None:
                fields['traces'] = self.traces.summary()
        except Exception:
            pass
        try:
            if self.health is not None:
                fields['wearable'] = self.health.connection_brief()
        except Exception:
            pass
        return fields
RUNTIME = DashboardRuntime()

def configure(data_dir=None, trace_db=None, health_db=None):
    return RUNTIME.configure(data_dir=data_dir, trace_db=trace_db, health_db=health_db)

def install_trace_hooks():
    return RUNTIME.install_trace_hooks()

def start_call(provider, model, purpose, request, input_text=None, chat=None):
    try:
        if RUNTIME.traces is None:
            return None
        return RUNTIME.traces.start_call(provider, model, purpose, request, input_text=input_text, chat=chat)
    except Exception:
        return None

def finish_call(call_id, response=None, output=None, reasoning=None, usage=None, ms=None, status='complete'):
    try:
        if RUNTIME.traces is not None:
            RUNTIME.traces.finish_call(call_id, response=response, output=output, reasoning=reasoning, usage=usage, ms=ms, status=status)
    except Exception:
        pass

def fail_call(call_id, error_type, http_status=None):
    try:
        if RUNTIME.traces is not None:
            RUNTIME.traces.fail_call(call_id, error_type, http_status=http_status)
    except Exception:
        pass

def ingest_packet(data, received_at=None):
    return RUNTIME.ingest_packet(data, received_at=received_at)

def ingest_health(*args, **kwargs):
    return RUNTIME.ingest_health(*args, **kwargs)

def handle_get(path, query):
    return RUNTIME.handle_get(path, query)

def state_fields():
    return RUNTIME.state_fields()

def health_snapshot(date=None, range_key='day'):
    return RUNTIME.health_snapshot(date=date, range_key=range_key)

def reset_for_tests():
    global RUNTIME
    RUNTIME = DashboardRuntime()

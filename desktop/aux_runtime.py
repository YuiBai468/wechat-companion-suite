import json
import hashlib
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
try:
    import config_loader
except Exception:
    config_loader = None
BOT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(BOT_ROOT, 'bot-config.json')
AUX_PROVIDER = 'mimo'
ALLOWED_AUX_HOST = 'api.xiaomimimo.com'
DEFAULT_IMAGE_MODEL = 'qwen-image-3.0-pro'
DEFAULTS = {'provider': AUX_PROVIDER, 'baseUrl': 'https://api.xiaomimimo.com/v1', 'model': 'mimo-v2.6-flash', 'credentialsFile': '', 'credentialFormat': 'env', 'thinking': 'low', 'maxTokens': 4096, 'timeoutSeconds': 60}
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 0.6
_lock = threading.Lock()
_config_path_override = None
_config = None
_config_mtime = None
_key_loader_override = None
_transport_override = None
_telemetry_hook = None
_trace_hook = None
_key_cache = None
_last_call = {}
_REFUSAL_COOLDOWN_SECONDS = 300.0
_refusal_cooldown = {}
_VENDOR_RISK_REFUSAL = 'the request was rejected because it was considered high risk'
_SAFE_MESSAGES = {'http_401': 'MiMo request failed with HTTP 401', 'http_403': 'MiMo request failed with HTTP 403', 'http_429': 'MiMo request failed with HTTP 429', 'http_5xx': 'MiMo server error', 'http_error': 'MiMo request failed', 'network_error': 'MiMo network error', 'timeout': 'MiMo request timed out', 'invalid_json': 'MiMo returned invalid JSON', 'invalid_response': 'MiMo returned an invalid response', 'empty_output': 'MiMo returned no usable output', 'incomplete_output': 'MiMo response was incomplete', 'response_failed': 'MiMo response failed', 'response_error': 'MiMo response reported an error', 'refusal': 'MiMo response was refused', 'vendor_risk_refusal': 'MiMo refused the request as high risk', 'refusal_cooldown': 'MiMo recently refused the same request'}
_RETRYABLE_REASONS = {'http_429': True, 'http_5xx': True, 'network_error': True, 'timeout': True}
_REFUSAL_REASONS = {'refusal', 'vendor_risk_refusal'}
_BEARER_RE = re.compile('(?i)^\\s*(bearer|basic)\\s+\\S+\\s*$')

class AuxError(Exception):

    def __init__(self, message='', status=None, reason_code=None, retryable=None, output='', reasoning='', raw_response=None, usage=None, elapsed_ms=None):
        super().__init__(str(message))
        self.status = status
        self.reason_code = reason_code or 'aux_error'
        if retryable is None:
            retryable = _RETRYABLE_REASONS.get(self.reason_code, False)
        self.retryable = bool(retryable)
        self.output = output or ''
        self.reasoning = reasoning or ''
        self.raw_response = raw_response
        self.usage = usage or {}
        self.elapsed_ms = elapsed_ms

    @property
    def safe_message(self):
        return _SAFE_MESSAGES.get(self.reason_code, 'MiMo request failed')

class AuxConfigError(AuxError):
    pass

class AuxRequestError(AuxError):

    def __init__(self, message='', status=None, **kwargs):
        super().__init__(message, status=status, **kwargs)

class AuxRefusalError(AuxRequestError):
    pass

class AuxIncompleteError(AuxRequestError):
    pass

def _provider_error(cls, reason_code, status=None, **kwargs):
    return cls(_SAFE_MESSAGES.get(reason_code, 'MiMo request failed'), status=status, reason_code=reason_code, **kwargs)

def _usage_dict(raw_usage):
    raw_usage = dict(raw_usage or {})
    return dict(raw_usage, prompt_tokens=raw_usage.get('input_tokens', 0), completion_tokens=raw_usage.get('output_tokens', 0), prompt_tokens_details=raw_usage.get('input_tokens_details') or {}, completion_tokens_details=raw_usage.get('output_tokens_details') or {})

def _request_digest(body, purpose):
    canonical = json.dumps({'purpose': str(purpose or ''), 'body': body}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()

def _cooldown_key(model, purpose, digest):
    return (str(model or ''), str(purpose or ''), digest)

def _cooldown_active(model, purpose, digest):
    now = time.monotonic()
    key = _cooldown_key(model, purpose, digest)
    with _lock:
        expiry = _refusal_cooldown.get(key)
        if expiry is None:
            return False
        if expiry <= now:
            _refusal_cooldown.pop(key, None)
            return False
        return True

def _set_refusal_cooldown(model, purpose, digest):
    key = _cooldown_key(model, purpose, digest)
    with _lock:
        _refusal_cooldown[key] = time.monotonic() + _REFUSAL_COOLDOWN_SECONDS

def _normalize_risk_text(value):
    text = str(value or '').strip().casefold()
    text = re.sub('\\s+', ' ', text)
    return text.rstrip('.').strip()

def _is_vendor_risk_refusal(value):
    return _normalize_risk_text(value) == _VENDOR_RISK_REFUSAL

def set_config_path(path):
    global _config_path_override, _config, _config_mtime
    with _lock:
        _config_path_override = os.path.abspath(path) if path else None
        _config = None
        _config_mtime = None

def _config_file():
    return _config_path_override or DEFAULT_CONFIG_PATH

def _normalize_config(raw):
    cfg = dict(DEFAULTS)
    cfg.update(raw or {})
    provider = str(cfg.get('provider') or '').strip().lower()
    if provider != AUX_PROVIDER:
        raise AuxConfigError("auxiliary.provider must be 'mimo'; refusing to route auxiliary tasks to any other provider")
    base_url = str(cfg.get('baseUrl') or '').strip().rstrip('/')
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != 'https' or (parsed.hostname or '').lower() != ALLOWED_AUX_HOST:
        raise AuxConfigError('auxiliary.baseUrl must be https://api.xiaomimimo.com/v1; third-party resale hosts are not allowed')
    model = str(cfg.get('model') or '').strip()
    if not model:
        raise AuxConfigError('auxiliary.model is not configured')
    credentials = str(cfg.get('credentialsFile') or '').strip()
    credential_format = str(cfg.get('credentialFormat') or 'env').strip().lower()
    if credential_format not in ('env', 'dpapi', ''):
        raise AuxConfigError("auxiliary.credentialFormat must be 'env' or 'dpapi'")
    if credential_format == 'dpapi' and not credentials:
        raise AuxConfigError('auxiliary.credentialsFile is required for dpapi credentials')
    thinking = str(cfg.get('thinking') or 'low').strip().lower()
    if thinking not in ('low', 'disabled', 'off', 'none', 'false', '0'):
        raise AuxConfigError('auxiliary.thinking must be low or none')
    thinking = 'low' if thinking == 'low' else 'none'
    try:
        max_tokens = int(cfg.get('maxTokens') or 4096)
    except (TypeError, ValueError):
        max_tokens = 4096
    if max_tokens <= 0:
        max_tokens = 4096
    try:
        timeout_seconds = float(cfg.get('timeoutSeconds') or 60)
    except (TypeError, ValueError):
        timeout_seconds = 60.0
    if timeout_seconds <= 0:
        timeout_seconds = 60.0
    return {'provider': AUX_PROVIDER, 'baseUrl': base_url, 'model': model, 'credentialsFile': credentials, 'credentialFormat': credential_format or 'env', 'thinking': thinking, 'maxTokens': max_tokens, 'timeoutSeconds': timeout_seconds}

def _load_config():
    global _config, _config_mtime
    path = _config_file()
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        mtime = None
    with _lock:
        if _config is not None and mtime == _config_mtime:
            return dict(_config)
    raw_aux = {}
    if mtime is not None:
        try:
            if config_loader is not None:
                raw_aux = config_loader.load_section(path, 'auxiliary', DEFAULTS, BOT_ROOT)
            else:
                with open(path, encoding='utf-8') as handle:
                    raw_aux = (json.load(handle) or {}).get('auxiliary') or {}
        except Exception:
            raise AuxConfigError('cannot read auxiliary settings from the bot config')
    try:
        cfg = _normalize_config(raw_aux)
    except AuxConfigError:
        raise
    except Exception:
        raise AuxConfigError('invalid auxiliary configuration')
    with _lock:
        _config = cfg
        _config_mtime = mtime
        return dict(cfg)

def current_config():
    cfg = _load_config()
    return {'provider': cfg['provider'], 'baseUrl': cfg['baseUrl'], 'model': cfg['model'], 'thinking': cfg['thinking'], 'maxTokens': cfg['maxTokens'], 'timeoutSeconds': cfg['timeoutSeconds']}

def dashboard_fields(image_model=DEFAULT_IMAGE_MODEL):
    fields = {'auxProvider': '', 'auxModel': '', 'auxBaseUrl': '', 'auxThinking': '', 'imageModel': str(image_model or '')}
    try:
        cfg = _load_config()
    except Exception:
        return fields
    fields.update({'auxProvider': AUX_PROVIDER, 'auxModel': cfg['model'], 'auxBaseUrl': cfg['baseUrl'], 'auxThinking': cfg['thinking']})
    return fields

def _credential_path(cfg):
    path = str(cfg.get('credentialsFile') or '').strip()
    if not path:
        return ''
    if not os.path.isabs(path):
        path = os.path.join(BOT_ROOT, path)
    return path

def _load_api_key():
    global _key_cache
    if _key_loader_override is not None:
        return _key_loader_override()
    env_key = os.environ.get('MIMO_API_KEY', '').strip()
    if env_key:
        return env_key
    cfg = _load_config()
    path = _credential_path(cfg)
    if not path:
        raise AuxConfigError('MiMo API key is not configured (set MIMO_API_KEY)')
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        raise AuxConfigError('MiMo credential file is missing')
    with _lock:
        if _key_cache is not None and _key_cache[0] == (path, mtime):
            return _key_cache[1]
    try:
        with open(path, 'rb') as handle:
            blob = handle.read()
    except OSError:
        raise AuxConfigError('MiMo credential file cannot be read')
    try:
        import win32crypt
        _description, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        key = data.decode('utf-8').strip()
    except Exception:
        raise AuxConfigError('cannot decrypt the MiMo credential with DPAPI')
    if not key:
        raise AuxConfigError('MiMo credential is empty')
    with _lock:
        _key_cache = ((path, mtime), key)
    return key

def set_telemetry_hook(hook):
    global _telemetry_hook
    _telemetry_hook = hook

def set_trace_hook(hook):
    global _trace_hook
    _trace_hook = hook

def _trace_start(body, purpose):
    hook = _trace_hook
    if hook is None:
        return None
    try:
        return hook({'kind': 'start', 'provider': AUX_PROVIDER, 'model': body.get('model'), 'purpose': purpose, 'request': body})
    except Exception:
        return None

def _trace_finish(trace_id, response, content, thinking, usage, ms):
    hook = _trace_hook
    if hook is None or trace_id is None:
        return
    try:
        hook({'kind': 'finish', 'call_id': trace_id, 'response': response, 'output': content, 'reasoning': thinking, 'usage': usage, 'ms': ms})
    except Exception:
        pass

def _trace_error(trace_id, exc):
    hook = _trace_hook
    if hook is None or trace_id is None:
        return
    try:
        event = {'kind': 'error', 'call_id': trace_id, 'error_type': type(exc).__name__, 'status': getattr(exc, 'status', None), 'reason_code': getattr(exc, 'reason_code', None), 'retryable': getattr(exc, 'retryable', None), 'message': getattr(exc, 'safe_message', None), 'output': getattr(exc, 'output', None), 'reasoning': getattr(exc, 'reasoning', None), 'raw_response': getattr(exc, 'raw_response', None), 'usage': getattr(exc, 'usage', None), 'ms': getattr(exc, 'elapsed_ms', None)}
        hook(event)
    except Exception:
        pass

def _emit(event):
    hook = _telemetry_hook
    if hook is None:
        return
    try:
        hook(dict(event))
    except Exception:
        pass

def _open(request, timeout):
    if _transport_override is not None:
        return _transport_override(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)

def _post_json(body, purpose, timeout, request_meta):
    cfg = _load_config()
    key = _load_api_key()
    url = cfg['baseUrl'] + '/responses'
    payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(url, data=payload, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    _emit(dict({'kind': 'request', 'provider': AUX_PROVIDER, 'model': body.get('model'), 'purpose': purpose, 'thinking': (body.get('reasoning') or {}).get('effort'), 'max_tokens': body.get('max_output_tokens')}, **request_meta))
    last_status = None
    last_error_type = None
    last_reason = 'http_error'
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            response = _open(request, timeout=timeout)
            with response:
                raw = response.read()
            try:
                return json.loads(raw.decode('utf-8'))
            except Exception:
                raise _provider_error(AuxRequestError, 'invalid_json')
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            last_error_type = 'HTTPError'
            try:
                exc.close()
            except Exception:
                pass
            if last_status in (401, 403):
                reason = 'http_401' if last_status == 401 else 'http_403'
                _emit({'kind': 'error', 'provider': AUX_PROVIDER, 'model': body.get('model'), 'purpose': purpose, 'status': last_status, 'error_type': last_error_type, 'reason_code': reason, 'retryable': False})
                raise _provider_error(AuxRequestError, reason, status=last_status)
            retryable = last_status == 429 or 500 <= last_status < 600
            if last_status == 429:
                last_reason = 'http_429'
            elif 500 <= last_status < 600:
                last_reason = 'http_5xx'
            else:
                last_reason = 'http_error'
            if retryable and attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                continue
            _emit({'kind': 'error', 'provider': AUX_PROVIDER, 'model': body.get('model'), 'purpose': purpose, 'status': last_status, 'error_type': last_error_type, 'reason_code': last_reason, 'retryable': retryable})
            raise _provider_error(AuxRequestError, last_reason, status=last_status, retryable=retryable)
        except AuxRequestError:
            raise
        except urllib.error.URLError as exc:
            last_error_type = 'URLError'
            last_reason = 'network_error'
            try:
                if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                    last_reason = 'timeout'
            except Exception:
                pass
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                continue
            _emit({'kind': 'error', 'provider': AUX_PROVIDER, 'model': body.get('model'), 'purpose': purpose, 'status': None, 'error_type': last_error_type, 'reason_code': last_reason, 'retryable': True})
            raise _provider_error(AuxRequestError, last_reason, retryable=True)
        except (TimeoutError, OSError) as exc:
            if isinstance(exc, TimeoutError):
                last_error_type = 'timeout'
                last_reason = 'timeout'
            else:
                last_error_type = 'network_error'
                last_reason = 'network_error'
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                continue
            _emit({'kind': 'error', 'provider': AUX_PROVIDER, 'model': body.get('model'), 'purpose': purpose, 'status': None, 'error_type': last_error_type, 'reason_code': last_reason, 'retryable': True})
            raise _provider_error(AuxRequestError, last_reason, retryable=True)
    _emit({'kind': 'error', 'provider': AUX_PROVIDER, 'model': body.get('model'), 'purpose': purpose, 'status': last_status, 'error_type': last_error_type or 'unknown', 'reason_code': last_reason, 'retryable': False})
    raise _provider_error(AuxRequestError, last_reason, status=last_status)

def _content_to_text(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get('text') or ''))
            else:
                parts.append(str(item))
        return ''.join(parts).strip()
    if isinstance(value, dict):
        return str(value.get('text') or value.get('content') or '').strip()
    return str(value).strip()

def _validate_text_only(messages):
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise AuxConfigError('auxiliary runtime is text-only: message %d is not an object' % index)
        content = message.get('content')
        if isinstance(content, str):
            continue
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get('type') != 'text':
                    raise AuxConfigError('auxiliary runtime is text-only: refusing multimodal content; use qwen_runtime for image/audio input')
                if not isinstance(part.get('text'), str):
                    raise AuxConfigError('auxiliary runtime is text-only: text part is missing its text')
            continue
        raise AuxConfigError('auxiliary runtime is text-only: unsupported content type')

def _build_messages(system, history, user, messages):
    if messages is not None:
        return [dict(m) for m in messages]
    built = []
    if system:
        built.append({'role': 'system', 'content': str(system)})
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        if role not in ('user', 'assistant'):
            continue
        content = item.get('content')
        if content is None:
            continue
        built.append({'role': role, 'content': content})
    built.append({'role': 'user', 'content': str(user or '')})
    return built

def text_chat(system=None, history=None, user=None, messages=None, images=None, purpose='aux', max_tokens=None, temperature=None, timeout=None, return_meta=False):
    if images:
        raise AuxConfigError('auxiliary runtime is text-only: refusing image input; use qwen_runtime for multimodal work')
    built = _build_messages(system, history, user, messages)
    _validate_text_only(built)
    cfg = _load_config()
    inputs = []
    for message in built:
        content = message['content']
        if isinstance(content, list):
            content = '\n'.join((part['text'] for part in content))
        inputs.append({'role': message['role'], 'content': content})
    budget = int(max_tokens or cfg['maxTokens'])
    if cfg['thinking'] == 'low':
        budget = max(budget, int(cfg['maxTokens']))
    body = {'model': cfg['model'], 'input': inputs, 'max_output_tokens': budget, 'reasoning': {'effort': cfg['thinking']}, 'stream': False}
    if temperature is not None and cfg['thinking'] == 'none':
        body['temperature'] = float(temperature)
    request_meta = {'system': str(system or ''), 'user': str(user or '')[:6000], 'history': len(history or [])}
    started = time.time()
    trace_id = _trace_start(body, purpose)
    try:
        digest = _request_digest(body, purpose)
        if _cooldown_active(body['model'], purpose, digest):
            raise _provider_error(AuxRefusalError, 'refusal_cooldown')
        response = _post_json(body, purpose, timeout or cfg['timeoutSeconds'], request_meta)
        elapsed_ms = int((time.time() - started) * 1000)
        if not isinstance(response, dict):
            raise _provider_error(AuxIncompleteError, 'invalid_response', elapsed_ms=elapsed_ms)
        raw_usage = response.get('usage') or {}
        usage = _usage_dict(raw_usage)
        texts, thoughts, refusals = ([], [], [])
        explicit_refusal = False
        output = response.get('output')
        if output is not None and (not isinstance(output, list)):
            raise _provider_error(AuxIncompleteError, 'invalid_response', raw_response=response, elapsed_ms=elapsed_ms)
        for item in output or []:
            if not isinstance(item, dict):
                continue
            item_type = item.get('type')
            if item_type == 'message':
                parts = item.get('content')
                if isinstance(parts, dict):
                    parts = [parts]
                elif not isinstance(parts, list):
                    parts = []
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    part_type = part.get('type')
                    if part_type == 'output_text':
                        texts.append(str(part.get('text') or ''))
                    elif part_type == 'refusal':
                        explicit_refusal = True
                        refusals.append(str(part.get('refusal') or part.get('text') or ''))
            elif item_type == 'refusal':
                explicit_refusal = True
                refusals.append(str(item.get('refusal') or item.get('text') or ''))
            elif item_type == 'reasoning':
                parts = item.get('content')
                if parts is None:
                    parts = item.get('summary')
                if isinstance(parts, dict):
                    parts = [parts]
                elif not isinstance(parts, list):
                    parts = []
                for part in parts:
                    if isinstance(part, dict) and part.get('type') in ('reasoning_text', 'summary_text'):
                        thoughts.append(str(part.get('text') or ''))
        top_refusal = response.get('refusal')
        if isinstance(top_refusal, str) and top_refusal.strip():
            explicit_refusal = True
            refusals.append(top_refusal)
        content = ''.join(texts).strip() or str(response.get('output_text') or '').strip()
        thinking = ''.join(thoughts).strip()
        status = str(response.get('status') or '').strip().lower()
        refusal_text = ''.join(refusals).strip()

        def _fail(cls, reason, output_text=None, **extra):
            return _provider_error(cls, reason, output=content if output_text is None else output_text, reasoning=thinking, raw_response=response, usage=raw_usage, elapsed_ms=elapsed_ms, **extra)
        if explicit_refusal:
            _set_refusal_cooldown(body['model'], purpose, digest)
            raise _fail(AuxRefusalError, 'refusal', output_text=content or refusal_text)
        if _is_vendor_risk_refusal(content):
            _set_refusal_cooldown(body['model'], purpose, digest)
            raise _fail(AuxRefusalError, 'vendor_risk_refusal')
        if status == 'failed':
            raise _fail(AuxRequestError, 'response_failed')
        if status == 'error':
            raise _fail(AuxRequestError, 'response_error')
        if response.get('error'):
            raise _fail(AuxRequestError, 'response_error')
        if status == 'incomplete' or response.get('incomplete_details'):
            raise _fail(AuxIncompleteError, 'incomplete_output')
        if status not in ('', 'completed'):
            raise _fail(AuxIncompleteError, 'invalid_response')
        if not content:
            raise _fail(AuxIncompleteError, 'empty_output')
        finish = 'stop' if status == 'completed' else (response.get('incomplete_details') or {}).get('reason') or status
        meta = {'provider': AUX_PROVIDER, 'model': body['model'], 'purpose': purpose, 'ms': elapsed_ms, 'usage': usage, 'content': content, 'thinking': thinking, 'finish': finish, 'effort': cfg['thinking']}
        global _last_call
        with _lock:
            _last_call = dict(meta)
        _emit(dict({'kind': 'reply'}, **meta))
        _trace_finish(trace_id, response, content, thinking, usage, elapsed_ms)
        if return_meta:
            return dict(meta)
        return content
    except Exception as exc:
        if isinstance(exc, AuxError) and exc.elapsed_ms is None:
            exc.elapsed_ms = max(0, int((time.time() - started) * 1000))
        _trace_error(trace_id, exc)
        raise

def last_call():
    with _lock:
        return dict(_last_call)

def set_key_loader(loader):
    global _key_loader_override, _key_cache
    _key_loader_override = loader
    _key_cache = None

def set_transport(transport):
    global _transport_override
    _transport_override = transport

def reset_for_tests():
    global _config_path_override, _config, _config_mtime
    global _key_loader_override, _transport_override, _telemetry_hook
    global _trace_hook
    global _key_cache, _last_call
    _config_path_override = None
    _config = None
    _config_mtime = None
    _key_loader_override = None
    _transport_override = None
    _telemetry_hook = None
    _trace_hook = None
    _key_cache = None
    _last_call = {}
    _refusal_cooldown.clear()

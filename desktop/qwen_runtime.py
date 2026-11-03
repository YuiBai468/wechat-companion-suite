import base64
import json
import os
import re
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
QWEN_PROVIDER = 'qwen'
DASHSCOPE_HOST = 'dashscope.aliyuncs.com'
VISION_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
VISION_MODEL = 'qwen3-vl-plus'
ASR_URL = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
ASR_MODEL = 'qwen3-asr-flash'
DEFAULT_CREDENTIAL_REF = 'QWEN_API_KEY'
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

class QwenError(Exception):
    pass

class QwenConfigError(QwenError):
    pass

class QwenRequestError(QwenError):

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

def set_config_path(path):
    global _config_path_override, _config, _config_mtime
    with _lock:
        _config_path_override = os.path.abspath(path) if path else None
        _config = None
        _config_mtime = None

def _config_file():
    return _config_path_override or DEFAULT_CONFIG_PATH

def _validate_dashscope_url(url, expected_path_prefix, label):
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme != 'https' or (parsed.hostname or '').lower() != DASHSCOPE_HOST:
        raise QwenConfigError('%s must stay on https://%s; third-party hosts are not allowed' % (label, DASHSCOPE_HOST))
    if not parsed.path.startswith(expected_path_prefix):
        raise QwenConfigError('%s has an unexpected endpoint path' % label)
    return url

def _normalize_config(raw):
    vision = dict(raw.get('vision') or {})
    asr = dict(raw.get('asr') or {})
    credentials_file = str(raw.get('credentialsFile') or '').strip()
    if credentials_file and not os.path.isabs(credentials_file):
        credentials_file = os.path.join(BOT_ROOT, credentials_file)
    if str(vision.get('provider') or 'qwen').strip().lower() != QWEN_PROVIDER:
        raise QwenConfigError("vision.provider must be 'qwen'")
    vision_base = str(vision.get('baseUrl') or VISION_BASE_URL).strip().rstrip('/')
    _validate_dashscope_url(vision_base, '/compatible-mode/v1', 'vision.baseUrl')
    vision_model = str(vision.get('model') or VISION_MODEL).strip()
    if not vision_model:
        raise QwenConfigError('vision.model is not configured')
    vision_ref = str(vision.get('ref') or DEFAULT_CREDENTIAL_REF).strip()
    try:
        vision_max_tokens = int(vision.get('maxTokens') or 1000)
    except (TypeError, ValueError):
        vision_max_tokens = 1000
    try:
        vision_timeout = float(vision.get('timeoutSeconds') or 180)
    except (TypeError, ValueError):
        vision_timeout = 180.0
    if str(asr.get('provider') or 'qwen').strip().lower() != QWEN_PROVIDER:
        raise QwenConfigError("asr.provider must be 'qwen'")
    asr_model = str(asr.get('model') or ASR_MODEL).strip()
    if not asr_model:
        raise QwenConfigError('asr.model is not configured')
    asr_ref = str(asr.get('ref') or DEFAULT_CREDENTIAL_REF).strip()
    asr_language = str(asr.get('language') or 'zh').strip() or 'zh'
    return {'credentialsFile': credentials_file, 'vision': {'provider': QWEN_PROVIDER, 'baseUrl': vision_base, 'model': vision_model, 'ref': vision_ref, 'maxTokens': vision_max_tokens, 'timeoutSeconds': vision_timeout, 'enableThinking': False}, 'asr': {'provider': QWEN_PROVIDER, 'model': asr_model, 'ref': asr_ref, 'language': asr_language}}

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
    raw = {}
    if mtime is not None:
        try:
            if config_loader is not None:
                raw = config_loader.load_config(path, {}, BOT_ROOT)
            else:
                with open(path, encoding='utf-8') as handle:
                    raw = json.load(handle) or {}
        except Exception:
            raise QwenConfigError('cannot read Qwen settings from the bot config')
    cfg = _normalize_config(raw)
    with _lock:
        _config = cfg
        _config_mtime = mtime
        return dict(cfg)

def current_config():
    cfg = _load_config()
    return {'visionProvider': cfg['vision']['provider'], 'visionModel': cfg['vision']['model'], 'visionBaseUrl': cfg['vision']['baseUrl'], 'asrProvider': cfg['asr']['provider'], 'asrModel': cfg['asr']['model'], 'asrLanguage': cfg['asr']['language']}

def dashboard_fields(image_model=''):
    fields = {'visionProvider': '', 'visionModel': '', 'asrProvider': '', 'asrModel': '', 'imageModel': str(image_model or '')}
    try:
        cfg = _load_config()
    except Exception:
        return fields
    fields.update({'visionProvider': cfg['vision']['provider'], 'visionModel': cfg['vision']['model'], 'asrProvider': cfg['asr']['provider'], 'asrModel': cfg['asr']['model']})
    return fields

def _load_api_key(ref):
    global _key_cache
    if _key_loader_override is not None:
        return _key_loader_override(ref)
    for env_name in ('DASHSCOPE_API_KEY', str(ref or '').strip(), 'QWEN_API_KEY'):
        if env_name and os.environ.get(env_name, '').strip():
            return os.environ[env_name].strip()
    cfg = _load_config()
    path = cfg['credentialsFile']
    if not path:
        raise QwenConfigError('QWEN/DashScope API key is not configured (set DASHSCOPE_API_KEY)')
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        raise QwenConfigError('QWEN credential file is missing')
    with _lock:
        if _key_cache is not None and _key_cache[0] == (path, mtime, ref):
            return _key_cache[1]
    try:
        with open(path, encoding='utf-8') as handle:
            raw = handle.read()
    except OSError:
        raise QwenConfigError('QWEN credential file cannot be read')
    match = re.search('(?m)^\\s*%s\\s*:\\s*(.+?)\\s*$' % re.escape(ref), raw)
    if not match:
        raise QwenConfigError('QWEN credential ref %s was not found' % ref)
    key = match.group(1).strip().strip('"\'')
    if not key:
        raise QwenConfigError('QWEN credential is empty')
    with _lock:
        _key_cache = ((path, mtime, ref), key)
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
        return hook({'kind': 'start', 'provider': QWEN_PROVIDER, 'model': body.get('model'), 'purpose': purpose, 'request': body})
    except Exception:
        return None

def _trace_finish(trace_id, response, output, reasoning, usage, ms):
    hook = _trace_hook
    if hook is None or trace_id is None:
        return
    try:
        hook({'kind': 'finish', 'call_id': trace_id, 'response': response, 'output': output, 'reasoning': reasoning, 'usage': usage, 'ms': ms})
    except Exception:
        pass

def _trace_error(trace_id, exc):
    hook = _trace_hook
    if hook is None or trace_id is None:
        return
    try:
        hook({'kind': 'error', 'call_id': trace_id, 'error_type': type(exc).__name__, 'status': getattr(exc, 'status', None)})
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

def _post_json(url, body, ref, purpose, timeout, request_meta):
    cfg = _load_config()
    key = _load_api_key(ref)
    payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(url, data=payload, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    _emit(dict({'kind': 'request', 'provider': QWEN_PROVIDER, 'model': body.get('model'), 'purpose': purpose}, **request_meta))
    last_status = None
    last_type = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            response = _open(request, timeout=timeout)
            with response:
                raw = response.read()
            try:
                return json.loads(raw.decode('utf-8'))
            except Exception:
                raise QwenRequestError('Qwen returned invalid JSON')
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            last_type = 'HTTPError'
            try:
                exc.close()
            except Exception:
                pass
            if (exc.code == 429 or 500 <= exc.code < 600) and attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                continue
            _emit({'kind': 'error', 'provider': QWEN_PROVIDER, 'model': body.get('model'), 'purpose': purpose, 'status': last_status, 'error_type': last_type})
            raise QwenRequestError('Qwen request failed with HTTP %d' % last_status, status=last_status)
        except QwenRequestError:
            raise
        except urllib.error.URLError:
            last_type = 'URLError'
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                continue
            _emit({'kind': 'error', 'provider': QWEN_PROVIDER, 'model': body.get('model'), 'purpose': purpose, 'status': None, 'error_type': last_type})
            raise QwenRequestError('Qwen request failed: URLError')
        except (TimeoutError, OSError) as exc:
            last_type = type(exc).__name__
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                continue
            _emit({'kind': 'error', 'provider': QWEN_PROVIDER, 'model': body.get('model'), 'purpose': purpose, 'status': None, 'error_type': last_type})
            raise QwenRequestError('Qwen request failed: %s' % last_type)
    _emit({'kind': 'error', 'provider': QWEN_PROVIDER, 'model': body.get('model'), 'purpose': purpose, 'status': last_status, 'error_type': last_type or 'unknown'})
    raise QwenRequestError('Qwen request failed')

def _content_to_text(value):
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
        return str(value.get('text') or '').strip()
    return str(value or '').strip()

def _message_summary(messages):
    system = ''
    user = ''
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        content = item.get('content')
        if isinstance(content, list):
            texts = [str(p.get('text') or '') for p in content if isinstance(p, dict) and p.get('type') == 'text']
            text = ' '.join(texts)
        else:
            text = str(content or '')
        if item.get('role') == 'system':
            system = text[:4000]
        elif item.get('role') == 'user':
            user = text[:4000]
    return (system, user)

def vision_chat(messages, purpose='video_summary', max_tokens=None, timeout=None, return_meta=False):
    cfg = _load_config()
    vision = cfg['vision']
    if not messages:
        raise QwenRequestError('vision request has no messages')
    body = {'model': vision['model'], 'messages': messages, 'max_tokens': int(max_tokens or vision['maxTokens']), 'enable_thinking': False, 'stream': False}
    image_count = 0
    for item in messages:
        content = item.get('content') if isinstance(item, dict) else None
        if isinstance(content, list):
            image_count += sum((1 for part in content if isinstance(part, dict) and part.get('type') == 'image_url'))
    system, user = _message_summary(messages)
    started = time.time()
    trace_id = _trace_start(body, purpose)
    try:
        response = _post_json(vision['baseUrl'] + '/chat/completions', body, vision['ref'], purpose, timeout or vision['timeoutSeconds'], {'system': system, 'user': user, 'history': 0, 'imageCount': image_count, 'enableThinking': False})
        elapsed_ms = int((time.time() - started) * 1000)
        choices = response.get('choices') or []
        choice = choices[0] if choices else {}
        message = choice.get('message') or {}
        content = _content_to_text(message.get('content'))
        usage = response.get('usage') or {}
        thinking = _content_to_text(message.get('reasoning_content') or message.get('reasoning') or '')
        meta = {'provider': QWEN_PROVIDER, 'model': vision['model'], 'purpose': purpose, 'ms': elapsed_ms, 'usage': usage, 'content': content, 'thinking': thinking, 'finish': choice.get('finish_reason'), 'imageCount': image_count}
        global _last_call
        with _lock:
            _last_call = dict(meta)
        _emit(dict({'kind': 'reply'}, **meta))
        _trace_finish(trace_id, response, content, thinking, usage, elapsed_ms)
        if return_meta:
            return dict(meta)
        return content
    except Exception as exc:
        _trace_error(trace_id, exc)
        raise

def asr_transcribe(audio_bytes, audio_format='wav', language=None, purpose='asr', return_meta=False):
    cfg = _load_config()
    asr = cfg['asr']
    fmt = str(audio_format or 'wav').strip().lower().lstrip('.')
    if fmt not in ('wav', 'mp3'):
        raise QwenRequestError('Qwen ASR supports wav/mp3 only')
    lang = str(language or asr['language'] or 'zh')
    body = {'model': asr['model'], 'input': {'messages': [{'role': 'user', 'content': [{'audio': 'data:audio/%s;base64,%s' % (fmt, base64.b64encode(audio_bytes).decode())}]}]}, 'parameters': {'asr_options': {'language': lang}}}
    started = time.time()
    trace_id = _trace_start(body, purpose)
    try:
        response = _post_json(ASR_URL, body, asr['ref'], purpose, 300.0, {'system': '', 'user': '[audio]', 'history': 0, 'hasAudio': True, 'audioFormat': fmt})
        elapsed = time.time() - started
        output = response.get('output') or {}
        choices = output.get('choices') or []
        message = (choices[0] if choices else {}).get('message') or {}
        text = ''.join((str(part.get('text') or '') for part in message.get('content') or [])).strip()
        annotations = message.get('annotations') or [{}]
        annotation = annotations[0] if annotations else {}
        emotion = str(annotation.get('emotion') or '').strip().lower()
        detected_language = str(annotation.get('language') or '').strip()
        usage = response.get('usage') or {}
        meta = {'provider': QWEN_PROVIDER, 'model': asr['model'], 'purpose': purpose, 'ms': int(elapsed * 1000), 'usage': usage, 'content': text, 'thinking': '', 'emotion': emotion, 'detectedLanguage': detected_language}
        global _last_call
        with _lock:
            _last_call = dict(meta)
        _emit(dict({'kind': 'reply'}, **meta))
        _trace_finish(trace_id, response, text, '', usage, int(elapsed * 1000))
        result = {'text': text, 'emotion': emotion, 'language': detected_language or lang, 'secs': elapsed, 'usage': usage, 'provider': QWEN_PROVIDER, 'model': asr['model']}
        if return_meta:
            result['meta'] = dict(meta)
        return result
    except Exception as exc:
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

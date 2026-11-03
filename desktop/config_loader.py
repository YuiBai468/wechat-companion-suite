"""Portable configuration and .env loader.

No shell/eval: values of the form ``env:NAME`` or ``${NAME}`` are replaced
only by ``os.environ`` lookups.  Relative filesystem defaults are resolved
against the repository root, never against an author machine path.
"""
import copy
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ENV_FILE = os.path.join(ROOT, ".env")
_ENV_REF = re.compile(r"^(?:env:|\$\{)([A-Za-z_][A-Za-z0-9_]*)\}?$")
_PATH_KEYS = {
    "contextfile", "memoryfile", "logfile", "personafile", "credentialsfile",
    "silkdecoder", "savedir", "datadir", "cachedir", "ffmpeg", "ffprobe",
    "backupdir", "framesdir", "asrdir", "workdir", "root", "referencepath",
}


def load_env_file(path=None):
    """Load KEY=VALUE lines into os.environ without overwriting real env."""
    path = path or os.environ.get("COMPANION_ENV_FILE") or DEFAULT_ENV_FILE
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _env_ref(value):
    if not isinstance(value, str):
        return None
    match = _ENV_REF.match(value.strip())
    return match.group(1) if match else None


def _resolve_value(value, key="", root=ROOT):
    if isinstance(value, dict):
        return {k: _resolve_value(v, k, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(v, "referencePath" if key.lower() == "refs" else key, root) for v in value]
    name = _env_ref(value)
    if name is not None:
        value = os.environ.get(name, "")
    if not isinstance(value, str):
        return value
    if key.lower() in _PATH_KEYS and value.strip():
        text = value.strip()
        if key.lower() in ("ffmpeg", "ffprobe") and not any(c in text for c in ("/", "\\")):
            return text
        if not os.path.isabs(text) and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
            return os.path.normpath(os.path.join(root, text))
    return value


def merge_defaults(defaults, raw):
    result = copy.deepcopy(defaults or {})
    for key, value in (raw or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_defaults(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path, defaults=None, root=None):
    root = os.path.abspath(root or ROOT)
    load_env_file()
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a JSON object")
    return _resolve_value(merge_defaults(defaults, raw), "", root)


def load_section(path, name, defaults=None, root=None):
    config = load_config(path, defaults, root)
    section = config.get(name)
    return section if isinstance(section, dict) else {}


def apply_environment(config):
    """Apply documented runtime overrides without ever writing secrets to disk."""
    data_dir = os.environ.get("DATA_DIR", "").strip()
    if data_dir:
        data_dir = data_dir if os.path.isabs(data_dir) else os.path.join(ROOT, data_dir)
        data_dir = os.path.abspath(data_dir)
        config["contextFile"] = os.path.join(data_dir, "contexts.json")
        config["memoryFile"] = os.path.join(data_dir, "memory.json")
        config["logFile"] = os.path.join(data_dir, "logs", "wechat-bot.log")
        image = config.setdefault("image", {})
        if isinstance(image, dict):
            image["saveDir"] = os.path.join(data_dir, "images")
    ffmpeg = os.environ.get("FFMPEG_BIN", "").strip()
    if ffmpeg:
        config["ffmpeg"] = _resolve_value(ffmpeg, "ffmpeg", ROOT)
    ffprobe = os.environ.get("FFPROBE_BIN", "").strip()
    if ffprobe:
        config["ffprobe"] = _resolve_value(ffprobe, "ffprobe", ROOT)
    silk = os.environ.get("SILK_DECODER", "").strip()
    if silk:
        config["silkDecoder"] = _resolve_value(silk, "silkDecoder", ROOT)
    if not config.get("chats"):
        private = [x.strip() for x in os.environ.get("WECHAT_PRIVATE_CHAT", "").split(",") if x.strip()]
        groups = [x.strip() for x in os.environ.get("WECHAT_GROUP_CHATS", "").split(",") if x.strip()]
        chats = []
        chats.extend({"name": name, "mode": "always", "kind": "private"} for name in private)
        chats.extend({"name": name, "mode": "wake", "kind": "group"} for name in groups)
        config["chats"] = chats
    health = config.setdefault("health", {})
    if isinstance(health, dict) and not str(health.get("token") or "").strip():
        health["token"] = os.environ.get("HEALTH_PUSH_TOKEN", "")
    return config


def check_config(config):
    """Return human-readable warnings; never prints or returns secret values."""
    warnings = []
    if not config.get("model"):
        warnings.append("model is empty")
    if not config.get("baseUrl"):
        warnings.append("baseUrl is empty")
    if not os.environ.get("DEEPSEEK_API_KEY") and not str(config.get("apiKey") or "").strip():
        warnings.append("DeepSeek API key is not set (DEEPSEEK_API_KEY or apiKey)")
    chats = config.get("chats") or []
    if not chats:
        warnings.append("chats is empty; configure WECHAT_PRIVATE_CHAT / WECHAT_GROUP_CHATS")
    aux = config.get("auxiliary") or {}
    if aux.get("enabled", True) and not os.environ.get("MIMO_API_KEY"):
        warnings.append("MiMo key is not set (MIMO_API_KEY); auxiliary model calls stay disabled")
    vision = config.get("vision") or {}
    if vision.get("enabled", True) and not os.environ.get("DASHSCOPE_API_KEY"):
        warnings.append("DashScope key is not set (DASHSCOPE_API_KEY); vision/ASR stay disabled")
    health = config.get("health") or {}
    if health.get("enabled") and not str(health.get("token") or "").strip():
        warnings.append("health endpoint is enabled but token is empty")
    return warnings

import copy
import json
import math
import os
from pathlib import Path
import tempfile

class InstanceLock:

    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, 'a+b')
        if self.file.tell() == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError('此配置已有机器人实例运行，请先停止旧实例') from None
        return self

    def __exit__(self, *args):
        if self.file:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()
            self.file = None

def atomic_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent, prefix=target.name + '.', suffix='.tmp', delete=False) as out:
            temporary = out.name
            json.dump(value, out, ensure_ascii=False, indent=1, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)

def clock_minutes(value):
    try:
        hour, minute = str(value).split(':')
        hour, minute = (int(hour), int(minute))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
    except (ValueError, TypeError):
        pass
    raise ValueError(f'时间必须是有效的 HH:MM：{value}')

def load_config(path, defaults):
    path = Path(path).resolve()
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ValueError('配置文件顶层必须是对象')

    def merge(base, update):
        result = copy.deepcopy(base)
        for key, value in update.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = merge(result[key], value)
            else:
                result[key] = value
        return result
    config = merge(defaults, raw)
    if config.get('provider', 'deepseek') not in ('codex', 'deepseek'):
        raise ValueError('provider 必须是 codex 或 deepseek')
    if not isinstance(config.get('chats', []), list):
        raise ValueError('chats 必须是列表')
    names = set()
    for chat in config.get('chats', []):
        if not isinstance(chat, dict) or not isinstance(chat.get('name'), str) or (not chat['name'].strip()):
            raise ValueError('每个会话必须有非空 name')
        if chat['name'] in names:
            raise ValueError('会话名称重复：' + chat['name'])
        names.add(chat['name'])
        if chat.get('mode', 'wake') not in ('always', 'wake', 'off'):
            raise ValueError('会话 mode 必须是 always/wake/off')
    for section in ('health', 'schedule', 'proactive'):
        if not isinstance(config.get(section, {}), dict):
            raise ValueError(section + ' 必须是对象')
    for key, lo, hi in (('historyLimit', 0, 100000), ('codexTimeout', 1, 1800)):
        if key in config:
            value = config[key]
            if isinstance(value, bool) or not isinstance(value, (float, int)) or (not math.isfinite(value)) or (not lo <= value <= hi):
                raise ValueError(f'{key} 必须在 {lo}..{hi} 之间')
    if config.get('provider') == 'codex' and config.get('codexReasoningEffort', 'low') not in ('low', 'medium', 'high', 'xhigh', 'max'):
        raise ValueError('codexReasoningEffort 不受支持')
    health = config.get('health', {})
    port = health.get('port', 8765)
    if isinstance(port, bool) or not isinstance(port, int) or (not 0 <= port <= 65535):
        raise ValueError('health.port 必须是 0..65535 的整数（0 供自检使用）')
    schedule = config.get('schedule', {})
    if not isinstance(schedule.get('meals', []), list):
        raise ValueError('schedule.meals 必须是列表')
    for item in [schedule.get('wake', {}), schedule.get('sleep', {})] + schedule.get('meals', []):
        if not isinstance(item, dict):
            raise ValueError('日程项目必须是对象')
        if item.get('enabled'):
            clock_minutes(item.get('at'))
            jitter = item.get('jitterMin', 0)
            if isinstance(jitter, bool) or not isinstance(jitter, int) or (not 0 <= jitter <= 180):
                raise ValueError('jitterMin 必须在 0..180 之间')
    for key in ('insomnia', 'randomChats'):
        item = schedule.get(key, {})
        if not isinstance(item, dict):
            raise ValueError('schedule.' + key + ' 必须是对象')
        if item.get('enabled'):
            clock_minutes(item.get('from'))
            clock_minutes(item.get('to'))
    for key in ('contextFile', 'logFile', 'personaFile', 'credentialsFile', 'codexHome'):
        if config.get(key):
            p = Path(config[key])
            config[key] = str(p if p.is_absolute() else path.parent / p)
    for chat in config.get('chats', []):
        if chat.get('personaFile'):
            p = Path(chat['personaFile'])
            chat['personaFile'] = str(p if p.is_absolute() else path.parent / p)
    return config
HEALTH_METRICS = frozenset(('hr', 'stress', 'spo2', 'steps', 'sleep', 'awake', 'workout', 'calories', 'distance', 'workout_steps', 'samples', 'sleep_summary', 'daily_summary', 'manual'))

def health_payload(body, now):
    data = json.loads(body.decode('utf-8'))
    if not isinstance(data, dict):
        raise ValueError('body 必须是 JSON 对象')
    metric = str(data.get('metric') or '').strip().lower()
    if metric not in HEALTH_METRICS:
        raise ValueError('未知健康指标')
    raw = data.get('value')
    if isinstance(raw, bool):
        raise ValueError('value 必须是数值')
    number = float(raw)
    if not math.isfinite(number):
        raise ValueError('value 必须是有限数值')
    value = int(round(number))
    limits = {'hr': (20, 250), 'stress': (0, 100), 'spo2': (1, 100)}
    if metric in limits and (not limits[metric][0] <= value <= limits[metric][1]):
        raise ValueError('健康读数超出有效范围')
    if value < 0:
        raise ValueError('健康读数不能为负')
    ts = float(data.get('ts') or now)
    if ts > 100000000000:
        ts /= 1000
    if not math.isfinite(ts) or ts <= 0 or ts > now + 300:
        raise ValueError('时间戳无效')
    text = data.get('text') or ''
    if not isinstance(text, str):
        raise ValueError('text 必须是字符串')
    return (metric, value, text, ts)

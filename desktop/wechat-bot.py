import argparse
import base64
import glob
import json
import os
import queue
import random
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional
import image_prompting
import aux_runtime
import qwen_runtime
import dashboard_runtime
import config_loader
HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(HERE)
_VIDEO_DIR = os.path.join(HERE, 'video')
for _p in (HERE, _VIDEO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
DEFAULT_CONFIG: Dict[str, Any] = {'baseUrl': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'thinking': 'high', 'temperature': 1.15, 'maxTokens': 900, 'credentialsFile': '', 'credentialsRef': 'DEEPSEEK_API_KEY', 'apiKey': '', 'personaFile': 'personas/group-friend.md', 'runtimeNote': '你是一个由用户配置的微信聊天角色。直接输出可以发送的聊天内容；不要输出动作旁白、系统标签或分析过程。', 'chats': [], 'nicknames': [], 'wakeProbability': 0.05, 'followupSeconds': 0, 'historyLimit': 12, 'contextFile': 'data/contexts.json', 'silkDecoder': 'bin/silk_v3_decoder.exe', 'asr': {'enabled': False, 'provider': 'qwen', 'model': 'qwen3-asr-flash', 'ref': 'DASHSCOPE_API_KEY', 'language': 'zh', 'maxSeconds': 180}, 'vision': {'provider': 'qwen', 'model': 'qwen3-vl-plus', 'baseUrl': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'ref': 'DASHSCOPE_API_KEY', 'maxTokens': 1000, 'timeoutSeconds': 180, 'enableThinking': False, 'enabled': False}, 'auxiliary': {'provider': 'mimo', 'baseUrl': 'https://api.xiaomimimo.com/v1', 'model': 'mimo-v2.6-flash', 'credentialsFile': '', 'credentialFormat': 'env', 'thinking': 'low', 'maxTokens': 4096, 'timeoutSeconds': 60, 'enabled': True}, 'stripEmojiInPrivate': True, 'image': {'enabled': False, 'model': 'qwen-image-3.0-pro', 'size': '1152*1536', 'promptExtend': False, 'ref': 'DASHSCOPE_API_KEY', 'refs': [], 'saveDir': 'data/images', 'maxPerDay': 4, 'requireAsk': True, 'writerModel': 'mimo-v2.6-flash', 'character': '用户配置的角色身份；保持中性，不指向任何真实人物。', 'referenceMode': 'identity-first'}, 'sendDelayMin': 1.5, 'sendDelayMax': 8.0, 'dailySendLimit': 600, 'quietHours': [2, 7], 'readDelay': 0.6, 'selfSenderId': 2, 'debounceSeconds': 3.0, 'debounceMaxSeconds': 9.0, 'rhythmProfile': 'natural', 'rhythmBurst': 0, 'rhythmWindow': 0, 'proactive': {'enabled': False, 'checkSeconds': 30, 'silenceMin': [50, 160], 'nudgeMin': [6, 20], 'angryMin': [45, 110], 'lastMin': [200, 400], 'dailyLimit': 5, 'minGapMin': 40, 'chance': 0.65, 'quietHours': [1, 8]}, 'humanize': {'enabled': True, 'sleepHours': [2, 8], 'sleepDelayMin': 40, 'sleepDelayMax': 150, 'sleepSilentChance': 0.25, 'sleepEscalateFactor': 0.55, 'sleepEscalateWindowMin': 12, 'sleepWakeWords': ['睡不着', '失眠', '睡不着觉', '难受', '好难受', '想你了', '好想你', '出事了', '怎么办', '哭了', '救', '急', '怕'], 'sleepWakeDelayMin': 6, 'sleepWakeDelayMax': 25, 'slowReplyChance': 0.1, 'slowReplyMin': 3, 'slowReplyMax': 10}, 'schedule': {'enabled': False, 'wake': {'enabled': True, 'at': '07:00', 'jitterMin': 40}, 'meals': [{'enabled': True, 'name': '早饭', 'at': '09:00', 'jitterMin': 50}, {'enabled': True, 'name': '午饭', 'at': '12:40', 'jitterMin': 60}, {'enabled': True, 'name': '晚饭', 'at': '18:40', 'jitterMin': 60}], 'sleep': {'enabled': True, 'at': '01:00', 'jitterMin': 45}, 'catchUpMinutes': 60, 'insomnia': {'enabled': True, 'from': '23:30', 'to': '00:45', 'minDays': 1, 'maxDays': 2, 'aheadMin': 2, 'aheadMax': 6}, 'randomChats': {'enabled': True, 'from': '09:30', 'to': '23:30', 'minPerDay': 1, 'maxPerDay': 3, 'avoidMinutes': 45}}, 'health': {'enabled': False, 'port': 8765, 'token': '', 'hrHigh': 150, 'hrHard': 170, 'hrExtreme': 190, 'hrSustainSeconds': 60, 'spo2Low': 90, 'stressLow': 20, 'stressHigh': 40, 'sleepShortH': 5, 'sleepLongH': 10, 'wakeLateHour': 12, 'nightStartH': 1, 'nightEndH': 5, 'sleepFreshMin': 120, 'workoutGraceMin': 10, 'workoutStaleHours': 3, 'hrExtremeInWorkout': 190, 'cooldownMin': 25, 'dailyLimit': 10, 'quietHours': []}, 'logFile': 'data/logs/wechat-bot.log', 'memoryFile': 'data/memory.json'}
CFG: Dict[str, Any] = {}
CFG_PATH = ''
CFG_MTIME = [0.0]
_logLock = threading.Lock()
_EMOJI_RE = re.compile('[🌀-\U0001faff🇦-🇿☀-➿⬀-⯿️⃣]+')
_VOICE_OBS = {'sad': '偏低偏慢', 'angry': '偏急偏重', 'happy': '偏亮偏快', 'fearful': '发紧', 'surprised': '偏高', 'disgusted': '偏冷', 'neutral': ''}

def reload_config() -> None:
    if not CFG_PATH:
        return
    try:
        mt = os.stat(CFG_PATH).st_mtime
    except Exception:
        return
    if mt == CFG_MTIME[0]:
        return
    CFG_MTIME[0] = mt
    try:
        CFG.update(config_loader.apply_environment(config_loader.load_config(CFG_PATH, DEFAULT_CONFIG, _ROOT)))
        log('配置已热重载')
    except Exception as e:
        log(f'配置热重载失败（忽略）: {e}')

class Telemetry:
    MAX_EVENTS = 800
    MAX_SUBS = 8
    SUB_QUEUE = 512
    SERIES = 240

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []
        self._subs: List[Any] = []
        self._seq = 0
        self.started = time.time()
        self.stats: Dict[str, Any] = {'llm_calls': 0, 'tokens_in': 0, 'tokens_out': 0, 'cached': 0, 'health_in': 0, 'sent': 0, 'send_secs': 0.0, 'send_timeout': 0, 'fired': 0, 'blocked': 0, 'inbound': 0, 'bubble_over': 0}
        self.last_health: Dict[str, Any] = {}
        self.last_health_at = 0.0
        self.last_llm: Dict[str, Any] = {}
        self.health_series: List[Dict[str, Any]] = []
        self.send_marks: List[float] = []
        self.rhythm: Dict[str, Any] = {'name': '?', 'burst': 0, 'window': 120.0}
        self.model_cfg: Dict[str, Any] = {}

    def emit(self, kind: str, **data: Any) -> None:
        try:
            with self._lock:
                self._seq += 1
                ev: Dict[str, Any] = {'seq': self._seq, 't': time.time(), 'kind': kind}
                ev.update(data)
                self._events.append(ev)
                if len(self._events) > self.MAX_EVENTS:
                    del self._events[:-self.MAX_EVENTS]
                subs = list(self._subs)
            for q in subs:
                try:
                    q.put_nowait(ev)
                except Exception:
                    pass
        except Exception:
            pass

    def subscribe(self):
        q: Any = queue.Queue(maxsize=self.SUB_QUEUE)
        with self._lock:
            if len(self._subs) >= self.MAX_SUBS:
                return None
            self._subs.append(q)
        return q

    def unsubscribe(self, q: Any) -> None:
        try:
            with self._lock:
                if q in self._subs:
                    self._subs.remove(q)
        except Exception:
            pass

    def recent(self, kind: Optional[str]=None, limit: int=120) -> List[Dict[str, Any]]:
        with self._lock:
            evs = list(self._events)
        if kind:
            evs = [e for e in evs if e.get('kind') == kind]
        return evs[-limit:]

    def add_health_point(self, metric: str, value: int, text: str, ts: float) -> None:
        with self._lock:
            self.last_health[metric] = {'value': value, 'text': text, 't': time.time(), 'ts': ts}
            if metric in ('hr', 'stress', 'spo2'):
                self.health_series.append({'t': ts or time.time(), 'm': metric, 'v': value})
                if len(self.health_series) > self.SERIES:
                    del self.health_series[:-self.SERIES]
            self.last_health_at = time.time()

    def feed_samples(self, text: str) -> int:
        try:
            arr = json.loads(text or '[]')
        except Exception:
            return 0
        if not isinstance(arr, list):
            return 0
        got = 0
        try:
            with self._lock:
                for s in arr[-300:]:
                    if not isinstance(s, dict):
                        continue
                    t = float(s.get('t') or 0) or time.time()
                    for key, name in (('hr', 'hr'), ('st', 'stress'), ('sp', 'spo2')):
                        try:
                            v = int(s.get(key) or 0)
                        except Exception:
                            continue
                        if not v:
                            continue
                        self.health_series.append({'t': t, 'm': name, 'v': v})
                        got += 1
                        self.last_health[name] = {'value': v, 'text': '', 't': time.time(), 'ts': t}
                if len(self.health_series) > self.SERIES:
                    del self.health_series[:-self.SERIES]
                self.last_health_at = time.time()
        except Exception:
            pass
        return got

    def mark_send(self, secs: float=0.0) -> None:
        now = time.time()
        with self._lock:
            self.send_marks.append(now)
            self.send_marks = [t for t in self.send_marks if now - t < 600]
            self.stats['sent'] += 1
            self.stats['send_secs'] += float(secs or 0)

    def send_window_usage(self) -> Dict[str, Any]:
        win = float(self.rhythm.get('window') or 120.0)
        now = time.time()
        with self._lock:
            marks = [t for t in self.send_marks if now - t < win]
        burst = int(self.rhythm.get('burst') or 0)
        oldest = min(marks) if marks else None
        return {'window': win, 'burst': burst, 'used': len(marks), 'remain': max(0, burst - len(marks)) if burst else None, 'resetIn': win - (now - oldest) if oldest else 0.0}

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            stats = dict(self.stats)
            health = dict(self.last_health)
            series = list(self.health_series)
            llm = dict(self.last_llm)
            evs = list(self._events)[-160:]
        snapshot = {'now': time.time(), 'started': self.started, 'uptime': time.time() - self.started, 'stats': stats, 'health': health, 'healthAt': self.last_health_at, 'series': series, 'llm': llm, 'model': dict(self.model_cfg), 'rhythm': self.send_window_usage(), 'events': evs, 'seq': self._seq}
        _image_model = str((CFG.get('image') or {}).get('model') or '')
        try:
            snapshot['model'].update(aux_runtime.dashboard_fields(image_model=_image_model))
        except Exception:
            snapshot['model'].update({'auxProvider': '', 'auxModel': '', 'auxBaseUrl': '', 'imageModel': _image_model})
        try:
            snapshot['model'].update(qwen_runtime.dashboard_fields(image_model=_image_model))
        except Exception:
            snapshot['model'].update({'visionProvider': '', 'visionModel': '', 'asrProvider': '', 'asrModel': '', 'imageModel': _image_model})
        return snapshot
TELEM = Telemetry()

def log(*a: Any) -> None:
    line = time.strftime('[%Y-%m-%d %H:%M:%S] ') + ' '.join((str(x) for x in a))
    with _logLock:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
            safe = line.encode(enc, errors='backslashreplace').decode(enc)
            try:
                print(safe, flush=True)
            except (OSError, ValueError):
                pass
        except (OSError, ValueError):
            pass
        try:
            with open(CFG['logFile'], 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass
    try:
        TELEM.emit('log', line=line.split('] ', 1)[-1])
    except Exception:
        pass

def _data_dir() -> str:
    configured = str(CFG.get('contextFile') or '')
    if configured:
        return os.path.dirname(os.path.abspath(configured))
    return os.path.join(_ROOT, 'data')

def _companion_dir() -> str:
    return os.path.join(_data_dir(), 'companion')

def _is_local_client(addr: Any) -> bool:
    try:
        import ipaddress
        return ipaddress.ip_address(str(addr)).is_loopback
    except Exception:
        return str(addr) in ('127.0.0.1', '::1', 'localhost')

def read_credential(path: str, ref: str) -> str:
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                m = re.match(f'^\\s*{re.escape(ref)}\\s*:\\s*(\\S+)\\s*$', line)
                if m:
                    return m.group(1)
    except Exception as e:
        log(f'凭据读取失败: {e}')
    return ''
SILK_RATE = 24000

def _wav_header(data_bytes: int, rate: int) -> bytes:
    return b'RIFF' + struct.pack('<I', 36 + data_bytes) + b'WAVE' + b'fmt ' + struct.pack('<IHHIIHH', 16, 1, 1, rate, rate * 2, 2, 16) + b'data' + struct.pack('<I', data_bytes)

def silk_to_wav(silk_path: str) -> bytes:
    exe = os.path.join(HERE, str(CFG.get('silkDecoder') or 'bin/silk_v3_decoder.exe'))
    if not os.path.exists(exe):
        raise RuntimeError(f'没有 SILK 解码器：{exe}')
    fd, pcm_path = tempfile.mkstemp(suffix='.pcm')
    os.close(fd)
    try:
        p = subprocess.run([exe, silk_path, pcm_path], capture_output=True, timeout=30)
        pcm = b''
        try:
            with open(pcm_path, 'rb') as f:
                pcm = f.read()
        except OSError:
            pass
        if not pcm:
            err = (p.stderr or b'').decode('utf-8', 'replace').strip()[:150]
            raise RuntimeError(f'silk 解码没出数据（rc={p.returncode}）{err}')
        return _wav_header(len(pcm), SILK_RATE) + pcm
    finally:
        try:
            os.remove(pcm_path)
        except OSError:
            pass

def asr_transcribe(wav: bytes, a: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
    a = a if a is not None else CFG.get('asr') or {}
    if not a.get('enabled', True):
        return {}
    try:
        r = qwen_runtime.asr_transcribe(wav, audio_format='wav', language=str(a.get('language') or 'zh'), purpose='asr')
    except Exception as e:
        log(f'  语音转文字失败（Qwen {type(e).__name__}），只留 [语音] 标记')
        return {}
    return {'text': str(r.get('text') or ''), 'emotion': str(r.get('emotion') or ''), 'language': str(r.get('language') or '')}

def voice_note(text: str, secs: float, emotion: str, a: Optional[Dict[str, Any]]=None) -> str:
    a = a or {}
    obs = _VOICE_OBS.get(str(emotion or '').strip().lower(), '')
    if obs:
        return obs
    n = len(re.sub('\\s', '', text or ''))
    rate = n / max(float(secs), 0.1)
    if rate >= float(a.get('fastCharsPerSec') or 6.5):
        return '语速偏快'
    if rate <= float(a.get('slowCharsPerSec') or 2.5):
        return '语速偏慢'
    return ''
IMAGE_API = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
_IMAGE_WRITER_SYSTEM = image_prompting.WRITER_SYSTEM
_PHOTO_CAPTION_ASK = '（你刚把上面这张照片拍好、准备发给他。现在写**一两句话**配着这张照片一起发出去。）\n要求：\n· 就写你看到这张照片时的反应，别描述画面（他又不是看不见）\n· 一句话就够，最多两条，每条短\n· 不要重复你刚才已经说过的话\n· 别解释、别加引号、别写动作描写'

def _datauri(path: str) -> str:
    with open(path, 'rb') as f:
        raw = f.read()
    ext = os.path.splitext(path)[1].lower().lstrip('.')
    mime = {'png': 'image/png', 'webp': 'image/webp'}.get(ext, 'image/jpeg')
    return f'data:{mime};base64,' + base64.b64encode(raw).decode()

def write_image_prompt(situation: str, key: str, context: str='') -> str:
    del key
    cfg = CFG.get('image') or {}
    user_msg = image_prompting.writer_user(situation, context)
    system_msg = _IMAGE_WRITER_SYSTEM + '\n\n角色造型：' + str(cfg.get('character') or '')
    return aux_runtime.text_chat(system=system_msg, user=user_msg, purpose='photo_prompt', max_tokens=800, timeout=90)

def _append_manifest(entry: Dict[str, Any]) -> None:
    cfg = CFG.get('image') or {}
    d = os.path.join(HERE, str(cfg.get('saveDir') or 'images_out'))
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'manifest.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception as e:
        log(f'  [配图] manifest 写入失败：{e}')

def _img_ext(data: bytes) -> str:
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    if data[:2] == b'\xff\xd8':
        return '.jpg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return '.gif'
    return '.png'

def gen_image(prompt: str, key: str) -> str:
    cfg = CFG.get('image') or {}
    content = []
    for p in image_prompting.reference_paths(cfg, prompt):
        full = p if os.path.isabs(str(p)) else os.path.join(HERE, str(p))
        if not os.path.isfile(full):
            raise FileNotFoundError('Configured character reference image is missing')
        content.append({'image': _datauri(full)})
    content.append({'text': prompt})
    body = {'model': str(cfg.get('model') or 'qwen-image-3.0-pro'), 'input': {'messages': [{'role': 'user', 'content': content}]}, 'parameters': image_prompting.parameters(cfg)}
    req = urllib.request.Request(IMAGE_API, data=json.dumps(body, ensure_ascii=False).encode('utf-8'), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    _trace_id = dashboard_runtime.start_call('qwen', body.get('model'), 'image_generate', body)
    _t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            j = json.loads(r.read().decode('utf-8'))
        url = str(j['output']['choices'][0]['message']['content'][0]['image'])
        dashboard_runtime.finish_call(_trace_id, response=j, output=url, reasoning='', usage=j.get('usage') or {}, ms=int((time.time() - _t0) * 1000))
        return url
    except urllib.error.HTTPError as e:
        dashboard_runtime.fail_call(_trace_id, 'HTTPError', e.code)
        raise
    except Exception as e:
        dashboard_runtime.fail_call(_trace_id, type(e).__name__)
        raise
_ASK_PHOTO_RE = re.compile('照片|相片|自拍|拍一?[张个]|看图|图片|长什么样|什么样子|发一?[张个]图|看看你|让我看|想看')
_BV_RE = re.compile('bilibili\\.com/video/(BV[0-9A-Za-z]{10})')
_B23_RE = re.compile('b23\\.tv/([0-9A-Za-z]+)')

def extract_bv(text: str) -> Optional[str]:
    if not text:
        return None
    m = _BV_RE.search(text)
    if m:
        return m.group(1)
    m = _B23_RE.search(text)
    if m:
        try:
            req = urllib.request.Request('https://b23.tv/' + m.group(1), headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=15) as r:
                final = r.geturl()
            m2 = re.search('(BV[0-9A-Za-z]{10})', final)
            if m2:
                return m2.group(1)
        except Exception as e:
            log(f'  b23 短链解析失败: {e}')
    return None

def _extract_photo_markers(text: str):
    sits: List[str] = []

    def _grab(m):
        s = m.group(1).strip()
        if s:
            sits.append(s)
        return ''
    out = re.sub('(?m)^[ \\t]*[\\[【]配图\\s*[：:]\\s*([^\\]】\\n]{1,120})[\\]】][ \\t]*$', _grab, str(text or ''))
    return (out.strip(), sits)

def load_persona(path: Optional[str]=None) -> str:
    p = path or CFG.get('personaFile') or ''
    try:
        with open(p, encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        log(f'人设文件读不到（{p}）：{e}')
        return ''
_send_lock = threading.Lock()
_PRIVATE_NAMES = set()
_RHYTHM_TL = threading.local()
_send_stats = {'ok': 0, 'timeout': 0, 'error': 0, 'stuck': 0}
_send_paused_until = [0.0]
_wx = [None]
_recent_send_secs: List[float] = []

def _desktop_locked() -> bool:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        DESKTOP_SWITCHDESKTOP = 256
        h = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not h:
            return True
        try:
            return not user32.SwitchDesktop(h)
        finally:
            user32.CloseDesktop(h)
    except Exception:
        return False

def _restore_wechat_window() -> bool:
    try:
        import win32con
        import win32gui
    except Exception:
        return False
    found = []

    def cb(h, _):
        try:
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) in ('微信', 'WeChat'):
                found.append(h)
        except Exception:
            pass
        return True
    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        return False
    if not found:
        return False
    h = found[0]
    changed = False
    was_hidden = False
    try:
        r = win32gui.GetWindowRect(h)
        offscreen = r[0] < -10000 or r[1] < -10000
        if win32gui.IsIconic(h) or offscreen:
            was_hidden = True
            if win32gui.IsIconic(h):
                win32gui.ShowWindow(h, win32con.SW_RESTORE)
            if offscreen or win32gui.IsIconic(h):
                win32gui.SetWindowPos(h, 0, 80, 60, 1100, 800, win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW)
            log('  微信窗口是最小化/离屏，已拉回来')
            changed = True
        if not win32gui.IsIconic(h):
            win32gui.SetForegroundWindow(h)
            changed = True
    except Exception as e:
        log(f'  摆正微信窗口失败: {e}')
    if changed:
        time.sleep(2.0 if was_hidden else 0.4)
    return changed

def _quiet_wechatauto():
    try:
        from wechatauto.guia import WeChatGUI
    except Exception:
        return
    if getattr(WeChatGUI, '_dsh_no_minimize', False):
        return
    try:
        sc = CFG.get('send') or {}
        if sc.get('keepWindows', True):
            WeChatGUI._minimize_blockers = lambda self: 0
            log('  已禁用「最小化遮挡窗口」（不会再切走你正在用的窗口）')
        WeChatGUI._dsh_no_minimize = True
    except Exception as e:
        log(f'  禁用最小化遮挡失败: {e}')

def _get_gui():
    _quiet_wechatauto()
    if _wx[0] is None:
        try:
            from wechatauto.guia import WeChatGUI
            _wx[0] = WeChatGUI()
        except Exception as e:
            log(f'  WeChatGUI 初始化失败，回退 quick_send：{e}')
            _wx[0] = False
    return _wx[0] or None

def _do_send(text: str, who: str, verify: bool):
    gui = _get_gui()
    if gui is not None:
        return gui.send_msg(text, who, verify)
    from wechatauto.guia import quick_send
    return quick_send(text, who=who, verify=verify)

def _do_send_image(path: str, who: str, verify: bool):
    _restore_wechat_window()
    gui = _get_gui()
    if gui is None:
        raise RuntimeError('WeChatGUI 不可用，发不了图片')
    return gui.send_image(path, who, verify)

def _res_says_ok(res: Any) -> bool:
    if res is None:
        return False
    if isinstance(res, dict):
        st = str(res.get('status') or '')
        msg = str(res.get('message') or '')
    else:
        st = str(getattr(res, 'status', '') or '')
        msg = str(getattr(res, 'message', '') or '')
    if st:
        return st == '成功'
    return '成功' in msg or '已发送并确认' in msg

def _res_msg(res: Any) -> str:
    if isinstance(res, dict):
        return str(res.get('message') or res)
    return str(getattr(res, 'message', res) or res)

def _quick_send_image_safe(path: str, who: str, soft: float, hard: float):
    box: Dict[str, Any] = {}

    def run():
        if who in _PRIVATE_NAMES:
            _RHYTHM_TL.free = True
        try:
            if _desktop_locked():
                box['locked'] = True
                return
            _restore_wechat_window()
            time.sleep(0.4)
            box['r'] = _do_send_image(path, who, True)
        except Exception as e:
            box['e'] = e
        finally:
            _RHYTHM_TL.free = False
    t = threading.Thread(target=run, daemon=True, name='wximg')
    t0 = time.time()
    t.start()
    t.join(soft)
    if not t.is_alive():
        if box.get('locked'):
            return (None, time.time() - t0, 'LOCKED')
        if 'e' in box:
            return (None, time.time() - t0, f"ERR {box['e']}")
        return (box.get('r'), time.time() - t0, 'OK')
    log(f'    发图超过 {soft:.0f}s（多半在重跑布局校准），继续等…')
    t.join(max(1.0, hard - soft))
    if t.is_alive():
        return (None, time.time() - t0, 'TIMEOUT')
    if 'e' in box:
        return (None, time.time() - t0, f"ERR {box['e']}")
    return (box.get('r'), time.time() - t0, 'SLOW')

def _xml_unescape(s: str) -> str:
    return s.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&apos;', "'").replace('&amp;', '&')

def _hhmm(ts=None) -> str:
    try:
        ts = float(ts) if ts not in (None, '', 0, '0') else time.time()
    except Exception:
        ts = time.time()
    if ts > 1000000000000.0:
        ts /= 1000.0
    lt = time.localtime(ts)
    if lt.tm_year != time.localtime().tm_year:
        return time.strftime('[%Y-%m-%d %H:%M]', lt)
    return time.strftime('[%m-%d %H:%M]', lt)
_now_lock = threading.Lock()
_now_last_day: Dict[str, str] = {}

def _now_note(chat: str='') -> str:
    lt = time.localtime()
    day = time.strftime('%Y-%m-%d', lt)
    with _now_lock:
        fresh = _now_last_day.get(chat) != day
        _now_last_day[chat] = day
    wd = '（周%s）' % '一二三四五六日'[lt.tm_wday] if fresh else ''
    return '（现在是 %s%s %02d:%02d）' % (day, wd, lt.tm_hour, lt.tm_min)

class HealthServer:

    def __init__(self, bot, port: int, token: str=''):
        self.bot = bot
        self.port = int(port)
        self.token = str(token or '')
        self.httpd = None
        self.thread = None

    def start(self) -> bool:
        import http.server
        outer = self

        class H(http.server.BaseHTTPRequestHandler):

            def log_message(self, *a):
                pass

            def _json(self, code: int, obj) -> None:
                import json as _j
                body = _j.dumps(obj, ensure_ascii=False).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, body: bytes) -> None:
                if not (CFG.get('health') or {}).get('enabled', False) or not outer.token:
                    return self._json(403, {'ok': False, 'error': 'health input disabled'})
                import json as _j
                if outer.token:
                    got = self.headers.get('X-Token') or self.headers.get('x-token') or ''
                    if got != outer.token:
                        log('  健康推送：token 不对，拒绝')
                        self._json(403, {'ok': False, 'error': 'bad token'})
                        return
                try:
                    data = _j.loads(body.decode('utf-8') or '{}')
                except Exception as e:
                    self._json(400, {'ok': False, 'error': 'bad json: %s' % e})
                    return
                try:
                    if isinstance(data, dict):
                        dashboard_runtime.ingest_packet(data)
                except Exception:
                    pass
                metric = str(data.get('metric') or '')
                if outer.bot.health_debug:
                    log(f"  健康推送 [{self.client_address[0]}] {metric}={data.get('value')}")
                try:
                    value = int(round(float(data.get('value'))))
                except Exception:
                    self._json(400, {'ok': False, 'error': 'bad value'})
                    return
                if metric == 'hr' and (not 20 <= value <= 250):
                    self._json(200, {'ok': True, 'ignored': True})
                    return
                try:
                    text = str(data.get('text') or '')
                    ts = float(data.get('ts') or 0) or time.time()
                    TELEM.stats['health_in'] += 1
                    TELEM.add_health_point(metric, value, text, ts)
                    if metric == 'samples' and text:
                        TELEM.feed_samples(text)
                    TELEM.emit('health', metric=metric, value=value, text=text, ts=ts, src=self.client_address[0])
                except Exception:
                    pass
                outer.bot._on_health(metric, value, str(data.get('text') or ''))
                self._json(200, {'ok': True})

            def _life_regen_error(self, status, code, message):
                payload = {'ok': False, 'error': code, 'message': message}
                try:
                    payload['generation'] = outer.bot._life_generation_public()
                except Exception:
                    pass
                self._json(status, payload)

            def _life_host_ok(self) -> bool:
                from urllib.parse import urlparse
                raw = self.headers.get('Host') or ''
                if not raw:
                    return False
                try:
                    u = urlparse('//' + raw)
                    host = (u.hostname or '').lower()
                    return host in ('127.0.0.1', 'localhost', '::1') and u.port == int(outer.port)
                except Exception:
                    return False

            def _life_origin_ok(self) -> bool:
                from urllib.parse import urlparse
                origin = self.headers.get('Origin')
                if not origin:
                    return True
                try:
                    u = urlparse(origin)
                    return u.scheme == 'http' and (u.hostname or '').lower() in ('127.0.0.1', 'localhost', '::1') and (u.port == int(outer.port)) and (u.path in ('', '/')) and (not u.query) and (not u.fragment)
                except Exception:
                    return False

            def _life_regen_request(self, n: int) -> None:
                if not _is_local_client(self.client_address[0]):
                    return self._life_regen_error(403, 'forbidden', '仅允许本机访问')
                if not self._life_host_ok():
                    return self._life_regen_error(403, 'forbidden', 'Host 不是本机服务地址')
                if not self._life_origin_ok():
                    return self._life_regen_error(403, 'forbidden', 'Origin 不是本机同源地址')
                ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()
                if ctype != 'application/json':
                    return self._life_regen_error(400, 'unsupported_media_type', '只接受 application/json')
                if n <= 0:
                    return self._life_regen_error(400, 'invalid_body', '请求体不能为空')
                if n > 4096:
                    self.close_connection = True
                    return self._life_regen_error(413, 'body_too_large', '请求体过大')
                body = self.rfile.read(n)
                if len(body) > 4096:
                    self.close_connection = True
                    return self._life_regen_error(413, 'body_too_large', '请求体过大')
                import json as _j
                try:
                    data = _j.loads(body.decode('utf-8') or '{}')
                except Exception:
                    return self._life_regen_error(400, 'invalid_json', '请求体不是有效 JSON')
                if not isinstance(data, dict) or set(data.keys()) - {'date'}:
                    return self._life_regen_error(400, 'invalid_body', '请求结构无效')
                if 'date' in data and (not isinstance(data.get('date'), str)):
                    return self._life_regen_error(400, 'invalid_date', '日期无效')
                try:
                    code, payload = outer.bot._life_regenerate(data.get('date'))
                except Exception:
                    return self._life_regen_error(500, 'generation_unavailable', '日程生成服务暂不可用')
                return self._json(code, payload)

            def _companion_http_error(self, status, code, message):
                self._json(status, {'ok': False, 'error': code, 'message': message})

            def _companion_post(self, path, n):
                if not _is_local_client(self.client_address[0]):
                    return self._companion_http_error(403, 'forbidden', '仅允许本机访问')
                if not self._life_host_ok():
                    return self._companion_http_error(403, 'forbidden', 'Host 不是本机服务地址')
                if not self._life_origin_ok():
                    return self._companion_http_error(403, 'forbidden', 'Origin 不是本机同源地址')
                ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()
                if ctype != 'application/json':
                    return self._companion_http_error(400, 'unsupported_media_type', '只接受 application/json')
                if n <= 0:
                    return self._companion_http_error(400, 'invalid_body', '请求体不能为空')
                if n > 65536:
                    self.close_connection = True
                    return self._companion_http_error(413, 'body_too_large', '请求体过大')
                body = self.rfile.read(n)
                if len(body) > 65536:
                    self.close_connection = True
                    return self._companion_http_error(413, 'body_too_large', '请求体过大')
                import json as _j
                try:
                    data = _j.loads(body.decode('utf-8'))
                except Exception:
                    return self._companion_http_error(400, 'invalid_json', '请求体不是有效 JSON')
                if not isinstance(data, dict):
                    return self._companion_http_error(400, 'invalid_body', '请求结构无效')
                if path == '/api/companion/organizer':
                    try:
                        code, payload = outer.bot._companion_organizer_post(data)
                    except Exception:
                        return self._companion_http_error(500, 'organizer_error', '记忆整理暂不可用')
                    return self._json(code, payload)
                if path == '/api/companion/planner':
                    try:
                        code, payload = outer.bot._companion_planner_post(data)
                    except Exception:
                        return self._companion_http_error(500, 'planner_error', '日程规划暂不可用')
                    return self._json(code, payload)
                return self._companion_http_error(404, 'not_found', '未知接口')

            def _companion_get(self, path, query):
                if not _is_local_client(self.client_address[0]):
                    return self._companion_http_error(403, 'forbidden', '仅允许本机访问')
                if not self._life_host_ok():
                    return self._companion_http_error(403, 'forbidden', 'Host 不是本机服务地址')
                if not self._life_origin_ok():
                    return self._companion_http_error(403, 'forbidden', 'Origin 不是本机同源地址')
                try:
                    if path == '/api/companion/diagnostics':
                        payload = outer.bot._companion_diagnostics(query)
                    elif path == '/api/companion/organizer':
                        payload = outer.bot._companion_organizer_get((query or {}).get('chat') or '')
                    elif path == '/api/companion/planner':
                        payload = outer.bot._companion_planner_get()
                    elif path == '/api/companion/models':
                        payload = outer.bot._companion_models()
                    elif path == '/api/companion/furina':
                        payload = outer.bot._companion_furina()
                    else:
                        return self._companion_http_error(404, 'not_found', '未知接口')
                    return self._json(200, payload)
                except Exception:
                    return self._companion_http_error(500, 'companion_error', '陪伴功能暂不可用')

            def do_POST(self):
                from urllib.parse import urlparse
                path = urlparse(self.path).path
                if path.startswith('/api/') and (not _is_local_client(self.client_address[0]) or not self._life_host_ok() or (not self._life_origin_ok())):
                    return self._json(403, {'ok': False, 'error': 'local access only'})
                if outer.bot.health_debug:
                    log(f'  收到 HTTP POST 来自 {self.client_address[0]}  {path[:40]}')
                try:
                    n = int(self.headers.get('Content-Length') or 0)
                except Exception:
                    n = 0
                if path.startswith('/api/companion'):
                    if not path.startswith('/api/companion/'):
                        return self._companion_http_error(404, 'not_found', '未知接口')
                    return self._companion_post(path, n)
                if path == '/api/life/regenerate':
                    return self._life_regen_request(n)
                if path.startswith('/api/life'):
                    return self._life_regen_error(404, 'not_found', '未知接口')
                body = self.rfile.read(n) if n > 0 else b'{}'
                if path == '/api/clear':
                    return self._json(200, self._api_clear(body))
                self._handle(body)

            def do_GET(self):
                from urllib.parse import urlparse, parse_qs
                path = urlparse(self.path).path
                if not _is_local_client(self.client_address[0]) or not self._life_host_ok() or (not self._life_origin_ok()):
                    return self._json(403, {'ok': False, 'error': 'local access only'})
                _quiet = path in ('/api/state', '/console', '/console/', '/index.html', '/favicon.ico', '/events', '/api/chats', '/api/history', '/api/video', '/api/life', '/api/images', '/api/imagefile') or path.startswith('/api/dashboard/')
                if outer.bot.health_debug and (not _quiet):
                    log(f'  收到 HTTP GET  来自 {self.client_address[0]}  {self.path[:60]}')
                if path.startswith('/api/companion'):
                    if not path.startswith('/api/companion/'):
                        return self._companion_http_error(404, 'not_found', '未知接口')
                    q = {k: v[0] if v else '' for k, v in parse_qs(urlparse(self.path).query).items()}
                    return self._companion_get(path, q)
                if path in ('/console', '/console/', '/', '/index.html'):
                    return self._serve_console()
                if path == '/api/state':
                    snap = TELEM.snapshot()
                    try:
                        snap.update(dashboard_runtime.state_fields())
                    except Exception:
                        pass
                    return self._json(200, snap)
                if path.startswith('/api/dashboard/'):
                    if not _is_local_client(self.client_address[0]):
                        return self._json(403, {'ok': False, 'error': 'local access only'})
                    q = {k: v[0] if v else '' for k, v in parse_qs(urlparse(self.path).query).items()}
                    result = dashboard_runtime.handle_get(path, q)
                    if result is None:
                        return self._json(404, {'ok': False, 'error': 'unknown dashboard endpoint'})
                    code, payload = result
                    return self._json(code, payload)
                if path == '/events':
                    return self._serve_sse()
                if path == '/api/chats':
                    return self._json(200, self._api_chats())
                if path == '/api/history':
                    q = parse_qs(urlparse(self.path).query)
                    return self._json(200, self._api_history((q.get('chat') or [''])[0]))
                if path == '/api/initiative':
                    return self._json(200, self._api_initiative())
                if path == '/api/life':
                    return self._json(200, self._api_life())
                if path == '/api/video':
                    return self._json(200, self._api_video())
                if path == '/api/images':
                    return self._json(200, self._api_images())
                if path == '/api/imagefile':
                    q = parse_qs(urlparse(self.path).query)
                    data = self._api_imagefile((q.get('name') or [''])[0])
                    if data is None:
                        return self._json(404, {'ok': False, 'error': '没有这张图'})
                    ctype = 'image/png' if data[:8] == b'\x89PNG\r\n\x1a\n' else 'image/webp' if data[:4] == b'RIFF' and data[8:12] == b'WEBP' else 'image/jpeg'
                    self.send_response(200)
                    self.send_header('Content-Type', ctype)
                    self.send_header('Cache-Control', 'max-age=600')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                q = parse_qs(urlparse(self.path).query)
                if 'v' in q:
                    self._handle(('{"metric":"hr","value":%s}' % q['v'][0]).encode('utf-8'))
                else:
                    self._json(200, {'ok': True, 'hint': 'POST JSON to /hr', 'console': '/console'})

            def _serve_console(self) -> None:
                import os as _os
                fp = _os.path.join(HERE, 'console.html')
                try:
                    with open(fp, 'rb') as f:
                        body = f.read()
                except Exception as e:
                    self._json(500, {'ok': False, 'error': 'console.html 读不到: %s' % e})
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _bot(self):
                return getattr(outer, 'bot', None)

            def _api_chats(self) -> Dict[str, Any]:
                b = self._bot()
                if b is None:
                    return {'ok': False, 'error': 'bot 未就绪'}
                now = time.time()
                chats = []
                for conf in CFG.get('chats') or []:
                    name = str(conf.get('name') or '')
                    msgs = b.ctx.get(name)
                    chars = sum((len(m.get('content') or '') for m in msgs))
                    kind = str(conf.get('kind') or '').lower()
                    if kind not in ('private', 'group'):
                        kind = 'group' if str(conf.get('mode') or '').lower() == 'wake' else 'private'
                    try:
                        persona = os.path.basename(b._persona_path(name))
                    except Exception:
                        persona = ''
                    lu = b.last_user_at.get(name) or 0
                    lb = b.last_bot_at.get(name) or 0
                    chats.append({'name': name, 'kind': kind, 'mode': str(conf.get('mode') or ''), 'persona': persona, 'context': len(msgs), 'chars': chars, 'memory': len(b.memory.get(name) or ''), 'lastUser': lu or None, 'lastBot': lb or None, 'idleSec': now - max(lu, lb) if lu or lb else None, 'proactive': bool(conf.get('proactive'))})
                return {'ok': True, 'now': now, 'chats': chats, 'nicknames': list(CFG.get('nicknames') or []), 'wakeProbability': CFG.get('wakeProbability'), 'maxBubbles': CFG.get('maxBubbles'), 'groupMaxBubbles': CFG.get('groupMaxBubbles'), 'personaDefault': os.path.basename(str(CFG.get('personaFile') or ''))}

            def _api_life(self) -> Dict[str, Any]:
                bot = self._bot()
                out: Dict[str, Any] = {'ok': True}
                try:
                    day = bot._life_load(bot._life_date(0)) or {}
                except Exception:
                    day = {}
                out['date'] = day.get('date') or bot._life_date(0)
                out['timeline'] = day.get('timeline') or []
                out['period'] = day.get('period') or ''
                out['weather'] = day.get('weather') or {}
                out['stateIn'] = day.get('state_in') or {}
                out['stateOut'] = day.get('state_out') or {}
                out['chats'] = day.get('chats') or []
                out['videos'] = day.get('videos') or []
                try:
                    out['state'] = bot._state_load() or {}
                except Exception:
                    out['state'] = {}
                try:
                    out['promises'] = bot._promises_load() or []
                except Exception:
                    out['promises'] = []
                try:
                    import life_planner as LP
                    mp = os.path.join(LP.MONTHS, out['date'][:7] + '.json')
                    if os.path.exists(mp):
                        out['month'] = json.load(open(mp, encoding='utf-8'))
                    else:
                        out['month'] = {}
                except Exception:
                    out['month'] = {}
                out['generation'] = bot._life_generation_public()
                return out

            def _api_initiative(self) -> Dict[str, Any]:
                bot = self._bot()
                out: Dict[str, Any] = {'ok': True}
                try:
                    day = bot._life_load(bot._life_date(0)) or {}
                    out['planChats'] = day.get('chats') or []
                    out['planVideos'] = day.get('videos') or []
                except Exception:
                    out['planChats'] = []
                    out['planVideos'] = []
                try:
                    out['dynamicUsed'] = bot._dynamic_used()
                    try:
                        _lim = int((bot._life_cfg().get('proactive') or {}).get('dailyLimit') or 0)
                    except Exception:
                        _lim = 0
                    out['dynamicMax'] = _lim if _lim > 0 else '不限'
                    st = bot._sched_load() or {}
                    out['lastDynamicAt'] = st.get('lastDynamicAt') or 0
                    out['decisionFired'] = (st.get('decisionFired', {}) or {}).get(time.strftime('%Y-%m-%d'), [])
                except Exception:
                    out['dynamicUsed'] = 0
                try:
                    out['reminders'] = [r for r in bot._rem_load() if r.get('status') in ('open', 'deferred')][-10:]
                except Exception:
                    out['reminders'] = []
                try:
                    out['intents'] = bot._intents_active()[-10:]
                except Exception:
                    out['intents'] = []
                out['points'] = bot._decision_points()
                out['now'] = time.strftime('%Y-%m-%d %H:%M:%S')
                return out

            def _api_video(self) -> Dict[str, Any]:
                b = self._bot()
                if b is None:
                    return {'ok': False, 'error': 'bot 未就绪'}
                now = time.time()
                day = time.strftime('%Y-%m-%d')
                vc = CFG.get('video') or {}
                limit = int(vc.get('dailyLimit') or 3)
                if not b.video_times:
                    try:
                        b.video_times = b._video_state_load()
                    except Exception:
                        pass
                times = [t for t in b.video_times or [] if time.strftime('%Y-%m-%d', time.localtime(t)) == day]
                videos = []
                for conf in CFG.get('chats') or []:
                    name = str(conf.get('name') or '')
                    for m in b.ctx.get(name) or []:
                        c = str(m.get('content') or '')
                        if '（你看完的视频《' not in c:
                            continue
                        rest = c.split('（你看完的视频《', 1)[1]
                        i = rest.rfind('》')
                        if i < 0:
                            continue
                        title = rest[:i]
                        summary = rest[i + 2:].rstrip('）').strip()
                        ts = 0.0
                        mm = re.match('^\\[(\\d{2}-\\d{2}) (\\d{2}:\\d{2})\\]', c)
                        if mm:
                            try:
                                ts = time.mktime(time.strptime(time.strftime('%Y-') + mm.group(1) + ' ' + mm.group(2), '%Y-%m-%d %H:%M'))
                            except Exception:
                                ts = 0.0
                        videos.append({'chat': name, 'title': title, 'summary': summary, 'ts': ts or now})
                videos.sort(key=lambda x: x.get('ts') or 0, reverse=True)
                shares = []
                for conf in CFG.get('chats') or []:
                    name = str(conf.get('name') or '')
                    for m in b.ctx.get(name) or []:
                        if m.get('role') != 'assistant':
                            continue
                        c = str(m.get('content') or '')
                        if 'b23.tv/' in c:
                            shares.append({'chat': name, 'content': c[:300]})
                shares = shares[-6:]
                return {'ok': True, 'now': now, 'enabled': bool(vc.get('enabled', True)), 'dailyLimit': limit, 'todayCount': len(times), 'videos': videos[:6], 'shares': shares}

            def _photo_dir(self) -> str:
                cfg = CFG.get('image') or {}
                return os.path.join(HERE, str(cfg.get('saveDir') or 'images_out'))

            def _api_images(self) -> Dict[str, Any]:
                cfg = CFG.get('image') or {}
                d = self._photo_dir()
                meta: Dict[str, Dict[str, Any]] = {}
                try:
                    with open(os.path.join(d, 'manifest.jsonl'), encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                e = json.loads(line)
                            except Exception:
                                continue
                            n = str(e.get('name') or '')
                            if not n:
                                continue
                            meta.setdefault(n, {}).update(e)
                except OSError:
                    pass
                items: List[Dict[str, Any]] = []
                try:
                    for fn in os.listdir(d):
                        if not fn.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
                            continue
                        try:
                            st = os.stat(os.path.join(d, fn))
                        except OSError:
                            continue
                        m = meta.get(fn) or {}
                        items.append({'name': fn, 'size': st.st_size, 'mtime': st.st_mtime, 'chat': m.get('chat') or '', 'situation': m.get('situation') or '', 'prompt': m.get('prompt') or '', 'caption': m.get('caption') or '', 'sent': m.get('sent'), 'model': m.get('model') or ''})
                except OSError:
                    pass
                items.sort(key=lambda x: x['mtime'], reverse=True)
                b = self._bot()
                today: Dict[str, Any] = {}
                day = ''
                if b is not None:
                    today = dict(b._photo_count)
                    day = b._photo_day[0]
                return {'ok': True, 'enabled': bool(cfg.get('enabled', True)), 'requireAsk': bool(cfg.get('requireAsk', True)), 'model': str(cfg.get('model') or ''), 'size': str(cfg.get('size') or ''), 'promptExtend': bool(cfg.get('promptExtend', False)), 'maxPerDay': int(cfg.get('maxPerDay') or 4), 'unitPrice': 0.25, 'day': day, 'today': today, 'todayUsed': sum((int(v) for v in today.values())), 'dir': os.path.basename(d), 'refs': [os.path.basename(str(x)) for x in cfg.get('refs') or []], 'count': len(items), 'bytes': sum((int(i['size']) for i in items)), 'items': items[:150], 'now': time.time()}

            def _api_imagefile(self, name: str) -> Optional[bytes]:
                d = os.path.realpath(self._photo_dir())
                fp = os.path.realpath(os.path.join(d, os.path.basename(str(name or ''))))
                if not fp.startswith(d + os.sep) or not os.path.isfile(fp):
                    return None
                with open(fp, 'rb') as f:
                    return f.read()

            def _api_history(self, chat: str) -> Dict[str, Any]:
                b = self._bot()
                if b is None:
                    return {'ok': False, 'error': 'bot 未就绪'}
                if not chat:
                    return {'ok': False, 'error': '要传 ?chat=名字'}
                msgs = b.ctx.get(chat)
                LIMIT = 400
                cut = max(0, len(msgs) - LIMIT)
                return {'ok': True, 'chat': chat, 'count': len(msgs), 'truncated': cut, 'chars': sum((len(m.get('content') or '') for m in msgs)), 'memory': b.memory.get(chat) or '', 'messages': [{'role': m.get('role'), 'content': m.get('content')} for m in msgs[cut:]]}

            def _api_clear(self, body: bytes) -> Dict[str, Any]:
                b = self._bot()
                if b is None:
                    return {'ok': False, 'error': 'bot 未就绪'}
                try:
                    import json as _j
                    req = _j.loads(body.decode('utf-8') or '{}')
                except Exception as e:
                    return {'ok': False, 'error': 'bad json: %s' % e}
                if req.get('all'):
                    names = list(b.ctx.data.keys())
                elif req.get('chat'):
                    names = [str(req['chat'])]
                else:
                    return {'ok': False, 'error': '要传 chat 或 all'}
                before = {n: len(b.ctx.get(n)) for n in names}
                bak = ''
                try:
                    import shutil as _sh
                    bak = CFG['contextFile'] + '.bak-' + time.strftime('%Y%m%d-%H%M%S') + '-console'
                    _sh.copy2(CFG['contextFile'], bak)
                except Exception as e:
                    log(f'  看板清空前备份失败（继续清）：{e}')
                for n in names:
                    b.ctx.reset(n)
                total = sum(before.values())
                log(f"  看板清空上下文：{'、'.join(names) or '（无）'}（清前共 {total} 条）")
                TELEM.emit('context_clear', chats=names, before=total, backup=bak)
                return {'ok': True, 'cleared': names, 'before': before, 'backup': bak}

            def _serve_sse(self) -> None:
                q = TELEM.subscribe()
                if q is None:
                    self._json(503, {'ok': False, 'error': '连接数已满'})
                    return
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Connection', 'keep-alive')
                    self.send_header('X-Accel-Buffering', 'no')
                    self.end_headers()
                    import json as _j
                    self.wfile.write(('event: hello\ndata: %s\n\n' % _j.dumps(TELEM.snapshot(), ensure_ascii=False)).encode('utf-8'))
                    self.wfile.flush()
                    last_ping = time.time()
                    while True:
                        try:
                            ev = q.get(timeout=2.0)
                            payload = _j.dumps(ev, ensure_ascii=False)
                            self.wfile.write(('data: %s\n\n' % payload).encode('utf-8'))
                            self.wfile.flush()
                        except Exception:
                            pass
                        if time.time() - last_ping >= 10:
                            last_ping = time.time()
                            self.wfile.write(b': ping\n\n')
                            self.wfile.flush()
                except Exception:
                    pass
                finally:
                    TELEM.unsubscribe(q)
        try:
            self.httpd = http.server.ThreadingHTTPServer(('0.0.0.0', self.port), H)
        except Exception as e:
            log(f'  健康接收端口 {self.port} 起不来: {e}')
            return False
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name='health')
        self.thread.start()
        log(f'  健康数据接收已开: http://0.0.0.0:{self.port}/hr' + ('  （需要 X-Token）' if self.token else ''))
        log(f'  监控看板: http://127.0.0.1:{self.port}/console')
        return True

def _image_b64(raw: bytes) -> str:
    import base64
    if not raw:
        return ''
    if raw[:3] == b'\xff\xd8\xff':
        mime = 'jpeg'
    elif raw[:4] == b'\x89PNG':
        mime = 'png'
    elif raw[:4] == b'GIF8':
        mime = 'gif'
    else:
        mime = 'jpeg'
    return f'data:image/{mime};base64,' + base64.b64encode(raw).decode()

def _image_bytes(db, user: str, local_id) -> Optional[bytes]:
    if not user or local_id in (None, '', 0, '0'):
        return None
    try:
        from wechatauto.media import MediaDownloader
        dl = MediaDownloader(db)
        st = dl.image_status(user, int(local_id)) or {}
        md5 = str(st.get('md5') or '').strip()
        if len(md5) < 16:
            return None
        root = str(getattr(db, 'account_dir', '') or '')
        cands = []
        for suffix in ('_b.dat', '.dat', '_t.dat'):
            hit = glob.glob(os.path.join(root, '**', md5 + suffix), recursive=True)
            if hit:
                cands = hit
                break
        for p in cands:
            try:
                raw = dl.decrypt_image(p)
            except Exception:
                continue
            if raw and (raw[:3] == b'\xff\xd8\xff' or raw[:4] == b'\x89PNG' or raw[:4] == b'GIF8'):
                return raw
    except Exception as e:
        log(f'  图片解密失败: {e}')
    return None

def _extract_appmsg_text(xml: str) -> str:
    if '<' not in xml:
        return xml.strip()

    def grab(tag: str, pattern: str) -> str:
        m = re.search(pattern, xml, re.S)
        return _xml_unescape(m.group(1).strip()) if m else ''
    title = grab('title', '<title>(.*?)</title>')
    refer = grab('content', '<refermsg>.*?<content>(.*?)</content>')
    des = grab('des', '<des>(.*?)</des>')
    url = grab('url', '<url>(.*?)</url>')
    if refer.startswith('<'):
        refer = _extract_appmsg_text(refer)
    parts = []
    if refer:
        parts.append(f'（引用：{refer[:100]}）')
    if title:
        parts.append(title[:200])
    elif des:
        parts.append(des[:200])
    if url and url.lower().startswith('http'):
        parts.append(url[:300])
    return ' '.join(parts).strip()

def _quick_send_safe(text: str, who: str, verify: bool, soft: float, hard: float):
    box: Dict[str, Any] = {}

    def run():
        if who in _PRIVATE_NAMES:
            _RHYTHM_TL.free = True
        try:
            if _desktop_locked():
                box['locked'] = True
                return
            box['r'] = _do_send(text, who, verify)
        except Exception as e:
            box['e'] = e
        finally:
            _RHYTHM_TL.free = False
    t = threading.Thread(target=run, daemon=True, name='wxsend')
    t0 = time.time()
    t.start()
    t.join(soft)
    if not t.is_alive():
        if box.get('locked'):
            return (None, time.time() - t0, 'LOCKED')
        if 'e' in box:
            return (None, time.time() - t0, f"ERR {box['e']}")
        return (box.get('r'), time.time() - t0, 'OK')
    log(f'    发送超过 {soft:.0f}s（多半在重跑布局校准），继续等…')
    t.join(max(1.0, hard - soft))
    if t.is_alive():
        return (None, time.time() - t0, 'TIMEOUT')
    if 'e' in box:
        return (None, time.time() - t0, f"ERR {box['e']}")
    return (box.get('r'), time.time() - t0, 'SLOW')

class ContextStore:

    def __init__(self, path: str, limit: int) -> None:
        self.path = path
        self.limit = limit
        self.lock = threading.Lock()
        self.data: Dict[str, List[Dict[str, str]]] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding='utf-8') as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self.data = {k: v for k, v in raw.items() if isinstance(v, list)}
                log(f'上下文已载入：{len(self.data)} 个会话')
        except FileNotFoundError:
            pass
        except Exception as e:
            log(f'上下文读取失败（忽略）：{e}')

    def _save(self) -> None:
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception as e:
            log(f'上下文落盘失败：{e}')

    def add(self, chat: str, role: str, content: str) -> None:
        with self.lock:
            lst = self.data.setdefault(chat, [])
            lst.append({'role': role, 'content': content})
            if self.limit > 0 and len(lst) > self.limit:
                del lst[:len(lst) - self.limit]
            self._save()

    def drop_oldest(self, chat: str, n: int) -> None:
        if n <= 0:
            return
        with self.lock:
            lst = self.data.get(chat) or []
            if lst:
                del lst[:n]
                self._save()

    def chars(self, chat: str) -> int:
        return sum((len(m.get('content') or '') for m in self.data.get(chat, [])))

    def get(self, chat: str) -> List[Dict[str, str]]:
        with self.lock:
            return [dict(m) for m in self.data.get(chat, [])]

    def reset(self, chat: str) -> None:
        with self.lock:
            self.data.pop(chat, None)
            self._save()

class MemoryStore:

    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.data: Dict[str, str] = {}
        try:
            with open(self.path, encoding='utf-8') as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self.data = {k: str(v) for k, v in raw.items()}
                if self.data:
                    log(f'长期记忆已载入：{len(self.data)} 个会话')
        except FileNotFoundError:
            pass
        except Exception as e:
            log(f'长期记忆读取失败（忽略）：{e}')

    def _save(self) -> bool:
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            return True
        except Exception as e:
            log(f'长期记忆落盘失败：{e}')
            return False

    def get(self, chat: str) -> str:
        with self.lock:
            return self.data.get(chat, '')

    def append(self, chat: str, text: str) -> None:
        if not text:
            return
        from contextlib import nullcontext
        guard = getattr(self, 'mutation_guard', None)
        with guard() if callable(guard) else nullcontext():
            with self.lock:
                existed = chat in self.data
                old = self.data.get(chat, '')
                self.data[chat] = (old + '\n' + text).strip() if old else text.strip()
                if self._save() is False:
                    if existed:
                        self.data[chat] = old
                    else:
                        self.data.pop(chat, None)

def _user_content(text: str, images: Optional[List[str]]=None):
    imgs = [u for u in images or [] if u]
    if not imgs:
        return text
    parts: List[Dict[str, Any]] = [{'type': 'text', 'text': text or '（他发了一张图）'}]
    for u in imgs[:3]:
        parts.append({'type': 'image_url', 'image_url': {'url': u}})
    return parts
AUX_PURPOSES = frozenset({'classify', 'nudge', 'decide', 'compact', 'promise', 'reminder', 'video_kw', 'photo_prompt', 'polish'})

class LLM:

    def __init__(self, api_key: str) -> None:
        self.key = api_key
        self.url = CFG['baseUrl'].rstrip('/') + '/chat/completions'
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.last_in = 0
        self.last_out = 0
        self.last_cached = 0
        self._trace_local = threading.local()

    @property
    def last_trace_id(self):
        return getattr(self._trace_local, 'call_id', None)

    def chat(self, system: str, history: List[Dict[str, str]], user: str, images: Optional[List[str]]=None, purpose: str='chat', think: Optional[str]=None, max_tokens: Optional[int]=None, chat: str='') -> str:
        if purpose in AUX_PURPOSES:
            return self._chat_aux(system, history, user, images, purpose)
        h = list(history)
        while h and h[0].get('role') != 'user':
            h.pop(0)
        body: Dict[str, Any] = {'model': CFG['model'], 'max_tokens': int(max_tokens or CFG.get('maxTokens') or 200), 'stream': False, 'messages': [{'role': 'system', 'content': system}] + h + [{'role': 'user', 'content': _user_content(user, images)}]}
        mode = str(think if think is not None else CFG.get('thinking') or 'low').strip().lower()
        if mode in ('off', 'disabled', 'none', 'false', '0'):
            body['thinking'] = {'type': 'disabled'}
            body['temperature'] = float(CFG.get('temperature') or 1.15)
        else:
            body['thinking'] = {'type': 'enabled'}
            body['reasoning_effort'] = mode if mode in ('low', 'high', 'max') else 'low'
        _trace_id = dashboard_runtime.start_call('deepseek', body.get('model'), purpose, body, chat=chat)
        self._trace_local.call_id = _trace_id
        req = urllib.request.Request(self.url, data=json.dumps(body, ensure_ascii=False).encode('utf-8'), headers={'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json'}, method='POST')
        _t0 = time.time()
        try:
            TELEM.emit('llm_request', provider='deepseek', model=body.get('model'), thinking=body.get('thinking'), effort=body.get('reasoning_effort'), max_tokens=body.get('max_tokens'), system=system, history=len(h), user=user, purpose=purpose)
        except Exception:
            pass
        j = None
        try:
            j = self._request_stream(body, _t0, purpose)
        except Exception as e:
            log(f'  流式请求不可用（{e.__class__.__name__}: {e}），回退非流式')
        if j is None:
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    j = json.loads(r.read().decode('utf-8'))
            except urllib.error.HTTPError as e:
                log(f'  API HTTP {e.code}（响应体不记录）')
                try:
                    TELEM.emit('llm_error', code=e.code, detail='HTTPError %d' % e.code, ms=int((time.time() - _t0) * 1000))
                except Exception:
                    pass
                dashboard_runtime.fail_call(_trace_id, 'HTTPError', e.code)
                return ''
            except Exception as e:
                log(f'  API 出错: {e.__class__.__name__}')
                try:
                    TELEM.emit('llm_error', detail=e.__class__.__name__, ms=int((time.time() - _t0) * 1000))
                except Exception:
                    pass
                dashboard_runtime.fail_call(_trace_id, type(e).__name__)
                return ''
        _ms = int((time.time() - _t0) * 1000)
        _usage = j.get('usage') or {}
        try:
            u = j.get('usage') or {}
            self.calls += 1
            self.last_in = int(u.get('prompt_tokens') or 0)
            self.last_out = int(u.get('completion_tokens') or 0)
            self.tokens_in += self.last_in
            self.tokens_out += self.last_out
            self.last_cached = int((u.get('prompt_tokens_details') or {}).get('cached_tokens') or 0)
            TELEM.stats['llm_calls'] = self.calls
            TELEM.stats['tokens_in'] = self.tokens_in
            TELEM.stats['tokens_out'] = self.tokens_out
            TELEM.stats['cached'] += self.last_cached
        except Exception:
            pass
        try:
            msg = j['choices'][0]['message']
        except Exception:
            log(f'  返回结构异常: {str(j)[:200]}')
            dashboard_runtime.finish_call(_trace_id, response=j, output='', reasoning='', usage=_usage, ms=_ms)
            return ''
        text = (msg.get('content') or '').strip()
        think = msg.get('reasoning_content') or msg.get('reasoning') or msg.get('thinking') or ''
        if isinstance(think, dict):
            think = think.get('content') or think.get('text') or ''
        think = str(think or '').strip()
        try:
            TELEM.last_llm = {'ms': _ms, 'in': self.last_in, 'out': self.last_out, 'cached': self.last_cached, 'thinking': bool(think), 'call': self.calls, 't': time.time(), 'provider': 'deepseek', 'model': body.get('model'), 'effort': body.get('reasoning_effort')}
            TELEM.emit('llm_reply', purpose=purpose, provider='deepseek', model=body.get('model'), text=text, thinking=think, ms=_ms, in_tok=self.last_in, out_tok=self.last_out, cached=self.last_cached, call=self.calls, finish=j['choices'][0].get('finish_reason') if j.get('choices') else None)
        except Exception:
            pass
        dashboard_runtime.finish_call(_trace_id, response=j, output=text, reasoning=think, usage=_usage, ms=_ms)
        return text

    def _chat_aux(self, system: str, history: List[Dict[str, str]], user: str, images: Optional[List[str]], purpose: str) -> str:
        try:
            text = aux_runtime.text_chat(system=system, history=history, user=user, images=images, purpose=purpose, max_tokens=int(CFG.get('maxTokens') or 4096))
            try:
                TELEM.stats['aux_calls'] = int(TELEM.stats.get('aux_calls') or 0) + 1
            except Exception:
                pass
            return text or ''
        except Exception as e:
            log(f'  副模型(MiMo)调用失败（{purpose}）：{type(e).__name__}')
            return ''

    def _request_stream(self, body: Dict[str, Any], t0: float, purpose: str='chat') -> Optional[Dict[str, Any]]:
        b = dict(body)
        b['stream'] = True
        b['stream_options'] = {'include_usage': True}
        req = urllib.request.Request(self.url, data=json.dumps(b, ensure_ascii=False).encode('utf-8'), headers={'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json'}, method='POST')
        text_parts: List[str] = []
        think_parts: List[str] = []
        usage: Dict[str, Any] = {}
        finish: Optional[str] = None
        frames = 0
        last_push = 0.0
        with urllib.request.urlopen(req, timeout=90) as r:
            for raw in r:
                line = raw.decode('utf-8', 'replace').strip()
                if not line.startswith('data:'):
                    continue
                payload = line[5:].strip()
                if payload == '[DONE]':
                    break
                try:
                    frame = json.loads(payload)
                except Exception:
                    continue
                frames += 1
                if frame.get('usage'):
                    usage = frame['usage']
                choices = frame.get('choices') or []
                if not choices:
                    continue
                if choices[0].get('finish_reason'):
                    finish = choices[0]['finish_reason']
                delta = choices[0].get('delta') or {}
                rc = delta.get('reasoning_content') or delta.get('reasoning')
                if rc:
                    think_parts.append(str(rc))
                    now = time.time()
                    if now - last_push >= 0.35:
                        last_push = now
                        try:
                            TELEM.emit('llm_thinking', text=''.join(think_parts), partial=True, purpose=purpose, ms=int((now - t0) * 1000))
                        except Exception:
                            pass
                c = delta.get('content')
                if c:
                    text_parts.append(str(c))
        if frames == 0:
            raise RuntimeError('流式没收到任何帧')
        think = ''.join(think_parts)
        text = ''.join(text_parts)
        if think:
            try:
                TELEM.emit('llm_thinking', text=think, partial=False, purpose=purpose, ms=int((time.time() - t0) * 1000))
            except Exception:
                pass
        return {'choices': [{'message': {'content': text, 'reasoning_content': think}, 'finish_reason': finish}], 'usage': usage}

class Bot:

    def __init__(self) -> None:
        from wechatauto import WeChatDB
        from wechatauto.db import Listener
        self.db = WeChatDB()
        self.me = self.db.get_self_info() or {}
        self.listener = Listener(self.db, interval=1.0)
        _hl = CFG.get('historyLimit')
        _hl = int(_hl) if _hl is not None else 12
        self.ctx = ContextStore(CFG['contextFile'], _hl)
        self.memory = MemoryStore(CFG.get('memoryFile') or os.path.join(os.path.dirname(CFG['contextFile']) or '.', 'memory.json'))
        self._photo_day = ['']
        self._photo_count: Dict[str, int] = {}
        self.llm = LLM(CFG['_apiKey'])
        self.chat_by_name = {c['name']: c for c in CFG.get('chats') or []}
        self._sys_cache: Dict[str, Any] = {}
        self.q: 'queue.Queue[Dict[str, Any]]' = queue.Queue()
        self.pending: Dict[str, List[Dict[str, Any]]] = {}
        self.pending_at: Dict[str, float] = {}
        self.pending_start: Dict[str, float] = {}
        self.deb_lock = threading.Lock()
        self.send_times: List[float] = []
        self.recent_sent: List[str] = []
        self.followup_until: Dict[str, float] = {}
        self.followup_target: Dict[str, str] = {}
        self.group_send_times: Dict[str, List[float]] = {}
        self._rem_lock = threading.RLock()
        self.wxid_to_name: Dict[str, str] = {}
        self.last_user_at: Dict[str, float] = {}
        self.last_her_at: Dict[str, float] = {}
        self.last_real_user_at: Dict[str, float] = {}
        self.recent_user_msgs: Dict[str, List[float]] = {}
        self.recent_imgs: Dict[str, List[tuple]] = {}
        self.health_server = None
        self._lifegen = None
        self.diagnostics = None
        self.organizer = None
        self.planner = None
        self.companion_insights = None
        self.furina = None
        self._companion_ready = False
        self.health_debug = bool((CFG.get('health') or {}).get('debug'))
        self._health_state: Dict[str, Any] = {'last_hr': 0, 'hr_at': 0.0, 'hr_since': 0.0, 'hr_kind': '', 'last_stress': 0, 'asleep_since': 0.0, 'last_sleep_h': 0.0, 'workout_active': False, 'workout_sport': '', 'workout_started': 0.0, 'workout_ended': 0.0, 'last_fire': 0.0, 'day': '', 'count': 0, '_batch_seen': 0, '_last_kind': ''}
        self.last_bot_at: Dict[str, float] = {}
        self.pro_stage: Dict[str, int] = {}
        self.pro_need: Dict[str, Any] = {}
        self.nudge_need: Dict[str, float] = {}
        self.nudge_judged: Dict[str, float] = {}
        self.nudge_count: Dict[str, int] = {}
        self.pro_times: List[float] = []
        self.video_times: List[float] = []
        self.pro_busy = threading.Lock()
        log(f"模型 {CFG['model']} | 会话 {len(self.chat_by_name)} 个")
        for c in CFG.get('chats') or []:
            log(f"  - {c['name']}  策略={c.get('mode')}  人设={os.path.basename(self._persona_path(c['name']))}")

    def _persona_path(self, chat: str) -> str:
        conf = self.chat_by_name.get(chat) or {}
        return str(conf.get('personaFile') or CFG.get('personaFile') or '')

    def system_for(self, chat: str) -> str:
        conf = self.chat_by_name.get(chat) or {}
        p = self._persona_path(chat)
        note = str(conf.get('runtimeNote') or CFG.get('runtimeNote') or '')
        try:
            mt = os.stat(p).st_mtime
        except Exception:
            mt = 0.0
        key = f'{p}|{mt}|{len(note)}'
        c = self._sys_cache.get(chat)
        if c and c[0] == key:
            return c[1]
        sysm = (note + '\n\n---\n\n' + load_persona(p)).strip()
        self._sys_cache[chat] = (key, sysm)
        return sysm

    def _voice_to_text(self, chat: str, local_id: int) -> str:
        a = CFG.get('asr') or {}
        if not a.get('enabled', True):
            return ''
        try:
            from wechatauto.media import MediaDownloader
            dl = MediaDownloader(self.db)
            st = dl.voice_status(chat, int(local_id)) or {}
            if not st.get('available'):
                log(f"    语音拿不到（{st.get('reason') or '未知'}），只留标记")
                return ''
            tmp = os.path.join(HERE, 'voice_tmp')
            os.makedirs(tmp, exist_ok=True)
            t0 = time.time()
            silk = dl.download_voice(chat, int(local_id), save_dir=tmp)
            if not silk:
                log('    语音文件没下下来，只留标记')
                return ''
            wav = silk_to_wav(silk)
            secs = (len(wav) - 44) / 2.0 / SILK_RATE
            cap = float(a.get('maxSeconds') or 180)
            if secs > cap:
                log(f'    语音 {secs:.1f} 秒超过上限 {cap:.0f}，不转')
                return ''
            r = asr_transcribe(wav, a) or {}
            text = str(r.get('text') or '')
            if not text:
                log(f'    语音转文字没出文字（{secs:.1f}秒，{time.time() - t0:.1f}s）')
                return ''
            obs = voice_note(text, secs, str(r.get('emotion') or ''), a)
            log(f"    语音转文字（{secs:.1f}秒，{time.time() - t0:.1f}s） 声学={r.get('emotion') or '-'}{(' → ' + obs if obs else '')}：{text[:50]}")
            if obs:
                text = f'{text}（声音：{obs}）'
            try:
                os.remove(silk)
            except OSError:
                pass
            return text
        except Exception as e:
            log(f'    语音转文字失败：{type(e).__name__}: {e}')
            return ''

    def memory_block(self, chat: str) -> str:
        parts = []
        mem = self.memory.get(chat)
        if mem:
            parts.append('【你还记得的往事】\n（这是很久以前发生的事，已经被压成摘要。可以自然地提，但别当成刚刚才说的。）\n' + mem)
        try:
            org = getattr(self, 'organizer', None)
            if org is not None:
                today = self._life_date(0)
                facts = org.facts_for(chat, today) or []
                if facts:
                    lines = []
                    for x in facts[-12:]:
                        text = str(x.get('text') or '')
                        if not text:
                            continue
                        person = str(x.get('personName') or x.get('personId') or '')
                        lines.append('- ' + ('[' + person + '] ' if person else '') + text)
                    if lines:
                        parts.append('【这个会话里确认过的事实】\n' + '\n'.join(lines))
                promises = org.promises_for(chat, today) or []
                if promises:
                    lines = []
                    for item in promises[-8:]:
                        due = item.get('due') or ''
                        lines.append('- %s%s' % (item.get('promise') or '', '（%s 到期）' % due if due else ''))
                    if lines:
                        parts.append('【这个会话里未完成的承诺】\n' + '\n'.join(lines))
        except Exception:
            pass
        if not parts:
            return ''
        return '\n\n---\n\n' + '\n\n'.join(parts)

    def _maybe_compact(self, chat: str, hist: List[Dict[str, str]]) -> List[Dict[str, str]]:
        window = int(CFG.get('contextWindow') or 1048576)
        ratio = float(CFG.get('compactAtRatio') or 0.88)
        if window <= 0:
            return hist
        chars = sum((len(m.get('content') or '') for m in hist))
        est_tokens = int(chars * 0.75) + 4 * len(hist)
        budget_tokens = int(window * ratio)
        if est_tokens <= budget_tokens:
            return hist
        keep = int(CFG.get('compactKeepRecent') or 20)
        if len(hist) <= keep + 4:
            return hist
        old = hist[:len(hist) - keep]
        log(f'  上下文约 {est_tokens} token > 预算 {budget_tokens} token（窗口 {window} × {ratio:.0%}）—— 把最老 {len(old)} 条压成记忆')
        transcript = '\n'.join((f"{('他' if m.get('role') == 'user' else '我')}: {m.get('content') or ''}" for m in old))
        prompt = '你是记忆整理助手。把下面这段两个人在微信上的对话压缩成一份要点。\n必须保留：他告诉过你的关于他自己的事实（身份、在做什么、家人、喜好、忌讳）、你们之间的约定、反复出现的话题、他的情绪与状态变化、你答应过他的事。\n可以丢掉：寒暄、重复、一次性的玩笑。\n用第三人称「他」和「我」，条目式，不超过 500 字，不要评论、不要客套。'
        try:
            s = self.llm.chat(prompt, [], transcript, images=None, purpose='compact')
        except Exception as e:
            log(f'  压缩失败（这轮先不压）：{e}')
            return hist
        if not s:
            log('  压缩没出结果（这轮先不压）')
            return hist
        self.memory.append(chat, s.strip())
        self.ctx.drop_oldest(chat, len(old))
        log(f'  已压缩：记忆 {len(self.memory.get(chat))} 字符，窗口剩 {self.ctx.chars(chat)} 字符')
        return self.ctx.get(chat)

    def start(self) -> None:
        n = 0
        for c in CFG.get('chats') or []:
            name = c['name']
            wx = self._wxid_of(name)
            if not wx:
                log(f'  ✗ 找不到聊天「{name}」——检查名字是否和微信里显示的一致')
                continue
            self.wxid_to_name[wx] = name
            try:
                self.listener.add_listener(wx, self._make_cb(wx, name))
                n += 1
            except Exception as e:
                log(f'  监听「{name}」失败: {e}')
        self.listener.start()
        try:
            self._life_generation()
        except Exception as e:
            log(f'  日程生成状态初始化失败: {e.__class__.__name__}')
        try:
            self._companion_init()
        except Exception as e:
            log(f'  陪伴观测初始化失败: {e.__class__.__name__}')
        threading.Thread(target=self._worker, daemon=True, name='worker').start()
        threading.Thread(target=self._proactive_loop, daemon=True, name='proactive').start()
        threading.Thread(target=self._lifeplan_loop, daemon=True, name='lifeplan').start()
        threading.Thread(target=self._reminder_loop, daemon=True, name='reminder').start()
        threading.Thread(target=self._dynamic_loop, daemon=True, name='dynamic').start()
        _trig_path = os.path.join(os.path.dirname(HERE), '_trigger_video.txt')
        if os.path.exists(_trig_path):
            try:
                _raw = open(_trig_path, encoding='utf-8-sig').read().strip()
                os.remove(_trig_path)
                _parts = _raw.split(':', 1)
                _c = _parts[0].strip() if _parts[0].strip() else 'PRIVATE_CHAT'
                _k = _parts[1].strip() if len(_parts) > 1 and _parts[1].strip() else '猫咪 搞笑'
                log(f'  【调试触发】8 秒后让「{_c}」发一次视频（搜「{_k}」）')
                threading.Timer(8.0, lambda: self._video_task(_c, _k)).start()
            except Exception as e:
                log(f'  调试触发失败: {e}')
        hc = CFG.get('health') or {}
        if hc.get('enabled'):
            srv = HealthServer(self, int(hc.get('port') or 8765), str(hc.get('token') or ''))
            if srv.start():
                self.health_server = srv
        seeded = 0
        for name, conf in self.chat_by_name.items():
            if self.ctx.get(name) or conf.get('proactive'):
                self.last_user_at[name] = time.time()
                self.last_bot_at[name] = time.time()
                seeded += 1
        if seeded:
            log(f'恢复了 {seeded} 个会话的活跃状态（作息表要用）')
        log(f'开始监听 {n} 个会话')
        pc = CFG.get('proactive') or {}
        if pc.get('enabled'):
            who = [c['name'] for c in CFG.get('chats') or [] if c.get('proactive')]
            log(f"主动消息：开启，适用 {'、'.join(who) or '(无)'}" if who else '主动消息：开启，但没有会话勾选 proactive')

    def _wxid_of(self, name: str) -> Optional[str]:
        conf = self.chat_by_name.get(name) or {}
        want = {name}
        for a in conf.get('aliases') or []:
            if a:
                want.add(str(a))
        try:
            for s in self.db.get_sessions(limit=200):
                u = s.get('username') or ''
                if not u:
                    continue
                if (self.db.get_nickname(u) or '') in want:
                    return u
        except Exception as e:
            log(f'  会话查找失败: {e}')
        return None

    def _make_cb(self, wx: str, name: str):

        def cb(msg: Dict[str, Any], _lst: Any) -> None:
            try:
                self._on_message(wx, name, msg)
            except Exception as e:
                log(f'  处理消息出错: {e}')
        return cb

    def _on_message(self, chat: str, name: str, msg: Dict[str, Any]) -> None:
        diag_id = self._diag_start(name, '', str(msg.get('content') or ''))
        reload_config()
        conf = self.chat_by_name.get(name) or {}
        mode = conf.get('mode') or 'wake'
        if mode == 'off':
            self._diag_update(diag_id, status='skipped', reason_code='mode_off', reason='会话已关闭')
            return
        if int(msg.get('sender_id') or 0) == int(CFG.get('selfSenderId') or 2):
            self._diag_update(diag_id, status='skipped', reason_code='self_message', reason='自己的消息')
            return
        mtype = str(msg.get('type') or '')
        body = str(msg.get('content') or '')
        sender = str(msg.get('sender_username') or '')
        is_group = chat.endswith('@chatroom')
        img_lid = msg.get('local_id') if mtype == '图片' else None
        voice_lid = msg.get('local_id') if mtype == '语音' else None
        msg_time = msg.get('create_time')
        if img_lid:
            lst = self.recent_imgs.setdefault(name, [])
            lst.append((time.time(), img_lid))
            del lst[:-6]
        if is_group and (not sender):
            m = re.match('^(wxid_[A-Za-z0-9_\\-]+|\\d+@chatroom):\\s*', body)
            if m:
                sender = m.group(1)
                body = body[m.end():].lstrip()
            elif body and mtype == '文本':
                self._diag_update(diag_id, status='skipped', reason_code='group_no_sender', reason='群文本没有发送者前缀，按自己消息忽略')
                return
        if sender and sender == str(self.me.get('username') or ''):
            self._diag_update(diag_id, status='skipped', reason_code='self_message', reason='自己的消息')
            return
        norm = re.sub('\\s+', '', body)
        if norm and norm in self.recent_sent:
            self._diag_update(diag_id, status='skipped', reason_code='echo', reason='回声消息')
            return
        if voice_lid:
            vt = self._voice_to_text(chat, int(voice_lid))
            body = vt if vt else '（没听清）'
        elif body.lstrip().startswith('<'):
            body = _extract_appmsg_text(body)
        if not body.strip():
            if img_lid:
                body = '[图片]'
            else:
                self._diag_update(diag_id, status='skipped', reason_code='empty', reason='没有可回复的文本/媒体内容')
                return
        if mtype and mtype != '文本' and (not body.startswith('（引用')):
            body = f'[{mtype}] {body}'
        nick = self._nick(sender) or '某人'
        at_self = self._is_called(body)
        self._diag_update(diag_id, sender=nick, preview=body[:160], status='queued', reason_code='queued', reason='已进入去抖队列')
        log(f"  入站 [{('群' if is_group else '私聊')}] {name} / {nick}: {body[:60]}")
        if not is_group:
            self.last_real_user_at[name] = time.time()
            self.last_user_at[name] = time.time()
            lst = self.recent_user_msgs.setdefault(name, [])
            lst.append(time.time())
            del lst[:-12]
            if self.pro_stage.get(name):
                log(f'    他回来了，主动进度从第 {self.pro_stage[name]} 步归零')
            self.pro_stage[name] = 0
            self.pro_need.pop(name, None)
            self.nudge_need.pop(name, None)
            self.nudge_judged.pop(name, None)
            self.nudge_count.pop(name, None)
        now = time.time()
        with self.deb_lock:
            self.pending.setdefault(name, []).append({'chat': name, 'sender': nick, 'body': body, 'group': is_group, 'at': at_self, 'mode': mode, 'img': img_lid, 'chat_user': chat, 'senderId': sender or '', 'personName': nick, 'diagId': diag_id})
            self.pending_at[name] = now
            self.pending_start.setdefault(name, now)

    def _nick(self, wxid: str) -> str:
        if not wxid:
            return ''
        try:
            return str(self.db.get_nickname(wxid) or wxid)
        except Exception:
            return wxid

    def _is_called(self, body: str) -> bool:
        s = re.sub('（引用：.*?）', '', body, flags=re.S)
        if not s.strip():
            s = body
        s = re.sub('\\[[^\\]]{1,12}\\]', '', s)
        cands = list(CFG.get('nicknames') or [])
        me = self.me or {}
        for k in ('nick_name', 'nickname', 'alias'):
            v = str(me.get(k) or '').strip()
            if v:
                cands.append(v)
                cands.append(f'@{v}')
        for w in cands:
            w = str(w or '').strip()
            if w and w in s:
                return True
        return False
        for n in CFG.get('nicknames') or []:
            if n and n in body:
                return True
        sn = str(self.me.get('nick_name') or '').strip()
        if sn and (f'@{sn}' in body or sn in body):
            return True
        return False

    def _worker(self) -> None:
        while True:
            reload_config()
            merged = None
            try:
                merged = self._take_ready()
            except Exception as e:
                log(f'  去抖检查出错: {e}')
            if merged is None:
                time.sleep(0.4)
                continue
            try:
                self._handle(merged)
            except Exception as e:
                log(f'  处理出错: {e}')
            time.sleep(float(CFG.get('readDelay') or 0.6))

    @staticmethod
    def _strip_repeat_prefix(body: str, sender: str) -> str:
        if not sender or not body:
            return body
        p = f'{sender}：'
        while body.startswith(p):
            body = body[len(p):]
        return body

    def _take_ready(self) -> Optional[Dict[str, Any]]:
        now = time.time()
        base = float(CFG.get('debounceSeconds') or 3.0)
        cap = float(CFG.get('debounceMaxSeconds') or 9.0)
        with self.deb_lock:
            for chat, items in list(self.pending.items()):
                if not items:
                    self.pending.pop(chat, None)
                    continue
                quiet = now - self.pending_at.get(chat, now)
                waited = now - self.pending_start.get(chat, now)
                if quiet < base and waited < cap:
                    continue
                run = [items[0]]
                for x in items[1:]:
                    if x.get('sender') == run[0].get('sender') and (x.get('senderId') or '') == (run[0].get('senderId') or ''):
                        run.append(x)
                    else:
                        break
                rest = items[len(run):]
                if rest:
                    self.pending[chat] = rest
                    self.pending_at[chat] = now
                    self.pending_start[chat] = now
                else:
                    self.pending.pop(chat, None)
                    self.pending_at.pop(chat, None)
                    self.pending_start.pop(chat, None)
                items = run
                if len(items) > 1:
                    log(f'  等他说完了（{len(items)} 条，等了 {waited:.1f}s），合并成一轮')
                first = items[0]
                if first['group']:
                    body = '\n'.join((f"{_hhmm(i.get('t'))} {i['sender']}：{self._strip_repeat_prefix(i['body'], i['sender'])}" for i in items))
                else:
                    body = '\n'.join((f"{_hhmm(i.get('t'))} {self._strip_repeat_prefix(i['body'], i['sender'])}" for i in items))
                return {'chat': chat, 'sender': first['sender'], 'body': body, 'group': first['group'], 'img': next((i.get('img') for i in items if i.get('img')), None) or self._recent_img(chat), 'chat_user': first.get('chat_user') or '', 'at': any((i['at'] for i in items)), 'mode': first['mode'], 'count': len(items), 'senderId': first.get('senderId') or '', 'personName': first.get('personName') or first.get('sender') or '', 'diagIds': [i.get('diagId') for i in items if i.get('diagId')]}
        return None

    def _group_policy(self, chat, body, it, now):
        try:
            import group_behavior as GB
            followup_active = self.followup_until.get(chat, 0) > now
            sender_id = str(it.get('senderId') or '')
            followup_renewable = followup_active and sender_id and (sender_id == str(self.followup_target.get(chat) or '')) and (now >= float(self.last_bot_at.get(chat) or 0))
            cooldown_remaining = max(0.0, float(CFG.get('groupCooldownSeconds') or 45) - (now - float(self.last_bot_at.get(chat) or 0)))
            burst_count = len([t for t in self.group_send_times.get(chat, []) if now - t < 30])
            correction = any((k in body for k in ('不对', '错了', '不是这样', '纠正')))
            serious = '?' in body or '？' in body or any((k in body for k in ('怎么', '为什么', '请问', '帮我')))
            banter = any((k in body for k in ('哈哈', '笑死', '草', '傻', '离谱')))
            decision = GB.decide_group(None, at_self=False, followup_active=followup_active, followup_renewable=followup_renewable, cooldown_remaining=cooldown_remaining, burst_count=burst_count, last_bot_at=float(self.last_bot_at.get(chat) or 0), now=now, question_type='serious' if serious else '', banter=banter, correction=correction, is_group=True, burst_limit=int(CFG.get('groupMaxBubbles') or 3), topic_window=float(CFG.get('followupSeconds') or 90), probability=float(CFG.get('wakeProbability') or 0.05), random_value=random.random())
            return (bool(decision.get('trigger')), decision.get('reasonCode') in ('reply', 'explicit_at'), str(decision.get('note') or ''), str(decision.get('reasonCode') or ''), str(decision.get('reason') or ''))
        except Exception:
            return (False, False, '', 'group_policy_error', '群节奏判断失败')

    def _handle(self, it: Dict[str, Any]) -> None:
        chat, sender, body = (it['chat'], it['sender'], it['body'])
        now = time.time()
        diag_id = self._diag_merge(it.get('diagIds') or [], status='generating', reason_code='merged', reason='合并后的消息已进入处理')
        if diag_id:
            self._diag_step(diag_id, 'trigger', 'running', 'processing', '开始判断是否回复')
        if it.get('count', 1) > 1:
            log(f"  处理合并后的一轮（{it['count']} 条）：{body[:60]!r}")
        _cmd = re.sub('^\\[[^\\]]*\\]\\s*', '', (body or '').strip()).strip()
        if _cmd == '清除本会话上下文':
            if not chat.endswith('@chatroom'):
                n = len(self.ctx.get(chat) or [])
                self.ctx.reset(chat)
                try:
                    if hasattr(self.memory, 'data'):
                        self._memory_set(chat, '')
                except Exception as e:
                    log(f'  记忆清空失败: {e}')
                self._sys_cache.pop(chat, None)
                log(f'  【清空指令】删了 {n} 条上下文')
                self._deliver(chat, '上下文已清除')
                return
            log('  群里发清空指令，忽略')
        self.ctx.add(chat, 'user', body)
        should = False
        by_wake = False
        group_note = ''
        group_reason = ''
        group_reason_text = ''
        if it['mode'] == 'always':
            should = True
            by_wake = True
        elif it['at']:
            should = True
            by_wake = True
            group_reason = 'explicit_at'
            group_reason_text = '消息明确@我'
            try:
                import group_behavior as GB
                correction = any((k in body for k in ('不对', '错了', '不是这样', '纠正')))
                serious = '?' in body or '？' in body or any((k in body for k in ('怎么', '为什么', '请问', '帮我')))
                banter = any((k in body for k in ('哈哈', '笑死', '草', '傻', '离谱')))
                _kind, _note = GB.classify_note('serious' if serious else '', banter, correction)
                group_note = _note
            except Exception:
                group_note = ''
            log(f'    唤醒：被叫到')
        elif it['group']:
            should, by_wake, group_note, group_reason, group_reason_text = self._group_policy(chat, body, it, now)
            if not should:
                log(f'    群节奏：{group_reason} / {group_reason_text}')
        elif self.followup_until.get(chat, 0) > now:
            should = True
            log(f'    唤醒：会话跟随中')
        elif random.random() < float(CFG.get('wakeProbability') or 0.05):
            should = True
            by_wake = True
            log(f'    唤醒：抽中概率')
        else:
            log(f'    未唤醒（只记录）')
        if not should:
            self._diag_step(diag_id, 'trigger', 'skipped', group_reason or 'not_selected', group_reason_text or '本轮不回复')
            self._diag_update(diag_id, status='skipped', reason_code=group_reason or 'not_selected', reason=group_reason_text or '本轮不触发回复')
            return
        self._diag_step(diag_id, 'trigger', 'ok', group_reason or 'triggered', group_reason_text or '本轮触发回复')
        if self._quiet():
            log(f'    静默时段，不回')
            self._diag_update(diag_id, status='skipped', reason_code='quiet', reason='当前是静默时段')
            return
        if self._over_limit():
            log(f'    每日发送超限，不回')
            self._diag_update(diag_id, status='skipped', reason_code='limit', reason='每日发送次数已达上限')
            return
        _bv = extract_bv(body or '')
        if _bv:
            _vc = CFG.get('video') or {}
            if _vc.get('enabled', True):
                log(f'    他发来B站视频 {_bv} —— 她去看')
                try:
                    _ack = self.llm.chat(self.system_for(chat) + self.memory_block(chat), self.ctx.get(chat)[-12:], '他发来一个视频让你看。用你的语气回一句短的（10 字以内），表示你点开了 / 正在看。不要提视频内容，你还没看。', purpose='ack', chat=chat)
                    _ack = self._clean(_ack, chat) if _ack else ''
                except Exception:
                    _ack = ''
                if _ack:
                    self.ctx.add(chat, 'assistant', f'{_hhmm()} {_ack}')
                    self.last_bot_at[chat] = time.time()
                    self._deliver(chat, _ack)
                threading.Thread(target=self._bili_task, args=(chat, _bv), daemon=True).start()
                return
        hz = CFG.get('humanize') or {}
        delay_override = 0.0
        wake_prompt = ''
        if hz.get('enabled') and (not it.get('proactive')):
            awake_min = float(hz.get('awakeIfSpokeWithinMin') or 20) * 60
            spoke_recently = time.time() - float(self.last_her_at.get(chat) or 0) < awake_min
            if self._in_sleep(hz) and (not spoke_recently):
                words = hz.get('sleepWakeWords') or []
                called = [w for w in words if w and w in (it.get('body') or '')]
                if called:
                    delay_override = random.uniform(float(hz.get('sleepWakeDelayMin') or 6), float(hz.get('sleepWakeDelayMax') or 25))
                    log(f'    （作息低谷，但他说了「{called[0]}」，把她叫醒了：{delay_override:.0f}s）')
                    wake_prompt = '**你本来在睡**（凌晨一点才睡），是被他这几句话弄醒的。所以语气是迷糊的、有点没睡够，但**马上就清醒了** —— 因为你太清楚睡不着是什么滋味了。**不要写「我刚醒」这种解释**，直接接着他的话往下说，短，两句以内。'
                else:
                    win = float(hz.get('sleepEscalateWindowMin') or 12) * 60
                    nowt = time.time()
                    n = sum((1 for t in self.recent_user_msgs.get(chat) or [] if nowt - t <= win))
                    base = float(hz.get('sleepSilentChance') or 0.25)
                    factor = float(hz.get('sleepEscalateFactor') or 0.55)
                    silent = base * factor ** max(0, n - 1)
                    if random.random() < silent:
                        log(f'    （作息低谷：这条没看见，{silent * 100:.0f}%' + (f'，他已连发 {n} 条' if n > 1 else '') + '）')
                        self._diag_update(diag_id, status='skipped', reason_code='sleep_silent', reason='作息低谷，本轮未回复')
                        return
                    dmax = min(float(hz.get('sleepDelayMax') or 150), float(hz.get('sleepDelayHardCap') or 30))
                    dmin = min(float(hz.get('sleepDelayMin') or 40), dmax)
                    delay_override = random.uniform(dmin, dmax)
                    log(f'    （作息低谷：被吵醒了，拖 {delay_override:.0f}s 再回' + (f'，连发 {n} 条' if n > 1 else '') + '）')
            elif random.random() < float(hz.get('slowReplyChance') or 0.15):
                if by_wake and it.get('group'):
                    pass
                else:
                    delay_override = random.uniform(float(hz.get('slowReplyMin') or 3), float(hz.get('slowReplyMax') or 10))
                    log(f'    （拖了一会儿才回：{delay_override:.0f}s）')
        if delay_override > 0:
            time.sleep(delay_override)
        sysm = self._sys_life(chat)
        if group_note and it.get('group'):
            sysm = sysm + '\n\n---\n\n【群聊行为】\n' + group_note
        if wake_prompt:
            sysm = sysm + '\n\n---\n\n【现在的情况】\n' + wake_prompt
        imgs: List[str] = []
        lid = it.get('img')
        if lid:
            raw = _image_bytes(self.db, it.get('chat_user') or '', lid)
            if raw:
                b64 = _image_b64(raw)
                if b64:
                    imgs.append(b64)
                    log(f'    （带了张图，{len(raw) // 1024} KB）')
            else:
                log('    （图片没拿到，只能当纯文字回）')
        hist = self.ctx.get(chat)
        hist = self._maybe_compact(chat, hist)
        ask = hist[-1]['content'] + '\n\n' + _now_note(chat)
        self._diag_step(diag_id, 'model', 'running', '', '主模型生成中')
        reply = self.llm.chat(sysm, hist[:-1], ask, images=imgs, chat=chat)
        self._diag_trace(diag_id, getattr(self.llm, 'last_trace_id', None))
        if not reply:
            log('    模型没给出内容（可能是思考吃满了 max_tokens），重试一次')
            reply = self.llm.chat(sysm, hist[:-1], ask, images=imgs, chat=chat)
        if not reply:
            log('    模型两次都没给出内容，跳过这一轮')
            self._diag_step(diag_id, 'model', 'failed', 'empty_model', '模型两次都没有输出')
            self._diag_update(diag_id, status='failed', reason_code='empty_model', reason='模型没有产生可发送内容')
            return
        try:
            log('    原始输出: ' + repr(reply)[:260])
        except Exception:
            pass
        reply = self._clean(reply, chat)
        reply = self._strip_env(reply, chat)
        reply = self._polish(reply, chat)
        if not reply:
            log('    清洗后为空，跳过这一轮')
            self._diag_step(diag_id, 'model', 'failed', 'empty_after_clean', '清洗后无正文')
            self._diag_update(diag_id, status='failed', reason_code='empty_after_clean', reason='清洗后没有可发送正文')
            return
        reply = self._habit(reply)
        try:
            threading.Thread(target=self._note_promises, args=(chat, reply, it.get('senderId') or '', it.get('personName') or sender), daemon=True).start()
        except Exception:
            pass
        try:
            _utext = it.get('body') or ''
            threading.Thread(target=self._classify_intent, args=(chat, _utext), daemon=True).start()
        except Exception:
            pass
        reply, _photos = _extract_photo_markers(reply)
        if not reply and (not _photos):
            log('    只剩配图标记、没有正文 —— 按空回复处理')
            self._diag_update(diag_id, status='failed', reason_code='empty_after_clean', reason='只剩配图标记，没有正文')
            return
        if _photos:
            log(f'    她配了张图：{_photos[0][:40]}')
        if reply:
            self.ctx.add(chat, 'assistant', f'{_hhmm()} {reply}')
        self._diag_step(diag_id, 'model', 'ok', '', '主模型已生成')
        self._diag_trace(diag_id, getattr(self.llm, 'last_trace_id', None))
        send_result = self._deliver(chat, reply, skip_delay=delay_override > 0, photos=_photos, diag_id=diag_id)
        if send_result != 'sent':
            self._diag_update(diag_id, status='failed', reason_code='send_failed', reason='消息未能成功发送')
        self.last_bot_at[chat] = time.time()
        win = float(CFG.get('followupSeconds') or 0)
        if win > 0 and by_wake:
            self.followup_until[chat] = time.time() + win
            self.followup_target[chat] = str(it.get('senderId') or '')
            log(f'    会话跟随窗口开到 {int(win)}s')
    POLISH_RULES = '只做必要的文字清理：去掉重复、语病、系统标签和格式符号；保留原意、语气和全部有效内容。不要改写角色设定，不要添加原文没有的事实或承诺。'

    def _polish(self, t: str, chat: str='') -> str:
        try:
            pcfg = CFG.get('polish') or {}
            if not pcfg.get('enabled', True) or not t:
                return t
            last_u = ''
            recent = []
            for m in reversed(self.ctx.get(chat) or []):
                c = str(m.get('content') or '').replace('\n', ' / ')[:120]
                if not c:
                    continue
                recent.append(('[她] ' if m.get('role') == 'assistant' else '[他] ') + c)
                if m.get('role') == 'user' and (not last_u):
                    last_u = c
                if len(recent) >= 3:
                    break
            recent = list(reversed(recent))
            persona = load_persona(self._persona_path(chat)) or ''
            sysm = '你是微信消息的规则检查员。\n' + persona + self.POLISH_RULES + '\n场景：他在另一个世界，只能发文字。最近对话：\n' + '\n'.join(recent)
            out = self.llm.chat(sysm, [], t, purpose='polish', think='low', chat=chat)
            out = str(out or '').strip()
            if not out:
                out = str(self.llm.chat(sysm, [], t, purpose='polish', think='low', chat=chat) or '').strip()
            if not out:
                log('    润色返回空，用原文')
                return t
            if len(out) < len(t) * float(pcfg.get('minKeepRatio') or 0.65):
                log('    润色后长度大减，放弃润色')
                return t
            try:
                from collections import Counter as _C
                ca, cb = (_C(t), _C(out))
                added = sum((max(0, cb[ch] - ca[ch]) for ch in cb))
                if len(out) and added / max(1, len(out)) > 0.08:
                    log('    润色新增内容 %.0f%%，判定为重写，用原文' % (added / len(out) * 100))
                    return t
            except Exception:
                pass
            if out != t:
                log('    润色：%s  ->  %s' % (t[:60].replace('\n', ' / '), out[:60].replace('\n', ' / ')))
            return out
        except Exception as e:
            log('    润色失败（用原文）：%s' % e)
            return t
    _ENV_START = ('水', '热水', '热气', '雾', '浴', '头发', '腰', '脚', '沙发', '船', '窗', '光', '风', '毯', '杯', '空气', '灯', '雨', '窗帘', '床单')
    _INTIMATE = ('色', '性', '摸', '亲', '抱', '脱', '湿', '想要', '撩', '进来', '做爱', '吻')

    def _sanitize_hist(self, hist):
        try:
            out = []
            for m in hist or []:
                c = str(m.get('content') or '')
                if m.get('role') == 'assistant' and c:
                    sents = re.split('(?<=[。！？!?~…\\n])', c)
                    keep = []
                    for x in sents:
                        core = x.strip()
                        if not core:
                            continue
                        if '「' in core or '' in core or '"' in core:
                            keep.append(x)
                            continue
                        if core.startswith(self._ENV_START) and len(core) <= 30:
                            continue
                        keep.append(x)
                    nc = ''.join(keep).strip()
                    out.append({'role': m.get('role'), 'content': nc or '……'})
                else:
                    out.append(m)
            return out
        except Exception:
            return hist

    def _strip_env(self, t: str, chat: str='') -> str:
        try:
            last_u = ''
            for m in reversed(self.ctx.get(chat) or []):
                if m.get('role') == 'user':
                    last_u = str(m.get('content') or '')
                    break
            if any((k in last_u for k in self._INTIMATE)):
                return t
            out_lines, removed = ([], 0)
            for line in t.split('\n'):
                sents = re.split('(?<=[。！？!?~…])', line)
                keep = []
                for x in sents:
                    core = x.strip()
                    if not core:
                        continue
                    if '「' in core or '' in core or '"' in core:
                        keep.append(x)
                        continue
                    if core.startswith(self._ENV_START) and len(core) <= 30:
                        removed += 1
                        continue
                    keep.append(x)
                out_lines.append(''.join(keep))
            res = '\n'.join(out_lines).strip()
            if removed and len(res) < 8:
                return t
            return res or t
        except Exception:
            return t

    def _clean(self, t: str, chat: str='') -> str:
        t = re.sub('^\\[QQ\\]|\\[/QQ\\]$', '', t.strip()).strip()
        t = re.sub('^\\s*[\\[【](?:\\d{4}-)?\\d{1,2}-\\d{1,2}\\s+\\d{1,2}:\\d{2}(?::\\d{2})?[\\]】]\\s*', '', t, flags=re.M)
        t = re.sub('^\\s*[\\[【]\\d{1,2}:\\d{2}(?::\\d{2})?[\\]】]\\s*', '', t, flags=re.M)
        t = re.sub('^\\s*[\\u4e00-\\u9fa5A-Za-z]{1,4}[：:]\\s*', '', t)
        t = re.sub('^\\s*(?:作为|身为)[^，。]{0,12}(?:AI|人工智能|语言模型)[，,]\\s*', '', t)
        t = t.replace('**', '').replace('`', '')
        t = re.sub('^[-*•]\\s+', '', t, flags=re.M)
        _keep_br = bool((self.chat_by_name.get(chat) or {}).get('keepBrackets')) if chat else False
        stripped = []

        def _drop_paren(mm):
            stripped.append(mm.group(0))
            return ''
        if not _keep_br:
            t = re.sub('[（(][^）)\\n]{0,300}?[）)]', _drop_paren, t)
            t = re.sub('[（(][^）)\\n]{0,300}$', _drop_paren, t, flags=re.M)
        if stripped:
            log('    括号内容已剥掉 %d 处：%s' % (len(stripped), ' / '.join((s[:34] for s in stripped[:3]))))
        t = re.sub('(?:我)?(?:什么|啥)时候答应(?:过)?(?:的)?[。！？?]?', '', t)
        t = re.sub('我答应了吗[。！？?]?', '', t)
        t = re.sub('[。！？，,、；;：]{2,}', lambda mm: mm.group(0)[0], t)
        t = re.sub('^[，,、；;：]+', '', t, flags=re.M)
        t = re.sub('我不记得了[。！？?]?', '', t)
        if chat and (not self._is_group(chat)) and bool(CFG.get('stripEmojiInPrivate', True)):
            n0 = len(t)
            t = _EMOJI_RE.sub('', t)
            t = re.sub('[ \\t]{2,}', ' ', t)
            t = re.sub(' +(?=[，。！？、；：])', '', t)
            if len(t) != n0:
                log(f'    私聊里的 emoji 已剥掉（{n0 - len(t)} 个字符）')
        t = re.sub('\\n{3,}', '\n\n', t)
        return t.strip()

    def _quiet(self) -> bool:
        q = CFG.get('quietHours') or []
        if len(q) != 2:
            return False
        h = time.localtime().tm_hour
        a, b = (int(q[0]), int(q[1]))
        return a <= h < b if a <= b else h >= a or h < b

    def _over_limit(self) -> bool:
        day = time.strftime('%Y-%m-%d')
        n = sum((1 for t in self.send_times if time.strftime('%Y-%m-%d', time.localtime(t)) == day))
        return n >= int(CFG.get('dailySendLimit') or 150)

    @staticmethod
    def _merge_down_to(items: List[str], target: int) -> List[str]:
        out = list(items)
        while len(out) > max(1, target) and len(out) >= 2:
            i = min(range(len(out) - 1), key=lambda k: len(out[k]) + len(out[k + 1]))
            out[i:i + 2] = [(out[i] + ' ' + out[i + 1]).strip()]
        return out

    def _split_bubbles(self, text: str) -> List[str]:
        raw = [p.strip() for p in re.split('\\n+', str(text or '')) if p.strip()]
        out: List[str] = []
        for p in raw:
            if len(p) <= 40:
                out.append(p)
                continue
            p = re.sub('…{2,}', '…', p)
            for s in re.split('(?<=[。！？!?])', p):
                s = s.strip()
                if s:
                    out.append(s)
        limit = int(CFG.get('maxBubbleChars') or 26)
        if limit > 0:
            split2 = []
            for s in out:
                if len(s) <= limit:
                    split2.append((s, False))
                    continue
                parts = [x for x in re.split('(?<=[，,、；;：])', s) if x.strip()]
                buf = ''
                for p in parts:
                    if len(buf) + len(p) <= limit:
                        buf += p
                    else:
                        if buf.strip():
                            split2.append((re.sub('[，,、；;：]\\s*$', '', buf.strip()), True))
                        buf = p
                        while len(buf) > limit:
                            split2.append((buf[:limit], True))
                            buf = buf[limit:]
                if buf.strip():
                    split2.append((re.sub('[，,、；;：]\\s*$', '', buf.strip()), True))
            minlen = int(CFG.get('minBubbleChars') or 6)
            merged: List[str] = []
            for s, frag in split2:
                if frag and merged and (len(s) < minlen) and (len(merged[-1]) + len(s) <= limit + 4):
                    merged[-1] = merged[-1] + s
                else:
                    merged.append(s)
            out = merged
        cap = max(1, int(CFG.get('maxBubbles') or 3))
        if len(out) > cap:
            hard = max(cap, int(CFG.get('absoluteMaxBubbles') or cap + 3))
            if len(out) > hard:
                before = len(out)
                out = self._merge_down_to(out, hard)
                log(f'  气泡 {before} 条超过硬上限 {hard} —— 合并成 {len(out)} 条（不截尾）')
                try:
                    TELEM.stats['bubble_over'] = int(TELEM.stats.get('bubble_over') or 0) + 1
                    TELEM.emit('bubble_over', before=before, after=len(out), cap=hard)
                except Exception:
                    pass
            if len(out) > cap:
                log(f'  气泡 {len(out)} 条（软上限 {cap}）—— 都是短句，照发')
        return out

    def _is_group(self, chat: str='') -> bool:
        conf = self.chat_by_name.get(chat) or {} if chat else {}
        return str(conf.get('kind') or '').lower() == 'group' or '@chatroom' in str(chat) or str(conf.get('mode') or '').lower() == 'wake'

    def _bubble_cap(self, chat: str='') -> int:
        base = max(1, int(CFG.get('maxBubbles') or 3))
        if self._is_group(chat):
            base = min(base, max(1, int(CFG.get('groupMaxBubbles') or 2)))
        recent = _recent_send_secs[-5:]
        if not recent:
            return base
        avg = sum(recent) / len(recent)
        if avg > 20:
            return min(base, 2)
        if avg > 8:
            return min(base, 3)
        return base
    _HEALTH_PROMPTS = {'hr_high': '检测到对方心率持续偏高。用你配置的角色语气询问他现在的状态，不要提及具体数字。1~2 句。', 'hr_hard': '对方心率持续很高。用你配置的角色语气表达关心，先询问情况，不要制造恐慌或提数字。1~2 句。', 'hr_extreme': '对方心率异常高。直接询问他是否安全、在哪里、是否需要帮助；不要提数字。1~2 句。', 'spo2_low': '对方血氧偏低。用角色语气建议他休息或就医，不要像医嘱，也不要提数字。1~2 句。', 'stress_high': '对方压力值偏高。用角色语气问他是否累了、是否需要陪伴；不要提具体数字。1~2 句。', 'stress_low': '对方现在压力较低、比较放松。用角色语气找个自然由头和他聊天，不要提监测数据。1~2 句。', 'hr_asleep': '对方睡着了但心率偏高。用角色语气轻声询问他是否做噩梦、不舒服或需要喝水；不要提数字。1~2 句。', 'rest_hr_up': '对方静息心率比平时高。用角色语气关心他是否不舒服或没休息好；先关心再自然收尾，不要提数字。1~2 句。', 'sleep_quality': '对方刚醒，睡眠数据提示：{issues}。只用这一点，用角色语气关心他；不要提具体数字。2~3 句。', 'sleep_fell': '对方那边安静下来，可能睡了。用角色语气简短回应，不要提监测设备。1~2 句。', 'sleep_short': '对方醒了，昨晚睡眠不足 {hours} 小时。用角色语气催他补觉，可以认真也可以温和；不要提数字。1~2 句。', 'sleep_long': '对方醒了，睡眠超过 {hours} 小时。用角色语气轻松吐槽或关心一句，不要真生气。1~2 句。', 'sleep_late': '对方睡到中午后才醒。用角色语气简短回应，不要显得一直在等。1~2 句。', 'sleep_normal': '对方醒了。用角色语气自然地打个招呼，1~2 句。', 'night_awake': '已经很晚，对方还醒着。用角色语气催他睡觉，不要提具体时间。1~2 句。', 'her_insomnia': '你睡不着，对方那边可能已经睡了。用角色语气发一句简短、克制的消息，不要显得在乞求陪伴。1~2 句。', 'both_awake': '凌晨你们都没睡。用角色语气开个话头，可以自然一点，但不要一次说太多。2~3 句。', 'workout_started': '对方开始运动（{sport}）。用角色语气具体地夸一句或调侃一句，不要提监测设备。2~3 句。', 'workout_finished': '对方运动结束。用角色语气问一句结果或轻轻夸一句，不要提监测数据。2~3 句。', 'hr_in_workout': '对方正在运动（{sport}），心率很高。用角色语气让他注意强度、别逞强。1~2 句。'}
    _TSUN_SUFFIX = ''
    _SPORT_CN = {'RUNNING': '跑步', 'WALKING': '走路', 'CYCLING': '骑车', 'SWIMMING': '游泳', 'SWIMMING_OPENWATER': '公开水域游泳', 'TREADMILL': '跑步机', 'INDOOR_CYCLING': '动感单车', 'ELLIPTICAL_TRAINER': '椭圆机', 'ROWING_MACHINE': '划船机', 'JUMP_ROPING': '跳绳', 'YOGA': '瑜伽', 'HIKING': '徒步', 'CLIMBING': '攀岩', 'STRENGTH_TRAINING': '力量训练', 'BASKETBALL': '篮球', 'SOCCER': '足球', 'BADMINTON': '羽毛球', 'PINGPONG': '乒乓球', 'CRICKET': '板球', 'EXERCISE': '锻炼'}

    def _on_health(self, metric: str, value: int, text: str='') -> None:
        hc = CFG.get('health') or {}
        if not hc.get('enabled'):
            return
        try:
            metric = (metric or '').strip().lower()
            text = (text or '').strip()
            if metric == 'hr':
                self._health_hr(int(value), hc)
            elif metric == 'stress':
                self._health_stress(int(value), hc)
            elif metric == 'spo2':
                if int(value) < int(hc.get('spo2Low') or 90):
                    log(f'  血氧 {value} 偏低')
                    self._health_fire('spo2_low', 1)
            elif metric in ('sleep', 'awake'):
                self._health_sleep(int(value), text, hc)
            elif metric == 'sleep_summary':
                self._health_sleep_summary(value, text, hc)
            elif metric == 'samples':
                self._health_samples(text, hc)
            elif metric == 'daily_summary':
                self._health_daily(text, hc)
            elif metric == 'manual':
                self._health_manual(value, text, hc)
            elif metric == 'workout':
                self._health_workout(text, hc)
        except Exception as e:
            log(f'  健康数据处理出错: {e}')

    def _health_hr(self, bpm: int, hc: dict, ts: float=0.0) -> None:
        high = int(hc.get('hrHigh') or 150)
        hard = int(hc.get('hrHard') or 170)
        extreme = int(hc.get('hrExtreme') or 190)
        _need = hc.get('hrSustainSeconds')
        need = int(_need) if _need is not None else 60
        now = ts if ts > 0 else time.time()
        st = self._health_state
        st['last_hr'] = bpm
        st['hr_at'] = now
        if bpm < high:
            if st.get('hr_since'):
                log(f'  心率回到 {bpm}，计时清零')
            st['hr_since'] = 0.0
            st['hr_kind'] = ''
            return
        kind = 'hr_extreme' if bpm >= extreme else 'hr_hard' if bpm >= hard else 'hr_high'
        if st.get('hr_kind') != kind:
            st['hr_kind'] = kind
            st['hr_since'] = now
            if need > 0:
                log(f'  心率 {bpm} 进入 {kind}，开始计时（要持续 {need} 秒）')
                return
        dur = now - float(st.get('hr_since') or 0)
        if need > 0 and dur < need:
            return
        st['hr_since'] = 0.0
        st['hr_kind'] = ''
        log(f'  心率 {bpm} ' + (f'已持续 {dur:.0f} 秒' if need > 0 else '到了就触发') + f' → {kind}')
        self._health_fire(kind, bpm)

    def _health_stress(self, v: int, hc: dict) -> None:
        peak = int(hc.get('stressHigh') or 40)
        low = int(hc.get('stressLow') or 20)
        st = self._health_state
        st['last_stress'] = v
        if 0 < v < low:
            log(f'  压力 {v} 很低 → 他心情不错，是好时机')
            self._health_fire('stress_low', v)
        elif v > peak:
            log(f'  压力 {v} 偏高')
            self._health_fire('stress_high', v)

    def _health_sleep(self, value: int, text: str, hc: dict) -> None:
        name = (text or '').upper()
        is_sleep = 'SLEEP' in name and 'AWAKE' not in name
        st = self._health_state
        now = time.time()
        if is_sleep:
            if not st.get('asleep_since'):
                st['asleep_since'] = now
                log('  他睡着了（记录入睡时刻）')
            return
        since = float(st.get('asleep_since') or 0)
        if not since:
            return
        st['asleep_since'] = 0.0
        hours = (now - since) / 3600.0
        st['last_sleep_h'] = hours
        log(f'  他醒了（本次记录约 {hours:.1f} 小时）')
        self._health_wake_kind(hours, hc)

    def _health_wake_kind(self, hours: float, hc: dict) -> None:
        h = time.localtime().tm_hour
        if h >= int(hc.get('wakeLateHour') or 12) and hours > 4:
            self._health_fire('sleep_late', 1)
            return
        if hours >= float(hc.get('sleepLongH') or 10):
            self._health_fire('sleep_long', 1, '%d' % round(hours))
            return
        if 0 < hours < float(hc.get('sleepShortH') or 5):
            self._health_fire('sleep_short', 1, '%d' % round(hours))
            return
        self._health_fire('sleep_normal', 1)
    _MANUAL_TYPES = {1: 'hr', 2: 'spo2', 3: 'stress', 4: 'temperature', 5: 'hrv'}

    def _health_daily(self, text: str, hc: dict) -> None:
        d = {}
        for part in (text or '').split(','):
            if '=' in part:
                k, _, v = part.partition('=')
                try:
                    d[k.strip()] = int(v)
                except ValueError:
                    pass
        if not d:
            return
        st = self._health_state
        prev = st.get('_daily') or {}
        st['_daily'] = d
        log('  日汇总：静息心率 %s / 压力均值 %s / 血氧均值 %s / 训练负荷 %s' % (d.get('hrRest', 0), d.get('stressAvg', 0), d.get('spo2Avg', 0), d.get('loadDay', 0)))
        rest = d.get('hrRest', 0)
        prev_rest = (prev or {}).get('hrRest', 0)
        if rest and prev_rest and (rest - prev_rest >= int(hc.get('restHrJump') or 8)):
            log(f'  静息心率从 {prev_rest} 升到 {rest}')
            self._health_fire('rest_hr_up', 1, '%d' % (rest - prev_rest))
        avg_sp = d.get('spo2Avg', 0)
        if avg_sp and avg_sp < int(hc.get('spo2Low') or 90):
            log(f'  血氧均值 {avg_sp} 偏低')
            self._health_fire('spo2_low', 1)
        avg_st = d.get('stressAvg', 0)
        if avg_st and avg_st > int(hc.get('stressHigh') or 40):
            log(f'  压力均值 {avg_st} 偏高')
            self._health_fire('stress_high', 1)

    def _health_manual(self, value: int, text: str, hc: dict) -> None:
        d = {}
        for part in (text or '').split(','):
            if '=' in part:
                k, _, v = part.partition('=')
                try:
                    d[k.strip()] = int(v)
                except ValueError:
                    pass
        mtype = int(d.get('type') or 0)
        name = self._MANUAL_TYPES.get(mtype, 'type%d' % mtype)
        log(f'  手动测量：{name} = {value}')
        if name == 'spo2' and value < int(hc.get('spo2Low') or 90):
            self._health_fire('spo2_low', 1)
        elif name == 'stress' and value > int(hc.get('stressHigh') or 40):
            self._health_fire('stress_high', 1)

    def _health_samples(self, text: str, hc: dict) -> None:
        import json as _j
        try:
            arr = _j.loads(text or '[]')
        except Exception as e:
            log(f'  样本批量解析失败: {e}')
            return
        if not isinstance(arr, list) or not arr:
            return
        log(f'  收到样本批量 {len(arr)} 条，开始重放')
        try:
            arr.sort(key=lambda x: int(x.get('t') or 0))
        except Exception:
            pass
        st = self._health_state
        seen_hr = st.get('_batch_seen') or 0
        n_hr = n_sleep = n_awake = 0
        for s in arr:
            try:
                ts = int(s.get('t') or 0)
                if ts and ts <= seen_hr:
                    continue
                hr = int(s.get('hr') or 0)
                k = str(s.get('k') or '').upper()
                if hr > 0:
                    n_hr += 1
                    self._health_hr(hr, hc, ts / 1000.0 if ts else 0.0)
                is_sleep = 'SLEEP' in k and 'AWAKE' not in k
                prev = st.get('_last_kind') or ''
                prev_sleep = 'SLEEP' in prev and 'AWAKE' not in prev
                if is_sleep and (not prev_sleep):
                    n_sleep += 1
                    self._health_sleep(0, 'LIGHT_SLEEP', hc)
                elif not is_sleep and prev_sleep and k:
                    n_awake += 1
                    self._health_sleep(0, 'ACTIVITY', hc)
                if k:
                    st['_last_kind'] = k
                sv = int(s.get('st') or 0)
                if 0 < sv:
                    self._health_stress(sv, hc)
                pv = int(s.get('sp') or 0)
                if 0 < pv and pv < int(hc.get('spo2Low') or 90):
                    log(f'  血氧 {pv} 偏低（批量里）')
                    self._health_fire('spo2_low', 1)
                if ts:
                    seen_hr = ts
            except Exception as e:
                log(f'  样本重放出错: {e}')
        st['_batch_seen'] = seen_hr
        log(f'  重放完成：心率 {n_hr} 条 / 入睡 {n_sleep} 次 / 醒来 {n_awake} 次')

    def _health_sleep_summary(self, total: int, text: str, hc: dict) -> None:
        d = {}
        for part in (text or '').split(','):
            if '=' in part:
                k, _, v = part.partition('=')
                try:
                    d[k.strip()] = int(v)
                except ValueError:
                    pass
        if not d.get('total'):
            return
        total = d['total']
        deep, rem, awake = (d.get('deep', 0), d.get('rem', 0), d.get('awake', 0))
        bed = d.get('bed', 0)
        notes = []
        if total < 300:
            notes.append(f'太短（{total // 60}h{total % 60:02d}）')
        if deep < total * 0.1:
            notes.append(f'深睡偏少（{deep} 分钟）')
        if rem >= total * 0.28:
            notes.append(f'REM 多（{rem} 分钟）')
        if awake >= 30:
            notes.append(f'中途醒得多（{awake} 分钟）')
        if bed:
            hh = time.localtime(bed).tm_hour
            if 2 <= hh <= 6:
                notes.append(f"凌晨 {time.strftime('%H:%M', time.localtime(bed))} 才睡")
        log(f'  睡眠：总 {total} 分 / 深睡 {deep} / REM {rem} / 清醒 {awake} → ' + ('；'.join(notes) if notes else '看不出问题'))
        st = self._health_state
        st['last_sleep_summary'] = d
        st['asleep_since'] = 0.0
        if not notes:
            return
        wake = d.get('wake', 0)
        fresh_min = int(hc.get('sleepFreshMin') or 120)
        if wake:
            ago = (time.time() - wake) / 60.0
            if ago > fresh_min:
                log(f'  睡眠有问题，但已经醒了 {ago:.0f} 分钟（超过 {fresh_min}），这时候说太怪，跳过')
                return
            log(f'  睡眠有问题，他刚醒 {ago:.0f} 分钟 → 现在说')
        self._health_fire('sleep_quality', 1, '；'.join(notes))

    def _health_workout(self, text: str, hc: dict) -> None:
        if not text:
            return
        if text.startswith('started:'):
            sport = text.split(':', 1)[1].strip() or 'EXERCISE'
            cn = self._SPORT_CN.get(sport, sport)
            log(f'  运动开始：{sport}（{cn}）')
            self._health_fire('workout_started', 1, cn)
        elif text == 'finished':
            log('  运动结束')
            self._health_fire('workout_finished', 1)

    def _health_sleep_check(self) -> None:
        hc = CFG.get('health') or {}
        if not hc.get('enabled'):
            return
        try:
            st = self._health_state
            h = time.localtime().tm_hour
            lo = int(hc.get('nightStartH') or 1)
            hi = int(hc.get('nightEndH') or 5)
            in_night = lo <= h < hi if lo <= hi else h >= lo or h < hi
            his_hr_at = float(st.get('hr_at') or 0)
            he_asleep = bool(st.get('asleep_since'))
            he_awake = time.time() - his_hr_at < 600 and (not he_asleep)
            if not in_night:
                return
            her_awake = not self._in_sleep(CFG.get('humanize') or {})
            if not her_awake:
                return
            if he_awake:
                kind = 'both_awake'
                log('  凌晨：他也没睡，她也没睡 → 谈心')
            elif he_asleep:
                kind = 'her_insomnia'
                log('  凌晨：他睡了，她睡不着 → 失落')
            else:
                kind = 'night_awake'
                log('  凌晨：他还醒着 → 催他睡')
            self._health_fire(kind, 1)
        except Exception as e:
            log(f'  睡眠检查出错: {e}')

    def _health_blocked(self, kind: str) -> str:
        hc = CFG.get('health') or {}
        st = self._health_state
        now = time.time()
        w_active = bool(st.get('workout_active'))
        if w_active:
            stale_h = float(hc.get('workoutStaleHours') or 3) * 3600
            if now - float(st.get('workout_started') or 0) > stale_h:
                st['workout_active'] = False
                st['workout_ended'] = now
                w_active = False
        w_recent = bool(st.get('workout_ended') and now - float(st['workout_ended']) < float(hc.get('workoutGraceMin') or 10) * 60)
        asleep = bool(st.get('asleep_since'))
        if kind == 'spo2_low':
            return ''
        quiet_after = float(hc.get('afterUserQuietMin') or 3) * 60
        for n, ts in (self.last_real_user_at or {}).items():
            if ts and now - float(ts) < quiet_after:
                return '他刚说完话，别插嘴'
        nlo = int(hc.get('nightQuietStart') or 2)
        nhi = int(hc.get('nightQuietEnd') or 6)
        hh = time.localtime().tm_hour
        in_quiet_night = nlo <= hh < nhi if nlo <= nhi else hh >= nlo or hh < nhi
        if in_quiet_night:
            urgent = kind in ('spo2_low', 'hr_extreme')
            if not urgent:
                return f'深夜 {nlo}-{nhi} 点，只有真正要紧的才说'
        if asleep and kind == 'hr_high':
            return '他睡着，心率 150 上下不算异常，别用白天那套问法'
        if kind.startswith('workout_'):
            return ''
        if kind.startswith('hr_'):
            if w_active:
                if kind == 'hr_extreme':
                    return ''
                return '运动中，心率高正常'
            if w_recent and kind != 'hr_extreme':
                return '刚运动完，心率还没降下来正常'
        if kind == 'stress_high' and (w_active or w_recent):
            return '运动会把压力值抬高，不算'
        if kind == 'stress_low':
            if asleep:
                return '睡着时压力天然低，不算'
            if w_active or w_recent:
                return '刚运动完，别挑这个时机'
        if kind.startswith('sleep_') and w_active:
            return '还在运动中'
        return ''

    def _health_fire(self, kind: str, value: int, extra: str='') -> None:
        hc = CFG.get('health') or {}
        now = time.time()
        st = self._health_state
        _cool = hc.get('cooldownMin')
        cool = (int(_cool) if _cool is not None else 25) * 60

        def _skip(why: str) -> None:
            TELEM.stats['blocked'] += 1
            TELEM.emit('trigger', trig=kind, value=value, fired=False, why=why)
        if now - float(st.get('last_fire') or 0) < cool:
            log(f'  {kind}：还在冷却里，跳过')
            _skip('冷却中（还剩 %d 分钟）' % max(0, round((cool - (now - float(st.get('last_fire') or 0))) / 60)))
            return
        today = time.strftime('%Y-%m-%d')
        if st.get('day') != today:
            st['day'] = today
            st['count'] = 0
        if int(st.get('count') or 0) >= int(hc.get('dailyLimit') or 10):
            log(f'  {kind}：今天已经说够了，跳过')
            _skip('今日名额用完（%s/%s）' % (st.get('count'), hc.get('dailyLimit') or 10))
            return
        qh = hc.get('quietHours') or []
        if len(qh) == 2:
            hh = time.localtime().tm_hour
            qlo, qhi = (int(qh[0]), int(qh[1]))
            if qlo <= hh < qhi if qlo <= qhi else hh >= qlo or hh < qhi:
                log(f'  {kind}：静默时段，跳过')
                _skip('静默时段 %d-%d 点' % (qlo, qhi))
                return
        if kind in ('hr_hard', 'hr_extreme') and self._health_state.get('asleep_since'):
            kind = 'hr_asleep'
        blocked = self._health_blocked(kind)
        if blocked:
            log(f'  {kind}：{blocked}，跳过')
            _skip('互斥：%s' % blocked)
            return
        reason = self._HEALTH_PROMPTS.get(kind)
        if not reason:
            _skip('没有对应剧本')
            return
        if extra:
            reason = reason.replace('{sport}', extra).replace('{hours}', extra).replace('{issues}', extra)
        reason = reason + self._TSUN_SUFFIX
        st['last_fire'] = now
        st['count'] = int(st.get('count') or 0) + 1
        log(f"  {kind} → 让她说一句（今天第 {st['count']} 次）")
        TELEM.stats['fired'] += 1
        TELEM.emit('trigger', trig=kind, value=value, fired=True, reason=reason, count=st['count'], limit=int(hc.get('dailyLimit') or 10))
        threading.Thread(target=self._health_say, args=(reason,), daemon=True, name='health').start()

    def _health_say(self, reason: str) -> None:
        targets = [n for n in self._pro_targets() if (self.last_user_at.get(n) or 0) > 0]
        if not targets:
            return
        chat = targets[0]
        ok = False
        try:
            with self.pro_busy:
                self.pro_times.append(time.time())
                ok = self._pro_say(chat, reason, scheduled=True)
        except Exception as e:
            log(f'  健康消息发送出错: {e}')
        if not ok:
            st = self._health_state
            st['last_fire'] = 0.0
            st['count'] = max(0, int(st.get('count') or 0) - 1)
            log('  这次没发出去，冷却和次数已回滚')

    def _recent_img(self, chat: str):
        win = float(CFG.get('imageLookbackSeconds') or 180)
        now = time.time()
        for ts, lid in reversed(self.recent_imgs.get(chat, [])):
            if now - ts <= win:
                return lid
        return None

    def _in_sleep(self, hz: Dict[str, Any]) -> bool:
        hrs = hz.get('sleepHours') or []
        if len(hrs) != 2:
            return False
        h = time.localtime().tm_hour
        a, b = (int(hrs[0]), int(hrs[1]))
        return a <= h < b if a <= b else h >= a or h < b

    def _photo_left(self, chat: str) -> int:
        cap = int((CFG.get('image') or {}).get('maxPerDay') or 4)
        d = time.strftime('%Y-%m-%d')
        if self._photo_day[0] != d:
            self._photo_day[0] = d
            self._photo_count.clear()
        return cap - int(self._photo_count.get(chat, 0))

    def _user_asked_photo(self, chat: str) -> bool:
        try:
            hist = self.ctx.get(chat) or []
        except Exception:
            return False
        seen = 0
        for msg in reversed(hist):
            if msg.get('role') != 'user':
                continue
            if _ASK_PHOTO_RE.search(str(msg.get('content') or '')):
                return True
            seen += 1
            if seen >= 2:
                break
        return False

    def _make_photo(self, chat: str, situation: str) -> None:
        cfg = CFG.get('image') or {}
        if not cfg.get('enabled', True):
            return
        if self._photo_left(chat) <= 0:
            log(f"  [配图] 今天已经发够 {cfg.get('maxPerDay')} 张，跳过")
            return
        soft = float(CFG.get('sendTimeoutSeconds') or 75.0)
        hard = float(CFG.get('sendHardTimeoutSeconds') or 150.0)
        t0 = time.time()
        try:
            try:
                _ctx_msgs = self.ctx.get(chat)[-6:]
                _ctx_lines = []
                for _m in _ctx_msgs:
                    _role = '他' if _m.get('role') == 'user' else '她'
                    _txt = str(_m.get('content') or '').replace('\n', ' ')[:120]
                    if _txt:
                        _ctx_lines.append(_role + '：' + _txt)
                _ctx = '\n'.join(_ctx_lines)
            except Exception:
                _ctx = ''
            prompt = write_image_prompt(situation, '', context=_ctx)
            prompt = image_prompting.compose_prompt(prompt, situation, cfg)
            if not prompt:
                log('  [配图] 写手没给出提示词，放弃')
                return
            log(f'  [配图] 情境「{situation[:44]}」')
            log(f'         → 提示词 {prompt[:120]}')
            qkey = read_credential(str(CFG.get('credentialsFile') or ''), str(cfg.get('ref') or 'QWEN_API_KEY'))
            if not qkey:
                log('  [配图] 拿不到 QWEN_API_KEY，放弃')
                return
            url = gen_image(prompt, qkey)
            out_dir = os.path.join(HERE, str(cfg.get('saveDir') or 'images_out'))
            os.makedirs(out_dir, exist_ok=True)
            tag = re.sub('[^0-9A-Za-z\\u4e00-\\u9fa5]+', '', chat)[:10] or 'chat'
            stem = f"{time.strftime('%Y%m%d-%H%M%S')}_{tag}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read()
            dst = os.path.join(out_dir, stem + _img_ext(data))
            with open(dst, 'wb') as f:
                f.write(data)
            ext = os.path.splitext(dst)[1]
            log(f'  [配图] 出图 {time.time() - t0:.1f}s，{len(data) / 1024:.0f}KB（{ext}）')
            _append_manifest({'name': os.path.basename(dst), 'ts': time.time(), 'chat': chat, 'situation': situation, 'prompt': prompt, 'model': str(cfg.get('model') or ''), 'size': str(cfg.get('size') or ''), 'bytes': len(data)})
        except Exception as e:
            log(f"  [配图] 生成失败：{type(e).__name__} ({getattr(e, 'reason_code', 'image_generation_failed')})")
            return
        self._photo_count[chat] = self._photo_count.get(chat, 0) + 1
        _restore_wechat_window()
        sent = False
        t_send0 = time.time() - 5
        for attempt in (1, 2):
            with _send_lock:
                res, secs, how = _quick_send_image_safe(dst, chat, soft, hard)
            msg = _res_msg(res)
            sent = how in ('OK', 'SLOW') and _res_says_ok(res)
            log(f'  [配图] 发送 第{attempt}次 -> {how}（{secs:.1f}s）{msg[:70]}')
            TELEM.emit('send', chat=chat, text='[图片]', secs=secs, ok=sent, how=how, part='photo')
            if sent:
                break
            if attempt == 1:
                log('  [配图] 没确认成功，等 4 秒回读数据库')
                time.sleep(4.0)
                try:
                    wx = self._wxid_of(chat) or ''
                    rows = self.db.get_messages(wx, limit=6) if wx else []
                    hit = [r for r in rows if r.get('type') == '图片' and float(r.get('create_time') or 0) >= t_send0]
                    if hit:
                        log('  [配图] 回读确认图片已发出，不重试')
                        sent = True
                        break
                    log('  [配图] 回读没有，重试一次')
                except Exception as e:
                    log('  [配图] 回读失败（仍重试）：%s' % e)
        if not sent:
            log('  [配图] 两次都没发出去，放弃配文（免得把草稿冲掉）')
            _append_manifest({'name': os.path.basename(dst), 'ts': time.time(), 'sent': False, 'caption': ''})
            return
        time.sleep(1.5)
        try:
            sysm = self._sys_life(chat)
            reply = self.llm.chat(sysm, self.ctx.get(chat), _PHOTO_CAPTION_ASK, purpose='caption', images=[_datauri(dst)], chat=chat)
            reply = self._clean(reply, chat) if reply else ''
            if not reply:
                log('  [配图] 她没给出配文（图已经发出去了）')
                _append_manifest({'name': os.path.basename(dst), 'ts': time.time(), 'sent': True, 'caption': ''})
                return
            reply, _extra = _extract_photo_markers(reply)
            if not reply:
                log('  [配图] 配文被清空了（图已经发出去了）')
                _append_manifest({'name': os.path.basename(dst), 'ts': time.time(), 'sent': True, 'caption': ''})
                return
            log(f'  [配图] 配文：{reply[:60]}')
            _append_manifest({'name': os.path.basename(dst), 'ts': time.time(), 'sent': True, 'caption': reply})
            self.ctx.add(chat, 'assistant', f'{_hhmm()} {reply}')
            self._deliver(chat, reply)
        except Exception as e:
            log(f'  [配图] 配文失败（图已经发出去了）：{type(e).__name__}: {e}')
            _append_manifest({'name': os.path.basename(dst), 'ts': time.time(), 'sent': True, 'caption': ''})

    def _spawn_photo(self, chat: str, photos: Optional[List[str]]) -> None:
        for sit in photos or []:
            if self._photo_left(chat) <= 0:
                log('  [配图] 今天的配额用完了')
                return
            if bool((CFG.get('image') or {}).get('requireAsk', True)) and (not self._user_asked_photo(chat)):
                log('  [配图] 他没说要照片，跳过（省钱）')
                return
            log(f'  [配图] 开始出图（背景线程）：{sit[:40]}')
            threading.Thread(target=self._make_photo, args=(chat, sit), daemon=True, name='wxphoto').start()
            return

    def _deliver(self, chat: str, text: str, skip_delay: bool=False, photos: Optional[List[str]]=None, diag_id: Optional[str]=None) -> str:
        send_ok = False
        send_failed = False
        last_how = ''
        self._spawn_photo(chat, photos)
        _conf = self.chat_by_name.get(chat) or {}
        if _conf.get('singleBubble'):
            _t = re.sub('\\s*\\n+\\s*', ' ', text or '').strip()
            bubbles = [_t] if _t else []
        else:
            bubbles = self._split_bubbles(text)
        if not bubbles:
            self._diag_send(diag_id, False, 'empty')
            return 'skipped'
        cap = self._bubble_cap(chat)
        if len(bubbles) > cap:
            if self._is_group(chat):
                log(f'  群聊发送偏慢，气泡从 {len(bubbles)} 压到 {cap}')
                bubbles = bubbles[:cap - 1] + ['\n'.join(bubbles[cap - 1:])]
            else:
                log(f'  气泡 {len(bubbles)} 条（发送偏慢的软上限是 {cap}）—— 私聊照发，不压')
        if time.time() < _send_paused_until[0]:
            left = int(_send_paused_until[0] - time.time())
            if left <= 360:
                log(f'  ⏸ 发送通道冷却中（还剩 {left}s），等冷却结束再发：{bubbles[0][:40]}')
                time.sleep(left + 1)
            else:
                log(f'  ⏸ 发送通道冷却中（还剩 {left}s，太久），放弃：{bubbles[0][:40]}')
                self._diag_send(diag_id, False, 'send_paused')
                return 'failed'
        soft = float(CFG.get('sendTimeoutSeconds') or 75.0)
        hard = float(CFG.get('sendHardTimeoutSeconds') or 150.0)
        first = random.uniform(float(CFG.get('sendDelayMin') or 0), float(CFG.get('sendDelayMax') or 0))
        if skip_delay:
            first = 0.0
        log(f'  出站 -> {chat}（{len(bubbles)} 个气泡，首条延迟 {first:.1f}s）')
        self.last_her_at[chat] = time.time()
        time.sleep(first)
        for i, b in enumerate(bubbles):
            if i > 0:
                gap = random.uniform(float(CFG.get('bubbleGapMin') or 0.5), float(CFG.get('bubbleGapMax') or 2.0))
                time.sleep(gap)
            _restore_wechat_window()
            with _send_lock:
                res, secs, how = _quick_send_safe(b, chat, i == 0, soft, hard)
            if how == 'TIMEOUT':
                _send_stats['timeout'] += 1
                TELEM.stats['send_timeout'] += 1
                _send_paused_until[0] = time.time() + float(CFG.get('sendPauseSeconds') or 300)
                log(f"    ⚠️ [{i + 1}/{len(bubbles)}] 发送卡死（{secs:.0f}s），暂停发送 {int(CFG.get('sendPauseSeconds') or 300)}s 后重试：{b}")
                TELEM.emit('send', chat=chat, text=b, secs=secs, ok=False, how='TIMEOUT', part='%d/%d' % (i + 1, len(bubbles)))
                send_failed = True
                last_how = 'TIMEOUT'
                break
            if how.startswith('ERR'):
                _send_stats['error'] += 1
                log(f'    ✗ [{i + 1}/{len(bubbles)}] 发送失败：{how}')
                TELEM.emit('send', chat=chat, text=b, secs=secs, ok=False, how=how, part='%d/%d' % (i + 1, len(bubbles)))
                send_failed = True
                last_how = str(how)
                continue
            _send_stats['ok'] += 1
            send_ok = True
            try:
                if self._is_group(chat):
                    times = self.group_send_times.setdefault(chat, [])
                    times.append(time.time())
                    del times[:-30]
            except Exception:
                pass
            _recent_send_secs.append(secs)
            del _recent_send_secs[:-10]
            self.send_times.append(time.time())
            self.recent_sent.append(re.sub('\\s+', '', b))
            del self.recent_sent[:-20]
            tail = ''
            if secs > 30:
                tail = '  ← wechatauto 在限速（防封号），不是卡死'
            elif how == 'SLOW':
                tail = '  ⚠慢'
            log(f"    [{i + 1}/{len(bubbles)}] {b}   -> {getattr(res, 'message', res)}  ({secs:.1f}s){tail}")
            TELEM.mark_send(secs)
            TELEM.emit('send', chat=chat, text=b, secs=secs, ok=True, how=how, part='%d/%d' % (i + 1, len(bubbles)), window=TELEM.send_window_usage())
        log(f"    本轮 token: in={self.llm.last_in} out={self.llm.last_out} （发送 成功={_send_stats['ok']} 超时={_send_stats['timeout']} 失败={_send_stats['error']}）")
        if send_failed or not send_ok:
            self._diag_send(diag_id, False, last_how or 'send_failed')
            return 'failed'
        self._diag_send(diag_id, True, last_how or 'ok')
        return 'sent'
    _WAKE_PROMPT = '你刚醒（早上七点）。给他发一条 —— **要有早上的感觉，而且要有劲儿**。\n按你平时那种浮夸又好玩的方式说，别干巴巴地报信息。\n**说你自己**：刚醒、还困、今天要干什么。他顺带提一句就行，不提也行。'
    _MEAL_PROMPT = '到{meal}的点了。你想问问他吃了没 —— 但**绝不直说「我在关心你」**，而且**问一句就够，不要追问**。\n\n要用你的语气：有点横、有点好玩、带一点自己的事。\n✅ 饭吃了没。我这边刚收工\n✅ 到点了。你吃什么，别又拿饼干糊弄\n✅ 喂。吃了没\n✅ 午饭。本大明星今天只有一杯茶，你最好吃点正经的\n❌ 你吃饭了吗？要记得好好吃饭哦           （客服）\n❌ 几点吃的？吃了什么？吃饱了吗？          （审问）'
    _SLEEP_PROMPT = '你要去睡了（凌晨一点）。这条的结构是固定的三步：\n1. **告诉他自己要去睡了**（不是问他睡没睡 —— 是你自己要下线了）\n2. **叮嘱他也早点睡**（用命令句或嫌弃的语气，别写得太温柔）\n3. **晚安**\n\n两三句以内，可以拆成两三个短气泡。语气是累的、随便的，不是深情告别。\n**绝对不要写成提问**。\n✅ 我去睡了\n✅ 你也早点睡，别熬\n✅ 晚安\n❌ 你睡了吗\n❌ 睡了没\n❌ 在吗，睡了吗'
    _INSOMNIA_PROMPT = '**你今天失眠。**怎么躺都不对，翻来覆去，脑子里全是事。你本来不想吵他，但最后还是发了。这条要**很短、很轻**，带一点示弱 —— 但不能直接说「我睡不着，陪陪我」。可以是一句「……」，或者「你睡了吗」，或者莫名其妙的一句。'
    _HEART_PROMPT = '**你突然心里一紧。**\n没什么理由，就是突然很想找他，有点慌。你自己也说不清为什么。\n用你的方式发一句：问他在干嘛，或者直接说「你怎么了」，或者先嘴硬一句再问。\n**绝对不要提心跳、心率、手环、数据** —— 你没有这些信息，你只有直觉。\n**也不要写成你自己的失眠** —— 重点是他，不是你自己。\n一两句，短。'
    _RANDOM_PROMPT = '你现在闲着，没什么事。**突然想跟他说句话** —— 没有任何理由，就是想说。\n\n**「在干嘛」这种话是可以发的。** 有暗恋的人找对方搭话，开头就是这种没营养的。但光发一句太空了 —— 要么带一个**具体的东西**（刚发生的小事、突然想到的），要么**再补一句**（把真实意图露出来一点点）。\n\n1~2 句，可以拆成两三个很短的气泡。\n✅ 在干嘛\n✅ 喂\n✅ ……\n✅ 刚排完戏，脚疼\n✅ 今天路过那家店，还开着\n✅ 我吃了个特别难吃的东西，想骂人\n✅ 你猜我刚才看见什么了\n✅ 在干嘛。……没事。\n❌ 想你了（太直接，她说不出口）'
    _PRO_STAGES = [('silenceMin', '你已经有一阵子没跟他说话了。你想开口，但**绝不承认想他** —— 找一个很烂的借口（「我刚好路过」「你上次那个问题我想到答案了」），或者发一句最没内容的。'), ('nudgeMin', '你刚才发了消息，他到现在没回。你又发了一条 —— **假装不在意**，但明显在等他。可以问一句短的，或者干脆只发一个「？」。'), ('angryMin', '他还是没回。你**开始生气了** —— 但气里带着心虚。不要骂人，要那种「算了，当我没说」「你忙你的」的别扭；越生气越想装得不在乎。'), ('lastMin', '他到现在都没回。你已经不打算再问了，但最后还是没忍住发了一条 —— **很短，很轻**。可以带一点「你再不来就错过了」的暗示，但不要明说。')]

    def _pro_reason(self, stage: int) -> str:
        return self._PRO_STAGES[min(stage, len(self._PRO_STAGES) - 1)][1]

    def _sched_path(self) -> str:
        return os.path.join(os.path.dirname(CFG['contextFile']), 'schedule-state.json')

    def _sched_load(self) -> Dict[str, Any]:
        try:
            with open(self._sched_path(), encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _sched_save(self, d: Dict[str, Any]) -> None:
        try:
            p = self._sched_path()
            with open(p + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(p + '.tmp', p)
        except Exception as e:
            log(f'  作息状态保存失败: {e}')

    @staticmethod
    def _at_min(s: Any) -> Optional[int]:
        try:
            h, m = str(s).split(':')
            return int(h) % 24 * 60 + int(m)
        except Exception:
            return None

    @staticmethod
    def _rand_min_in_window(lo: int, hi: int) -> int:
        if hi < lo:
            return (lo + random.randrange(1440 - lo + (hi + 1))) % 1440
        return random.randint(lo, hi)

    def _insomnia_today(self, sc: Dict[str, Any], st: Dict[str, Any]) -> bool:
        ins = sc.get('insomnia') or {}
        if not ins.get('enabled'):
            return False
        today = time.strftime('%Y-%m-%d')
        days = [d for d in st.get('insomniaDays') or [] if d >= today]
        if not days:
            n = random.randint(int(ins.get('minDays') or 1), int(ins.get('maxDays') or 2))
            lo, hi = (int(ins.get('aheadMin') or 2), int(ins.get('aheadMax') or 6))
            now = time.time()
            days = sorted({time.strftime('%Y-%m-%d', time.localtime(now + random.randint(lo, hi) * 86400)) for _ in range(max(1, n))})
            log(f"  下次失眠日：{'、'.join(days)}")
        st['insomniaDays'] = days
        return today in days

    def _due_event(self, sc: Dict[str, Any], st: Dict[str, Any]):
        today = time.strftime('%Y-%m-%d')
        fired_map = st.setdefault('fired', {})
        for d in list(fired_map.keys()):
            if d != today:
                fired_map.pop(d, None)
        fired = set(fired_map.get(today) or [])
        now = time.localtime()
        now_min = now.tm_hour * 60 + now.tm_min
        jit_map = st.setdefault('jitter', {})

        def due(name: str, base: Any, jitter: int) -> bool:
            b = self._at_min(base)
            if b is None:
                return False
            k = f'{today}:{name}'
            j = jit_map.get(k)
            if j is None:
                j = random.randint(-abs(jitter), abs(jitter))
                jit_map[k] = j
            return now_min >= b + j

        def stale(name: str, base: Any, jitter: int, catch: int) -> bool:
            b = self._at_min(base)
            if b is None:
                return False
            j = jit_map.get(f'{today}:{name}') or 0
            return now_min > b + j + catch
        events = []
        w = sc.get('wake') or {}
        if w.get('enabled'):
            events.append(('起床', w.get('at') or '07:00', int(w.get('jitterMin') or 40), self._WAKE_PROMPT))
        for m in sc.get('meals') or []:
            if m.get('enabled'):
                nm = str(m.get('name') or '饭点')
                events.append((nm, m.get('at') or '12:00', int(m.get('jitterMin') or 60), self._MEAL_PROMPT.replace('{meal}', nm)))
        s = sc.get('sleep') or {}
        if s.get('enabled'):
            events.append(('睡觉', s.get('at') or '02:00', int(s.get('jitterMin') or 50), self._SLEEP_PROMPT))
        if self._life_cfg().get('enabled') and events:
            _lt = self._life_schedule_times()
            if _lt:
                _new = []
                for name, base, jitter, prompt in events:
                    t = _lt.get(name)
                    if t is not None:
                        base = '%02d:%02d' % (t // 60, t % 60)
                        jitter = 0
                        jit_map.pop(f'{today}:{name}', None)
                    _new.append((name, base, jitter, prompt))
                events = _new
        catch = int(sc.get('catchUpMinutes') or 60)
        for name, base, jitter, prompt in events:
            if name in fired:
                continue
            if not due(name, base, jitter):
                continue
            if stale(name, base, jitter, catch):
                log(f'  作息 [{name}] 已经过了 {catch} 分钟以上，跳过（不补发）')
                fired_map.setdefault(today, []).append(name)
                continue
            return (name, prompt)
        if '失眠' not in fired and self._insomnia_today(sc, st):
            ins = sc['insomnia']
            lo = self._at_min(ins.get('from') or '00:20')
            hi = self._at_min(ins.get('to') or '01:40')
            if lo is not None and hi is not None:
                k = f'{today}:失眠'
                j = jit_map.get(k)
                if j is None:
                    j = self._rand_min_in_window(lo, hi)
                    jit_map[k] = j
                if now_min >= j and now_min < 5 * 60 or (now_min >= 21 * 60 and j >= now_min):
                    if now_min >= j + catch and now_min < 12 * 60:
                        log('  作息 [失眠] 已经过了太久，跳过（不补发）')
                        fired_map.setdefault(today, []).append('失眠')
                    else:
                        return ('失眠', self._INSOMNIA_PROMPT)
        _manual = len(st.get('manualFired', {}).get(today) or [])
        _cap = int((sc.get('randomChats') or {}).get('maxPerDay') or 8)
        if _manual >= _cap:
            return None
        rc = sc.get('randomChats') or {}
        if rc.get('enabled'):
            slots = self._random_slots(sc, st, today, now_min, jit_map)
            for idx, t in enumerate(slots):
                name = f'闲聊{idx + 1}'
                if name in fired or now_min < t:
                    continue
                if now_min > t + catch:
                    fired_map.setdefault(today, []).append(name)
                    continue
                return (name, self._RANDOM_PROMPT)
        return None

    def _random_slots(self, sc: Dict[str, Any], st: Dict[str, Any], today: str, now_min: int, jit_map: Dict[str, Any]) -> List[int]:
        key = f'{today}:闲聊槽位'
        cached = jit_map.get(key)
        if isinstance(cached, list) and cached:
            return sorted((int(x) for x in cached))
        rc = sc.get('randomChats') or {}
        try:
            _day = self._life_load(today)
            _times = []
            for x in (_day or {}).get('chats') or []:
                t = self._at_min(x)
                if t is not None:
                    _times.append(t)
            if len(_times) >= 1:
                _want = max(1, int(rc.get('maxPerDay') or 8))
                _lo = self._at_min(rc.get('from') or '09:30') or 9 * 60 + 30
                _hi = self._at_min(rc.get('to') or '23:30') or 23 * 60 + 30
                while len(_times) < _want:
                    _times.append(random.randint(_lo, _hi))
                _times = sorted(set(_times))[:_want]
                jit_map[key] = _times
                log('  今天的闲聊时刻（按日程补足 %d 条）：%s' % (len(_times), '、'.join(('%02d:%02d' % (t // 60, t % 60) for t in _times))))
                return _times
        except Exception as e:
            log(f'  闲聊取日程失败: {e}')
        lo = self._at_min(rc.get('from') or '09:30') or 9 * 60 + 30
        hi = self._at_min(rc.get('to') or '23:30') or 23 * 60 + 30
        if hi <= lo:
            hi = lo + 60
        n = random.randint(int(rc.get('minPerDay') or 1), int(rc.get('maxPerDay') or 3))
        avoid = int(rc.get('avoidMinutes') or 45)
        blocked = []
        w = sc.get('wake') or {}
        if w.get('enabled'):
            blocked.append(self._at_min(w.get('at')) or 0)
        for m in sc.get('meals') or []:
            if m.get('enabled'):
                blocked.append(self._at_min(m.get('at')) or 0)
        s = sc.get('sleep') or {}
        if s.get('enabled'):
            blocked.append(self._at_min(s.get('at')) or 0)

        def ok(t: int) -> bool:
            for b in blocked:
                d = min(abs(t - b), 1440 - abs(t - b))
                if d < avoid:
                    return False
            return True
        slots: List[int] = []
        span = max(1, (hi - lo) // max(1, n))
        for seg in range(n):
            seg_lo = lo + seg * span
            seg_hi = lo + (seg + 1) * span if seg < n - 1 else hi
            for _ in range(40):
                t = random.randint(seg_lo, max(seg_lo, seg_hi))
                if not ok(t):
                    continue
                if any((abs(t - x) < 45 for x in slots)):
                    continue
                slots.append(t)
                break
        slots.sort()
        jit_map[key] = slots
        if slots:
            log(f"  今天的随机搭话时刻：{'、'.join(('%02d:%02d' % (t // 60, t % 60) for t in slots))}")
        return slots

    def _life_chat(self) -> str:
        return str((CFG.get('lifeplan') or {}).get('chat') or 'PRIVATE_CHAT')

    def _life_cfg(self) -> Dict[str, Any]:
        return CFG.get('lifeplan') or {}

    def _life_date(self, offset_days: int=0) -> str:
        return time.strftime('%Y-%m-%d', time.localtime(time.time() + 86400 * offset_days))

    def _life_load(self, date_str: str):
        try:
            import life_planner as LP
            return LP.load_day(date_str)
        except Exception as e:
            log(f'  生活计划加载失败: {e}')
            return None

    def _life_now(self, chat: str) -> str:
        try:
            import life_planner as LP
            day = self._life_load(self._life_date(0))
            if not day:
                return ''
            lt = time.localtime()
            seg = LP.current_segment(day, lt.tm_hour * 60 + lt.tm_min)
            if not seg:
                return ''
            act = str(seg.get('activity') or '').strip()
            place = str(seg.get('place') or '').strip()
            s = ('在' + place + '，' if place and place != '家（别墅）' else '') + act
            return s[:24]
        except Exception:
            return ''

    def _life_schedule_times(self) -> Dict[str, int]:
        try:
            import life_planner as LP
            day = self._life_load(self._life_date(0))
            if not day:
                return {}

            def tmin(s):
                h, m = str(s).split(':')
                return int(h) * 60 + int(m)
            out: Dict[str, int] = {}
            fallback_wake = None
            for s in day.get('timeline') or []:
                act = str(s.get('activity') or '')
                st = tmin(s.get('from') or '00:00')
                if '起床' in act and '起床' not in out:
                    out['起床'] = st
                if fallback_wake is None and '洗漱' in act and (5 * 60 <= st <= 12 * 60):
                    fallback_wake = st
                if ('早餐' in act or '早饭' in act) and '早饭' not in out:
                    out['早饭'] = st
                if ('午餐' in act or '午饭' in act) and '午饭' not in out:
                    out['午饭'] = st
                if ('晚餐' in act or '晚饭' in act) and '晚饭' not in out:
                    out['晚饭'] = st
                if ('入睡' in act or '熄灯' in act or '关灯' in act or ('上床' in act) or ('准备入睡' in act)) and st >= 20 * 60:
                    out['睡觉'] = st
            if '起床' not in out and fallback_wake is not None:
                out['起床'] = fallback_wake
            return out
        except Exception as e:
            log(f'  作息取日程失败: {e}')
            return {}

    def _body_state_now(self) -> str:
        try:
            day = self._life_load(self._life_date(0))
            if not day:
                return ''
            per = str(day.get('period') or '')
            if '排卵期' in per:
                return per + '：身体敏感、欲望偏高，容易想要，可能会主动撩他；但这只是倾向，不是必须'
            if '经期' in per:
                return per + '：小腹坠痛、腰酸、容易累，血量多的时候会烦躁；欲望低，更想被抱着、被照顾，不要主动发起性事；他主动时可以顺着，但身体不舒服要直接说，不要硬撑'
            if '经前' in per:
                return per + '：腹胀、腰酸、胸口胀、情绪容易低落或烦躁；欲望中低，想被抱、被哄；不要主动发起性事'
            if per:
                return per + '：身体放松、欲望一般'
            return ''
        except Exception:
            return ''

    def _life_prompt(self, chat: str) -> str:
        if chat != str((CFG.get('lifeplan') or {}).get('chat') or 'PRIVATE_CHAT'):
            return ''
        block = self._life_block(chat)
        now = self._life_now(chat)
        if not block and (not now) and (not self._state_load()):
            return ''
        parts = []
        if block:
            parts.append(block)
        if now:
            parts.append('【你现在正在】' + now)
        body = self._body_state_now()
        if body:
            parts.append('【身体状态】' + body)
        st_line = self._life_state_line()
        if st_line:
            parts.append(st_line)
        pr_line = self._promises_line(chat)
        if pr_line:
            parts.append(pr_line)
        parts.append('【输出规则】你是在发微信：只写真正会发送的正文，不写括号动作、旁白、分析或系统标签；换行表示新的聊天气泡。')
        if body:
            parts.append('【生理期规则】身体状态里写了经期/经前/排卵期，这就是你今天的真实状态：不要装作没事，也不要硬撑；聊到身体、出门、吃饭、睡觉时可以自然带一句不舒服（小腹酸、腰胀、犯困、想喝热的）；经期/经前不要主动发起性事，他要的时候可以顺着，但累了就直接说累。')
        parts.append('【角色设定】严格遵循用户在 personaFile 中配置的角色设定与说话方式；不要自行添加身份、经历或关系。')
        parts.append('【环境与身体】环境可以写，但别每句都带着：三四条里最多一条带一句背景，而且不超过半句。不要用环境句开头（比如「水凉了」「雾气重」「头发还滴着水」），先回应他的话；他问你在做什么时，再自然说一句就够了。身体不适只在他问起或确实影响行动时提。写成游记、每条都报天气和房间，就太假了。')
        parts.append('【话题推进】可以反复聊同一件事，但不要写得像他重复发了同一条；图片只代表当时那一下，不要反复拿它当话题，也不要每次都拿旧话题开场或收尾；他换了话题就跟着换。')
        parts.append('【使用方式】今天的事件只是背景：大部分时候正常聊天，只在话题相关、或他问你在做什么时，才自然地带一句此刻的事；不要每句都提日程，也不要把日程逐条念出来或解释这是日程。')
        parts.append('【边界】遵守用户配置的角色设定和当地法律；不生成违法、伤害或未经同意的内容。')
        return '\n\n'.join(parts)

    def _sys_life(self, chat: str) -> str:
        base = self.system_for(chat) + self.memory_block(chat)
        lp = self._life_prompt(chat)
        return base + ('\n\n---\n\n' + lp if lp else '')

    def _life_block(self, chat: str) -> str:
        try:
            day = self._life_load(self._life_date(0))
            if not day:
                return ''
            keep = ('出门', '回', '排练', '演出', '见', '买', '吃', '喝', '读', '写', '起床', '醒')
            lines = []
            for s in day.get('timeline') or []:
                act = str(s.get('activity') or '').strip()
                place = str(s.get('place') or '').strip()
                if place != '家（别墅）' or any((k in act for k in keep)):
                    t = '%s-%s' % (s.get('from'), s.get('to'))
                    lines.append('%s 在%s，%s' % (t, place, act) if place and place != '家（别墅）' else '%s %s' % (t, act))
            if len(lines) > 8:
                step = max(1, len(lines) // 8)
                lines = lines[::step][:8]
            return '【今天（%s）你的安排（只写你在哪、在做什么）】\n%s' % (day.get('date'), '\n'.join(lines))
        except Exception:
            return ''

    def _lifeplan_dir(self) -> str:
        fallback = os.path.join(os.environ.get('DATA_DIR') or os.path.join(_ROOT, 'data'), 'life-plan')
        try:
            import life_planner as LP
            return getattr(LP, 'DIR', fallback)
        except Exception:
            return fallback

    def _state_load(self) -> Dict[str, Any]:
        try:
            return json.load(open(os.path.join(self._lifeplan_dir(), 'state.json'), encoding='utf-8'))
        except Exception:
            return {}

    def _state_save(self, d: Dict[str, Any]) -> None:
        try:
            p = os.path.join(self._lifeplan_dir(), 'state.json')
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f'  状态保存失败: {e}')

    def _promises_load(self) -> List[Dict[str, Any]]:
        try:
            items = json.load(open(os.path.join(self._lifeplan_dir(), 'promises.json'), encoding='utf-8'))
            return items if isinstance(items, list) else []
        except Exception:
            return []

    def _promises_save(self, items: List[Dict[str, Any]]) -> None:
        try:
            p = os.path.join(self._lifeplan_dir(), 'promises.json')
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f'  承诺保存失败: {e}')

    def _life_state_line(self) -> str:
        st = self._state_load()
        if not st:
            return ''

        def lab(v: Any) -> str:
            return {1: '很低', 2: '偏低', 3: '一般', 4: '不错', 5: '很好'}.get(int(v), '一般')
        line = '【状态】心情 %s/5（%s）｜疲劳 %s/5｜嗓子 %s/5' % (st.get('mood', 3), lab(st.get('mood', 3)), st.get('fatigue', 2), st.get('voice', 3))
        w = st.get('weather') or ''
        if w:
            line += '｜天气：' + str(w)
        return line

    def _promises_line(self, chat: str='') -> str:
        try:
            org = getattr(self, 'organizer', None)
            if org is not None and chat:
                items = org.promises_for(chat, self._life_date(0)) or []
            else:
                items = [x for x in self._promises_load() if not x.get('legacy') and x.get('promise') and (x.get('chat') == chat) and (not x.get('due') or x.get('due') >= self._life_date(0))]
        except Exception:
            items = []
        items = [x for x in items if x.get('status') in ('open', 'overdue') and x.get('promise')]
        if not items:
            return ''
        lines = []
        for i, x in enumerate(items[-3:]):
            due = x.get('due') or ''
            tag = '（已过期）' if x.get('status') == 'overdue' else '（%s 到期）' % due if due else ''
            lines.append('%d. %s%s' % (i + 1, x.get('promise', ''), tag))
        return '【你答应过他】\n' + '\n'.join(lines)

    def _lifeplan_extra(self) -> str:
        st = self._state_load()
        parts = []
        if st:
            parts.append('昨天结束时的状态：疲劳 %s/5、嗓子 %s/5、心情 %s/5。' % (st.get('fatigue', 3), st.get('voice', 3), st.get('mood', 3)))
        ps = [x for x in self._promises_load() if x.get('status') == 'open' and x.get('promise') and (not x.get('legacy')) and (x.get('chat') == self._life_chat()) and (not x.get('due') or x.get('due') >= self._life_date(0))]
        if ps:
            parts.append('明天需要兑现的承诺：' + '；'.join((x.get('promise', '') for x in ps[-3:])))
        return '\n'.join(parts)

    def _lifeplan_apply_state(self) -> None:
        try:
            day = self._life_load(self._life_date(0))
            if not day:
                return
            so = day.get('state_out') or {}
            st = self._state_load() or {}
            st.update({'date': day.get('date'), 'fatigue': so.get('fatigue', st.get('fatigue', 2)), 'voice': so.get('voice', st.get('voice', 3)), 'mood': so.get('mood', st.get('mood', 3)), 'weather': (day.get('weather') or {}).get('kind', '')})
            self._state_save(st)
            today = self._life_date(0)
            items = self._promises_load()
            changed = False
            for x in items:
                if x.get('legacy'):
                    continue
                if x.get('status') == 'open' and x.get('due') and (x['due'] < today):
                    x['status'] = 'overdue'
                    changed = True
            if changed:
                self._promises_save(items)
        except Exception as e:
            log(f'  状态应用失败: {e}')

    def _note_promises(self, chat: str, reply: str, sender_id: str='', person_name: str='') -> None:
        if not reply or not self._life_cfg().get('promises', True):
            return
        kw = ('明天', '下次', '以后', '等我', '回头', '晚点', '下周', '改天', '一定', '答应', '周末')
        if not any((k in reply for k in kw)):
            return
        try:
            hist = self.ctx.get(chat)[-6:]
            mine = ''
            for m in reversed(hist):
                if m.get('role') == 'user':
                    mine = m.get('content', '')
                    break
            today = date.today()
            sysm = '你是承诺抽取器。读她刚说的话，判断有没有对他做出具体承诺。今天是 ' + today.isoformat() + '。有则只输出 JSON {"promise":"...","due":"YYYY-MM-DD"}；due 只能基于今天的真实日期，不要臆造过去年份；没有明确日期就留空。没有承诺则输出 {}。不要解释。'
            out = self.llm.chat(sysm, [], '他刚说的：' + mine + '\n她回的：' + reply, purpose='promise', chat=chat)
            data = json.loads((out or '').strip())
            pr = str(data.get('promise') or '').strip()
            if pr:
                due_raw = str(data.get('due') or '').strip()
                due = ''
                if len(due_raw) == 10:
                    try:
                        date.fromisoformat(due_raw)
                        due = due_raw
                    except Exception:
                        due = ''
                promise_id = ''
                try:
                    org = getattr(self, 'organizer', None)
                    if org is not None:
                        with org.exclusive():
                            status, payload = org.register_promise(chat, pr, due=due, made=today.isoformat(), person_id=str(sender_id or ''), person_name=str(person_name or ''))
                            if status != 200:
                                log(f"    承诺写入被拒绝：{payload.get('message') or status}")
                                return
                            matches = [x for x in payload.get('promises') or [] if x.get('promise') == pr and x.get('chat') == chat and (x.get('due') == due)]
                            if matches:
                                promise_id = str(matches[-1].get('id') or '')
                            if due and due >= today.isoformat():
                                with self._rem_lock:
                                    rems = self._rem_load()
                                    self._backup_reminders()
                                    rems.append({'chat': chat, 'due': due + 'T08:30', 'intent': pr, 'status': 'open', 'promiseId': promise_id, 'created': time.strftime('%Y-%m-%dT%H:%M')})
                                    self._rem_save(rems)
                                log(f'    承诺已登记提醒：{due} — {pr}')
                    else:
                        import uuid
                        promise_id = uuid.uuid4().hex
                        items = self._promises_load()
                        items.append({'id': promise_id, 'promise': pr, 'due': due, 'status': 'overdue' if due and due < today.isoformat() else 'open', 'made': today.isoformat(), 'chat': chat, 'personId': str(sender_id or ''), 'personName': str(person_name or ''), 'legacy': False})
                        self._promises_save(items)
                        if due and due >= today.isoformat():
                            with self._rem_lock:
                                rems = self._rem_load()
                                self._backup_reminders()
                                rems.append({'chat': chat, 'due': due + 'T08:30', 'intent': pr, 'status': 'open', 'promiseId': promise_id, 'created': time.strftime('%Y-%m-%dT%H:%M')})
                                self._rem_save(rems)
                except Exception as e:
                    log(f'  承诺登记失败: {e}')
                    return
                log(f'    记下一个承诺：{pr}')
        except Exception as e:
            log(f'  承诺抽取失败: {e}')

    def _habit(self, reply: str) -> str:
        try:
            if not reply or len(reply) < 6:
                return reply
            r = random.random()
            if r < 0.06:
                return reply.rstrip('。')
            if r < 0.08:
                return reply.rstrip('。') + '……'
            return reply
        except Exception:
            return reply

    def _life_text_chat(self, messages, purpose='life_day', max_tokens=12000, timeout=240, return_meta=False):
        system = ''
        user = ''
        history = []
        if messages:
            if str(messages[0].get('role')) == 'system':
                system = str(messages[0].get('content') or '')
                rest = messages[1:]
            else:
                rest = messages
            if rest:
                user = str(rest[-1].get('content') or '')
                history = list(rest[:-1])
        text = self.llm.chat(system, history, user, purpose=purpose, think='low', max_tokens=max_tokens)
        return {'content': str(text or ''), 'thinking': '', 'usage': {}}

    def _life_generation(self):
        if getattr(self, '_lifegen', None) is None:
            import life_generation as LG
            import life_planner as LP
            LP.set_text_generator(self._life_text_chat)
            self._lifegen = LG.LifeGenerationManager(today_provider=lambda: self._life_date(0), load_day=LP.load_day, generate_day=lambda date, extra: LP.generate_day(date, extra=extra), day_path=lambda date: os.path.join(LP.DAYS, date + '.json'), extra_provider=self._lifeplan_extra, apply_state=self._lifeplan_apply_state, backup_dir=os.path.join(LP.DIR, 'days_backup'), log=log)
        return self._lifegen

    def _life_generation_public(self):
        try:
            return self._life_generation().public(self._life_date(0))
        except Exception:
            return {'state': 'idle', 'jobId': None, 'date': self._life_date(0), 'source': None, 'startedAt': None, 'endedAt': None, 'message': '暂无生成任务', 'reasonCode': None, 'retryAfterSeconds': 0}

    def _life_regenerate(self, date=None):
        try:
            guard = None
            if getattr(self, 'planner', None) is not None:
                guard = lambda: not self.planner.has_locks(self._life_date(0))
            return self._life_generation().request_manual(date, guard=guard)
        except Exception:
            return (500, {'ok': False, 'error': 'generation_unavailable', 'message': '日程生成服务暂不可用', 'generation': self._life_generation_public()})

    def _companion_error(self, code='unavailable', message='陪伴功能暂不可用'):
        return {'ok': False, 'error': code, 'message': message}

    def _companion_diagnostics(self, query):
        self._companion_init()
        diag = self._diag_store()
        if diag is None:
            return self._companion_error()
        try:
            limit = int((query or {}).get('limit') or 50)
        except Exception:
            limit = 50
        return diag.list(limit=limit, chat=(query or {}).get('chat') or None)

    def _companion_organizer_get(self, chat=''):
        self._companion_init()
        if not self.organizer:
            return self._companion_error()
        try:
            return self.organizer.get(chat or '')
        except Exception:
            return self._companion_error('organizer_error', '记忆整理暂不可用')

    def _companion_organizer_post(self, body):
        self._companion_init()
        if not self.organizer or not isinstance(body, dict):
            return (400, self._companion_error('invalid_input', '请求结构无效'))
        try:
            import companion_organizer as CO
            action = body.get('action')
            chat = body.get('chat') or ''
            revision = body.get('revision')
            fields = {k: v for k, v in body.items() if k not in ('action', 'chat', 'revision')}
            return self.organizer.post(action, chat, revision, **fields)
        except Exception as exc:
            try:
                import companion_organizer as CO
                if isinstance(exc, CO.OrganizerConflict):
                    return (409, exc.payload())
            except Exception:
                pass
            return (409, self._companion_error('conflict', '数据已被修改，请刷新后重试'))

    def _companion_planner_get(self):
        self._companion_init()
        if not self.planner:
            return self._companion_error()
        try:
            return self.planner.get()
        except Exception:
            return self._companion_error('planner_error', '日程规划暂不可用')

    def _companion_planner_post(self, body):
        self._companion_init()
        if not self.planner or not isinstance(body, dict):
            return (400, self._companion_error('invalid_input', '请求结构无效'))
        try:
            return self.planner.post(body)
        except Exception:
            return (500, self._companion_error('planner_error', '日程规划暂不可用'))

    def _companion_models(self):
        self._companion_init()
        if not self.companion_insights:
            return self._companion_error()
        try:
            return self.companion_insights.get()
        except Exception:
            return self._companion_error('metrics_error', '模型统计暂不可用')

    def _companion_furina(self):
        self._companion_init()
        if not self.furina:
            return self._companion_error()
        try:
            return self.furina.get()
        except Exception:
            return self._companion_error('furina_error', '芙芙数据暂不可用')

    def _lifeplan_ensure(self) -> None:
        if not self._life_cfg().get('enabled'):
            return
        today = self._life_date(0)
        tomorrow = self._life_date(1)
        for date in (today, tomorrow):
            result = self._lifeplan_generate_automatic(date, only_if_missing=True)
            generation = result.get('generation') or {}
            if result.get('reason') in ('disabled', 'cooldown') or generation.get('state') in ('failed', 'cooldown'):
                log(f'  生活计划：{date} 尚未生成成功')
                return
        self._lifeplan_apply_state()
        if self._life_load(today) and self._life_load(tomorrow):
            log(f'  生活计划：就绪（今天 {today} / 明天 {tomorrow}）')

    def _lifeplan_generate_automatic(self, date, only_if_missing=True):
        mgr = self._life_generation()
        while self._life_cfg().get('enabled'):
            result = mgr.ensure_automatic(date, only_if_missing=only_if_missing)
            if result.get('reason') != 'busy':
                return result
            time.sleep(1)
        return {'started': False, 'reason': 'disabled'}

    def _companion_chats(self):
        out = []
        try:
            for name, conf in self.chat_by_name.items():
                out.append({'name': name, 'kind': 'group' if str((conf or {}).get('kind') or '') == 'group' else 'private' if str((conf or {}).get('kind') or '') == 'private' else 'group' if str((conf or {}).get('mode') or '') == 'wake' else 'private'})
        except Exception:
            pass
        return out

    def _diag_store(self):
        if self.diagnostics is False:
            return None
        if self.diagnostics is None:
            try:
                import companion_diagnostics as CD
                data_dir = _companion_dir()
                self.diagnostics = CD.DiagnosticsStore(os.path.join(data_dir, 'diagnostics.json'), chats=self._companion_chats)
            except Exception as e:
                self.diagnostics = False
                log(f'  诊断存储初始化失败: {e.__class__.__name__}')
        return self.diagnostics if self.diagnostics is not False else None

    def _diag_start(self, chat, sender, preview, status='received', reason_code='', reason=''):
        try:
            diag = self._diag_store()
            return diag.start(chat, sender, preview, status, reason_code, reason) if diag is not None else None
        except Exception:
            return None

    def _diag_update(self, diag_id, **kwargs):
        try:
            diag = self._diag_store()
            if diag is not None and diag_id:
                diag.update(diag_id, **kwargs)
        except Exception:
            pass

    def _diag_step(self, diag_id, stage, status, reason_code='', message=''):
        try:
            diag = self._diag_store()
            if diag is not None and diag_id:
                diag.step(diag_id, stage, status, reason_code, message)
        except Exception:
            pass

    def _diag_trace(self, diag_id, trace_id):
        try:
            diag = self._diag_store()
            if diag is not None and diag_id and trace_id:
                diag.trace(diag_id, trace_id)
        except Exception:
            pass

    def _diag_merge(self, ids, **kwargs):
        try:
            diag = self._diag_store()
            if diag is not None and ids:
                return diag.merge(ids, **kwargs)
        except Exception:
            pass
        return None

    def _diag_send(self, diag_id, ok, how=''):
        try:
            diag = self._diag_store()
            if diag is not None and diag_id:
                diag.send(diag_id, ok, how)
        except Exception:
            pass

    def _companion_init(self):
        if self._companion_ready:
            return
        self._companion_ready = True
        try:
            self._diag_store()
        except Exception:
            pass
        try:
            import companion_insights as CI
            import life_planner as LP
            traces = dashboard_runtime.RUNTIME.traces
            self.companion_insights = CI.ModelsService(traces, lambda: CFG)
            life_chat = self._life_chat()
            self.furina = CI.FurinaService(traces, lambda: CFG, life_chat, LP.load_day, lambda: self._life_date(0), self._state_load, None, self._life_generation_public, lambda: [p for p in self._promises_load() if not p.get('legacy') and p.get('chat') == life_chat and (not p.get('due') or p.get('due') >= self._life_date(0))], lambda: len(self.ctx.get(life_chat)), lambda: len(self.memory.get(life_chat)))
        except Exception as e:
            log(f'  统计模块初始化失败: {e.__class__.__name__}')
        try:
            import life_planner as LP
            import companion_planner as CP
            manager = self._life_generation()
            data_dir = os.path.join(_companion_dir(), 'planner')
            self.planner = CP.CompanionPlanner(manager, LP.load_day, LP.save_day, LP.generate_day, lambda date: os.path.join(LP.DAYS, date + '.json'), data_dir, lambda: self._life_date(0), os.path.join(LP.DIR, 'days_backup'), extra_provider=self._lifeplan_extra, apply_state=self._lifeplan_apply_state, log=log)
        except Exception as e:
            self.planner = None
            log(f'  日程规划版本模块初始化失败: {e.__class__.__name__}')
        try:
            import companion_organizer as CO
            data_dir = _companion_dir()
            self.promises_path = os.path.join(self._lifeplan_dir(), 'promises.json')
            self.organizer = CO.OrganizerStore(os.path.join(data_dir, 'organizer.json'), memory_get=self._memory_get, memory_set=self._memory_set, promises_load=self._promises_load, promises_save=self._promises_save, backup_root=os.path.join(data_dir, 'legacy-backups'), cancel_reminders=self._cancel_promise_reminders, today_provider=lambda: self._life_date(0), chats_provider=self._companion_chats)
            self.memory.mutation_guard = self.organizer.exclusive
        except Exception as e:
            self.organizer = None
            log(f'  记忆组织初始化失败: {e.__class__.__name__}')

    def _memory_get(self, chat):
        return self.memory.get(chat)

    def _memory_set(self, chat, text):
        from contextlib import nullcontext
        guard = getattr(self.memory, 'mutation_guard', None)
        with guard() if callable(guard) else nullcontext():
            with self.memory.lock:
                existed = chat in self.memory.data
                old = self.memory.data.get(chat)
                if text:
                    self.memory.data[chat] = str(text)
                else:
                    self.memory.data.pop(chat, None)
                if self.memory._save() is False:
                    if existed:
                        self.memory.data[chat] = old
                    else:
                        self.memory.data.pop(chat, None)
                    raise RuntimeError('memory_write_failed')
        self._sys_cache.pop(chat, None)

    def _cancel_promise_reminders(self, promise):
        try:
            pid = str(promise.get('id') or '')
            if not pid:
                return
            with self._rem_lock:
                rems = self._rem_load()
                changed = False
                for rem in rems:
                    if rem.get('status') == 'sent':
                        continue
                    if str(rem.get('promiseId') or '') == pid:
                        rem['status'] = 'cancelled'
                        changed = True
                if changed:
                    self._backup_reminders()
                    self._rem_save(rems)
        except Exception:
            pass

    def _lifeplan_tick(self) -> None:
        if not self._life_cfg().get('enabled'):
            return
        try:
            tomorrow = self._life_date(1)
            log(f'  生活计划：0 点生成 {tomorrow} 的日程…')
            result = self._lifeplan_generate_automatic(tomorrow, only_if_missing=False)
            if (result.get('generation') or {}).get('state') == 'succeeded':
                log(f'  生活计划：{tomorrow} 的安排已载入她的上下文（不回复）')
            else:
                log(f'  生活计划：{tomorrow} 尚未生成成功')
        except Exception as e:
            log(f'  生活计划生成失败: {e}')

    def _lifeplan_loop(self) -> None:
        try:
            self._lifeplan_ensure()
            _t = self._life_schedule_times()
            if _t:
                log('  作息按日程：' + '、'.join(('%s %02d:%02d' % (k, v // 60, v % 60) for k, v in _t.items())))
        except Exception as e:
            log(f'  生活计划启动失败: {e}')
        while True:
            try:
                lt = time.localtime()
                now = lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec
                time.sleep(86400 - now + 5)
                self._lifeplan_tick()
            except Exception as e:
                log(f'  生活计划循环出错: {e}')
                time.sleep(60)

    def _rem_path(self) -> str:
        return os.path.join(os.path.dirname(CFG.get('contextFile') or HERE), 'reminders.json')

    def _backup_reminders(self):
        try:
            src = self._rem_path()
            if not os.path.isfile(src):
                return
            dest_dir = os.path.join(_companion_dir(), 'reminder-backups')
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, 'reminders-%s.json' % int(time.time() * 1000))
            import shutil as _shutil
            _shutil.copy2(src, dest)
        except Exception:
            raise

    def _rem_load(self) -> List[Dict[str, Any]]:
        with self._rem_lock:
            try:
                items = json.load(open(self._rem_path(), encoding='utf-8'))
                return items if isinstance(items, list) else []
            except Exception:
                return []

    def _rem_save(self, items: List[Dict[str, Any]]) -> None:
        with self._rem_lock:
            try:
                with open(self._rem_path(), 'w', encoding='utf-8') as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
            except Exception as e:
                log(f'  提醒保存失败: {e}')

    def _dynamic_used(self) -> int:
        st = self._sched_load() or {}
        return len((st.get('dynamicFired', {}) or {}).get(time.strftime('%Y-%m-%d'), []))

    def _dynamic_mark(self) -> None:
        try:
            st = self._sched_load() or {}
            st.setdefault('dynamicFired', {}).setdefault(time.strftime('%Y-%m-%d'), []).append(time.strftime('%H:%M'))
            self._sched_save(st)
        except Exception:
            pass

    def _plan_event_time(self, event: str):
        try:
            day = self._life_load(self._life_date(0))
            if not day:
                return None
            keys = []
            if '家' in event:
                keys = ('回家', '到家', '进门', '回到别墅', '回别墅')
            elif '公司' in event or '剧院' in event:
                keys = ('到达', '抵达', '到欧庇克莱', '进后台')
            for seg in day.get('timeline') or []:
                act = str(seg.get('activity') or '')
                if any((k in act for k in keys)):
                    h, m = str(seg.get('from')).split(':')
                    return int(h) * 60 + int(m)
            prev_place = ''
            for seg in day.get('timeline') or []:
                place = str(seg.get('place') or '')
                h, m = str(seg.get('from')).split(':')
                tmin = int(h) * 60 + int(m)
                if '家' in event and '家' in place and ('家' not in prev_place) and (tmin >= 12 * 60):
                    return tmin
                if ('公司' in event or '剧院' in event) and '欧庇克莱' in place and ('欧庇克莱' not in prev_place):
                    return tmin
                prev_place = place
            return None
        except Exception:
            return None

    def _note_reminders(self, chat: str, text: str) -> None:
        if not text:
            return
        kw = ('提醒', '跟我说', '告诉我', '到时候', '分钟后', '小时后', '到家', '到公司', '到了', '回我', '叫我')
        if not any((k in text for k in kw)):
            return
        if any((k in text for k in ('别提醒', '取消提醒', '不用提醒'))):
            items = self._rem_load()
            for x in items:
                if x.get('chat') == chat and x.get('status') == 'open':
                    x['status'] = 'cancelled'
            self._rem_save(items)
            log('  提醒：已取消')
            return
        try:
            now = time.strftime('%Y-%m-%d %H:%M')
            sysm = '你是提醒抽取器。判断他是不是要你在某个时间主动跟他说一声/提醒他。输出 JSON：{"due":"YYYY-MM-DDTHH:MM","intent":"你要说的话的要点","event":"到家/到公司/其他"}；没有则输出 {}。due 必须是绝对时间。现在是 ' + now + '。'
            out = self.llm.chat(sysm, [], text, purpose='reminder')
            data = json.loads((out or '').strip())
            event = str(data.get('event') or '')
            due = str(data.get('due') or '').strip()
            if event:
                tmin = self._plan_event_time(event)
                if tmin is not None:
                    lt = time.localtime()
                    due = '%s %02d:%02d' % (time.strftime('%Y-%m-%d'), tmin // 60, tmin % 60)
            if not due:
                if event:
                    due = time.strftime('%Y-%m-%dT%H:%M', time.localtime(time.time() + 40 * 60))
                else:
                    return
            if len(due) == 16 and 'T' not in due:
                due = due.replace(' ', 'T')
            ts = time.mktime(time.strptime(due.replace('T', ' '), '%Y-%m-%d %H:%M'))
            if ts <= time.time():
                ts = time.time() + 120
            items = self._rem_load()
            items.append({'chat': chat, 'due': time.strftime('%Y-%m-%dT%H:%M', time.localtime(ts)), 'intent': str(data.get('intent') or text), 'status': 'open', 'created': time.strftime('%Y-%m-%dT%H:%M')})
            self._rem_save(items)
            log(f"  提醒：已登记 {items[-1]['due']} — {items[-1]['intent']}")
        except Exception as e:
            log(f'  提醒抽取失败: {e}')

    def _reminder_trigger(self, rem: Dict[str, Any]) -> None:
        chat = rem.get('chat') or 'PRIVATE_CHAT'
        lt = time.localtime()
        quiet = 1 <= lt.tm_hour < 7
        pc_ = self._proactive_cfg() if hasattr(self, '_proactive_cfg') else {}
        lim = int(pc_.get('dailyLimit') or 0)
        if lim > 0 and self._dynamic_used() >= lim or quiet:
            rem['defer'] = int(rem.get('defer') or 0) + 1
            if rem['defer'] > 3:
                rem['status'] = 'skipped'
                log('  提醒：多次顺延仍无法发送，放弃')
                return
            delay = 8 * 3600 - (lt.tm_hour * 3600 + lt.tm_min * 60) if quiet else 3600
            try:
                ts = time.mktime(time.strptime(str(rem.get('due')).replace('T', ' '), '%Y-%m-%d %H:%M')) + max(600, delay)
            except Exception:
                ts = time.time() + 3600
            rem['due'] = time.strftime('%Y-%m-%dT%H:%M', time.localtime(ts))
            log(f"  提醒：顺延到 {rem['due']}（配额满/静默）")
            return
        try:
            sysm = self._sys_life(chat)
            trigger = '你之前答应他，到点跟他说一声：' + str(rem.get('intent') or '') + '。现在时间到了，主动发一条微信消息，短，2~4 行，别解释这是提醒。'
            reply = ''
            for _ in (1, 2):
                reply = self.llm.chat(sysm, self.ctx.get(chat), trigger, purpose='reminder_say', chat=chat)
                reply = self._clean(reply, chat) if reply else ''
                if reply:
                    break
            if not reply:
                rem['status'] = 'failed'
                return
            self.ctx.add(chat, 'assistant', f'{_hhmm()} {reply}')
            self.last_bot_at[chat] = time.time()
            self._deliver(chat, reply)
            self._dynamic_mark()
            try:
                st['lastDynamicText'] = txt
                self._sched_save(st)
            except Exception:
                pass
            rem['status'] = 'done'
            log(f'  提醒：已触发 -> {chat}')
        except Exception as e:
            rem['status'] = 'error'
            log(f'  提醒触发失败: {e}')

    def _reminder_loop(self) -> None:
        while True:
            try:
                items = self._rem_load()
                now = time.time()
                changed = False
                for rem in items:
                    if rem.get('status') != 'open':
                        continue
                    try:
                        ts = time.mktime(time.strptime(str(rem.get('due')).replace('T', ' '), '%Y-%m-%d %H:%M'))
                    except Exception:
                        rem['status'] = 'bad'
                        changed = True
                        continue
                    if ts <= now:
                        self._reminder_trigger(rem)
                        changed = True
                if changed:
                    self._rem_save(items)
            except Exception as e:
                log(f'  提醒循环出错: {e}')
            time.sleep(30)

    def _intents_path(self) -> str:
        return os.path.join(os.path.dirname(CFG.get('contextFile') or HERE), 'intents.json')

    def _intents_load(self) -> List[Dict[str, Any]]:
        try:
            items = json.load(open(self._intents_path(), encoding='utf-8'))
            return items if isinstance(items, list) else []
        except Exception:
            return []

    def _intents_save(self, items: List[Dict[str, Any]]) -> None:
        try:
            with open(self._intents_path(), 'w', encoding='utf-8') as f:
                json.dump(items[-40:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f'  意图保存失败: {e}')

    def _intents_active(self) -> List[Dict[str, Any]]:
        now = time.time()
        out = []
        for x in self._intents_load():
            if x.get('status') != 'active':
                continue
            try:
                if x.get('decay_until') and time.mktime(time.strptime(x['decay_until'], '%Y-%m-%dT%H:%M')) < now:
                    continue
            except Exception:
                pass
            out.append(x)
        return out

    def _classify_intent(self, chat: str, text: str) -> None:
        if chat != self._life_chat():
            return
        if not text or len(text.strip()) < 2:
            return
        if any((k in text for k in ('别提醒', '取消提醒', '不用提醒', '别主动', '不用主动'))):
            items = self._rem_load()
            for x in items:
                if x.get('status') == 'open':
                    x['status'] = 'cancelled'
            self._rem_save(items)
            its = self._intents_load()
            for x in its:
                if x.get('status') == 'active':
                    x['status'] = 'cancelled'
            self._intents_save(its)
            log('  意图：已取消待触发的提醒/授权')
            return
        try:
            now = time.strftime('%Y-%m-%d %H:%M')
            hist = self.ctx.get(chat)[-6:]
            recent = '\n'.join(('%s：%s' % ('他' if m.get('role') == 'user' else '她', (m.get('content') or '')[:80]) for m in hist))
            sysm = '你是意图分类器。判断他这句话是不是给当前配置角色的指令：定时提醒、事件提醒、或长期授权（比如「想我了就来找我」）。输出严格 JSON（只能一个）：{"type":"reminder","due":"YYYY-MM-DDTHH:MM","intent":"..."} / {"type":"event","event":"到家/到公司/到剧院","intent":"..."} / {"type":"standing","intent":"...","condition":"..."} / {"type":"none"}。现在：' + now + '。如果他说了具体时间（半小时后/20分钟后/今晚八点/明天早上），优先 type=reminder 并算出 due；只有没给时间、只说「到家/到公司」时才用 event。不确定就输出 none。'
            out = self.llm.chat(sysm, [], '最近对话：\n' + recent + '\n他刚说：' + text, purpose='classify', chat=chat)
            data = json.loads((out or '').strip())
            typ = str(data.get('type') or 'none')
            if typ == 'none':
                return
            if typ in ('reminder', 'event'):
                due = str(data.get('due') or '').strip()
                event = str(data.get('event') or '')
                if typ == 'event' or not due:
                    tmin = self._plan_event_time(event) if event else None
                    if tmin is not None:
                        due = time.strftime('%Y-%m-%dT%H:%M', time.localtime(time.mktime(time.strptime(time.strftime('%Y-%m-%d'), '%Y-%m-%d')) + tmin * 60))
                    elif event and typ == 'event':
                        due = time.strftime('%Y-%m-%dT%H:%M', time.localtime(time.time() + 40 * 60))
                if not due:
                    return
                try:
                    ts = time.mktime(time.strptime(due.replace('T', ' '), '%Y-%m-%d %H:%M'))
                except Exception:
                    return
                if ts <= time.time():
                    ts = time.time() + 120
                items = self._rem_load()
                items.append({'chat': chat, 'due': time.strftime('%Y-%m-%dT%H:%M', time.localtime(ts)), 'intent': str(data.get('intent') or text), 'status': 'open', 'created': time.strftime('%Y-%m-%dT%H:%M')})
                self._rem_save(items)
                log(f"  意图：登记提醒 {items[-1]['due']} — {items[-1]['intent']}")
            elif typ == 'standing':
                its = self._intents_load()
                its.append({'intent': str(data.get('intent') or text), 'condition': str(data.get('condition') or ''), 'status': 'active', 'created': time.strftime('%Y-%m-%dT%H:%M'), 'decay_until': time.strftime('%Y-%m-%dT%H:%M', time.localtime(time.time() + 14 * 86400))})
                self._intents_save(its)
                log(f"  意图：登记长期授权 — {its[-1]['intent']}")
        except Exception as e:
            log(f'  意图分类失败: {e}')

    def _decision_points(self) -> List[str]:
        try:
            t = self._life_schedule_times() or {}
            wake = int(t.get('起床') or 7 * 60)
            sleep = int(t.get('睡觉') or 23 * 60)
        except Exception:
            wake, sleep = (7 * 60, 23 * 60)
        start = (wake + 59) // 60
        end = sleep // 60
        if end <= start:
            start, end = (7, 23)
        pts = []
        h = start
        while h < end:
            pts.append('%02d:00' % h)
            h += 2
        return pts

    def _decision_point(self) -> bool:
        lt = time.localtime()
        return time.strftime('%H:00', lt) in self._decision_points() and lt.tm_min < 3

    def _dynamic_used(self) -> int:
        st = self._sched_load() or {}
        return len((st.get('dynamicFired', {}) or {}).get(time.strftime('%Y-%m-%d'), []))

    def _dynamic_mark(self) -> None:
        try:
            st = self._sched_load() or {}
            st.setdefault('dynamicFired', {}).setdefault(time.strftime('%Y-%m-%d'), []).append(time.strftime('%H:%M'))
            st['lastDynamicAt'] = time.time()
            self._sched_save(st)
        except Exception:
            pass

    def _maybe_dynamic(self) -> None:
        chat = self._life_chat()
        st = self._sched_load() or {}
        today = time.strftime('%Y-%m-%d')
        _pts = self._decision_points()
        if len((st.get('decisionFired', {}) or {}).get(today, [])) >= len(_pts):
            return
        lt = time.localtime()
        if time.strftime('%H:00', lt) not in _pts:
            return
        _fired = (st.get('decisionFired', {}) or {}).get(today, [])
        if any((str(x).startswith('%02d:' % lt.tm_hour) for x in _fired)):
            return
        last = float(st.get('lastDynamicAt') or 0)
        if last and time.time() - last < 5 * 60:
            return
        if any(self.pending.values()):
            return
        intents = self._intents_active()
        st.setdefault('decisionFired', {}).setdefault(today, []).append(time.strftime('%H:%M'))
        self._sched_save(st)
        if not intents:
            return
        _pc = self._life_cfg().get('proactive') or {}
        _lim = int(_pc.get('dailyLimit') or 0)
        if _lim > 0 and self._dynamic_used() >= _lim:
            return
        try:
            sysm = self._sys_life(chat)
            hist = self.ctx.get(chat)
            _last_txt = str(st.get('lastDynamicText') or '')
            decide_trigger = '你手里有这些他留下的授权：\n' + '\n'.join(('- ' + x.get('intent', '') for x in intents)) + ('\n你上一次主动说的是：「' + _last_txt + '」，这次别重复类似内容或句式。' if _last_txt else '') + '\n现在：' + time.strftime('%H:%M') + '。结合你正在做的事，判断你现在想不想主动给他发消息。只输出 JSON：想发 {"send":true,"why":"20字内"}；不想发 {"send":false,"why":"20字内"}。不要写你要发的内容，正文由主模型另写。'
            out = self.llm.chat(sysm, hist, decide_trigger, purpose='decide')
            data = json.loads((out or '').strip())
            if not data.get('send'):
                log('  动态决策：不发（%s）' % str(data.get('why') or '')[:40])
                return
            say_trigger = '现在：' + time.strftime('%H:%M') + '。你决定现在主动给他发一条微信消息。结合你正在做的事和你俩刚聊的内容，直接写出你要发的那条消息：1~2 句，短，像你平时说话。只输出消息正文，不要解释，不要 JSON。'
            txt = self._clean(str(self.llm.chat(sysm, hist, say_trigger, purpose='decide_say', chat=chat) or ''), chat)
            if not txt:
                return
            self.ctx.add(chat, 'assistant', f'{_hhmm()} {txt}')
            self.last_bot_at[chat] = time.time()
            self._deliver(chat, txt)
            try:
                st['lastDynamicText'] = txt
                self._sched_save(st)
            except Exception:
                pass
            self._dynamic_mark()
            log(f'  动态决策：发了 -> {chat}')
        except Exception as e:
            log(f'  动态决策失败: {e}')

    def _dynamic_loop(self) -> None:
        while True:
            try:
                if self._decision_point():
                    self._maybe_dynamic()
            except Exception as e:
                log(f'  动态循环出错: {e}')
            time.sleep(45)

    def _nudge_judge(self, chat: str) -> Dict[str, Any]:
        try:
            hist = self.ctx.get(chat)[-10:]
            recent = '\n'.join((('他：' if m.get('role') == 'user' else '她：') + str(m.get('content') or '')[:120] for m in hist))
            sysm = '你是关系分析师。下面是一段微信聊天：她说完了最后一句，他一直没回。\n她是谁：当前配置角色，骄傲、嘴硬、别扭、会害羞、藏不住喜欢。\n她的「不生气」经常是嘴硬：说「随便你」「我又没等你」「顺口问问，不是盯着你」「你忙你的」「算了」，其实是在意、在等回复——不要把傲娇判成无所谓。\n判断她该不该因为没回而有点生气/委屈，过一会儿主动再发一条。如果话题真的自然结束（她说了晚安、事情说完了、他不需要回），才判 no。只输出 JSON：{"should":"yes|no","emotion":"生气/委屈/担心/正常","reason":"20字内"}'
            out = self.llm.chat(sysm, [], '最近对话：\n' + recent, purpose='nudge')
            import json as _json
            v = _json.loads((out or '').strip())
            if str(v.get('should', '')).lower() not in ('yes', 'no'):
                v['should'] = 'no'
            return v
        except Exception as e:
            log(f'  空窗判定失败（按不生气处理）：{e}')
            return {'should': 'no'}

    def _pro_active(self) -> bool:
        pc = CFG.get('proactive') or {}
        if not pc.get('enabled'):
            return False
        q = pc.get('quietHours') or []
        if len(q) == 2:
            h = time.localtime().tm_hour
            a, b = (int(q[0]), int(q[1]))
            if a <= h < b if a <= b else h >= a or h < b:
                return False
        day = time.strftime('%Y-%m-%d')
        n = sum((1 for t in self.pro_times if time.strftime('%Y-%m-%d', time.localtime(t)) == day))
        if n >= int(pc.get('dailyLimit') or 5):
            return False
        if self.pro_times:
            gap = (time.time() - max(self.pro_times)) / 60.0
            if gap < float(pc.get('minGapMin') or 40):
                return False
        return True

    def _proactive_loop(self) -> None:
        while True:
            time.sleep(float((CFG.get('proactive') or {}).get('checkSeconds') or 30))
            try:
                self._maybe_proactive()
            except Exception as e:
                log(f'  主动消息检查出错: {e}')
            try:
                self._maybe_scheduled_video()
            except Exception as e:
                log(f'  定时视频检查出错: {e}')

    def _pro_targets(self) -> List[str]:
        return [n for n, c in self.chat_by_name.items() if c.get('proactive')]

    def _maybe_proactive(self) -> None:
        pc = CFG.get('proactive') or {}
        if not pc.get('enabled'):
            return
        if any(self.pending.values()):
            return
        self._health_sleep_check()
        sc = CFG.get('schedule') or {}
        if sc.get('enabled'):
            st = self._sched_load()
            targets = [n for n in self._pro_targets() if (self.last_user_at.get(n) or 0) > 0]
            if targets:
                try:
                    ev = self._due_event(sc, st)
                except Exception as e:
                    log(f'  作息检查出错: {e}')
                    ev = None
                self._sched_save(st)
                if ev:
                    name, prompt = ev
                    today = time.strftime('%Y-%m-%d')
                    st.setdefault('fired', {}).setdefault(today, []).append(name)
                    self._sched_save(st)
                    chat = targets[0]
                    if name.startswith('闲聊'):
                        gap_min = float((sc.get('randomChats') or {}).get('minGapAfterHerMin') or 20)
                        last = float(self.last_her_at.get(chat) or 0)
                        idle = (time.time() - last) / 60.0 if last else 9999.0
                        if idle < gap_min:
                            log(f'  作息 [{name}] 跳过：她 {idle:.0f} 分钟前刚说过话（要求间隔 {gap_min:.0f} 分钟）')
                            return
                    if name.startswith('闲聊') or name == '失眠':
                        st.setdefault('manualFired', {}).setdefault(today, []).append(name)
                        self._sched_save(st)
                    log(f'  作息 [{name}] -> {chat}')
                    with self.pro_busy:
                        self._pro_say(chat, prompt, scheduled=True)
                    return
        if not pc.get('nudgeEnabled', True):
            return
        if not self._pro_active():
            return
        now = time.time()
        lo_hi = pc.get('nudgeJudgeMin') or [20, 40]
        limit = int(pc.get('dailyLimit') or 0)
        used = len([t for t in self.pro_times if time.strftime('%Y-%m-%d', time.localtime(t)) == time.strftime('%Y-%m-%d')])
        if limit > 0 and used >= limit:
            return
        gap_min = float(pc.get('minGapMin') or 40)
        if self.pro_times and (now - max(self.pro_times)) / 60.0 < gap_min:
            return
        for name in self._pro_targets():
            last_u = self.last_user_at.get(name) or 0.0
            if last_u <= 0:
                continue
            last_b = self.last_bot_at.get(name) or 0.0
            if last_b <= last_u:
                continue
            need = self.nudge_need.get(name)
            if need is None:
                need = random.uniform(float(lo_hi[0]), float(lo_hi[1]))
                self.nudge_need[name] = need
            idle_min = (now - last_b) / 60.0
            if idle_min < need:
                continue
            jt = self.nudge_judged.get(name) or 0.0
            if now - jt < float(pc.get('judgeRecheckMin') or 15) * 60:
                continue
            self.nudge_judged[name] = now
            per_silence = int(pc.get('nudgeMaxPerSilence') or 2)
            if self.nudge_count.get(name, 0) >= per_silence:
                self.nudge_need[name] = idle_min + 120
                continue
            v = self._nudge_judge(name)
            if str(v.get('should', 'no')).lower() != 'yes':
                log(f"  空窗判定：话题已结束，不打扰（{v.get('reason', '')}）")
                self.nudge_need[name] = idle_min + 90
                continue
            emo = str(v.get('emotion') or '生气')
            reason = '他最后一条消息之后已经安静了 %.0f 分钟，你一直没等到回复，你现在%s。写一条你主动发过去的话，可以带点情绪，但别质问太多。' % (idle_min, '有点' + emo if emo != '正常' else '有点想法')
            with self.pro_busy:
                if not self._pro_active():
                    return
                self.pro_stage[name] = self.pro_stage.get(name, 0) + 1
                self.pro_need.pop(name, None)
                self.pro_times.append(time.time())
                self.nudge_count[name] = self.nudge_count.get(name, 0) + 1
                log(f'  空窗升级 [{name}] 安静 {idle_min:.0f} 分钟（第 {self.nudge_count[name]} 次），判定：{emo}')
                self._pro_say(name, reason)
            return

    def _video_state_load(self) -> List[float]:
        try:
            raw = (self._sched_load() or {}).get('videoTimes') or []
            return [float(x) for x in raw if isinstance(x, (int, float))]
        except Exception:
            return []

    def _video_state_save(self) -> None:
        try:
            st = self._sched_load() or {}
            st['videoTimes'] = [float(t) for t in self.video_times or []][-300:]
            self._sched_save(st)
        except Exception as e:
            log(f'  视频计数保存失败: {e}')

    def _video_allowed(self) -> bool:
        vc = CFG.get('video') or {}
        if not vc.get('enabled', True):
            return False
        limit = int(vc.get('dailyLimit') or 1)
        if not self.video_times:
            self.video_times = self._video_state_load()
        day = time.strftime('%Y-%m-%d')
        n = sum((1 for t in self.video_times if time.strftime('%Y-%m-%d', time.localtime(t)) == day))
        return n < limit

    def _video_slots(self, st: Dict[str, Any]) -> List[int]:
        today = time.strftime('%Y-%m-%d')
        jm = st.setdefault('jitter', {})
        key = f'{today}:视频槽位'
        cached = jm.get(key)
        if isinstance(cached, list) and cached:
            return sorted((int(x) for x in cached))
        vc = CFG.get('video') or {}
        try:
            _day = self._life_load(today)
            _times = []
            for x in (_day or {}).get('videos') or []:
                t = self._at_min(x)
                if t is not None:
                    _times.append(t)
            if len(_times) >= 1:
                _times = sorted(_times)[:2]
                jm[key] = _times
                log('  今天的视频时刻（按日程）：%s' % '、'.join(('%02d:%02d' % (t // 60, t % 60) for t in _times)))
                return _times
        except Exception as e:
            log(f'  视频取日程失败: {e}')
        lo = self._at_min(vc.get('from') or '10:00') or 10 * 60
        hi = self._at_min(vc.get('to') or '23:00') or 23 * 60
        if hi <= lo:
            hi = lo + 60
        n = max(1, int(vc.get('slotsPerDay') or 2))
        span = max(1, (hi - lo) // n)
        slots: List[int] = []
        for seg in range(n):
            seg_lo = lo + seg * span
            seg_hi = min(hi, lo + (seg + 1) * span) - 1
            if seg_hi < seg_lo:
                seg_hi = seg_lo
            slots.append(random.randint(seg_lo, seg_hi))
        slots = sorted(set(slots))
        jm[key] = slots
        log('  今天的视频时刻：%s' % '、'.join(('%02d:%02d' % (t // 60, t % 60) for t in slots)))
        return slots

    def _video_keyword(self, chat: str) -> str:
        try:
            sysm = self._sys_life(chat)
            hist = self._maybe_compact(chat, self.ctx.get(chat))
            trigger = '你想刷一会儿 B 站。根据你们最近聊的内容和你自己的兴趣，想一个 B 站搜索词。只输出搜索词本身：不要解释、不要引号、不要标点，不超过 15 个字。'
            for _ in (1, 2):
                r = self.llm.chat(sysm, hist, trigger, purpose='video_kw', chat=chat)
                r = self._clean(r, chat) if r else ''
                r = (r or '').strip().strip('“”"\'「」').strip()
                r = r.splitlines()[0][:20] if r else ''
                if r:
                    return r
        except Exception as e:
            log(f'  视频搜索词生成失败: {e}')
        return ''

    def _maybe_scheduled_video(self) -> None:
        vc = CFG.get('video') or {}
        if not vc.get('enabled', True):
            return
        pc = CFG.get('proactive') or {}
        if any(self.pending.values()):
            return
        q = pc.get('quietHours') or []
        if len(q) == 2:
            h = time.localtime().tm_hour
            a, b = (int(q[0]), int(q[1]))
            if a <= h < b if a <= b else h >= a or h < b:
                return
        if not self._video_allowed():
            return
        st = self._sched_load()
        today = time.strftime('%Y-%m-%d')
        lt = time.localtime()
        now_min = lt.tm_hour * 60 + lt.tm_min
        slots = self._video_slots(st)
        fired = st.setdefault('videoFired', {}).setdefault(today, [])
        catch = int(vc.get('catchUpMinutes') or 90)
        due = None
        for idx, t in enumerate(slots):
            name = str(idx + 1)
            if name in fired:
                continue
            if now_min < t:
                continue
            if now_min > t + catch:
                fired.append(name)
                log(f'  视频槽位 {idx + 1} 已过 {now_min - t} 分钟，跳过')
                continue
            due = (name, t)
            break
        self._sched_save(st)
        if not due:
            return
        targets = [n for n in self._pro_targets() if (self.last_user_at.get(n) or 0) > 0]
        if not targets:
            return
        chat = targets[0]
        kw = self._video_keyword(chat)
        if not kw:
            log('  视频：没想出搜索词，放弃这次')
            return
        fired.append(due[0])
        self._sched_save(st)
        log(f'  定时视频 [{chat}] 槽位{due[0]} -> 搜「{kw}」')
        threading.Thread(target=self._video_task, args=(chat, kw), daemon=True).start()

    def _bili_task(self, chat: str, bv: str) -> None:
        try:
            import task as V
            import bili as B
            import pipeline as P
        except Exception as e:
            log('  B站模块加载失败: %s' % e)
            return
        log('  他要她看: %s' % bv)
        try:
            _dur = float((B.video_info(bv) or {}).get('duration') or 0)
        except Exception:
            _dur = 0.0
        if _dur > 600:
            log('  视频太长（%.0f 秒 > 600），不看了' % _dur)
            try:
                self._deliver(chat, '这个太长了，%d分钟，我看不完。' % round(_dur / 60))
            except Exception:
                pass
            return
        try:
            path = B.download(bv)
        except Exception as e:
            log('  下载失败: %s' % e)
            return
        try:
            r = P.watch(path)
        except Exception as e:
            log('  处理失败: %s' % e)
            return
        if r.get('error') or not r.get('summary'):
            log('  总结失败，放弃')
            return
        title = ''
        _desc = ''
        _up = ''
        try:
            _vi = B.video_info(bv) or {}
            title = _vi.get('title') or ''
            _desc = (_vi.get('desc') or '').strip()
            _up = (_vi.get('up') or '').strip()
        except Exception:
            pass
        log('  看完了: %s  %.0fs  ¥%.4f' % (title[:26], r.get('cost_cny') or 0, r.get('cost_cny') or 0))
        TELEM.emit('video', action='watched', chat=chat, title=title[:60])
        _brief = (r.get('summary') or '').strip().replace('\n', ' ')
        if _brief:
            self.ctx.add(chat, 'user', '%s （你看完的视频《%s》：%s）' % (_hhmm(), title or '', _brief))
        reply = self._make_watched_reply(chat, title, bv, r['summary'], _desc, _up)
        if not reply:
            log('  生成评论失败，放弃')
            return
        self.ctx.add(chat, 'assistant', f'{_hhmm()} {reply}')
        self.last_bot_at[chat] = time.time()
        self._deliver(chat, reply)

    def _make_watched_reply(self, chat: str, title: str, bv: str, summary: str, desc: str='', up: str='') -> str:
        sysm = self._sys_life(chat)
        hist = self.ctx.get(chat)
        hist = self._maybe_compact(chat, hist)
        _info = '标题：%s\n' % (title or '（无标题）')
        if up:
            _info += 'UP主：%s\n' % up
        _d = (desc or '').strip()
        if _d and _d != (title or '').strip():
            _info += 'B站简介：%s\n' % _d[:300]
        _info += '你看到的：%s' % (summary or '')
        trigger = '他刚才给你发了一个视频，你点开看完了。\n\n' + _info + '\n\n用你的语气跟他说说你看了之后的感受，1~2 句。就聊内容，不要再把链接发回去。'
        for _ in (1, 2):
            reply = self.llm.chat(sysm, hist, trigger, purpose='bili', chat=chat)
            reply = self._clean(reply, chat) if reply else ''
            if reply:
                return reply
        return ''

    def _video_task(self, chat: str, keyword: str) -> None:
        try:
            import task as V
        except Exception as e:
            log('  视频模块加载失败: %s' % e)
            return
        self.video_times.append(time.time())
        self._video_state_save()
        log('  视频任务开始：搜「%s」' % keyword)
        try:
            r = V.pick_and_watch(keyword, verbose=False)
        except Exception as e:
            log('  视频任务异常: %s' % e)
            return
        if not r.get('ok'):
            log('  视频任务放弃: %s' % r.get('error'))
            return
        log('  看完: %s  ¥%.4f  %.0fs  %d张' % (str(r.get('title') or '')[:26], r.get('cost_cny') or 0, r.get('secs') or 0, r.get('n_grids') or 0))
        TELEM.emit('video', action='shared', chat=chat, title=str(r.get('title') or '')[:60])
        _brief = (r.get('summary') or '').strip().replace('\n', ' ')
        if _brief:
            self.ctx.add(chat, 'user', '%s （你看完的视频《%s》：%s）' % (_hhmm(), r.get('title') or '', _brief))
        share = self._make_video_share(chat, r)
        if not share:
            log('  生成分享失败，放弃（不发半截）')
            return
        self.ctx.add(chat, 'assistant', f'{_hhmm()} {share}')
        self.last_bot_at[chat] = time.time()
        self._deliver(chat, share)

    def _make_video_share(self, chat: str, r: Dict[str, Any]) -> str:
        sysm = self._sys_life(chat)
        hist = self.ctx.get(chat)
        hist = self._maybe_compact(chat, hist)
        _desc = (r.get('desc') or '').strip()
        _info = '标题：%s\n' % (r.get('title') or '')
        if r.get('up'):
            _info += 'UP主：%s\n' % r['up']
        _info += '链接：%s\n' % (r.get('url') or '')
        if _desc and _desc != (r.get('title') or '').strip():
            _info += 'B站简介：%s\n' % _desc[:300]
        _info += '你看到的内容：%s' % (r.get('summary') or '')
        trigger = '你刚才刷B站的时候刷到了这个视频，从头到尾看完了，觉得挺有意思。\n\n' + _info + '\n\n现在你想把它发给他看看。用你的语气说 1~2 句：\n先说一个你看到的**具体**的东西（谁在干嘛、哪里好笑、你看到啥），\n再把链接原样带上。\n别只说「我刷到一个觉得你会感兴趣」——他还没看，你得让他知道是什么。'
        for attempt in (1, 2):
            reply = self.llm.chat(sysm, hist, trigger, chat=chat)
            reply = self._clean(reply, chat) if reply else ''
            if reply:
                return reply
        return ''

    def _pro_say(self, chat: str, reason: str, scheduled: bool=False) -> bool:
        if scheduled:
            extra = '\n\n**这条是你主动发的，没有任何人跟你说话。** 按上面【现在的情况】里的要求写，**直接发内容**，不要解释你为什么发。'
        else:
            extra = '\n\n**这条是你主动发的，没有任何人跟你说话。** 直接写你要发的内容，1~2 句，短。不要解释你为什么发，不要写「我主动来找你」这种元话。不要发视频链接，视频由独立的定时任务负责。'
        _life = self._life_block(chat)
        _now = self._life_now(chat)
        sysm = self.system_for(chat) + self.memory_block(chat) + ('\n\n---\n\n' + _life if _life else '') + ('\n\n【你现在正在】' + _now if _now else '') + '\n\n---\n\n【现在的情况】\n' + reason + extra
        hist = self.ctx.get(chat)
        hist = self._maybe_compact(chat, hist)
        trigger = '（现在是你先开口。按上面的情境写出你要发的那条微信消息。）\n' + _now_note()
        reply = ''
        for attempt in (1, 2):
            reply = self.llm.chat(sysm, hist, trigger, chat=chat)
            if reply and self._clean(reply, chat):
                break
            log('    模型没给出内容（第 %d 次）%s' % (attempt, '，重试' if attempt == 1 else '，放弃'))
        reply = self._clean(reply, chat) if reply else ''
        if not reply:
            return False
        reply, _photos = _extract_photo_markers(reply)
        if not reply and (not _photos):
            return False
        if reply:
            self.ctx.add(chat, 'assistant', f'{_hhmm()} {reply}')
        self.last_bot_at[chat] = time.time()
        self._deliver(chat, reply, photos=_photos)
        return True

def main() -> None:
    global CFG, CFG_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=os.path.join(HERE, 'bot-config.json'))
    ap.add_argument('--check-config', action='store_true', help='只校验环境变量和配置，不连接微信/模型')
    ap.add_argument('--reset', action='store_true', help='清空所有会话的上下文然后退出')
    ap.add_argument('--reset-chat', default='', metavar='名字', help='只清空指定会话的上下文然后退出')
    ap.add_argument('--list-chats', action='store_true', help='列出各会话现在存了多少条上下文然后退出')
    args = ap.parse_args()
    CFG_PATH = os.path.abspath(args.config)
    try:
        aux_runtime.set_config_path(CFG_PATH)
        qwen_runtime.set_config_path(CFG_PATH)
    except Exception:
        pass
    try:
        CFG_MTIME[0] = os.stat(CFG_PATH).st_mtime
    except Exception:
        CFG_MTIME[0] = 0.0
    if not os.path.exists(CFG_PATH):
        print('config file is missing; copy desktop/bot-config.example.json to desktop/bot-config.json and fill it', file=sys.stderr)
        sys.exit(2)
    try:
        CFG = config_loader.load_config(CFG_PATH, DEFAULT_CONFIG, _ROOT)
        CFG = config_loader.apply_environment(CFG)
    except Exception as e:
        print(f'configuration is invalid: {e}', file=sys.stderr)
        sys.exit(2)
    if args.check_config:
        warnings = config_loader.check_config(CFG)
        print('config: OK')
        print('configFile:', os.path.relpath(CFG_PATH, _ROOT))
        print('chats:', len(CFG.get('chats') or []))
        print('dataDir:', os.path.dirname(str(CFG.get('contextFile') or '')))
        for warning in warnings:
            print('warning:', warning)
        return
    if args.reset or args.reset_chat or args.list_chats:
        cpath = CFG['contextFile']
        try:
            with open(cpath, encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except FileNotFoundError:
            data = {}
        except Exception as e:
            print(f'上下文读不出来：{e}', file=sys.stderr)
            sys.exit(1)
        if args.list_chats:
            if not data:
                print('（还没有任何上下文）')
            for k, v in data.items():
                print(f'  {k}: {len(v)} 条')
            return
        if args.reset_chat:
            name = args.reset_chat
            if name in data:
                n = len(data.pop(name))
                with open(cpath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=1)
                print(f'已清空「{name}」（原来 {n} 条），其余会话保留')
            else:
                print(f'没有找到会话「{name}」')
                print('现有的:', '、'.join(data.keys()) or '（无）')
        if args.reset:
            backup = f"{cpath}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
            try:
                with open(cpath, encoding='utf-8') as src, open(backup, 'w', encoding='utf-8') as dst:
                    dst.write(src.read())
                print(f'已备份到 {backup}')
            except Exception:
                pass
            with open(cpath, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=1)
            print(f'已清空全部 {len(data)} 个会话的上下文')
        return
    key = str(CFG.get('apiKey') or os.environ.get('DEEPSEEK_API_KEY') or '').strip()
    if not key and CFG.get('credentialsFile'):
        key = read_credential(CFG['credentialsFile'], CFG['credentialsRef'])
    if not key:
        print('DeepSeek API key is not set; set DEEPSEEK_API_KEY or apiKey in the config', file=sys.stderr)
        sys.exit(1)
    CFG['_apiKey'] = key
    os.makedirs(os.path.dirname(str(CFG['logFile'])) or '.', exist_ok=True)
    _proxy = str(CFG.get('proxy') or '').strip()
    if _proxy:
        os.environ['HTTP_PROXY'] = _proxy
        os.environ['HTTPS_PROXY'] = _proxy
        log(f'  API 走代理: {_proxy}')
    else:
        _had = [k for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy') if os.environ.pop(k, None)]
        os.environ['NO_PROXY'] = '*'
        os.environ['no_proxy'] = '*'
        log('  API 直连（不走代理）' + ('，已清掉继承来的 ' + '/'.join(_had) if _had else ''))
    try:
        import wechatauto.rhythm as _rhythm
        prof = str(CFG.get('rhythmProfile') or 'natural').strip().lower()
        if prof == 'custom':
            kw = {}
            if int(CFG.get('rhythmBurst') or 0) > 0:
                kw['burst'] = int(CFG['rhythmBurst'])
            if float(CFG.get('rhythmWindow') or 0) > 0:
                kw['window'] = float(CFG['rhythmWindow'])
            p = _rhythm.configure(**kw)
            print(f'wechatauto 节奏：custom burst={p.burst} window={p.window}')
        elif prof:
            p = _rhythm.set_profile(prof)
            print(f'wechatauto 节奏：{p.name} burst={p.burst} window={p.window}')
        try:
            _PRIVATE_NAMES.clear()
            for _c in CFG.get('chats') or []:
                _k = str(_c.get('kind') or '').lower()
                _mode = str(_c.get('mode') or '').lower()
                if _k == 'group' or _mode == 'wake':
                    continue
                _PRIVATE_NAMES.add(str(_c.get('name') or ''))
            _orig_gate = _rhythm.gate

            def _gate(kind='write'):
                if getattr(_RHYTHM_TL, 'free', False):
                    return 0.0
                return _orig_gate(kind)
            _rhythm.gate = _gate
            print('私聊不限速：', '、'.join(sorted(_PRIVATE_NAMES)) or '（无）')
        except Exception as e:
            print(f'安装私聊不限速失败（忽略）：{e}', file=sys.stderr)
        TELEM.rhythm = {'name': getattr(p, 'name', prof), 'burst': int(getattr(p, 'burst', 0) or 0), 'window': float(getattr(p, 'window', 120.0) or 120.0)}
    except Exception as e:
        print(f'设置 wechatauto 节奏失败（忽略）：{e}', file=sys.stderr)
    try:
        TELEM.model_cfg = {'model': CFG.get('model'), 'baseUrl': CFG.get('baseUrl'), 'thinking': CFG.get('thinking'), 'maxTokens': CFG.get('maxTokens'), 'historyLimit': CFG.get('historyLimit'), 'temperature': CFG.get('temperature'), 'contextFile': os.path.basename(str(CFG.get('contextFile') or '')), 'conversationId': CFG.get('activeConversationId') or ''}
    except Exception:
        pass
    try:

        def _model_telem(ev):
            try:
                kind = ev.get('kind')
                extra = {k: ev[k] for k in ('imageCount', 'hasAudio', 'audioFormat', 'enableThinking') if k in ev}
                if kind == 'request':
                    TELEM.emit('llm_request', provider=ev.get('provider'), model=ev.get('model'), purpose=ev.get('purpose'), thinking=ev.get('thinking'), max_tokens=ev.get('max_tokens'), system=ev.get('system') or '', history=int(ev.get('history') or 0), user=ev.get('user') or '', **extra)
                elif kind == 'reply':
                    usage = ev.get('usage') or {}
                    reply_extra = dict(extra)
                    for name in ('emotion', 'detectedLanguage'):
                        if name in ev:
                            reply_extra[name] = ev[name]
                    TELEM.emit('llm_reply', provider=ev.get('provider'), model=ev.get('model'), purpose=ev.get('purpose'), text=ev.get('content') or '', thinking=ev.get('thinking') or '', ms=int(ev.get('ms') or 0), in_tok=int(usage.get('prompt_tokens') or 0), out_tok=int(usage.get('completion_tokens') or 0), cached=int((usage.get('prompt_tokens_details') or {}).get('cached_tokens') or 0), **reply_extra)
                elif kind == 'error':
                    TELEM.emit('llm_error', provider=ev.get('provider'), model=ev.get('model'), purpose=ev.get('purpose'), code=ev.get('status'), detail=ev.get('error_type') or 'model_error')
            except Exception:
                pass
        aux_runtime.set_telemetry_hook(_model_telem)
        qwen_runtime.set_telemetry_hook(_model_telem)
        print('副模型（MiMo/Qwen）遥测已接入看板')
    except Exception as e:
        print(f'接副模型遥测失败: {e}', file=sys.stderr)
    try:
        dashboard_runtime.configure(data_dir=os.path.join(_data_dir(), 'dashboard'))
        dashboard_runtime.install_trace_hooks()
        log('  看板后端已就绪：逐模型调用跟踪 + 健康存储')
    except Exception as e:
        log(f'  看板后端初始化失败（忽略）：{type(e).__name__}')
    log('=' * 60)
    log('启动独立版')
    try:
        Bot().start()
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log('已停止')
if __name__ == '__main__':
    main()

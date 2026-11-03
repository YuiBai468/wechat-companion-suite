import json
import os
import re
import subprocess
import sys
import time
_HERE = os.path.dirname(os.path.abspath(__file__))
_DESKTOP = os.path.dirname(_HERE)
for _p in (_HERE, _DESKTOP):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import qwen_runtime
import settings
FFMPEG = settings.FFMPEG
FFPROBE = settings.FFPROBE
TMP = settings.ASR_TMP
MAX_SEC = 170

def probe_duration(video: str) -> float:
    r = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', video], capture_output=True, text=True, encoding='utf-8', errors='replace')
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0

def extract_audio(video: str, start: float=0.0, secs: float=None, out: str=None) -> str:
    os.makedirs(TMP, exist_ok=True)
    if out is None:
        out = os.path.join(TMP, '%s_%.0f.wav' % (re.sub('[^\\w.-]', '_', os.path.basename(video))[:30], start))
    cmd = [FFMPEG, '-y', '-ss', '%.2f' % start]
    if secs:
        cmd += ['-t', '%.2f' % secs]
    cmd += ['-i', video, '-vn', '-ac', '1', '-ar', '16000', '-f', 'wav', out]
    subprocess.run(cmd, capture_output=True)
    return out

def transcribe_wav(wav_path: str) -> dict:
    with open(wav_path, 'rb') as f:
        wav = f.read()
    r = qwen_runtime.asr_transcribe(wav, audio_format='wav', language='zh', purpose='video_asr')
    return {'text': str(r.get('text') or ''), 'emotion': str(r.get('emotion') or ''), 'language': str(r.get('language') or ''), 'secs': float(r.get('secs') or 0.0), 'usage': r.get('usage') or {}}

def transcribe(video: str) -> dict:
    dur = probe_duration(video)
    if dur <= 0:
        return {'text': '', 'emotion': '', 'secs': 0.0, 'usage': {}, 'chunks': 0}
    if dur <= MAX_SEC:
        wav = extract_audio(video)
        r = transcribe_wav(wav)
        r['chunks'] = 1
        try:
            os.remove(wav)
        except Exception:
            pass
        return r
    parts = []
    total_secs = 0.0
    usage = {}
    n = int(dur // MAX_SEC) + 1
    for i in range(n):
        start = i * MAX_SEC
        if start >= dur:
            break
        wav = extract_audio(video, start, MAX_SEC, out=os.path.join(TMP, 'part_%02d.wav' % i))
        r = transcribe_wav(wav)
        if r['text']:
            parts.append(r['text'])
        total_secs += r['secs']
        for k, v in (r['usage'] or {}).items():
            if isinstance(v, (int, float)):
                usage[k] = usage.get(k, 0) + v
        try:
            os.remove(wav)
        except Exception:
            pass
    return {'text': ''.join(parts), 'emotion': '', 'secs': total_secs, 'usage': usage, 'chunks': n}
if __name__ == '__main__':
    import sys
    v = sys.argv[1] if len(sys.argv) > 1 else ''
    print('视频:', v)
    r = transcribe(v)
    print('分段: %d   耗时 %.2fs' % (r['chunks'], r['secs']))
    print('用量:', json.dumps(r['usage'], ensure_ascii=False))
    print('转写 %d 字:' % len(r['text']))
    print(r['text'][:500])

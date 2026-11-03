import os
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.environ.get('DATA_DIR') or os.path.join(ROOT, 'data')
CACHE_DIR = os.environ.get('VIDEO_CACHE_DIR') or os.path.join(DATA_DIR, 'bili')
FRAMES_TMP = os.environ.get('VIDEO_FRAMES_TMP') or os.path.join(DATA_DIR, '_frames')
ASR_TMP = os.environ.get('VIDEO_ASR_TMP') or os.path.join(DATA_DIR, '_asr')
FFMPEG = os.environ.get('FFMPEG_BIN') or 'ffmpeg'
FFPROBE = os.environ.get('FFPROBE_BIN') or 'ffprobe'


def ensure_dirs():
    for d in (DATA_DIR, CACHE_DIR, FRAMES_TMP, ASR_TMP):
        os.makedirs(d, exist_ok=True)

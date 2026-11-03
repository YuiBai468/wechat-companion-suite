import math
import os
import re
import subprocess
import sys
import settings
from PIL import Image, ImageDraw, ImageFont
FFMPEG = settings.FFMPEG
FFPROBE = settings.FFPROBE
WORK_TMP = settings.FRAMES_TMP
SEGMENT_SEC = 5.0
PER_SEGMENT = 9
MAX_SEGMENTS = 60
CELL = 1280
COLS = 3
SEGMENT_RULES = [(120.0, 5.0), (240.0, 7.5), (360.0, 10.0), (480.0, 12.5), (600.0, 15.0)]
CELL = 1280
MAX_GRIDS = 40
QN_ALWAYS = 64

def segment_for(duration: float) -> float:
    for limit, seg in SEGMENT_RULES:
        if duration <= limit:
            return seg
    return SEGMENT_RULES[-1][1]

def quality_for(duration: float) -> int:
    return QN_ALWAYS

def cell_for(duration: float) -> int:
    return CELL
FONT_CANDIDATES = ['C:\\Windows\\Fonts\\msyh.ttc', 'C:\\Windows\\Fonts\\simhei.ttf', 'C:\\Windows\\Fonts\\arial.ttf']

def _font(size=26):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()

def probe_duration(video: str) -> float:
    r = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', video], capture_output=True, text=True, encoding='utf-8', errors='replace')
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0

def _fmt(t: float) -> str:
    m, s = divmod(int(round(t)), 60)
    return '%d:%02d' % (m, s)

def _grab_segment(video: str, outdir: str, start: float, secs: float, n: int, cell: int=CELL):
    fps = n / secs
    tag = 'seg%05.1f' % start
    pattern = os.path.join(outdir, tag + '_%02d.jpg')
    cmd = [FFMPEG, '-y', '-ss', '%.3f' % start, '-t', '%.3f' % secs, '-i', video, '-vf', 'fps=%.4f,scale=%d:%d:force_original_aspect_ratio=decrease' % (fps, cell, cell), '-q:v', '2', pattern]
    subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    files = sorted((f for f in os.listdir(outdir) if f.startswith(tag + '_') and f.endswith('.jpg')))
    out = []
    for i, f in enumerate(files[:n]):
        t = start + (i + 0.5) * secs / max(n, 1)
        out.append((t, os.path.join(outdir, f)))
    return out

def make_grid(frames, timestamps, out_path: str, cols=COLS, cell=None) -> str:
    cell = cell or CELL
    rows = (len(frames) + cols - 1) // cols
    canvas = Image.new('RGB', (cols * cell, rows * cell), (20, 20, 20))
    draw = ImageDraw.Draw(canvas)
    font = _font(26)
    for i, (fp, t) in enumerate(zip(frames, timestamps)):
        try:
            im = Image.open(fp).convert('RGB')
        except Exception:
            continue
        im.thumbnail((cell, cell))
        gx, gy = (i % cols * cell, i // cols * cell)
        canvas.paste(im, (gx + (cell - im.width) // 2, gy + (cell - im.height) // 2))
        label = 't=' + _fmt(t)
        bx, by = (gx + 6, gy + 6)
        bb = draw.textbbox((bx, by), label, font=font)
        draw.rectangle([bb[0] - 3, bb[1] - 2, bb[2] + 3, bb[3] + 2], fill=(0, 0, 0))
        draw.text((bx, by), label, fill=(255, 235, 60), font=font)
        draw.rectangle([gx, gy, gx + cell - 1, gy + cell - 1], outline=(70, 70, 70))
    canvas.save(out_path, quality=72)
    return out_path

def process_video(video: str, outdir: str=None, segment: float=None, per_segment: int=PER_SEGMENT, max_segments: int=MAX_SEGMENTS) -> dict:
    if outdir is None:
        base = os.path.join(WORK_TMP, re.sub('[^\\w.-]', '_', os.path.basename(video))[:40])
        os.makedirs(WORK_TMP, exist_ok=True)
        outdir = base
    os.makedirs(outdir, exist_ok=True)
    if not os.path.isfile(video):
        raise FileNotFoundError('视频不存在: %s' % video)
    duration = probe_duration(video)
    if duration <= 0:
        raise RuntimeError('读不出视频时长（文件可能损坏）: %s' % video)
    cell = cell_for(duration)
    if segment is None:
        segment = segment_for(duration)
    else:
        try:
            segment = float(segment)
        except Exception:
            segment = SEGMENT_SEC
    n_seg = int(math.ceil(duration / segment))
    if n_seg > MAX_GRIDS:
        segment = duration / MAX_GRIDS
        n_seg = MAX_GRIDS
    n_seg = max(1, min(n_seg, max_segments))
    grids = []
    for i in range(n_seg):
        start = i * segment
        secs = min(segment, max(duration - start, 0.5))
        picked = _grab_segment(video, outdir, start, secs, per_segment, cell)
        if not picked:
            continue
        frames = [p for _, p in picked]
        stamps = [t for t, _ in picked]
        sheet = os.path.join(outdir, 'sheet_%02d.jpg' % i)
        make_grid(frames, stamps, sheet, cell=cell)
        grids.append({'sheet': sheet, 'timestamps': stamps, 'start': start, 'end': start + secs})
    return {'grids': grids, 'duration': duration, 'segments': n_seg, 'outdir': outdir}
if __name__ == '__main__':
    import sys
    v = sys.argv[1] if len(sys.argv) > 1 else ''
    print('视频:', v)
    r = process_video(v)
    print('时长: %.1fs   段数: %d' % (r['duration'], r['segments']))
    for i, g in enumerate(r['grids']):
        print('  段%d  %.1f~%.1fs  时间戳 %s' % (i, g['start'], g['end'], ' '.join(('%.1f' % t for t in g['timestamps']))))
        print('        拼图: %s  %.0f KB' % (g['sheet'], os.path.getsize(g['sheet']) / 1024))

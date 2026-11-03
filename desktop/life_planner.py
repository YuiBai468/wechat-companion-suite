import json, os, sys, time
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import aux_runtime
DIR = os.environ.get('LIFE_PLAN_DIR') or os.path.join(os.environ.get('DATA_DIR') or os.path.join(_ROOT, 'data'), 'life-plan')
DAYS = os.path.join(DIR, 'days')
PERIOD_FILE = os.path.join(DIR, 'period.json')

def load_period():
    try:
        return json.load(open(PERIOD_FILE, encoding='utf-8')).get('months') or {}
    except Exception:
        return {}

def period_for(date_str: str):
    import datetime
    try:
        d = datetime.date.fromisoformat(date_str)
    except Exception:
        return ('平时', '')
    for _m, v in (load_period() or {}).items():
        try:
            st = datetime.date.fromisoformat(str(v.get('start')))
        except Exception:
            continue
        days = int(v.get('days') or 5)
        end = st + datetime.timedelta(days=days - 1)
        if st <= d <= end:
            return ('经期第%d天' % ((d - st).days + 1), str(v.get('severity') or '中'))
        try:
            ovu = datetime.date.fromisoformat(str(v.get('ovulation_start')))
            ovu_days = int(v.get('ovulation_days') or 3)
            if ovu <= d <= ovu + datetime.timedelta(days=ovu_days - 1):
                return ('排卵期第%d天' % ((d - ovu).days + 1), '轻')
        except Exception:
            pass
        delta = (st - d).days
        if 1 <= delta <= 3:
            return ('经前第%d天' % delta, str(v.get('severity') or '中'))
    return ('平时', '')
WORLD = '使用配置的人物背景；没有背景时采用普通虚构日常生活，不编造用户的真实经历、身份或健康资料。'
SYS = '你是虚构角色的日程规划器。根据用户明确配置的角色和约束生成中性的日常安排，只返回有效 JSON。'
TELEM_HOOK = None
_TEXT_GENERATOR = None

def set_text_generator(fn):
    global _TEXT_GENERATOR
    _TEXT_GENERATOR = fn

def _ask(prompt, max_tokens=12000, purpose='life_plan'):
    messages = [{'role': 'system', 'content': SYS + '\n\n' + WORLD}, {'role': 'user', 'content': prompt}]
    last = None
    for attempt in range(1, 4):
        try:
            if _TEXT_GENERATOR is not None:
                r = _TEXT_GENERATOR(messages=messages, purpose=purpose, max_tokens=max_tokens, timeout=240, return_meta=True)
            else:
                r = aux_runtime.text_chat(messages=messages, purpose=purpose, max_tokens=max_tokens, timeout=240, return_meta=True)
            content = str(r.get('content') or '')
            if TELEM_HOOK:
                try:
                    TELEM_HOOK(purpose, prompt, r.get('thinking') or '', content, r.get('usage') or {})
                except Exception:
                    pass
            return json.loads(content)
        except Exception as e:
            aux_error = getattr(aux_runtime, 'AuxError', None)
            if isinstance(aux_error, type) and isinstance(e, aux_error):
                code = getattr(e, 'reason_code', '') or type(e).__name__
                code = str(code)
                if not (code.replace('_', '').isalnum() and len(code) <= 48):
                    code = 'aux_error'
                raise RuntimeError('life planner aux model failed: %s' % code) from None
            last = e
            time.sleep(2)
    raise RuntimeError('life planner aux model failed: %s' % type(last).__name__)
MONTHS = os.path.join(DIR, 'months')
os.makedirs(MONTHS, exist_ok=True)

def month_plan(month_str: str) -> dict:
    fp = os.path.join(MONTHS, month_str + '.json')
    if os.path.exists(fp):
        try:
            return json.load(open(fp, encoding='utf-8'))
        except Exception:
            pass
    prompt = f'为 {month_str} 这个月排出当前配置角色的逐日安排（每天一行，不是周模板）。要求：\n1) theme：这个月的主题；\n2) days：这个月每一天，一天一项，按日期顺序，字段：date（YYYY-MM-DD）、weekday（周几）、theme（这一天的关键词，10 字内）、events（这一天 2~4 件关键事件，每件 20 字内）；\n   有规律的事可以固定在某几天，但每天要有自己的差异；隔周、节日、天气、身体状态都要排进去。角色是普通虚构日常，不指向任何真实人物。\n3) notes：这个月要避免的重复、天气/节日提示。\nJSON：{{"month":"' + month_str + '","theme":"...","days":[{"date":"' + month_str + '-01","weekday":"周四","theme":"...","events":["...","..."]}],"notes":"..."}'
    d = _ask(prompt, max_tokens=9000, purpose='life_month')
    d['month'] = month_str
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return d

def month_day(month_str: str, date_str: str) -> dict:
    try:
        mp = month_plan(month_str)
    except Exception:
        return {}
    for d in mp.get('days') or []:
        if str(d.get('date')) == date_str:
            return d
    return {}

def recent_activities(day: str, n: int=2) -> str:
    out = []
    import datetime
    try:
        d0 = datetime.date.fromisoformat(day)
    except Exception:
        return ''
    for i in range(1, n + 1):
        dp = os.path.join(DAYS, (d0 - datetime.timedelta(days=i)).isoformat() + '.json')
        if not os.path.exists(dp):
            continue
        try:
            prev = json.load(open(dp, encoding='utf-8'))
            acts = [x.get('activity') for x in prev.get('timeline') or []]
            acts = [a for a in acts if a and '睡' not in a]
            out.append((d0 - datetime.timedelta(days=i)).isoformat() + '：' + '；'.join(acts[:10]))
        except Exception:
            pass
    return '\n'.join(out)

def generate_day(date_str: str, period_phase: str='', not_before: str='', extra: str='', persist: bool=True) -> dict:
    _month = date_str[:7]
    try:
        _mp = month_plan(_month)
    except Exception:
        _mp = {}
    _day_plan = month_day(_month, date_str)
    _recent = recent_activities(date_str, 2)
    _phase, _sev = period_for(date_str)
    _period = period_phase or (_phase if _phase == '平时' else '%s（症状：%s）' % (_phase, _sev))
    prompt = f"请规划 {date_str} 的完整一天，覆盖 00:00 到 24:00，至少 40 个连续、不重叠的时间段。\n安排包含休息、用餐、工作或学习、散步和娱乐，写自然具体的日常细节。\n角色状态（可选）：{period_phase}。补充约束：{extra}。\nchats 至少 5 个、videos 至少 3 个 HH:MM 时刻，须在 {not_before or '00:00'} 之后，并选择合适的空闲时段。\n只返回 JSON，字段 date、period、weather(kind,note)、state_in(fatigue,voice,mood，各为1到5)、state_out(fatigue,voice,mood，各为1到5)、timeline(from,to,activity,place,detail)、chats、videos。\n不要输出用户的私人背景或真实健康记录，不包含露骨内容。"
    for _ in range(3):
        d = _ask(prompt, max_tokens=12000, purpose='life_day')
        try:
            import datetime as _dt
            _wd = '周' + '一二三四五六日'[_dt.date.fromisoformat(date_str).weekday()]
            d['period'] = '%s · %s' % (_wd, _period)
        except Exception:
            pass
        tl = d.get('timeline') or []

        def _mt(x):
            try:
                h, m = str(x).split(':')
                return int(h) * 60 + int(m)
            except Exception:
                return None
        chats = [_mt(x) for x in d.get('chats') or []]
        videos = [_mt(x) for x in d.get('videos') or []]
        ok = bool(tl) and tl[0].get('from') == '00:00' and (tl[-1].get('to') == '24:00') and all((a.get('to') == b.get('from') for a, b in zip(tl, tl[1:])))
        ok = ok and len(tl) >= 20 and (len([x for x in chats if x is not None]) >= 5) and (len([x for x in videos if x is not None]) >= 3)
        if ok:
            d['date'] = date_str
            if persist:
                save_day(d)
            return d
        time.sleep(1)
    raise RuntimeError('bad timeline')

def save_day(day: dict) -> None:
    os.makedirs(DAYS, exist_ok=True)
    p = os.path.join(DAYS, day['date'] + '.json')
    tmp = '%s.tmp-%d-%d' % (p, os.getpid(), time.time_ns())
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(day, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise

def load_day(date_str: str):
    p = os.path.join(DAYS, date_str + '.json')
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return None

def _to_min(s: str) -> int:
    h, m = s.split(':')
    return int(h) * 60 + int(m)

def current_segment(day: dict, now_min: int):
    if not day:
        return None
    for s in day.get('timeline') or []:
        if _to_min(s.get('from', '00:00')) <= now_min < _to_min(s.get('to', '24:00')):
            return s
    return None

def format_day(day: dict, max_segments: int=45) -> str:
    if not day:
        return ''
    lines = []
    for s in (day.get('timeline') or [])[:max_segments]:
        extra = '，' + s['detail'] if s.get('detail') else ''
        lines.append(f"{s.get('from')}-{s.get('to')} {s.get('activity')}（{s.get('place')}{extra}）")
    return '\n'.join(lines)
if __name__ == '__main__':
    import sys
    date = sys.argv[1] if len(sys.argv) > 1 else time.strftime('%Y-%m-%d')
    d = generate_day(date, sys.argv[2] if len(sys.argv) > 2 else '')
    print('日期:', d['date'], '| 身体:', d.get('period'), '|', len(d['timeline']), '段')
    for s in d['timeline']:
        print(f"{s['from']}-{s['to']}  {s['activity']} @{s['place']}  — {s.get('detail', '')}")

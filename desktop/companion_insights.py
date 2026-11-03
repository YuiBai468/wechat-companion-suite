import statistics
import time

def _effort(request):
    if not isinstance(request, dict):
        return ''
    effort = request.get('reasoning_effort')
    if isinstance(effort, str) and effort.strip():
        return effort.strip().lower()
    reasoning = request.get('reasoning')
    if isinstance(reasoning, dict):
        value = reasoning.get('effort')
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    thinking = request.get('thinking')
    if isinstance(thinking, dict):
        typ = str(thinking.get('type') or '').lower()
        if typ in ('disabled', 'off', 'none'):
            return 'off'
    return ''

def _pct(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]

class ModelsService:
    WINDOW_LABEL = '最近保留的调用'

    def __init__(self, trace_store, model_cfg):
        self.trace_store = trace_store
        self._cfg_source = model_cfg if callable(model_cfg) else lambda: model_cfg
        self._started = time.time()

    def _cfg(self):
        try:
            return self._cfg_source() or {}
        except Exception:
            return {}

    def routes(self):
        cfg = self._cfg()
        primary = str(cfg.get('model') or '')
        aux = cfg.get('auxiliary') or {}
        vision = cfg.get('vision') or {}
        asr = cfg.get('asr') or {}
        image = cfg.get('image') or {}
        thinking = str(cfg.get('thinking') or 'low').lower()
        return [{'id': 'primary_chat', 'label': '主聊天', 'purpose': 'chat', 'provider': 'deepseek', 'model': primary, 'effort': thinking, 'enabled': bool(cfg.get('enabled', True))}, {'id': 'life_plan', 'label': '生活计划', 'purpose': 'life_day', 'provider': 'deepseek', 'model': primary, 'effort': 'low', 'enabled': bool((cfg.get('lifeplan') or {}).get('enabled', True))}, {'id': 'aux', 'label': 'MiMo 辅助', 'purpose': 'aux', 'provider': str(aux.get('provider') or 'mimo'), 'model': str(aux.get('model') or ''), 'effort': str(aux.get('thinking') or 'low'), 'enabled': bool(aux.get('enabled', True))}, {'id': 'vision', 'label': '千问视觉', 'purpose': 'video_summary', 'provider': str(vision.get('provider') or 'qwen'), 'model': str(vision.get('model') or ''), 'effort': '', 'enabled': bool(vision.get('enabled', True))}, {'id': 'asr', 'label': '千问语音', 'purpose': 'asr', 'provider': str(asr.get('provider') or 'qwen'), 'model': str(asr.get('model') or ''), 'effort': '', 'enabled': bool(asr.get('enabled', True))}, {'id': 'image', 'label': '千问图片', 'purpose': 'image_generate', 'provider': 'qwen', 'model': str(image.get('model') or ''), 'effort': '', 'enabled': bool(image.get('enabled', True))}]

    def get(self):
        rows = []
        try:
            rows = self.trace_store.stats_rows(limit=5000)
        except Exception:
            rows = []
        groups = {}
        for row in rows:
            effort = _effort(row.get('request') or {})
            key = (str(row.get('provider') or ''), str(row.get('model') or ''), str(row.get('purpose') or ''), effort)
            item = groups.setdefault(key, {'provider': key[0], 'model': key[1], 'purpose': key[2], 'effort': key[3], 'calls': 0, 'success': 0, 'failed': 0, 'running': 0, 'rows': []})
            item['calls'] += 1
            status = str(row.get('status') or '')
            if status == 'complete':
                item['success'] += 1
            elif status in ('error', 'failed', 'interrupted'):
                item['failed'] += 1
            elif status == 'running':
                item['running'] += 1
            item['rows'].append(row)
        metrics = []
        for item in groups.values():
            rows_g = item.pop('rows')
            ms = [int(r.get('ms')) for r in rows_g if isinstance(r.get('ms'), (int, float))]
            terminal = item['success'] + item['failed']
            item['successRate'] = round(item['success'] / terminal, 4) if terminal else 0
            item['medianMs'] = int(statistics.median(ms)) if ms else None
            item['p95Ms'] = _pct(ms, 0.95)
            item['inputTokens'] = sum((int(r.get('inTokens') or 0) for r in rows_g))
            item['outputTokens'] = sum((int(r.get('outTokens') or 0) for r in rows_g))
            item['cachedTokens'] = sum((int(r.get('cachedTokens') or 0) for r in rows_g))
            metrics.append(item)
        metrics.sort(key=lambda m: (-m['calls'], m['provider'], m['purpose']))
        return {'ok': True, 'now': int(time.time()), 'windowLabel': '%s（%d 次）' % (self.WINDOW_LABEL, len(rows)), 'routes': self.routes(), 'metrics': metrics}

class FurinaService:

    def __init__(self, trace_store, model_cfg, chat, load_day, today, state_loader, segment_func, generation_func, promises_func, context_chars, memory_chars):
        self.trace_store = trace_store
        self._cfg_source = model_cfg if callable(model_cfg) else lambda: model_cfg
        self.chat = chat
        self.load_day = load_day
        self.today = today
        self.state_loader = state_loader
        self.segment_func = segment_func
        self.generation_func = generation_func
        self.promises_func = promises_func
        self.context_chars = context_chars
        self.memory_chars = memory_chars

    def get(self):
        date = self.today()
        day = self.load_day(date) or {}
        timeline = list(day.get('timeline') or [])
        now = time.localtime()
        now_minutes = now.tm_hour * 60 + now.tm_min
        current = None
        next_segment = None
        for index, segment in enumerate(timeline):
            start, end = (segment.get('from'), segment.get('to'))
            try:
                h, m = str(start).split(':')
                start_min = int(h) * 60 + int(m)
                h, m = str(end).split(':')
                end_min = int(h) * 60 + int(m)
            except Exception:
                continue
            if start_min <= now_minutes < end_min:
                current = segment
                if index + 1 < len(timeline):
                    next_segment = timeline[index + 1]
                break
            if start_min > now_minutes:
                next_segment = segment
                break
        recent = []
        last_id = None
        try:
            picked = []
            for call in self.trace_store.list_calls(limit=40, provider='deepseek', chat=self.chat).get('calls', []):
                purpose = str(call.get('purpose') or '')
                if purpose in ('compact', 'promise', 'classify', 'decide', 'nudge', 'video_kw', 'photo_prompt', 'life_day', 'life_month', 'life_plan'):
                    continue
                picked.append(call)
            recent = picked[:10]
            last_id = recent[0]['id'] if recent else None
        except Exception:
            recent = []
        return {'ok': True, 'now': int(time.time()), 'chat': self.chat, 'state': self.state_loader() or {}, 'weather': day.get('weather') or {}, 'period': str(day.get('period') or ''), 'currentSegment': current, 'nextSegment': next_segment, 'date': date, 'timelineCount': len(timeline), 'generation': self.generation_func(), 'promises': self.promises_func() or [], 'primary': {'provider': 'deepseek', 'model': str(self._cfg_source().get('model') or ''), 'effort': str(self._cfg_source().get('thinking') or 'low').lower(), 'lastCallId': last_id, 'contextMessages': int(self.context_chars() or 0), 'memoryChars': int(self.memory_chars() or 0)}, 'recentCalls': recent}

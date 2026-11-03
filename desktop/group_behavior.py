import math
__all__ = ['decide_group', 'classify_note', 'NOTE_NEUTRAL', 'NOTE_SERIOUS', 'NOTE_CORRECTION', 'NOTE_BANTER']
NOTE_NEUTRAL = '普通群聊：自然短句，接得上就接，接不上就收住，不必每句都怼。'
NOTE_SERIOUS = '这是认真问题：先直接回答重点，需要时补一句，不玩梗。'
NOTE_CORRECTION = '对方提出纠正：先核对事实，自己错了就简短承认；对方说得不对也可以平等说明，不硬抬杠。'
NOTE_BANTER = '普通互损：可以短促接梗，但不强行每句都怼，接完就收。'
_SERIOUS_TYPES = frozenset(('serious', 'question', 'fact', 'help', 'ask'))
_BANTER_TYPES = frozenset(('banter', 'tease', 'joke', 'playful'))

def classify_note(question_type='', banter=False, correction=False):
    if correction:
        return ('correction', NOTE_CORRECTION)
    kind = str(question_type or '').strip().lower()
    if kind in _SERIOUS_TYPES:
        return ('serious', NOTE_SERIOUS)
    if banter or kind in _BANTER_TYPES:
        return ('banter', NOTE_BANTER)
    return ('neutral', NOTE_NEUTRAL)

def _number(value, default=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not math.isfinite(float(value)):
        return default
    return float(value)

def _gate(trigger, probability, random_value, rng):
    if trigger is not None:
        return bool(trigger)
    try:
        chance = float(probability)
    except (TypeError, ValueError):
        chance = 1.0
    if not math.isfinite(chance):
        chance = 1.0
    chance = max(0.0, min(1.0, chance))
    draw = random_value
    if draw is None:
        if rng is not None and callable(getattr(rng, 'random', None)):
            try:
                draw = rng.random()
            except Exception:
                draw = 0.0
        else:
            draw = 0.0
    draw = _number(draw, 0.0)
    return draw < chance

def decide_group(trigger, at_self=False, followup_active=False, followup_renewable=False, cooldown_remaining=0.0, burst_count=0, last_bot_at=0.0, now=0.0, question_type='', banter=False, correction=False, *, is_group=True, burst_limit=3, topic_window=90.0, probability=1.0, random_value=None, rng=None):
    note_kind, note = classify_note(question_type, banter, correction)
    try:
        limit = int(burst_limit)
    except (TypeError, ValueError):
        limit = 3
    limit = max(1, limit)
    cooldown = _number(cooldown_remaining, 0.0)
    bursts = _number(burst_count, 0.0)
    window = max(0.0, _number(topic_window, 90.0))
    last_at = _number(last_bot_at, 0.0)
    current = _number(now, 0.0)
    wanted = _gate(trigger, probability, random_value, rng)

    def decision(will_reply, reason_code, reason):
        return {'trigger': bool(will_reply), 'reasonCode': reason_code, 'reason': reason, 'noteKind': note_kind, 'note': note}
    if not is_group:
        return decision(wanted, 'private_unchanged', '私聊不受群聊节奏策略抑制')
    if at_self:
        return decision(True, 'explicit_at', '消息明确@我，直接回复')
    if not wanted and (not followup_active):
        return decision(False, 'not_selected', '本轮基础判断未触发，保持安静')
    if followup_active:
        if not followup_renewable:
            return decision(False, 'followup_exhausted', '上一条追问没有回应，不再自我续跟随')
        if last_at > 0 and current > last_at and (current - last_at >= window):
            return decision(False, 'topic_cutoff', '距离上一句已过去较久，话题自然收住')
        return decision(True, 'followup_allowed', '明确延续未完成的追问，允许一次短回复')
    if cooldown > 0:
        return decision(False, 'cooldown', '群内冷却还剩约%d秒，先不插话' % max(1, int(math.ceil(cooldown))))
    if bursts >= limit:
        return decision(False, 'burst_limit', '本轮已连续发%d条，先收住' % int(bursts))
    return decision(True, 'reply', '正常接话')

import base64
import os
import sys
import time
_HERE = os.path.dirname(os.path.abspath(__file__))
_DESKTOP = os.path.dirname(_HERE)
for _p in (_HERE, _DESKTOP):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import qwen_runtime
HINT = '\n\n【人物识别】只按用户明确配置的公开角色设定识别画面；认不出就只描述外观（发色、穿着），不要猜测或编造名字。'
PROMPT = '下面是一个视频的若干张截图拼图，每张是 3×3 九宫格，每格左上角有绝对时间戳（t=分:秒）。视频按时间顺序切成 5 秒一段，每段一张图。\n\n请用中文写一段这个视频的内容描述，要求：\n· 500 字以内，一段话，不要分点、不要小标题\n· 【必须】每一张图都认真看完，不要只看前几张就开始写\n· 注意图里的小字、字幕、界面文字 —— 这些往往是关键信息，必须读出来\n· 按时间顺序讲发生了什么，前因后果连贯，不要逐格罗列\n· 不要用「这个视频」「本视频」开头，直接说内容\n· 不要评价好坏，只描述' + HINT
PROMPT_WITH_VOICE = '下面是一个视频的语音转写，以及若干张截图拼图（每张 3×3，每格左上角有绝对时间戳，视频按 5 秒一段切分）。\n\n【语音转写】\n%s\n\n请用中文写一段这个视频的内容描述，要求：\n· 500 字以内，一段话，不要分点、不要小标题\n· 【必须】每一张图都认真看完，不要只看前几张就开始写\n· 注意图里的小字、字幕、界面文字 —— 这些往往是关键信息，必须读出来\n· 按时间顺序讲，前因后果连贯，不要逐段复述语音\n· 语音内容为主，画面描述用来补充场景\n· 不要用「这个视频」「本视频」开头，直接说内容\n· 不要评价好坏，只描述' + HINT

def _b64(path):
    with open(path, 'rb') as f:
        return base64.b64encode(f.read()).decode()

def summarize(grid_paths, transcript=None, max_tokens=1000) -> dict:
    if not grid_paths:
        raise RuntimeError('没有图，拒绝调用（空输入会让模型编造内容）')
    prompt = PROMPT_WITH_VOICE % (transcript or '（无）') if transcript else PROMPT
    content = [{'type': 'text', 'text': prompt}]
    for p in grid_paths:
        content.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + _b64(p)}})
    r = qwen_runtime.vision_chat(messages=[{'role': 'user', 'content': content}], purpose='video_summary', max_tokens=max_tokens, timeout=180, return_meta=True)
    usage = r.get('usage') or {}
    return {'text': str(r.get('content') or '').strip(), 'usage': usage, 'cost_cny': 0.0, 'secs': float(r.get('ms') or 0) / 1000.0, 'finish': r.get('finish'), 'provider': r.get('provider'), 'model': r.get('model')}
if __name__ == '__main__':
    import sys
    import os
    import frames as F
    v = ''
    for a in sys.argv[1:]:
        if os.path.isfile(a):
            v = a
            break
    print('视频:', v)
    fr = F.process_video(v)
    print('时长 %.1fs → %d 张九宫格' % (fr['duration'], len(fr['grids'])))
    paths = [g['sheet'] for g in fr['grids']]
    r = summarize(paths)
    print('\n耗时 %.2fs   输入 %d tok  输出 %d tok   成本 ¥%.4f' % (r['secs'], r['usage'].get('prompt_tokens', 0), r['usage'].get('completion_tokens', 0), r['cost_cny']))
    print('finish:', r['finish'])
    print('=' * 60)
    print(r['text'])

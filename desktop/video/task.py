import json
import os
import random
import re
import sys
import time
_HERE = os.path.dirname(os.path.abspath(__file__))
_DESKTOP = os.path.dirname(_HERE)
for _p in (_HERE, _DESKTOP):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import aux_runtime
import bili
import frames
import pipeline as P
MAX_CANDIDATES = 6
FALLBACK_KEYWORDS = ['搞笑 短片', '可爱 动物', '沙雕 日常', '治愈 动画']

def _aux_text(prompt, max_tokens=200, purpose='video_pick'):
    return aux_runtime.text_chat(messages=[{'role': 'user', 'content': prompt}], purpose=purpose, max_tokens=max_tokens, timeout=90)

def ask_her_to_pick(cands, keyword):
    listing = '\n'.join(('%d. %s（%d 秒，%d 播放）' % (i + 1, c['title'][:40], c['secs'], c['play']) for i, c in enumerate(cands)))
    prompt = '你刚在B站搜了「%s」，下面是搜出来的视频：\n\n%s\n\n选一个你最想看的。只回答一个数字序号（1~%d），不要别的字。' % (keyword, listing, len(cands))
    try:
        txt = str(_aux_text(prompt, max_tokens=16, purpose='video_pick') or '').strip()
        m = re.search('\\d+', txt)
        if m:
            idx = int(m.group()) - 1
            if 0 <= idx < len(cands):
                return (cands[idx], {'raw': txt, 'how': 'llm'})
    except Exception as e:
        pass
    return (random.choice(cands), {'raw': '', 'how': 'random'})

def judge_share(title: str, summary: str) -> dict:
    prompt = '你刚在B站看完一个视频，正在犹豫要不要发给他看。\n\n标题：%s\n你看到的内容：%s\n\n发之前先问自己一句：这东西拿得出手吗？\n\n你见过的戏、看过的场面太多了。有些东西你根本不会往他那儿转——\n不是因为它「不健康」，是因为它**太掉价**：擦边的、low的、\n让人替它尴尬的、看完只想说「这什么玩意儿」的。\n这种东西你转过去，丢的是你自己的脸。\n\n但也有另一种情况：东西本身一般，可你知道他会笑、会喜欢、\n或者会正好戳中他。那种就值得发。\n\n只回答两个字：发 或 不发' % (title or '', (summary or '')[:1200])
    t = time.time()
    try:
        txt = str(_aux_text(prompt, max_tokens=16, purpose='video_judge') or '').strip()
        ans = txt.replace(' ', '')
        worth = not ('不发' in ans or '不值得' in ans)
        return {'worth': worth, 'raw': txt, 'secs': time.time() - t}
    except Exception as e:
        return {'worth': True, 'raw': '', 'secs': time.time() - t, 'error': type(e).__name__}

def search_with_fallback(keyword):
    cands = bili.find(keyword, limit=MAX_CANDIDATES)
    if cands:
        return (cands, keyword)
    for kw in FALLBACK_KEYWORDS:
        try:
            cands = bili.find(kw, limit=MAX_CANDIDATES)
        except Exception:
            continue
        if cands:
            return (cands, kw)
        time.sleep(0.8)
    return ([], keyword)

def pick_and_watch(keyword: str, verbose: bool=True, max_tries: int=3) -> dict:
    t_all = time.time()
    out = {'ok': False, 'keyword': keyword, 'tries': 0, 'seen': []}
    watched = set()
    total_cost = 0.0
    FALLBACK = ['搞笑 短片', '可爱 动物', '沙雕 日常', '治愈 动画', '离谱 瞬间']
    fb_i = 0
    for attempt in range(max_tries):
        try:
            cands, used_kw = search_with_fallback(keyword)
        except Exception as e:
            out['error'] = '搜索失败: ' + str(e)[:100]
            break
        cands = [c for c in cands if c['bvid'] not in watched]
        if not cands:
            while fb_i < len(FALLBACK):
                try:
                    cands, used_kw = search_with_fallback(FALLBACK[fb_i])
                except Exception:
                    cands = []
                fb_i += 1
                cands = [c for c in cands if c['bvid'] not in watched]
                if cands:
                    break
            if not cands:
                out['error'] = '没有没看过的候选了'
                break
        out['tries'] = attempt + 1
        if verbose:
            print('  [第%d次] 搜到 %d 个新候选（用词：%s）' % (attempt + 1, len(cands), used_kw))
        chosen, how = ask_her_to_pick(cands, used_kw)
        watched.add(chosen['bvid'])
        out['seen'].append(chosen['bvid'])
        if verbose:
            print('      她选了: [%s] %s (%d秒)' % (chosen['bvid'], chosen['title'][:30], chosen['secs']))
        try:
            t = time.time()
            path = bili.download(chosen['bvid'])
            if verbose:
                print('      下载 %.1f MB / %.1fs' % (__import__('os').path.getsize(path) / 1024 / 1024, time.time() - t))
        except Exception as e:
            if verbose:
                print('      下载失败，换下一个: %s' % str(e)[:60])
            continue
        try:
            r = P.watch(path)
        except Exception as e:
            if verbose:
                print('      处理失败，换下一个: %s' % str(e)[:60])
            continue
        if r.get('error') or not r.get('summary'):
            if verbose:
                print('      总结失败，换下一个')
            continue
        total_cost += r.get('cost_cny') or 0.0
        verdict = judge_share(chosen['title'], r['summary'])
        out['last_verdict'] = verdict
        if verbose:
            print('      她觉得: %s（%s）' % ('值得发' if verdict['worth'] else '不值得，换一个', (verdict.get('raw') or '?')[:16]))
        if not verdict['worth']:
            continue
        _desc = ''
        _up = ''
        try:
            _vi = B.video_info(chosen['bvid'])
            _desc = (_vi.get('desc') or '').strip()
            _up = (_vi.get('up') or '').strip()
        except Exception:
            pass
        out.update({'ok': True, 'bvid': chosen['bvid'], 'title': chosen['title'], 'desc': _desc, 'up': _up, 'url': 'https://b23.tv/' + chosen['bvid'], 'summary': r['summary'], 'duration': r.get('duration', 0), 'n_grids': len(r.get('grids') or []), 'cost_cny': round(total_cost, 4), 'secs': round(time.time() - t_all, 1)})
        return out
    out['cost_cny'] = round(total_cost, 4)
    out['secs'] = round(time.time() - t_all, 1)
    if not out.get('error'):
        out['error'] = '看了 %d 个都不满意' % out['tries']
    return out
if __name__ == '__main__':
    import sys
    kw = sys.argv[1] if len(sys.argv) > 1 else '猫咪 搞笑'
    print('她说想搜:', kw)
    print('=' * 66)
    r = pick_and_watch(kw)
    print('=' * 66)
    if not r.get('ok'):
        print('放弃:', r.get('error'))
        raise SystemExit(1)
    print('标题   ', r['title'])
    print('链接   ', r['url'])
    print('时长   %.1f 秒   九宫格 %d 张' % (r['duration'], r['n_grids']))
    print('成本   ¥%.4f' % r['cost_cny'])
    print('总耗时 %.1f 秒' % r['secs'])
    print('=' * 66)
    print(r['summary'])

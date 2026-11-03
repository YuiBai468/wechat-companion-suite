from pathlib import Path
import re, json, hashlib, subprocess
root = Path(__file__).resolve().parent
base = root / 'dashboard-redesign.html'
html = base.read_text(encoding='utf-8')
health = (root / 'dashboard-v2-health.html').read_text(encoding='utf-8')
traces = (root / 'dashboard-v2-traces.html').read_text(encoding='utf-8')
traces = traces.replace('<div class="trace-content-tabs"', '<div id="traceCallError" class="v2-notice" role="status" hidden></div><div class="trace-content-tabs"', 1)
css = (root / 'dashboard-v2.css').read_text(encoding='utf-8')
js = (root / 'dashboard-v2.js').read_text(encoding='utf-8')
css += '\n' + (root / 'life-retry.css').read_text(encoding='utf-8')
js += '\n' + (root / 'life-retry.js').read_text(encoding='utf-8')
css += '\n' + (root / 'companion-upgrade.css').read_text(encoding='utf-8')
js += '\n' + (root / 'companion-upgrade.js').read_text(encoding='utf-8')
html = html.replace('<section class="view" id="view-life">', '<section class="view" id="view-life">' + (root / 'life-retry.html').read_text(encoding='utf-8'), 1)
html = html.replace('<section class="view active" id="view-overview">', (root / 'companion-upgrade-views.html').read_text(encoding='utf-8') + '\n<section class="view active" id="view-overview">', 1)
life_retry = (root / 'life-retry.html').read_text(encoding='utf-8')
html = html.replace(life_retry, life_retry + (root / 'companion-upgrade-planner.html').read_text(encoding='utf-8'), 1)
html = html.replace('<section class="view" id="view-model">', '<section class="view" id="view-model">' + (root / 'companion-upgrade-models.html').read_text(encoding='utf-8'), 1)
nav_icon = '<span class="ico"><svg viewBox="0 0 24 24" aria-hidden="true">{}</svg></span>'

def nav(view, label, paths):
    return '<button type="button" class="nav-item" data-act="nav" data-view="' + view + '">' + nav_icon.format(paths) + '<span class="txt">' + label + '</span></button>'
html = html.replace('<div class="nav-group-label">自主生活</div>', '<div class="nav-group-label">自主生活</div>' + nav('furina', '芙宁娜', '<path d="M12 3c-2 4-7 8-7 12a7 7 0 0 0 14 0c0-4-5-8-7-12Z"/><path d="M9 15a3 3 0 0 0 3 3M4 4l2 2m12-2-2 2"/>'), 1)
html = html.replace('<div class="nav-group-label">系统记录</div>', '<div class="nav-group-label">系统记录</div>' + nav('diagnostics', '消息诊断', '<path d="M4 4h16v12H9l-5 4V4Z"/><path d="M8 8h8m-8 4h5"/>'), 1)
html = html.replace('<div class="nav-group-label">对话与内容</div>', '<div class="nav-group-label">对话与内容</div>' + nav('memory', '记忆与承诺', '<path d="M5 3h12l2 2v16H5V3Zm3 5h8m-8 4h8m-8 4h5"/>'), 1)
html = re.sub('var views\\s*=\\s*\\[\\s*"overview"\\s*,\\s*"health"\\s*,', 'var views = ["overview", "furina", "memory", "diagnostics", "health",', html, count=1)
html = re.sub('return\\s*\\[\\s*"overview"\\s*,\\s*"health"\\s*,', 'return ["overview", "furina", "memory", "diagnostics", "health",', html, count=1)
html = re.sub('var PAGE_META\\s*=\\s*\\{', 'var PAGE_META={\nfurina:["芙宁娜","她此刻的生活状态、心里的事，还有主模型的真实记录。","FURINA · PERSONAL JOURNAL"],\nmemory:["记忆与承诺","把每个会话、每个人的事记清楚，也让旧事有去处。","MEMORY & PROMISES"],\ndiagnostics:["消息诊断","从收到消息到微信发送，查看每一步实际发生了什么。","MESSAGE DIAGNOSTICS"],', html, count=1)
html = html.replace('判断、日程、记忆与提示词', '判断、记忆与提示词').replace('芙芙对话', '芙宁娜对话')
html = html.replace('</style>', css + '\n</style>', 1)
html = html.replace('健康感知', '运动健康').replace('查看手环数据、指标新鲜度与身体状态。', '活动、睡眠与身体数据，按实际记录展示。')
html = html.replace('max = j.dynamicMax === undefined ? 3 : Number(j.dynamicMax);', 'max = j.dynamicMax === undefined ? 3 : ((j.dynamicMax === "不限" || j.dynamicMax === null) ? 0 : Number(j.dynamicMax));')
start = html.index('<section class="view" id="view-health">')
end = html.index('</section>', start)
old = html[start + len('<section class="view" id="view-health">'):end]
html = html[:start] + '<section class="view" id="view-health">' + health + '<details class="well-legacy"><summary>原始指标与实时记录</summary><div class="legacy-health-body">' + old + '</div></details>' + html[end:]
anchor = '<section class="view" id="view-model">'
html = html.replace(anchor, anchor + traces, 1)
html = html.replace('</body>', '<script>\n' + js + '\n</script>\n</body>', 1)
ids = re.findall('\\bid="([^"\\s]+)"', html.split('<script>')[0])
assert len(ids) == len(set(ids))
old_ids = re.findall('\\bid="([^"\\s]+)"', base.read_text(encoding='utf-8').split('<script>')[0])
assert not set(old_ids) - set(ids)
for i, script in enumerate(re.findall('<script>(.*?)</script>', html, re.S)):
    p = root / ('dashboard-v2-check-' + str(i) + '.js')
    p.write_text(script, encoding='utf-8')
    subprocess.run(['node', '--check', str(p)], check=True)
(root / 'dashboard-v2.html').write_text(html, encoding='utf-8')
print(json.dumps({'retainedIds': len(old_ids), 'newIds': len(ids) - len(old_ids), 'bytes': len(html.encode()), 'baselineSHA256': hashlib.sha256(base.read_bytes()).hexdigest()}))

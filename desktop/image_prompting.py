import hashlib
import re
DEFAULT_IDENTITY = '原神角色芙宁娜。保留官方参考图的清秀五官、银白色层次短发、蓝色内层发束与发尾，标志性的弧形翘发；一浅蓝一深蓝的异色瞳和水滴状瞳孔细节，以参考图为准。保持原作二次元人物比例与角色辨识度。服装、配饰和表情由当前情境决定。'
WRITER_SYSTEM = '你是角色日常照片的图像导演。把当前情境写成一段可以直接出图的中文画面描述。\n角色身份由后续的官方参考图和固定身份说明锁定，你负责把这一次画面拍好。\n只描写当前可见的地点、动作、服装、表情、光线与镜头。最近对话仅用来消除歧义，不把对话、台词、聊天框、字幕画进画面。\n场景和动作优先：保留情境明确提出的事情，不把每次都改成躺床、咖啡馆、撒娇或微笑，不自行添加亲密情节。没有要求改变衣着就选择符合地点和活动的日常穿着，不因“睡前、起床”几个词擅自脱衣。\n表情写出眼睛、眉毛或嘴角的一个具体变化，服从当时状态；不要套用参考立绘的张嘴大笑。\n自拍、镜面自拍、固定机位按给定的拍摄方案执行。普通自拍的镜头就是拍摄设备，画面里没有另一部手机；手臂可以在画外，不要用巨大前臂占满前景。固定机位时双臂自然参与当前动作，不伸向镜头。镜面自拍才允许出现合乎反射关系的设备。\n动作只选一个主体动作。可见的手有自然支撑或与物体正确接触，别堆叠复杂手势。人物面部和眼睛清晰，发丝有层次，衣料和背景有材质；保持参考图的精致二次元画风，不变成真人、3D塑料脸或厚重油画。\n选择一种符合场景的主光，保留肤色和白发的干净颜色，不默认昏黄滤镜；背景有少量明确地点线索，别比人物更抢眼。\n只输出一段约160~280字的画面描述，不要标题、标签、参数、解释或多个备选。'
DEFAULT_NEGATIVE = '角色五官漂移，普通白发路人，双眼同色，写实真人脸，3D塑料皮肤，过度磨皮，白发发黄，过曝，灰蒙滤镜，低清晰度，模糊面部，重复人物，重复肢体，畸形手，多余手指，巨大前臂遮挡主体，穿模，文字，字幕，聊天界面，拼图'
FORMAL_MARKER = '图2仅作礼服结构参考'

def is_formal(situation):
    return bool(re.search('礼服|正装|登台|谢幕|舞台|晚宴|剧照', situation or ''))

def camera_plan(situation):
    text = str(situation or '')
    if re.search('镜面|对镜|镜子.*自拍|自拍.*镜子', text):
        return '镜面自拍；反射、视线与设备位置一致，避免重复人像，人物脸不被设备挡住。'
    if '自拍' in text:
        return '自然手持自拍，视线看向镜头，肩部以上或胸像，手臂尽量留在画外，不画巨大前臂；设备本身在画外。'
    if re.search('全身|全景', text):
        return '固定机位的完整全身人像，人物只占画面高度约78%；帽顶上方、鞋底下方各留一段环境，双脚和鞋尖完整入画。双手参与情境动作，不伸向镜头。'
    variants = ('固定机位的半身生活照，略偏离中心，视线自然看向镜头，双手参与情境动作，不伸向镜头。', '近距离肩部肖像，机位接近眼睛高度，视线看向镜头，以脸部神情和一个环境细节为重点，双臂留在画外。', '摆放好的固定机位，腰部以上的自然生活照，人物与身边一个道具建立动作联系，不伸手去碰镜头。')
    index = int(hashlib.sha256(text.encode('utf-8')).hexdigest()[:8], 16) % len(variants)
    return variants[index]

def writer_user(situation, context=''):
    parts = []
    if context:
        parts.append('最近对话（仅供理解，禁止作为画面文字）：\n' + str(context)[-2400:])
    parts += ['当前情境：' + str(situation), '拍摄方案：' + camera_plan(situation)]
    return '\n\n'.join(parts)

def compose_prompt(raw, situation, cfg):
    raw = str(raw or '').strip()
    if raw.startswith('```'):
        raw = re.sub('^```[^\\n]*\\n|\\n```$', '', raw).strip()
    raw = re.sub('^(?:提示词|画面描述)[：:]\\s*', '', raw)
    if not raw:
        return ''
    identity = str(cfg.get('character') or DEFAULT_IDENTITY)
    guide = '图1仅作为同一位芙宁娜的脸部身份、发型、异色瞳与二次元画风参考；重新设计本次表情、姿势、服装和背景，不照搬立绘的张嘴大笑、手势与海报构图；衣着与配饰服从本次场景。'
    if is_formal(situation) and len(cfg.get('refs') or []) > 1:
        guide += FORMAL_MARKER + '，不复制图2的姿势或背景。'
    return '【参考使用】' + guide + '\n【角色身份】' + identity + '\n【本次画面】' + raw + '\n【拍摄关系】' + camera_plan(situation) + '\n【完成要求】单幅完整画面。眼睛和脸部线条清晰，构图有呼吸感，人物、光线与环境属于同一个场景。不要海报、拼图、字幕或水印。'

def reference_paths(cfg, prompt):
    refs = list(cfg.get('refs') or [])
    if cfg.get('referenceMode', 'identity-first') == 'all':
        return refs[:3]
    return refs[:2] if FORMAL_MARKER in str(prompt) else refs[:1]

def parameters(cfg):
    size = str(cfg.get('size') or '1152*1536').replace('x', '*')
    match = re.fullmatch('(\\d+)\\*(\\d+)', size)
    if not match:
        raise ValueError('Image size must be width*height')
    width, height = map(int, match.groups())
    if min(width, height) <= 0 or not 512 * 512 <= width * height <= 2048 * 2048 or max(width, height) / min(width, height) > 8 or width % 16 or height % 16:
        raise ValueError('Unsupported image dimensions')
    params = {'size': size, 'n': 1, 'prompt_extend': bool(cfg.get('promptExtend', False)), 'watermark': False}
    if str(cfg.get('model') or 'qwen-image-3.0-pro').startswith('qwen-image-3.0'):
        negative = cfg.get('negativePrompt', DEFAULT_NEGATIVE)
        if negative:
            params['negative_prompt'] = str(negative)
    if cfg.get('seed') is not None:
        seed = int(cfg['seed'])
        if not 0 <= seed <= 2147483647:
            raise ValueError('Unsupported image seed')
        params['seed'] = seed
    return params

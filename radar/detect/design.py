"""外观与工艺特征：用家具设计词库从标题里提取风格、材质、造型、工艺、颜色、功能卖点，
再和全部家具基线对比，找出爆火/潜力商品明显偏多的设计特征。

词库是“英文标题写法 → 中文特征名”。一个商品可以命中同一维度的多个特征。
想补充词条直接改下面的 LEXICON 即可（正则，按单词边界匹配，不区分大小写）。
写法为 None 的只给主图识别用（标题里很少这样写，例如“方正直线”“细腿/高脚”）。
词库也是主图识别的词表（vision.py），改了词库，之后识别的图片会按新词表重看一遍。
2026-10 按监控范围（板材 / 实木 / 铁木的柜类、桌类、床架、置物架）重写：补上开放格、错落层板、壁挂、带轮、
可伸缩、升降台面、线缆管理、深咖 / 橡木色等，去掉沙发床垫用的云朵造型、翼背、深座、可躺、凉感、软包、记忆棉等。
"""
from __future__ import annotations

import re
from collections import Counter

# 维度顺序即报告里的展示顺序：外观和工艺在前，功能卖点在后
LEXICON: dict[str, list[tuple[str, str]]] = {
    "风格": [
        ("中古风", r"mid[- ]?century|\bmcm\b"),
        ("农舍风", r"farmhouse"),
        ("乡村复古", r"rustic"),
        ("工业风", r"industrial"),
        ("波西米亚", r"boho|bohemian"),
        ("日式侘寂", r"japandi|japanese|wabi[- ]?sabi"),
        ("北欧风", r"scandinavian|nordic"),
        ("极简", r"minimalis[tm]"),
        ("奶油风", r"cream style|french cream"),
        ("轻奢", r"\bglam\b|luxury|luxurious"),
        ("海岸风", r"coastal"),
        ("复古", r"vintage|retro|antique"),
        ("古典欧式", r"victorian|traditional|classic|french country"),
        ("夏克/传教士风", r"\bshaker\b|\bmission\b"),
        ("现代", r"\bmodern\b|contemporary"),
    ],
    "材质": [
        ("实木", r"solid wood|real wood|solid (?:oak|pine|acacia|walnut|rubberwood|mango)"),
        ("相思木/橡胶木/松木", r"acacia|rubberwood|rubber wood|\bpine\b|mango wood|paulownia"),
        ("竹", r"bamboo"),
        ("人造板", r"engineered wood|\bmdf\b|particle ?board|manufactured wood"),
        ("金属", r"\bmetal\b|\bsteel\b|\biron\b|aluminum"),
        ("藤/编织", r"rattan|wicker|\bcane\b|seagrass|woven"),
        ("皮革/仿皮", r"leather|\bpu\b|leatherette"),
        ("大理石纹", r"marble"),
        ("岩板", r"sintered stone|slate|\bstone\b"),
        ("玻璃", r"\bglass\b"),
        ("亚克力", r"acrylic"),
    ],
    "造型": [
        ("开放格", r"open (?:shel(?:f|ves|ving)|storage|cubb(?:y|ies)|compartments?)|cubbies|\bcube (?:storage|shelf|bookcase|organizer)"),
        ("错落/阶梯层板", r"staggered|asymmetr|ladder|zig[- ]?zag"),
        ("高柜/立式", r"\btall\b|tower|vertical"),
        ("窄小省空间", r"\bslim\b|\bnarrow\b|compact|small space|space[- ]saving"),
        ("L 形/U 形", r"\bl[- ]?shaped?\b|\bu[- ]?shaped?\b"),
        ("转角款", r"\bcorner\b"),
        ("壁挂/悬浮", r"wall[- ]mount(?:ed)?|floating|wall shel(?:f|ves)"),
        ("低矮落地", r"low[- ]profile|floor[- ]standing"),
        ("圆形/椭圆", r"\bround\b|circular|\boval\b"),
        ("弧形曲线", r"curved|\bcurve\b|crescent|half[- ]moon"),
        ("拱形", r"\barch(?:ed)?\b"),
        ("模块化组合", r"modular|stackable|combination"),
        ("单柱/郁金香底座", r"pedestal|tulip"),
        ("瀑布式", r"waterfall"),
        # 不加“方正直线”“框架式”“细腿”这类词：实测主图识别会给八九成商品都打上（方正直线 44/50、对照 32/40），
        # 说明不了什么，还挤掉了“锥形/外八腿”“玻璃门”这类具体特征（每张图每个维度最多 4 个）
    ],
    "工艺": [
        ("凹槽竖纹", r"fluted|reeded|ribbed"),
        ("条栅", r"slat(?:ted|s)?\b"),
        ("藤编/编织", r"rattan|cane|wicker|woven"),
        ("玻璃门", r"glass doors?"),
        ("谷仓门/推拉门", r"barn doors?|sliding doors?"),
        ("百叶门", r"louver(?:ed)?|shutter"),
        ("镜面", r"mirrored"),
        ("镂空/格栅", r"hollow(?:ed)?[- ]out|cut[- ]?out|lattice|fretwork"),
        ("雕刻", r"carved|carving"),
        ("自然边/做旧", r"live edge|distressed|reclaimed|weathered"),
        ("双色拼接", r"two[- ]tone"),
        ("金色金属件", r"\bgold\b|brass"),
        ("锥形/外八腿", r"tapered|splayed|hairpin"),
        ("雪橇床型", r"sled (?:base|legs?)|sleigh"),
        ("高光烤漆", r"high gloss|glossy|lacquer"),
    ],
    "颜色": [
        ("白色", r"\bwhite\b"),
        ("黑色", r"\bblack\b"),
        ("灰色", r"\bgr[ae]y\b"),
        ("灰褐/做旧灰", r"greige|gr[ae]y wash|driftwood|ash gr[ae]y|rustic gr[ae]y"),
        ("奶油/米色", r"\bbeige\b|\bcream\b|ivory|off[- ]white|oatmeal|\bsand\b"),
        ("驼色/焦糖", r"camel|caramel|cognac|\btan\b|taupe|mushroom"),
        ("棕色", r"\bbrown\b"),
        ("深咖色", r"espresso|chocolate|dark brown"),
        ("胡桃色", r"walnut"),
        ("橡木色", r"\boak\b"),
        ("原木色", r"natural|light wood|wood color"),
        ("樱桃木/红棕", r"cherry|mahogany"),
        ("绿色", r"green|sage|olive"),
        ("蓝色", r"\bblue\b|navy"),
        ("粉色", r"\bpink\b|blush"),
    ],
    "功能卖点": [
        ("带储物/抽屉", r"storage|drawers?"),
        ("LED 灯", r"\bled\b|light(?:s|ed)?\b"),
        ("充电/插座", r"charging|outlets?|\busb\b|power strip"),
        ("电动/可升降", r"sit[- ]stand|standing desk|height[- ]adjustable|adjustable height|electric (?:standing )?desk"),
        ("升降台面", r"lift[- ]?top"),
        ("可调层板", r"adjustable (?:shel(?:f|ves)|shelving)"),
        ("可伸缩/加长", r"extendable|expandable|extension|telescoping"),
        ("带轮可移动", r"wheels?|casters?|rolling|\bmobile\b"),
        ("带镜子", r"\bmirror\b|with mirror"),
        ("线缆管理", r"cable management|cord management|wire management|cable tray|cord hole"),
        ("带挂钩", r"hooks?"),
        ("带锁", r"lockable|with locks?\b|locking"),
        ("显示器架/键盘托", r"monitor (?:stand|shelf|riser)|keyboard tray"),
        ("配凳子", r"with (?:stool|bench|chair)"),
        # 按压开门只看标题：主图看不出来，实测主图识别把开放格（没有门）也当成“无把手”，给了 30/50 个商品
        ("按压开门/无把手", r"handle[- ]?free|handleless|push[- ]to[- ]open|push open"),
        ("可折叠", r"fold(?:able|ing)|collapsible"),
        ("旋转", r"swivel|rotating|360"),
        ("免安装/易安装", r"no assembly|tool[- ]free|easy assembly|quick assembly|assembly[- ]free"),
        ("人体工学", r"ergonomic"),
        ("防倾倒", r"anti[- ]tip|wall anchor"),
        ("防水/防潮", r"waterproof|water[- ]resistant|moisture[- ]proof"),
    ],
}

DESIGN_DIMENSIONS = ("风格", "材质", "造型", "工艺", "颜色", "功能卖点")  # 主图识别要读的维度
# 主图识别只认看得见的功能（抽屉、灯带、镜子、轮子、挂钩、插座口…）；按压开门、防倾倒、防水、线缆管理、可伸缩这类看不出来的只看标题
VISION_FUNCTIONS = ("带储物/抽屉", "LED 灯", "充电/插座", "带镜子", "带轮可移动", "带挂钩", "升降台面", "电动/可升降",
                    "显示器架/键盘托", "配凳子")
# 报告里对比的外观维度。材质只看“主材质”（亚马逊 Material 属性，见 materials.py），
# 不再用标题词库 / 主图标签另算一套，免得同一件事出现三种口径
APPEARANCE_DIMENSIONS = ("风格", "造型", "工艺", "颜色")
_COMPILED = {dim: [(label, re.compile(pattern, re.I)) for label, pattern in rules if pattern]
             for dim, rules in LEXICON.items()}
# 颜色以主图识别为准：标题里的颜色常是把可选变体都列出来（Walnut/Black/White），不代表商品本身。
# 有主图识别时这些维度只看主图，没有主图识别（没配 DeepSeek）时才看标题
IMAGE_FIRST_DIMENSIONS = ("颜色",)


# 主材质：板材 / 实木 / 铁木（金属 + 木）/ 金属。标题没写清楚的归为“木质（未注明）”或“未注明”。
MATERIAL_CLASSES = ("铁木（金属+木）", "实木", "板材", "木质（未注明）", "金属", "未注明")
_SOLID = re.compile(r"solid (?:wood|oak|pine|acacia|walnut|rubberwood|mango|teak|birch|maple)|real wood|hardwood|"
                    r"acacia|rubberwood|rubber wood|\bpine\b|mango wood|paulownia|bamboo|\bteak\b", re.I)
_PANEL = re.compile(r"engineered wood|\bmdf\b|particle ?board|manufactured wood|chipboard|melamine|laminate", re.I)
_WOOD = re.compile(r"\bwood(?:en)?\b|wood ?grain|walnut|\boak\b|farmhouse", re.I)
_METAL = re.compile(r"\bmetal\b|\bsteel\b|\biron\b|aluminum|wrought", re.I)


def main_material(title: str) -> str:
    title = title or ""
    solid, panel, wood, metal = (bool(rx.search(title)) for rx in (_SOLID, _PANEL, _WOOD, _METAL))
    if metal and (solid or panel or wood):
        return "铁木（金属+木）"
    if solid:
        return "实木"
    if panel:
        return "板材"
    if wood:
        return "木质（未注明）"
    if metal:
        return "金属"
    return "未注明"


def brand_key(row: dict) -> str:
    """同一品牌的多条链接算一个品牌；没有品牌的商品各算一个。"""
    return (row.get("brand") or "").strip().lower() or f"asin:{row.get('asin') or id(row)}"


def extract(title: str) -> dict[str, set[str]]:
    title = title or ""
    return {dim: {label for label, rx in rules if rx.search(title)} for dim, rules in _COMPILED.items()}


def shares(baseline: list[dict], focus: list[dict]) -> dict[str, dict[str, dict]]:
    """标题里每个特征在增长商品和全部头部商品中的占比（不筛选）；brands = 增长商品里有这个特征的品牌数。"""
    nf, nb = len(focus), len(baseline)
    if not nf or not nb:
        return {}
    feats_f = [extract(r.get("title", "")) for r in focus]
    feats_b = [extract(r.get("title", "")) for r in baseline]
    out: dict[str, dict[str, dict]] = {}
    for dim in LEXICON:
        cf = Counter(label for f in feats_f for label in f[dim])
        cb = Counter(label for f in feats_b for label in f[dim])
        brands = {label: len({brand_key(r) for r, f in zip(focus, feats_f) if label in f[dim]}) for label in cf}
        out[dim] = {label: {"label": label, "count": n, "share": round(n / nf, 3), "brands": brands[label],
                            "baseline_share": round(cb.get(label, 0) / nb, 3)} for label, n in cf.items()}
    return out


def compare(baseline: list[dict], focus: list[dict], *, per_dim: int = 5, min_count: int | None = None,
            min_share: float = 0.15, min_lift: float = 1.3) -> dict[str, list[dict]]:
    """每个维度里，增长商品中明显偏多的特征（按 提升倍数 × 占比 排序）。
    条件：至少 min_count 个增长商品的标题有、占比 ≥ min_share、占比是全部头部商品的 min_lift 倍以上（加 0.5 平滑）。"""
    nf, nb = len(focus), len(baseline)
    if nf < 3 or nb < 20:
        return {}
    feats_f = [extract(r.get("title", "")) for r in focus]
    feats_b = [extract(r.get("title", "")) for r in baseline]
    if min_count is None:
        min_count = 2 if nf < 15 else 3
    out: dict[str, list[dict]] = {}
    for dim in LEXICON:
        cf = Counter(label for f in feats_f for label in f[dim])
        cb = Counter(label for f in feats_b for label in f[dim])
        rows = []
        for label, count in cf.items():
            share, base_share = count / nf, cb.get(label, 0) / nb
            lift = ((count + 0.5) / (nf + 1)) / ((cb.get(label, 0) + 0.5) / (nb + 1))
            if count >= min_count and share >= min_share - 1e-9 and lift >= min_lift:
                examples = [r.get("asin") for r, f in zip(focus, feats_f) if label in f[dim]][:6]
                rows.append({"label": label, "count": count, "share": round(share, 3),
                             "baseline_share": round(base_share, 3), "lift": round(lift, 2),
                             "asins": examples})
        rows.sort(key=lambda r: -(r["lift"] * r["share"]))
        if rows:
            out[dim] = rows[:per_dim]
    return out

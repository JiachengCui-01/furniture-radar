"""外观与工艺特征：用家具设计词库从标题里提取风格、材质、造型、工艺、颜色、功能卖点，
再和全部家具基线对比，找出爆火/潜力商品明显偏多的设计特征。

词库是“英文标题写法 → 中文特征名”。一个商品可以命中同一维度的多个特征。
想补充词条直接改下面的 LEXICON 即可（正则，按单词边界匹配，不区分大小写）。
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
        ("现代", r"\bmodern\b|contemporary"),
    ],
    "材质": [
        ("实木", r"solid wood|real wood|solid (?:oak|pine|acacia|walnut|rubberwood|mango)"),
        ("相思木/橡胶木/松木", r"acacia|rubberwood|rubber wood|\bpine\b|mango wood|paulownia"),
        ("竹", r"bamboo"),
        ("人造板", r"engineered wood|\bmdf\b|particle ?board|manufactured wood"),
        ("金属", r"\bmetal\b|\bsteel\b|\biron\b|aluminum"),
        ("藤/编织", r"rattan|wicker|\bcane\b|seagrass|woven"),
        ("泰迪绒/羊羔绒", r"boucl[eé]|teddy|sherpa"),
        ("丝绒/灯芯绒", r"velvet|corduroy"),
        ("棉麻布艺", r"linen|chenille|fabric"),
        ("皮革/仿皮", r"leather|\bpu\b|leatherette"),
        ("大理石纹", r"marble"),
        ("岩板", r"sintered stone|slate|\bstone\b"),
        ("玻璃", r"\bglass\b"),
        ("记忆棉", r"memory foam"),
        ("乳胶", r"latex"),
        ("独立弹簧/混合", r"hybrid|pocket(?:ed)? (?:coil|spring)|innerspring"),
    ],
    "造型": [
        ("圆形/椭圆", r"\bround\b|circular|\boval\b"),
        ("弧形曲线", r"curved|\bcurve\b|crescent|half[- ]moon"),
        ("拱形", r"\barch(?:ed)?\b"),
        ("云朵造型", r"\bcloud\b"),
        ("模块化组合", r"modular|sectional"),
        ("L 形/U 形", r"\bl[- ]?shaped?\b|\bu[- ]?shaped?\b|corner"),
        ("低矮落地", r"low[- ]profile|floor (?:sofa|couch|bed|chair)"),
        ("翼背/高背", r"wingback|high back|tall back"),
        ("深座/超大", r"oversized|deep seat|extra (?:large|wide)"),
        ("窄小省空间", r"\bslim\b|\bnarrow\b|compact|small space|space[- ]saving"),
        ("单柱/郁金香底座", r"pedestal|tulip"),
        ("瀑布式", r"waterfall"),
    ],
    "工艺": [
        ("凹槽竖纹", r"fluted|reeded|ribbed"),
        ("竖条拉扣", r"channel[- ]tufted"),
        ("拉扣软包", r"(?<!channel )(?<!channel-)tufted|button"),
        ("软包", r"upholster|padded"),
        ("藤编/编织", r"rattan|cane|wicker|woven"),
        ("条栅", r"slat(?:ted|s)?\b"),
        ("雕刻", r"carved|carving"),
        ("自然边/做旧", r"live edge|distressed|reclaimed|weathered"),
        ("双色拼接", r"two[- ]tone"),
        ("金色金属件", r"\bgold\b|brass"),
        ("锥形/外八腿", r"tapered|splayed|hairpin"),
        ("雪橇脚", r"sled (?:base|legs?)"),
        ("铆钉装饰", r"nailhead"),
        ("高光烤漆", r"high gloss|glossy|lacquer"),
        ("玻璃门", r"glass doors?"),
    ],
    "颜色": [
        ("白色", r"\bwhite\b"),
        ("黑色", r"\bblack\b"),
        ("灰色", r"\bgr[ae]y\b"),
        ("奶油/米色", r"\bbeige\b|\bcream\b|ivory|off[- ]white|oatmeal|\bsand\b"),
        ("驼色/焦糖", r"camel|caramel|cognac|\btan\b|taupe|mushroom"),
        ("棕色", r"\bbrown\b|espresso|chocolate"),
        ("胡桃色", r"walnut"),
        ("原木色", r"\boak\b|natural|light wood"),
        ("绿色", r"green|sage|olive"),
        ("蓝色", r"\bblue\b|navy"),
        ("粉色", r"\bpink\b|blush"),
    ],
    "功能卖点": [
        ("带储物/抽屉", r"storage|drawers?"),
        ("LED 灯", r"\bled\b|light(?:s|ed)?\b"),
        ("充电/插座", r"charging|outlets?|\busb\b|power strip"),
        ("可升降/可调", r"adjustable|sit[- ]stand|standing desk|height"),
        ("可折叠", r"fold(?:able|ing)|collapsible"),
        ("可变形/沙发床", r"convertible|sleeper|pull[- ]out|futon"),
        ("可躺", r"reclin"),
        ("旋转", r"swivel|rotating|360"),
        ("免安装/易安装", r"no assembly|tool[- ]free|easy assembly|quick assembly|assembly[- ]free"),
        ("静音", r"noise[- ]free|silent"),
        ("人体工学", r"ergonomic|lumbar"),
        ("防倾倒", r"anti[- ]tip|wall anchor"),
        ("可拆洗/防水", r"washable|removable cover|waterproof|water[- ]resistant"),
        ("凉感", r"cooling|\bgel\b"),
    ],
}

DESIGN_DIMENSIONS = ("风格", "材质", "造型", "工艺", "颜色")  # 外观和工艺
_COMPILED = {dim: [(label, re.compile(pattern, re.I)) for label, pattern in rules]
             for dim, rules in LEXICON.items()}


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


def extract(title: str) -> dict[str, set[str]]:
    title = title or ""
    return {dim: {label for label, rx in rules if rx.search(title)} for dim, rules in _COMPILED.items()}


def compare(baseline: list[dict], focus: list[dict], *, per_dim: int = 5) -> dict[str, list[dict]]:
    """每个维度里，爆火/潜力中明显偏多的特征（按 提升倍数 × 占比 排序）。"""
    nf, nb = len(focus), len(baseline)
    if nf < 3 or nb < 20:
        return {}
    feats_f = [extract(r.get("title", "")) for r in focus]
    feats_b = [extract(r.get("title", "")) for r in baseline]
    min_count = 2 if nf < 15 else 3
    out: dict[str, list[dict]] = {}
    for dim in LEXICON:
        cf = Counter(label for f in feats_f for label in f[dim])
        cb = Counter(label for f in feats_b for label in f[dim])
        rows = []
        for label, count in cf.items():
            share, base_share = count / nf, cb.get(label, 0) / nb
            lift = ((count + 0.5) / (nf + 1)) / ((cb.get(label, 0) + 0.5) / (nb + 1))
            if count >= min_count and share >= 0.15 and lift >= 1.3:
                examples = [r.get("asin") for r, f in zip(focus, feats_f) if label in f[dim]][:6]
                rows.append({"label": label, "count": count, "share": round(share, 3),
                             "baseline_share": round(base_share, 3), "lift": round(lift, 2),
                             "asins": examples})
        rows.sort(key=lambda r: -(r["lift"] * r["share"]))
        if rows:
            out[dim] = rows[:per_dim]
    return out

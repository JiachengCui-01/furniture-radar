"""报告里列哪些商品：判定照旧，列出时去掉简单款、合并相近款，让有限的位置多放不同的、有特色的商品。

特色 = 主图识别和标题里看得出的、这个品类基本结构以外的设计：工艺（凹槽竖纹、藤编、谷仓门…）、
不算基本结构的造型（圆形、拱形、L 形、悬浮、锥形腿…）、看得见的功能（灯带、插座、升降台面、电子壁炉、轮子…）、特殊材质（藤编、大理石纹、岩板、亚克力）。
简单款 = 主图识别过、而主图和标题都没有任何特色的（例：开放格收纳柜、金属平台床架、普通三层边几）：数据达标也不列。
双色拼接、高光烤漆、做旧、金色金属件只是表面处理，只有这些的也算简单款。
造型和功能还要看子类目：这个子类目里主图识别过的商品多数（≥ common_share）都有的，是这个品类的基本结构，不算它的特色
（衣帽架的挂钩、单柱底座，梳妆台的镜子和灯，电动升降桌的升降）。工艺和特殊材质再常见也算设计，不按子类目扣。
没有主图识别的不判简单款（只看标题会误伤：标题没写的灯芯草坐面、人字纹床头板都算不出特色），照常列出。
相近款 = 同一个子类目里，和已经列出的某一款长得差不多的：主图特征（造型 / 工艺 / 功能 + 主材质）重合度和标题用词重合度的平均 ≥ similar_min。
只列排在前面（势头更好）的那一款，其余记在它的卡片上（“相近款 N 个”）。
只影响列出：KPI 数字、卡片、钉钉和本期要点里点名的商品；简单款报告里哪里都不放图和品名（小窗、与上期对比、开发方向、全部追踪商品表），
只写个数。外观与工艺依据、开发方向的统计仍用全部增长商品（市场证据不因为长得普通就不算）。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .. import vision
from .design import extract

BASIC_SHAPES = frozenset({"开放格", "高柜/立式", "窄小省空间", "低矮落地", "模块化组合"})  # 这些柜架类本来就是这样，不算特色
PLAIN_FUNCTIONS = frozenset({"带储物/抽屉"})
SPECIAL_MATERIALS = frozenset({"藤/编织", "大理石纹", "岩板", "亚克力"})
# 只是表面处理、不改结构的工艺：卡片上照样写，但只有这些的仍算简单款
# （11-02 那期：只有“双色拼接”的黑色八斗柜、金属框吧台桌，只有“金色金属件”的平板门储物柜，只有“做旧”的掀盖储物箱）
FINISHES = frozenset({"双色拼接", "高光烤漆", "自然边/做旧", "金色金属件"})
# 标题里写的功能，只认看得见、改变结构的；免安装、防倾倒、防水、人体工学这类说明不算特色
TITLE_FUNCTIONS = frozenset({"LED 灯", "充电/插座", "电动/可升降", "升降台面", "可伸缩/加长", "带轮可移动", "带镜子",
                             "显示器架/键盘托", "配凳子", "可折叠", "旋转", "电子壁炉",
                             # 结构上的设计（2026-11-07 加，只看标题）
                             "壁床/隐形床", "高架床/上下铺", "带拖床", "翻板/折叠台面", "酒架/杯架", "暗格/隐藏储物",
                             "床头储物/书架", "文件抽屉"})
FEATURE_ORDER = ("工艺", "造型", "功能卖点", "材质")
STRUCTURAL_DIMS = ("造型", "功能卖点")  # 按子类目扣“基本结构”的维度
# 决定款式的功能：同一个子类目里都带它的，不管别的细节，看上去就是同一类款（电子壁炉电视柜、升降台面茶几 / 储物凳）。
# 不放“电动/可升降”：标题写 standing desk 的小号带轮笔记本桌也算，和电动升降大桌长得并不像
CONCEPTS = frozenset({"电子壁炉", "升降台面"})

_WORD = re.compile(r"[a-z]{3,}")
# 标题用词比较时不算的词：尺寸、房间、通用材质 / 颜色和连接词，留下的多是品类和结构（lift top、fireplace、murphy…）
_STOP = frozenset("""with for and the of in to inch inches size set tier piece pcs pack home living room bedroom office
kitchen modern small large storage wood wooden metal black white brown gray grey natural rustic farmhouse your up tvs
oak walnut espresso beige cream finish style design new""".split())


def _vision_tags(state: dict, asin: str) -> dict | None:
    v = vision.of(state, asin)
    return v.get("tags") if v else None


def _recognized(state: dict, asin: str) -> bool:
    """按新提示词识别过主图（vision.PROTOCOL）。旧提示词的结果只留下了标题也写了的特征，说不清“没有特色”，
    不拿来判简单款、也不拿来算子类目的基本结构（照“没有主图识别”处理）。"""
    raw = vision.raw_of(state, asin)
    return bool(raw) and not vision.legacy(raw)


def _node(item: dict) -> str:
    return item.get("node") or item.get("node_cn") or ""


def _by_dim(item: dict, state: dict) -> list[tuple[str, str]]:
    """(维度, 特色)：主图在前、标题补充，按 工艺 → 造型 → 功能 → 材质 的顺序、去重。"""
    tags = _vision_tags(state, item["asin"]) or {}
    title = extract(item.get("title") or "")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(dim: str, labels) -> None:
        for label in labels:
            keep = (dim == "工艺" or (dim == "造型" and label not in BASIC_SHAPES)
                    or (dim == "功能卖点" and label not in PLAIN_FUNCTIONS)
                    or (dim == "材质" and label in SPECIAL_MATERIALS))
            if keep and label not in seen:
                seen.add(label)
                out.append((dim, label))

    for dim in FEATURE_ORDER:
        add(dim, tags.get(dim) or [])
        if dim == "材质" and tags:
            continue  # 材质以主图为准（同颜色）：标题里的 stone / slate / marble 常是底座配重或颜色名，11-02 一个普通铁艺衣帽架因此算有“岩板”
        labels = sorted(title.get(dim) or ())
        add(dim, [x for x in labels if dim != "功能卖点" or x in TITLE_FUNCTIONS])
    return out


def features(item: dict, state: dict, common: dict | None = None) -> list[str]:
    """这个商品的特色（主图在前、标题补充）。给了 common（common_features）时，去掉它所在子类目的基本结构。"""
    drop = (common or {}).get(_node(item)) or frozenset()
    return [label for dim, label in _by_dim(item, state) if not (dim in STRUCTURAL_DIMS and label in drop)]


def common_features(rows: list[dict], state: dict, cfg: dict) -> dict[str, frozenset]:
    """每个子类目的基本结构：rows（本期商品 + 月度名单商品）里主图识别过的，≥ common_share 都有的造型 / 功能。
    主图识别过的不到 common_min_items 个的子类目不算（样本太少）。11-02 那期：衣帽架 挂钩 100%、单柱底座 57%，
    梳妆台 镜子、LED 灯 100%、插座 65%，计算机工作站 电动升降 79%，床头柜 插座 70%。"""
    c = (cfg.get("report") or {}).get("showcase") or {}
    share, min_n = float(c.get("common_share", 0.5)), int(c.get("common_min_items", 10))
    if share <= 0:
        return {}
    by_node: dict[str, dict[str, set[str]]] = defaultdict(dict)
    for row in rows:
        if row["asin"] in by_node[_node(row)] or not _recognized(state, row["asin"]):
            continue
        by_node[_node(row)][row["asin"]] = {label for dim, label in _by_dim(row, state) if dim in STRUCTURAL_DIMS}
    out = {}
    for node, feats in by_node.items():
        if len(feats) >= min_n:
            counts = Counter(label for labels in feats.values() for label in labels)
            out[node] = frozenset(label for label, k in counts.items() if k / len(feats) >= share - 1e-9)
    return out


def is_plain(item: dict, state: dict, common: dict | None = None) -> bool:
    """简单款：主图识别过，而主图和标题都没有任何特色（只有表面处理 FINISHES、或只有这个子类目的基本结构的也算）。
    没有主图识别的、只有旧提示词识别结果的不算（照常列出，见 _recognized）。"""
    return _recognized(state, item["asin"]) and all(f in FINISHES for f in features(item, state, common))


def _shape(item: dict, state: dict) -> set[str]:
    """主图的造型 / 工艺 / 功能 + 特色（含标题里的），不含主材质；都没有时为空（说不出长什么样）。"""
    tags = _vision_tags(state, item["asin"]) or {}
    out = {x for dim in ("造型", "工艺", "功能卖点") for x in tags.get(dim) or []}
    return out | set(features(item, state))


def _words(item: dict) -> set[str]:
    brand = set((item.get("brand") or "").lower().split())
    return {w for w in _WORD.findall((item.get("title") or "").lower()) if w not in _STOP and w not in brand}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def similarity(a: dict, b: dict, state: dict) -> float:
    """两个商品长得多像：主图特征（造型 / 工艺 / 功能 + 主材质）重合度和标题用词重合度的平均（0~1）；
    都带同一个决定款式的功能（CONCEPTS）的记 1；有一边说不出长什么样（没有主图识别、标题也没有特色）时只看标题用词。
    不同子类目的记 0。"""
    if (a.get("node") or a.get("node_cn")) != (b.get("node") or b.get("node_cn")):
        return 0.0
    if CONCEPTS & set(features(a, state)) & set(features(b, state)):
        return 1.0
    words = _jaccard(_words(a), _words(b))
    sa, sb = _shape(a, state), _shape(b, state)
    if not sa or not sb:  # 以前这时两边只剩主材质，同材质就算一半像：月度补判的儿童书架和玩具收纳柜并到了一起
        return words
    mat = lambda i: {i.get("material") or "未注明"}  # noqa: E731
    return (_jaccard(sa | mat(a), sb | mat(b)) + words) / 2


def curate(rows: list[dict], state: dict, cfg: dict, common: dict | None = None) -> dict:
    """按 rows 的顺序（各板块自己的排序）挑出要列的商品。返回
    {"shown": [...], "plain": [...], "similar": {列出的 ASIN: [并进来的商品…]}}；
    common：各子类目的基本结构（common_features）。关掉（report.showcase.enabled: false）时全部列出。"""
    c = (cfg.get("report") or {}).get("showcase") or {}
    if not c.get("enabled", True):
        return {"shown": list(rows), "plain": [], "similar": {}}
    hide_plain = bool(c.get("hide_plain", True))
    th = float(c.get("similar_min", 0.5))
    shown: list[dict] = []
    plain: list[dict] = []
    similar: dict[str, list[dict]] = {}
    for item in rows:
        if hide_plain and is_plain(item, state, common):
            plain.append(item)
            continue
        host = next((s for s in shown if th > 0 and any(similarity(item, x, state) >= th - 1e-9
                                                         for x in (s, *similar.get(s["asin"], [])))), None)
        if host is None:
            shown.append(item)
        else:
            similar.setdefault(host["asin"], []).append(item)
    return {"shown": shown, "plain": plain, "similar": similar}


def gallery(rows: list[dict], limit: int, per_node: int) -> list[dict]:
    """主体“本期增长商品”的图墙：rows（已去掉简单款、相近款，按月增量排）里每个子类目最多 per_node 个，取前 limit 个。"""
    out: list[dict] = []
    seen: dict[str, int] = {}
    for item in rows:
        node = item.get("node_cn") or item.get("node_name") or item.get("node") or ""
        if per_node and seen.get(node, 0) >= per_node:
            continue
        seen[node] = seen.get(node, 0) + 1
        out.append(item)
        if len(out) >= limit:
            break
    return out

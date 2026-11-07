"""主材质：每个商品一个类别，依次取 亚马逊商品详情的 Material 属性 → 标题里写明的 → AI 主图识别 → 标题里笼统的“木质”。

标题里写明材质的商品不到一半（实测 48% 完全没写，“engineered wood”几乎没人写），
所以对增长商品和持续热销对照组查一次商品详情。材质不会变，查过的永久缓存，
之后每期只查新出现的商品。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date

from . import log, vision
from .detect.design import MATERIAL_CLASSES, main_material
from .vendor import BudgetExhausted, Vendor

_OTHER = re.compile(r"glass|fabric|plastic|resin|leather|velvet|polyester|marble|stone|rattan|wicker", re.I)


def classify(raw: str) -> str | None:
    """亚马逊 Material 文本 → 铁木 / 实木 / 板材 / 木质（未注明）/ 金属 / 其他。"""
    if not raw or not raw.strip():
        return None
    label = main_material(raw)
    if label == "未注明":
        return "其他" if _OTHER.search(raw) else None
    return label


def from_overviews(overviews) -> str:
    """overviews 可能是 JSON 字符串或字典；取所有带 material 的键（Material、Frame Material、Top Material…）。"""
    if isinstance(overviews, str):
        try:
            overviews = json.loads(overviews)
        except ValueError:
            return ""
    if not isinstance(overviews, dict):
        return ""
    values = [str(v) for k, v in overviews.items() if "material" in str(k).lower() and v]
    return ", ".join(dict.fromkeys(values))


def remember(rec: dict, detail: dict, today: date) -> None:
    raw = from_overviews(detail.get("overviews"))
    rec["amazon_material"] = {"raw": raw[:120], "class": classify(raw), "checked": today.isoformat()}


_SPECIFIC = ("铁木（金属+木）", "实木", "板材", "金属")
VAGUE = ("木质（未注明）", "未注明")  # 不知道具体是哪种材质


def denominators(m: dict, label: str) -> tuple[int, int]:
    """compare 结果里一类材质的占比分母（增长, 对照）：具体材质 = 知道具体材质的商品数，木质（未注明）/ 未注明 = 全部。"""
    if label in VAGUE or "known_focus" not in m:
        return m.get("n_focus") or 0, m.get("n_reference") or 0
    return m["known_focus"], m["known_reference"]


def from_vision(rec: dict | None) -> str | None:
    """主图识别的材质 → 主材质类别。主图只说得清“是不是金属”：
    木纹看不出是实木还是板材（11-02 实测：主图说实木的 57 个，亚马逊 Material 也说实木的只有 6 个，26 个是板材），
    “金属 + 木”也不可靠（主图把拉手、铰链、升降五金也算金属：主图说铁木、亚马逊说板材的 12 个里只有 1 个真有金属框架）。
    所以只有金属、没有木质的算“金属”（和亚马逊一致 18/20），有木质的只算“木质（未注明）”。"""
    v = vision.normalized((rec or {}).get("vision") or {}, (rec or {}).get("title") or "")
    tags = set((v.get("tags") or {}).get("材质") or [])
    wood = bool(tags & {"木质", "竹"})
    if "金属" in tags and not wood:
        return "金属"
    return "木质（未注明）" if wood else None


def best(rec: dict | None, title: str = "") -> tuple[str, str]:
    """(材质类别, 来源)；来源为 "amazon" / "title" / "image"。"""
    amazon = (rec or {}).get("amazon_material") or {}
    if amazon.get("class"):
        return amazon["class"], "amazon"
    from_title = main_material(title or (rec or {}).get("title") or "")
    if from_title in _SPECIFIC:
        return from_title, "title"
    image = from_vision(rec)
    if image:
        return image, "image"
    return from_title, "title"


def lookup(vendor: Vendor, items: list[dict], state: dict, cfg: dict, today: date, limit: int) -> int:
    """给还没有材质记录的商品查一次商品详情。返回查询个数。"""
    done = 0
    for item in items:
        if done >= limit:
            break
        rec = state["asins"].get(item["asin"])
        if rec is None or rec.get("amazon_material"):
            continue
        try:
            reply = vendor.call("asin_detail", vendor.args("asin_detail", nested=False,
                                                           marketplace=cfg["marketplace"], asin=item["asin"]),
                                purpose="材质")
        except BudgetExhausted:
            log.warn("材质查询：预算用完")
            break
        done += 1
        if reply.ok and isinstance(reply.data, dict):
            remember(rec, reply.data, today)
        else:
            rec["amazon_material"] = {"raw": "", "class": None, "checked": today.isoformat()}
    return done


def compare(focus: list[dict], reference: list[dict], state: dict) -> dict:
    """增长商品 vs 持续热销对照组 的主材质构成。"""
    def classes(items):
        out = []
        for item in items:
            label, source = best(state["asins"].get(item["asin"]), item.get("title", ""))
            out.append((label, source))
        return out

    f, r = classes(focus), classes(reference)
    cf, cr = Counter(label for label, _ in f), Counter(label for label, _ in r)
    # 占比只在知道具体材质的商品里算：没查到的（木质（未注明）、未注明）不猜。以前都算在分母里、再拿主图猜的“实木”补，
    # 增长商品多半靠主图猜、对照组多半查过亚马逊，两边口径不同，11-02 那期“实木 32% vs 10%”就是这么来的
    kf = sum(n for label, n in cf.items() if label not in VAGUE)
    kr = sum(n for label, n in cr.items() if label not in VAGUE)
    rows = []
    for label in (*MATERIAL_CLASSES, "其他"):
        if not cf.get(label) and not cr.get(label):
            continue
        vague = label in VAGUE
        share = (cf.get(label, 0) / (len(f) if vague else kf)) if (len(f) if vague else kf) else 0
        ref_n = len(r) if vague else kr
        ref_share = cr.get(label, 0) / ref_n if ref_n else None
        rows.append({"label": label, "count": cf.get(label, 0), "share": round(share, 3),
                     "reference": cr.get(label, 0), "reference_share": None if ref_share is None else round(ref_share, 3)})
    rows.sort(key=lambda row: (row["label"] in ("未注明", "其他", "木质（未注明）"), -row["share"]))
    return {"n_focus": len(f), "n_reference": len(r), "rows": rows, "known_focus": kf, "known_reference": kr,
            # 两组各有几个用的是亚马逊 Material（其余看标题 / 主图）：查询有每期名额，月度名单补判的新商品要等查过才有
            "amazon_focus": sum(1 for _, s in f if s == "amazon"), "amazon_reference": sum(1 for _, s in r if s == "amazon"),
            "from_amazon": sum(1 for _, s in f + r if s == "amazon"),
            "from_image": sum(1 for _, s in f + r if s == "image")}

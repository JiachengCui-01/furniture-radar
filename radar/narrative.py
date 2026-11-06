"""“本期要点”的内容：外观与工艺趋势、开发方向、风险提醒都直接取计算好的数据（报告里画成图标卡片，见 report/render.py），
只有“开发建议”交给大模型（默认 DeepSeek，OpenAI 兼容接口）挑特征组合和参考商品、写一个短的外观细节。

模型不参与判定，输出是固定格式的短行，逐条核对：特征必须是本期“增长商品更多”的特征、参考商品必须是列出的爆火 / 潜力商品
而且自己就有这些特征，对不上的整条删掉。没有 key、调用失败或一条都没留下时，按同样的规则从数据里选（template_tips）。
"""
from __future__ import annotations

import json
import re

import httpx

from . import log, shipping
from .analyze import display_name
from .detect import directions

SYSTEM_PROMPT = """你是亚马逊美国站家具类目的产品设计顾问。下面是自动化系统已经计算好的本期监控结果（JSON）。
请写报告“本期要点”里的“开发建议”：2~3 条可以用到不同品类上的设计手法。每条一行，格式固定为
- <特征> + <特征>｜[<品牌> <简短中文品名>](<ASIN>)｜<外观细节>
严格遵守：
1. 参考商品只能从“突然爆火”“潜力”里选“可作参考”为 true 的商品；每条换一个商品。
   方括号里必须有品牌，ASIN 照抄这个商品的“ASIN”，例如 [Huuger 窄长玄关桌](B0H2XKVQ5L)。
2. <特征> 只能照抄这个商品“可用特征”里的词（都是本期增长商品比对照组更多的外观特征，见“共性特点”），
   每条 1~2 个，两个时用“ + ”连接；不要用别的词，不要自己造词。各条的特征组合不要重复，尽量覆盖不同的特征。
3. <外观细节> 只能取自这个商品的“外观要点”，挑能看出这一条特征在商品上怎么做的部分，压缩成不超过 16 个字的短语
   （如“正面满布竖向凹槽”），不得编造。
4. 讲设计手法，不要把“开发方向”里某个品类的画像再写一遍；不要写数字、网址、解释或任何别的文字。
5. 找不到符合第 1、2 条的组合就少写几条；一条都写不出时只输出“无”。"""


FACT_ITEMS = 6  # 本期要点给大模型看的突然爆火、潜力各前几个（钉钉列前 notify.max_items 个，都在这里面）
TIP_MAX = 3  # 开发建议最多几条
DETAIL_MAX = 18  # 开发建议里外观细节最多几个字


def build_facts(ctx: dict) -> dict:
    """给大模型写开发建议用的事实（只写开发建议，其余几块报告直接取数据）。"""
    labels = trend_labels(ctx["traits"])

    def brief(item: dict) -> dict:
        out = {
            "品牌": item["brand"], "品名": display_name(item["brand"], item["title"])[:60],
            "子类目": item["node_cn"] or item["node_name"], "价格": item["price"],
            "评分": item["rating"], "评论数": item["ratings"], "上架天数": item["age_days"], "标签": item["tags"],
            "可作参考": can_refer(item), "ASIN": item["asin"],
        }
        if item.get("look"):
            out["外观要点"] = item["look"]
        if item.get("features"):
            out["特色"] = item["features"][:4]
        if can_refer(item):  # 这个商品自己有的、本期增长商品更多的特征（parse_tips 按同一个条件核对）
            out["可用特征"] = [label for label in labels if has_feature(item, label)]
        if item.get("surge"):
            s = item["surge"]
            out["突然爆火"] = {"近期日均": round(s["recent_avg"], 1), "之前日均": round(s["base_avg"], 1),
                           "倍数": None if s["sales_ratio"] is None else round(s["sales_ratio"], 1),
                           "形态": s["kind"]}
        if item.get("potential"):
            out["潜力"] = item["potential"]
        if item["fake"]["signals"]:
            out["异常信号"] = [s["text"] for s in item["fake"]["signals"]]
        return out

    return {
        "数据截至": ctx["as_of"],
        "监控范围": "亚马逊美国站板材 / 实木 / 铁木为主的柜类、桌类、床架、置物架（不含沙发、床垫、椅子、衣帽架，也不含包装在亚马逊标准尺寸以内的小件）",
        # 只给列出的（去掉简单款、合并相近款，detect.showcase），本期要点点名的和报告里看到的一致
        "突然爆火": [brief(i) for i in listed(ctx, "surge")[:FACT_ITEMS]],
        "潜力": [brief(i) for i in listed(ctx, "potential")[:FACT_ITEMS]],
        "共性特点": design_facts(ctx["traits"]),
        # 报告里够格的子类目全部列出，本期要点只写增量最大的前 summary_max 个
        "开发方向": direction_facts(ctx["traits"])[: summary_limit(ctx["cfg"])],
    }


def listed(ctx: dict, key: str) -> list[dict]:
    """报告里列出的商品（ctx["curated"]，没有时退回板块全部）。"""
    cur = (ctx.get("curated") or {}).get(key)
    return cur["shown"] if cur else ctx["sections"].get(key) or []


def summary_limit(cfg: dict) -> int:
    return int(cfg["thresholds"]["directions"].get("summary_max") or 5)


def _evidence(f: dict, tr: dict) -> str:
    """一个外观特征的依据：标题统计 / 主图识别，写清楚和谁比。"""
    parts = []
    if f.get("title"):
        t = f["title"]
        parts.append(f"标题统计：增长商品 {t['share']:.0%}，全部头部商品 {t['baseline_share']:.0%}")
    if f.get("image"):
        i, v = f["image"], tr.get("vision") or {}
        ref = f"，持续热销对照组 {i['reference']}/{v['n_reference']}" if v.get("n_reference") else ""
        parts.append(f"主图识别：增长商品 {i['focus']}/{v.get('n_focus')}{ref}")
    return "；".join(parts)


def design_facts(tr: dict) -> dict:
    """外观与工艺优先，结构特征其次。"""
    n = tr.get("n_focus") or 0
    mats = tr.get("materials") or {}
    out = {
        "样本说明": f"增长商品 {n} 个（突然爆火 + 潜力 + 上升中）。标题统计对比全部头部商品 {tr.get('n_baseline')} 个；"
                f"主图识别看增长商品里势头最强的 {(tr.get('vision') or {}).get('n_focus') or 0} 个，"
                f"主材质看全部增长商品，二者都对比持续热销对照组 {mats.get('n_reference') or 0} 个",
        "主材质（每个商品一类，依次取亚马逊 Material 属性、标题、主图识别）": [
            f"{m['label']}：增长商品 {m['count']}/{mats.get('n_focus')}（{m['share']:.0%}）"
            + (f"，持续热销对照组 {m['reference']}/{mats['n_reference']}（{m['reference_share']:.0%}）"
               if m.get("reference_share") is not None else "")
            for m in mats.get("rows", []) if m["count"] or m["reference"]],
        "外观特征（增长商品更多）": {dim: [f"{f['label']}（{_evidence(f, tr)}）" for f in rows.get("more") or []]
                             for dim, rows in (tr.get("appearance") or {}).items() if rows.get("more")},
        "常见特征（增长商品多数有，但不比对照组多）": {
            dim: [f"{f['label']}（{_evidence(f, tr)}）" for f in rows.get("common") or []]
            for dim, rows in (tr.get("appearance") or {}).items() if rows.get("common")},
        "其他结构特征": [f"{f['dimension']}={f['value']}：增长商品 {f['share']:.0%}，全部头部商品 {f['baseline_share']:.0%}"
                   for f in tr.get("facts", [])[:3]],
        "备注": tr.get("note"),
    }
    return out


def direction_facts(tr: dict) -> list[dict]:
    """开发方向：支持品牌数、品牌均占比和同口径的头部占比分别列明。"""
    out = []
    for d in tr.get("directions") or []:
        n, nb = d["n"], d.get("n_brands", d["n"])
        cmp = d.get("compare") or {}
        if cmp.get("mode"):
            basis = (f"“比头部多”= 比同子类目头部商品 {cmp['n_head']} 个（{'标题 + 主图' if cmp['mode'] == 'image' else '只看标题'}）"
                     "的品牌均占比明显多；“基本款”= 品牌均占比至少一半、但不比头部多")
        else:
            basis = "同子类目头部已知品牌不足，没有对比；只列共同特征，不能声称比头部更多"

        def feats(tier: str) -> dict:
            return {dim.replace("卖点", ""): [
                f"{f['label']} {f['count']}/{nb} 个已知品牌支持"
                + (f"，品牌均占比 {f['share']:.0%}" if f.get("share") is not None else "")
                + (f"（头部品牌均占比 {f['head_share']:.0%}）" if f.get("head_share") is not None else "")
                for f in d["features"][dim][tier]] for dim in directions.DIMENSIONS if d["features"][dim][tier]}

        sales = d.get("sales") or {}
        row = {"子类目": d["node"],
               "依据": f"该子类目的增长商品 {n} 个、来自 {nb} 个品牌（突然爆火 {d['n_surge']} 个），"
                     f"双方先计算品牌内商品特征占比，再对已知品牌等权平均；品牌缺失不作为独立品牌证据；{basis}",
               "销售额": f"月销售额约 {directions.money_text(sales.get('monthly'))}，"
                      f"增量约 {directions.money_text(sales.get('increment'))}（比之前 28 天多的部分）",
               "增量": directions.money_text(sales.get("increment")),
               "画像": directions.headline(d),
               "主材质": [f"{m['label']} {m['count']}/{nb} 个品牌" for m in d["materials"]],
               "比头部多": feats("more"),
               "基本款": feats("base"),
               "价格区间": directions.price_text(d["price"]),
               "包装": shipping.summary_text(d.get("pkg")) or "没有数据",
               "参考商品": [{"品牌": r["brand"], "品名": r["name"][:60], "外观要点": r["look"], "ASIN": r["asin"]}
                        for r in d["refs"]]}
        out.append(row)
    return out


def top_design_labels(tr: dict, k: int = 4) -> list[str]:
    """增长商品最突出的几个外观特征名（钉钉卡片用），取每个维度排第一的。"""
    rows = [rs["more"][0]["label"] for dim, rs in (tr.get("appearance") or {}).items()
            if rs.get("more") and dim != "功能卖点"]
    return rows[:k]


def llm_summary(facts: dict, cfg: dict, api_key: str) -> str | None:
    """大模型写的开发建议（原文，parse_tips 再逐条核对）；没有 key 或调用失败返回 None。"""
    llm = cfg["llm"]
    enabled = str(llm.get("enabled", "auto")).lower()
    if enabled in ("false", "0", "no", "off") or not api_key:
        return None
    body = {
        "model": llm["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
        ],
        "temperature": 0.3,
        "max_tokens": 600,
        **(llm.get("extra_body") or {}),
    }
    try:
        response = httpx.post(
            llm["base_url"].rstrip("/") + "/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=float(llm.get("timeout_seconds", 120)),
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        return text.strip() or None
    except Exception as exc:  # noqa: BLE001 — 总结失败不影响报告
        log.warn(f"开发建议生成失败，改按数据选：{type(exc).__name__}")
        return None


def feature_gap(f: dict, vision: dict) -> float:
    """一个“增长商品更多”的特征比对照多几成：主图（增长势头前 N vs 持续热销对照组）、标题（增长商品 vs 全部头部商品）取大的。"""
    gaps = []
    if f.get("image") and vision.get("n_reference"):
        gaps.append(f["image"]["focus_share"] - f["image"]["reference_share"])
    if f.get("title"):
        gaps.append(f["title"]["share"] - f["title"]["baseline_share"])
    return max(gaps) if gaps else 0.0


def trend_labels(tr: dict) -> list[str]:
    """本期“增长商品更多”的外观特征名，按 feature_gap 从大到小（开发建议只能用这些）。"""
    vision = tr.get("vision") or {}
    rows = [(feature_gap(f, vision), f["label"]) for groups in (tr.get("appearance") or {}).values()
            for f in groups.get("more") or []]
    return [label for _gap, label in sorted(rows, key=lambda r: -r[0])]


def reference_items(ctx: dict) -> list[dict]:
    """开发建议可以点名的商品：列出的突然爆火、潜力各前 FACT_ITEMS 个（和给大模型看的一样）；
    有异常信号、或标签有降价驱动 / 数据可能有误的不算（它们在风险提醒里，见 risks）。"""
    rows = listed(ctx, "surge")[:FACT_ITEMS] + listed(ctx, "potential")[:FACT_ITEMS]
    return [i for i in rows if can_refer(i)]


def can_refer(item: dict) -> bool:
    return not item["fake"]["score"] and not item["fake"]["signals"] and not any(t.startswith(RISK_TAGS) for t in item["tags"])


def has_feature(item: dict, label: str) -> bool:
    """商品自己有这个外观特征（标题或主图识别，item["design"]）。"""
    return any(label in labels for labels in (item.get("design") or {}).values())


def _clip_cn(text: str, limit: int = DETAIL_MAX) -> str:
    text = (text or "").strip().strip("。.;；，, ")
    return text if len(text) <= limit else text[:limit - 1].rstrip("，,、 ") + "…"


def cn_name(item: dict) -> str:
    """商品的短称：品牌 + 中文子类目（如“KCZDXH 书架”）。"""
    return " ".join(x for x in ((item.get("brand") or "").strip(), item.get("node_cn") or "") if x) or item["asin"]


TIP_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.、)])?\s*(?P<feats>[^｜|\[\]]+?)\s*[｜|]\s*\[(?P<name>[^\[\]]{1,60})\]"
                      r"\((?P<asin>[A-Z0-9]{10})\)\s*[｜|]\s*(?P<detail>[^｜|]+?)\s*$")


def parse_tips(text: str | None, ctx: dict) -> list[dict]:
    """核对大模型写的开发建议（格式见 SYSTEM_PROMPT），留下对得上的：
    特征必须在 trend_labels 里、而且参考商品自己有（has_feature）；参考商品必须在 reference_items 里、方括号里写着它的品牌，
    同一个商品只用一次。特征一个都不剩、或商品对不上的整条删掉。"""
    allowed = set(trend_labels(ctx["traits"]))
    refs = {i["asin"]: i for i in reference_items(ctx)}
    out, used = [], set()
    for line in (text or "").splitlines():
        m = TIP_LINE.match(line)
        if not m:
            continue
        item = refs.get(m["asin"])
        brand = ((item or {}).get("brand") or "").strip().lower()
        if not item or item["asin"] in used or (brand and brand not in m["name"].lower()):
            continue
        feats = [f for f in dict.fromkeys(x.strip() for x in re.split(r"[+＋]", m["feats"]))
                 if f in allowed and has_feature(item, f)][:2]
        if not feats:
            continue
        out.append({"features": feats, "asin": item["asin"], "name": m["name"].strip(), "detail": _clip_cn(m["detail"])})
        used.add(item["asin"])
    return out[:TIP_MAX]


def template_tips(ctx: dict) -> list[dict]:
    """没有大模型时按同样的规则选：参考商品（reference_items，要有外观要点）里有本期“增长商品更多”的特征的，
    特征多的先、同样多按原来的顺序（月增量）；同一组特征只用一次。外观细节取外观要点的第一句。"""
    labels = trend_labels(ctx["traits"])
    picked = []
    for k, item in enumerate(i for i in reference_items(ctx) if i.get("look")):
        hit = [label for label in labels if has_feature(item, label)][:2]
        if hit:
            picked.append((-len(hit), k, item, hit))
    out, seen = [], set()
    for _n, _k, item, hit in sorted(picked, key=lambda r: r[:2]):
        if tuple(hit) in seen:
            continue
        seen.add(tuple(hit))
        out.append({"features": hit, "asin": item["asin"], "name": cn_name(item),
                    "detail": _clip_cn(re.split(r"[，。；,;]", item["look"])[0])})
        if len(out) == TIP_MAX:
            break
    return out


def tips(ctx: dict, cfg: dict, api_key: str) -> tuple[list[dict], str]:
    """开发建议：[{features, asin, name, detail}]，来源 'llm' | 'template'。"""
    found = parse_tips(llm_summary(build_facts(ctx), cfg, api_key), ctx) if api_key else []
    if found:
        return found, "llm"
    return template_tips(ctx), "template"


RISK_TAGS = ("降价驱动", "变体变多", "名单和日数据对不上", "最近 28 天有")  # 标签以这些开头的


def _tag_head(tag: str) -> str:
    return re.split(r"[（(：:，,]", tag, maxsplit=1)[0].strip() or tag


def risks(ctx: dict) -> list[dict]:
    """“风险提醒”只点名两类商品：① 带异常信号的（异常信号板块前 5 个，或列出的爆火 / 潜力前 FACT_ITEMS 个自带的）；
    ② 列出的爆火 / 潜力前 FACT_ITEMS 个里，标签有降价驱动、或数据可能有误（变体变多、名单和日数据对不上、日数据有缺）的。
    返回 [{kind: fake | price | data, label, text, tag, item}]，一个商品只写一次。"""
    out: list[dict] = []
    seen: set[str] = set()

    def add(item: dict, kind: str, label: str, text: str, tag: str = "") -> None:
        if item["asin"] not in seen:
            seen.add(item["asin"])
            out.append({"kind": kind, "label": label, "text": text, "tag": tag, "item": item})

    def signal(item: dict) -> str:
        sigs = item["fake"]["signals"]
        return _tag_head(sigs[0]["text"]) if sigs else "疑似刷评或数据异常"

    for item in ctx["sections"].get("fake", [])[:5]:
        add(item, "fake", "异常信号", f"{signal(item)}，疑似异常，需人工核实",
            "；".join(s["text"] for s in item["fake"]["signals"]))
    for item in listed(ctx, "surge")[:FACT_ITEMS] + listed(ctx, "potential")[:FACT_ITEMS]:
        if item["fake"]["signals"]:
            add(item, "fake", "异常信号", f"{signal(item)}，疑似异常，需人工核实",
                "；".join(s["text"] for s in item["fake"]["signals"]))
            continue
        tag = next((t for t in item["tags"] if t.startswith(RISK_TAGS)), None)
        if tag is None:
            continue
        if tag.startswith("降价驱动"):
            add(item, "price", "降价驱动", "上涨可能是降价带来的，不宜当长期需求", tag)
        else:
            add(item, "data", _tag_head(tag), "数据可能有问题，需人工核实", tag)
    return out

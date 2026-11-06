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
- <特征> + <特征>｜[<品牌> <简短中文品名>](<ASIN>)
严格遵守：
1. 参考商品只能从“突然爆火”“潜力”里选“可作参考”为 true 的商品；每条换一个商品。
   方括号里必须有品牌，ASIN 照抄这个商品的“ASIN”，例如 [Huuger 窄长玄关桌](B0H2XKVQ5L)。
2. <特征> 只能照抄这个商品“可用特征”里的词（本期增长商品比对照组更多、而且这个商品主图上看得出的外观特征），
   每条 1~2 个，两个时用“ + ”连接；不要用别的词，不要自己造词。各条的特征组合不要重复，尽量覆盖不同的特征。
3. <简短中文品名> 要说清它是什么（如“壁炉电视柜”“浴室吊柜”），和主图、“外观要点”一致。
4. 讲设计手法，不要把“开发方向”里某个品类的画像再写一遍；不要写数字、网址、解释或任何别的文字。
5. 找不到符合第 1、2 条的组合就少写几条；一条都写不出时只输出“无”。"""


FACT_ITEMS = 6  # 本期要点给大模型看的突然爆火、潜力各前几个（钉钉列前 notify.max_items 个，都在这里面）
TIP_MAX = 3  # 开发建议最多几条
DETAIL_MAX = 18  # 开发建议里外观细节（取自主图的外观要点）最多几个字，说到特征的两句时再多 6 个字


def build_facts(ctx: dict) -> dict:
    """给大模型写开发建议用的事实（只写开发建议，其余几块报告直接取数据）。"""
    labels = tip_labels(ctx)

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
            out["可用特征"] = [label for label in labels if shows(item, label)]  # labels = tip_labels
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
        # 报告里有画像的子类目全部列出，本期要点只放月增量合计占大头的几个（directions.highlight）
        "开发方向": direction_facts({"directions": directions.highlight(ctx["traits"].get("directions") or [], ctx["cfg"])}),
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


def tip_labels(ctx: dict) -> list[str]:
    """开发建议能用的特征：本期“增长商品更多”的特征里，主图识别也比持续热销对照组多 ≥ appearance.image_min_gap 的
    （开发建议配的是主图，标题里多、图上看不出差别的不用；如“现代”：标题 20% vs 14%，主图 80% vs 80%）。按差距从大到小。"""
    tr = ctx["traits"]
    vision = tr.get("vision") or {}
    if not vision.get("n_reference"):
        return []
    min_gap = float(ctx["cfg"]["thresholds"]["appearance"].get("image_min_gap", 0.1))
    rows = [(f["image"]["focus_share"] - f["image"]["reference_share"], f["label"])
            for groups in (tr.get("appearance") or {}).values() for f in groups.get("more") or [] if f.get("image")]
    return [label for gap, label in sorted(rows, key=lambda r: -r[0]) if gap >= min_gap - 1e-9]


def reference_items(ctx: dict) -> list[dict]:
    """开发建议可以点名的商品：列出的突然爆火、潜力各前 FACT_ITEMS 个（和给大模型看的一样）；
    有任何风险因素的不算（它们在风险提醒里，见 risks）。"""
    rows = listed(ctx, "surge")[:FACT_ITEMS] + listed(ctx, "potential")[:FACT_ITEMS]
    return [i for i in rows if can_refer(i)]


def can_refer(item: dict) -> bool:
    return not risk_groups_of(item)


def shows(item: dict, label: str) -> bool:
    """商品主图上看得出这个外观特征（主图识别，item["seen"]）。报告里配图的地方都按它挑：图上看不出的特征不配这张图。"""
    return any(label in labels for labels in (item.get("seen") or {}).values())


def _clip_cn(text: str, limit: int = DETAIL_MAX) -> str:
    text = (text or "").strip().strip("。.;；，, ")
    return text if len(text) <= limit else text[:limit - 1].rstrip("，,、 ") + "…"


def _bigrams(label: str) -> set[str]:
    core = re.sub(r"[\s/（）()、]", "", label)
    return {core} if len(core) < 2 else {core[k:k + 2] for k in range(len(core) - 1)}


def look_detail(look: str, feats: list[str]) -> str:
    """外观细节：主图识别写的外观要点里，说到这几个特征的句子（和特征名有两个字相同），没有就取第一句。
    外观要点是对同一张主图的描述，所以细节和配图对得上。"""
    clauses = [c.strip() for c in re.split(r"[，。；,;]", look or "") if c.strip()]
    grams = set().union(*(_bigrams(f) for f in feats)) if feats else set()
    hits = [c for c in clauses if any(g in c for g in grams)]
    return _clip_cn("，".join(hits[:2]) if hits else (clauses[0] if clauses else ""), DETAIL_MAX + 6)


ROOMS = (("浴室", r"\b(?:bathroom|medicine cabinet|over (?:the )?toilet|toilet)\b"),
         ("厨房", r"\b(?:kitchen|pantry|coffee bar|microwave)\b"), ("车库", r"\b(?:garage|tool cabinets?)\b"),
         ("洗衣房", r"\blaundry\b"), ("玄关", r"\b(?:entryway|hallway|mudroom|foyer)\b"), ("客厅", r"\bliving room\b"),
         ("卧室", r"\bbedroom\b"), ("餐厅", r"\bdining room\b"), ("办公室", r"\boffice\b"))


def room_of(title: str) -> str:
    """标题里最先说到的房间（浴室、厨房、车库……）：收纳柜这类哪个房间都用的子类目，短称后面写上，免得图是浴室柜、字是“收纳柜”。"""
    found = [(m.start(), name) for name, rx in ROOMS if (m := re.search(rx, title or "", re.I))]
    return min(found)[1] if found else ""


def cn_name(item: dict) -> str:
    """商品的短称：品牌 + 中文子类目，用途不固定的子类目再加标题里的房间（如“Minifeel 收纳柜 · 浴室”）。"""
    name = " ".join(x for x in ((item.get("brand") or "").strip(), item.get("node_cn") or "") if x) or item["asin"]
    return f"{name} · {item['room']}" if item.get("room") else name


TIP_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.、)])?\s*(?P<feats>[^｜|\[\]]+?)\s*[｜|]\s*\[(?P<name>[^\[\]]{1,60})\]"
                      r"\((?P<asin>[A-Z0-9]{10})\)\s*(?:[｜|].*)?$")


def parse_tips(text: str | None, ctx: dict) -> list[dict]:
    """核对大模型写的开发建议（格式见 SYSTEM_PROMPT），留下对得上的：
    特征必须在 tip_labels 里、而且参考商品的主图上看得出（shows）；参考商品必须在 reference_items 里、方括号里写着它的品牌，
    同一个商品只用一次。特征一个都不剩、或商品对不上的整条删掉。外观细节不用模型写的，取这张主图的外观要点（look_detail）。"""
    allowed = set(tip_labels(ctx))
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
                 if f in allowed and shows(item, f)][:2]
        if not feats:
            continue
        out.append({"features": feats, "asin": item["asin"], "name": m["name"].strip(),
                    "detail": look_detail(item.get("look") or "", feats)})
        used.add(item["asin"])
    return out[:TIP_MAX]


def template_tips(ctx: dict) -> list[dict]:
    """没有大模型时按同样的规则选：参考商品（reference_items，要有外观要点）的主图上看得出开发建议能用的特征（tip_labels）的，
    特征多的先，再是外观要点里写到这些特征的先（配的那句说明对得上），同样的按原来的顺序（月增量）；同一组特征只用一次。"""
    labels = tip_labels(ctx)
    picked = []
    for k, item in enumerate(i for i in reference_items(ctx) if i.get("look")):
        hit = [label for label in labels if shows(item, label)][:2]
        if hit:
            told = sum(1 for f in hit if any(g in item["look"] for g in _bigrams(f)))
            picked.append((-len(hit), -told, k, item, hit))
    out, seen = [], set()
    for _n, _t, _k, item, hit in sorted(picked, key=lambda r: r[:3]):
        if tuple(hit) in seen:
            continue
        seen.add(tuple(hit))
        out.append({"features": hit, "asin": item["asin"], "name": cn_name(item), "detail": look_detail(item["look"], hit)})
        if len(out) == TIP_MAX:
            break
    return out


def tips(ctx: dict, cfg: dict, api_key: str) -> tuple[list[dict], str]:
    """开发建议：[{features, asin, name, detail}]，来源 'llm' | 'template'。"""
    found = parse_tips(llm_summary(build_facts(ctx), cfg, api_key), ctx) if api_key else []
    if found:
        return found, "llm"
    return template_tips(ctx), "template"


# 风险提醒：列出的商品（各板块列出的 + 异常信号）身上的风险因素，每类一个条件（标签以这些开头 / 含这些字），一个商品可以在几类里。
# 标签的写法见 analyze / detect；报告卡片上标成红色（render._tag_class）的标签每一个都要归进某一类，归不进的在“其他需要留意”。
RISK_GROUPS = (
    ("fake", "异常信号", "疑似刷评或数据异常，需人工核实（不是断言刷单）", ("假爆火嫌疑",)),
    ("suspect", "有可疑迹象", "有留评率偏高这类迹象，但分数没到异常信号的门槛；参考前先看看评论", ()),
    ("price", "降价驱动", "上涨可能是降价带来的，不宜当长期需求", ("降价驱动",)),
    ("data", "数据可能有误", "变体变多、名单和日数据对不上、日数据有缺或陈旧，先核对数据再判断",
     ("变体变多", "变体合并", "名单和日数据对不上", "最近 28 天有", "数据陈旧", "只有名单数据", "销量区间不完整")),
    ("base", "对比基准不足", "之前的销量基数太少或不完整，增幅可能被放大",
     ("对比期只有", "对比数据不足", "月度增长尚未验证", "累计销量数据不足", "新品起量（对比数据不足", "对比期包括它开卖的第一周")),
    ("relist", "老链接 / 断货恢复", "销量或评论里有老链接转来的、或断货后恢复的部分，不全是新需求",
     ("老品换链接", "老链接重新起量", "评论是从老链接继承来的", "评论多是继承来的", "断货恢复")),
    ("deal", "大促 / 数据待修订", "受大促或卖家精灵近几天数据修订影响，下期再确认",
     ("大促拉动", "大促脉冲", "最近几天的数还会被改", "本期是否偏高")),
    ("weak", "势头转弱", "最近在回落、近 7 天没涨够或只是短时冲高",
     ("最近回落", "近 7 天没涨够", "短时脉冲", "上涨形态不稳")),
    ("tide", "随大盘", "所在品类整体都在涨，增长不全是它自己的", ("随大盘",)),
    ("monthly", "只有月度数据", "不在日数据追踪里：没有走势图、不判回落，只按月度名单判断", ("只有月度数据",)),
)
WARN_WORDS = ("假爆火", "降价", "陈旧", "脉冲", "断货", "评分", "回落", "不稳定", "拉动", "还会被改", "变体变多",
              "数据可能有误", "没有日数据", "没涨够")  # 卡片上标成红色的标签（render._tag_class）


def risk_groups_of(item: dict) -> dict[str, str]:
    """一个商品的风险因素：{类别: 对应的那条标签}。异常信号 = 判成异常信号的（和 KPI 同一批）；有异常迹象（fake.signals）
    但没到门槛的记在 suspect；其余按 RISK_GROUPS 的前缀；红色标签归不进任何一类的记在 other。"""
    out: dict[str, str] = {}
    sigs = item.get("fake", {}).get("signals") or []
    text = "；".join(s["text"] for s in sigs)
    if item.get("label") == "fake":
        out["fake"] = text or "异常信号"
    elif sigs:
        out["suspect"] = text
    for tag in item.get("tags") or []:
        key = next((k for k, _n, _t, heads in RISK_GROUPS if tag.startswith(heads)), None)
        if key is None and any(w in tag for w in WARN_WORDS):
            key = "other"
        if key and key not in out:
            out[key] = tag
    return out


def risks(ctx: dict) -> list[dict]:
    """“风险提醒”：报告里列出的全部商品（突然爆火、潜力、持续热销、蓝海产品、增长商品里列出的，加上异常信号）身上的
    每一类风险因素，一类一组：[{kind, label, text, items: [(商品, 板块, 那条标签)]}]，按 RISK_GROUPS 的顺序，没有商品的类不出。"""
    cur = ctx.get("curated") or {}
    pool: dict[str, tuple[dict, str]] = {}
    for key in ("surge", "potential", "hot", "ocean", "growth"):
        rows = cur[key]["shown"] if cur.get(key) else (ctx.get("focus") if key == "growth" else ctx["sections"].get(key))
        for item in rows or []:
            pool.setdefault(item["asin"], (item, key))
    for item in ctx["sections"].get("fake") or []:
        pool.setdefault(item["asin"], (item, "fake"))
    groups = {k: [] for k, *_r in RISK_GROUPS} | {"other": []}
    for item, where in pool.values():
        for kind, tag in risk_groups_of(item).items():
            groups[kind].append((item, where, tag))
    meta = {k: (name, text) for k, name, text, _h in RISK_GROUPS} | {"other": ("其他需要留意", "其余标成红色的标签")}
    return [{"kind": k, "label": meta[k][0], "text": meta[k][1], "items": rows} for k, rows in groups.items() if rows]

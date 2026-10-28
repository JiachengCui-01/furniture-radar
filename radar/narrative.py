"""文字总结：把计算好的事实交给大模型（默认 DeepSeek，OpenAI 兼容接口）写成中文摘要。

模型只负责措辞，不参与判定；提示词要求只能使用给定数字。没有 key 或调用失败时用模板文字。
"""
from __future__ import annotations

import json
import re

import httpx

from . import log, shipping
from .analyze import display_name
from .detect import directions

SYSTEM_PROMPT = """你是亚马逊美国站家具类目的选品分析师兼产品设计顾问。下面是自动化系统已经计算好的本期监控结果（JSON）。
请用简体中文写一份给选品/开发团队看的“本期要点”，严格遵守：
1. 只能使用 JSON 里出现的数字和事实，不得编造或推算新数字；没有的数据就不提。
2. 不要复述各板块的商品数量（页面顶部已经有），也不要逐个罗列商品（点开板块就能看到）。
3. 结构固定为四个小节，用 "- " 开头：
   ### 外观与工艺趋势（3~5 条）
   ### 开发建议（2~3 条）
   ### 开发方向（JSON“开发方向”里有几个就写几条，一个方向一条）
   ### 风险提醒（1~3 条）
4. 名词严格按 JSON 的叫法：“增长商品”= 突然爆火 + 潜力 + 上升中；“全部头部商品”是标题统计的对比对象；
   “持续热销对照组”是主图识别和主材质的对比对象。引用数字时写清楚是哪种依据、和谁比。
5. “外观与工艺趋势”：第一条说主材质构成（只用“主材质”的数据）；后面按风格、造型、工艺、颜色写“增长商品更多”的特征，
   引用对应的占比；“常见特征”只能说“增长商品多数是…（对照组也一样多）”，不能说成趋势；可以引用“外观要点”的具体描述；
   功能卖点最多一条。不要另外讨论材质。
6. “开发建议”具体到设计方向（风格、造型、工艺、配色），可以点名 1~2 个参考商品（写法见第 10 条），
   只能选“可作参考”为 true 的商品（有异常信号的不能当参考）。
   它讲的是可以用到不同品类上的设计手法，不要把“开发方向”里某个品类的画像再写一遍。
7. “开发方向”：按 JSON 的顺序（增量销售额从大到小），每条写成
   “开发<品类>（月增量约 <增量>）：<主材质>，<比头部多的特征>，定价 <价格区间>”，
   品类就是该方向的子类目（可以说得更口语，如“梳妆台”），增量照抄该方向“销售额”里的增量；
   特征只能用该方向“比头部多”里列出的，不得添加、替换或挪用别的方向的，也不要写“基本款”里的；
   “比头部多”为空时写“外观没有比同类头部更多的特征”；“主材质”是“未注明”“木质（未注明）”“其他”的不写；
   不要写季节、淡旺季方面的内容（报告不做季节判断）；
   末尾可以加“参考 [<品牌 简称>](<ASIN>) 的<一个外观细节>”，外观细节只能取该方向参考商品的“外观要点”，
   并且不能和这条已经写出的颜色、主材质矛盾（矛盾就换一个细节或不写参考）。
8. “风险提醒”只写两类，每条都要点名 JSON 里的一个具体商品（写法见第 10 条）：
   ① 带“异常信号”的商品（“异常信号”列表里的，或爆火 / 潜力商品自带的“异常信号”字段）：只能说“疑似、需人工核实”，不要断言刷单；
   ② “标签”里有“降价驱动”“变体变多”“名单和日数据对不上”“最近 28 天有 N 天没有日数据”的爆火 / 潜力商品：
      降价驱动提醒上涨可能是降价带来的、不要当成长期需求；另外三种提醒“数据可能有问题，需人工核实”。
   不要根据功能卖点、外观、品类或价格自己推测风险；两类都没有就只写一条“本期没有需要特别提醒的风险”。
9. 不要输出链接和长标题。“开发方向”每条不超过 100 字，其余每条不超过 60 字、一条只说一个特征或一个建议；
   总长度不超过 950 字，宁可少写几条趋势。
10. 点名具体商品时（开发建议的参考商品、开发方向末尾的“参考”、风险提醒里的商品）一律写成 [品牌 简短中文品名](ASIN)，
   ASIN 用 JSON 里这个商品的“ASIN”，方括号里必须有品牌，例如 [Huuger 窄长玄关桌](B0H2XKVQ5L)；
   报告里会变成点开就是亚马逊商品页的链接。同一条里同一个商品只写一次链接，链接里的 ASIN 不计入字数。"""


FACT_ITEMS = 6  # 本期要点给大模型看的突然爆火、潜力各前几个（钉钉列前 notify.max_items 个，都在这里面）


def build_facts(ctx: dict) -> dict:
    def brief(item: dict) -> dict:
        out = {
            "品牌": item["brand"], "品名": display_name(item["brand"], item["title"])[:60],
            "子类目": item["node_cn"] or item["node_name"], "价格": item["price"],
            "评分": item["rating"], "评论数": item["ratings"], "上架天数": item["age_days"], "标签": item["tags"],
            "可作参考": not item["fake"]["score"], "ASIN": item["asin"],
        }
        if item.get("look"):
            out["外观要点"] = item["look"]
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

    sec = ctx["sections"]
    return {
        "数据截至": ctx["as_of"],
        "监控范围": "亚马逊美国站板材 / 实木 / 铁木为主的柜类、桌类、床架、置物架（不含沙发、床垫、椅子）",
        "突然爆火": [brief(i) for i in sec["surge"][:FACT_ITEMS]],
        "潜力": [brief(i) for i in sec["potential"][:FACT_ITEMS]],
        "异常信号": [brief(i) for i in sec["fake"][:5]],
        "共性特点": design_facts(ctx["traits"]),
        # 报告里够格的子类目全部列出，本期要点只写增量最大的前 summary_max 个
        "开发方向": direction_facts(ctx["traits"])[: summary_limit(ctx["cfg"])],
    }


def summary_limit(cfg: dict) -> int:
    return int(cfg["thresholds"]["directions"].get("summary_max") or 5)


def name_the_cut(text: str, total: int, shown: int) -> str:
    """“开发方向”小节只写了前几个时，标题写明是增量最大的几个、全部的在哪里（不然看着像只有这几个子类目）。"""
    if total <= shown:
        return text
    head = f"### 开发方向（增量最大的 {shown} 个，全部 {total} 个见下方“开发方向依据”）"
    return re.sub(r"(?m)^###\s*开发方向(?:（[^）\n]*）)?[ \t]*$", lambda _m: head, text, count=1)


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
    """开发方向（directions.py 算好的），每个特征带“几个品牌有 / 该子类目增长商品的品牌数”和头部商品里的占比。"""
    out = []
    for d in tr.get("directions") or []:
        n, nb = d["n"], d.get("n_brands", d["n"])
        cmp = d.get("compare") or {}
        if cmp.get("mode"):
            basis = (f"“比头部多”= 比同子类目头部商品 {cmp['n_head']} 个（{'标题 + 主图' if cmp['mode'] == 'image' else '只看标题'}）"
                     "明显多的；“基本款”= 多数品牌有、但头部也一样多（这个品类本来就这样做）")
        else:
            basis = "同子类目头部商品太少，没有对比；“基本款”里是多数品牌都有的共同特征"

        def feats(tier: str) -> dict:
            return {dim.replace("卖点", ""): [
                f"{f['label']} {f['count']}/{nb} 个品牌" + (f"（头部 {f['head_share']:.0%}）" if f.get("head_share") is not None else "")
                for f in d["features"][dim][tier]] for dim in directions.DIMENSIONS if d["features"][dim][tier]}

        sales = d.get("sales") or {}
        row = {"子类目": d["node"],
               "依据": f"该子类目的增长商品 {n} 个、来自 {nb} 个品牌（突然爆火 {d['n_surge']} 个），"
                     f"特征按品牌计数（同一品牌的多个商品只算一次）；{basis}",
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
        "max_tokens": 2000,
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
        log.warn(f"文字总结生成失败，改用模板：{type(exc).__name__}")
        return None


def _short(name: str, words: int = 4) -> str:
    """品牌 + 前几个词，不截断在单词中间。"""
    parts = (name or "").replace(",", " ").split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def _link(item: dict) -> str:
    """[品牌 + 前几个词](ASIN)：报告里渲染成商品链接（report/render.py 的 product_links）。"""
    return f"[{_short(item['品名'])}]({item['ASIN']})" if item.get("ASIN") else _short(item["品名"])


def template_summary(facts: dict) -> str:
    """没有大模型时的“本期要点”：结构和大模型版一致，只用计算好的事实。"""
    trait = facts["共性特点"]
    lines = ["### 外观与工艺趋势"]
    if trait.get("备注"):
        lines.append(f"- {trait['备注']}")
    mats = [m for m in next((v for k, v in trait.items() if k.startswith("主材质")), [])
            if not m.startswith(("未注明", "木质（未注明）", "其他")) and "：增长商品 0/" not in m]
    if mats:
        lines.append(f"- 主材质：{'；'.join(mats[:3])}。")
    for dim, rows in trait.get("外观特征（增长商品更多）", {}).items():
        lines.append(f"- {dim}：{'；'.join(rows[:2])}。")
    if len(lines) == 1:
        lines.append("- 增长商品没有明显偏多的外观/工艺特征。")
    lines.append("### 开发建议")
    refs = [i for i in facts["突然爆火"][:2] + facts["潜力"][:2] if i.get("外观要点") and i.get("可作参考")]
    for item in refs[:2]:
        lines.append(f"- 参考 {_link(item)}：{item['外观要点']}")
    if not refs:
        lines.append("- 点开“突然爆火”“潜力产品”查看本期的参考商品。")
    lines.append("### 开发方向")
    for d in facts["开发方向"]:
        ref = (d.get("参考商品") or [None])[0]
        head, node = d["画像"], d.get("子类目")
        if node and d.get("增量"):
            head = head.replace(f"{node}：", f"{node}（月增量约 {d['增量']}）：", 1)
        lines.append(f"- 开发{head}" + (f"，参考 {_link(ref)}" if ref else ""))
    if not facts["开发方向"]:
        lines.append("- 本期没有增长商品数达到门槛的子类目，暂不给开发方向。")
    lines.append("### 风险提醒")
    for item in facts["异常信号"][:2]:
        signal = (item.get("异常信号") or [""])[0]
        lines.append(f"- {_link(item)}：{signal}，疑似数据异常，需人工核实。")
    short = [_short(i["品名"]) for i in facts["突然爆火"] + facts["潜力"] if any(t.startswith(RISK_TAGS) for t in i["标签"])]
    if short:
        lines.append(f"- {'、'.join(short[:2])} 的上涨带降价驱动或数据可能有误的标签，需人工核实，不要直接当成长期需求。")
    if len(lines) and lines[-1] == "### 风险提醒":
        lines.append("- 本期没有异常信号，也没有降价驱动或数据可能有误的爆火 / 潜力商品。")
    return "\n".join(lines)


RISK_TAGS = ("降价驱动", "变体变多", "名单和日数据对不上", "最近 28 天有")  # 标签以这些开头的
NO_RISK = "- 本期没有需要特别提醒的风险。"


def risk_brands(facts: dict) -> set[str]:
    """“风险提醒”可以点名的商品的品牌：带异常信号的，或标签有降价驱动 / 数据可能有误的爆火、潜力商品。"""
    out = {i["品牌"] for i in facts["异常信号"]}
    out |= {i["品牌"] for i in facts["突然爆火"] + facts["潜力"]
            if i.get("异常信号") or any(t.startswith(RISK_TAGS) for t in i["标签"])}
    return {b.strip() for b in out if b and b.strip()}


def guard_risks(text: str, facts: dict) -> str:
    """大模型写的“风险提醒”里，每条都要点名一个可以点名的商品（见 risk_brands），否则删掉这条
    （实测：模型会自己推测“带充电/插座、LED 灯的需留意是否为短期促销”，数据里没有依据）。删完没有了就写 NO_RISK。"""
    brands = [b.lower() for b in risk_brands(facts)]
    out: list[str] = []
    in_risk, kept, dropped = False, 0, 0

    def close() -> None:
        if in_risk and not kept:
            out.append(NO_RISK)

    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            close()
            in_risk, kept = "风险提醒" in line, 0
            out.append(line)
        elif in_risk and line.lstrip().startswith("- "):
            if any(b in line.lower() for b in brands):
                out.append(line)
                kept += 1
            elif "没有需要特别提醒" not in line:
                dropped += 1
        else:
            out.append(line)
    close()
    if dropped:
        log.info(f"风险提醒：删掉 {dropped} 条没有点名具体商品依据的")
    return "\n".join(out)


def summary(ctx: dict, cfg: dict, api_key: str) -> tuple[str, str]:
    """返回 (markdown 文本, 来源 'llm'|'template')。大模型写的“风险提醒”会再按 guard_risks 核对一遍。"""
    facts = build_facts(ctx)
    total, shown = len(ctx["traits"].get("directions") or []), len(facts["开发方向"])
    text = llm_summary(facts, cfg, api_key)
    if text:
        return name_the_cut(guard_risks(text, facts), total, shown), "llm"
    return name_the_cut(template_summary(facts), total, shown), "template"

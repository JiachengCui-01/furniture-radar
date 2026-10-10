"""文字总结：把计算好的事实交给大模型（默认 DeepSeek，OpenAI 兼容接口）写成中文摘要。

模型只负责措辞，不参与判定；提示词要求只能使用给定数字。没有 key 或调用失败时用模板文字。
"""
from __future__ import annotations

import json

import httpx

from . import log
from .analyze import display_name

SYSTEM_PROMPT = """你是亚马逊美国站家具类目的选品分析师兼产品设计顾问。下面是自动化系统已经计算好的本期监控结果（JSON）。
请用简体中文写一份给选品/开发团队看的“本期要点”，严格遵守：
1. 只能使用 JSON 里出现的数字和事实，不得编造或推算新数字；没有的数据就不提。
2. 不要复述各板块的商品数量（页面顶部已经有），也不要逐个罗列商品（点开板块就能看到）。
3. 结构固定为三个小节，用 "- " 开头：
   ### 外观与工艺趋势（3~5 条）
   ### 开发建议（2~3 条）
   ### 风险提醒（1~3 条）
4. 名词严格按 JSON 的叫法：“增长商品”= 突然爆火 + 潜力 + 上升中；“全部头部商品”是标题统计的对比对象；
   “持续热销对照组”是主图识别和主材质的对比对象。引用数字时写清楚是哪种依据、和谁比。
5. “外观与工艺趋势”：第一条说主材质构成（只用“主材质”的数据）；后面按风格、造型、工艺、颜色写“增长商品更多”的特征，
   引用对应的占比；“常见特征”只能说“增长商品多数是…（对照组也一样多）”，不能说成趋势；可以引用“外观要点”的具体描述；
   功能卖点最多一条。不要另外讨论材质。
6. “开发建议”具体到设计方向（风格、造型、工艺、配色），可以点名 1~2 个参考商品（品牌 + 简短品名）。
7. “风险提醒”：异常信号只能说“疑似、需人工核实”，不要断言刷单；带季节性、大促、降价驱动标签的增长提醒不要当成长期需求。
8. 不要输出链接和长标题。每条不超过 60 字，一条只说一个特征或一个建议；总长度不超过 450 字，宁可少写几条。"""


def build_facts(ctx: dict) -> dict:
    def brief(item: dict) -> dict:
        out = {
            "品牌": item["brand"], "品名": display_name(item["brand"], item["title"])[:60],
            "子类目": item["node_cn"] or item["node_name"], "价格": item["price"],
            "评分": item["rating"], "评论数": item["ratings"], "上架天数": item["age_days"], "标签": item["tags"],
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
        "突然爆火": [brief(i) for i in sec["surge"][:6]],
        "潜力": [brief(i) for i in sec["potential"][:6]],
        "异常信号": [brief(i) for i in sec["fake"][:5]],
        "共性特点": design_facts(ctx["traits"]),
    }


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
        "max_tokens": 1500,
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
    refs = [i for i in facts["突然爆火"][:2] + facts["潜力"][:2] if i.get("外观要点")]
    for item in refs[:2]:
        lines.append(f"- 参考 {_short(item['品名'])}：{item['外观要点']}")
    if not refs:
        lines.append("- 点开“突然爆火”“潜力产品”查看本期的参考商品。")
    lines.append("### 风险提醒")
    for item in facts["异常信号"][:2]:
        signal = (item.get("异常信号") or [""])[0]
        lines.append(f"- {_short(item['品名'])}：{signal}，疑似数据异常，需人工核实。")
    short = [_short(i["品名"]) for i in facts["突然爆火"] if any(t in ("季节性", "降价驱动") or t.startswith("大促")
                                                              for t in i["标签"])]
    if short:
        lines.append(f"- {'、'.join(short[:2])} 的上涨带季节性 / 大促 / 降价驱动标签，不要当成长期需求。")
    if len(lines) and lines[-1] == "### 风险提醒":
        lines.append("- 本期没有异常信号，也没有季节性 / 大促 / 降价驱动的爆火。")
    return "\n".join(lines)


def summary(ctx: dict, cfg: dict, api_key: str) -> tuple[str, str]:
    """返回 (markdown 文本, 来源 'llm'|'template')。"""
    facts = build_facts(ctx)
    text = llm_summary(facts, cfg, api_key)
    if text:
        return text, "llm"
    return template_summary(facts), "template"

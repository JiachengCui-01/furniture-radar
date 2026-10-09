"""文字总结：把计算好的事实交给大模型（默认 DeepSeek，OpenAI 兼容接口）写成中文摘要。

模型只负责措辞，不参与判定；提示词要求只能使用给定数字。没有 key 或调用失败时用模板文字。
"""
from __future__ import annotations

import json

import httpx

from . import log
from .analyze import display_name

SYSTEM_PROMPT = """你是亚马逊美国站家具类目的选品分析师兼产品设计顾问。下面是自动化系统已经计算好的本期监控结果（JSON）。
请用简体中文写一份给选品/开发团队看的简报，严格遵守：
1. 只能使用 JSON 里出现的数字和事实，不得编造或推算新数字；没有的数据就不提。
2. 结构固定为四个小节，每节 2~5 条要点，用 "- " 开头：
   ### 本期结论
   ### 爆火产品的外观与工艺特点
   ### 选品与开发建议
   ### 风险提醒
3. “爆火产品的外观与工艺特点”是重点：先用一条说主材质（板材 / 实木 / 铁木）构成，再依据“外观与工艺”（标题统计）
   和“主图识别”，围绕风格、材质、造型、
   工艺细节、颜色来写，说清楚爆火/潜力商品相比全部家具（或持续热销）哪些外观/工艺特征明显更多，并引用对应的
   占比或倍数；可以引用“外观要点”里的具体描述。功能卖点放在最后一条；子类目、价格段等结构特征最多一句带过。
4. “选品与开发建议”要具体到设计方向：建议开发或跟进什么风格、材质、造型、工艺、配色的产品，
   可以点名 1~2 个参考商品（品牌 + 简短品名）。
5. “异常信号”只能表述为“疑似/需人工核实”，不要断言对方刷单。季节性、大促、降价、断货恢复带来的增长要提醒
   不要误判为长期需求。
6. 不要输出链接和长标题。总长度控制在 700 字以内。"""


def build_facts(ctx: dict) -> dict:
    def brief(item: dict) -> dict:
        out = {
            "品牌": item["brand"], "品名": display_name(item["brand"], item["title"])[:60], "子类目": item["node_cn"] or item["node_name"],
            "价格": item["price"], "评分": item["rating"], "评论数": item["ratings"],
            "上架天数": item["age_days"], "标签": item["tags"],
        }
        if item.get("surge"):
            s = item["surge"]
            out["暴涨"] = {"近期日均": round(s["recent_avg"], 1), "之前日均": round(s["base_avg"], 1),
                         "倍数": None if s["sales_ratio"] is None else round(s["sales_ratio"], 1),
                         "形态": s["kind"]}
        if item.get("potential"):
            out["潜力"] = item["potential"]
        if item.get("hot"):
            out["热销"] = {"月均销量": item["hot"]["avg_monthly"], "达标月数": item["hot"]["months_hit"]}
        if item["fake"]["signals"]:
            out["异常信号"] = [s["text"] for s in item["fake"]["signals"]]
        return out

    sec = ctx["sections"]
    return {
        "数据月份": ctx["period"],
        "数据截至": ctx["as_of"],
        "追踪商品数": sec["counts"]["total"],
        "监控范围": "亚马逊美国站板材 / 实木 / 铁木为主的柜类、桌类、床架、置物架（不含沙发、床垫、椅子）",
        "各类数量": {"突然爆火": sec["counts"]["surge"], "潜力": sec["counts"]["potential"],
                 "持续热销": sec["counts"]["hot"], "异常信号": sec["counts"]["fake"],
                 "上升中": sec["counts"].get("rising", 0)},
        "突然爆火": [brief(i) for i in sec["surge"][:8]],
        "潜力": [brief(i) for i in sec["potential"][:8]],
        "持续热销": [brief(i) for i in sec["hot"][:5]],
        "异常信号": [brief(i) for i in sec["fake"][:8]],
        "共性特点": design_facts(ctx["traits"]),
        "与上期对比": {
            "首期": ctx["diff"].get("first", False),
            "新增爆火": len(ctx["diff"]["new"]["surge"]),
            "新增潜力": len(ctx["diff"]["new"]["potential"]),
            "新增异常": len(ctx["diff"]["new"]["fake"]),
            "爆火回落": len(ctx["diff"].get("cooled", [])),
        },
    }


def design_facts(tr: dict) -> dict:
    """外观与工艺优先，结构特征其次。"""
    n = tr.get("n_focus") or 0
    design = tr.get("design") or {}

    def lines(rows):
        return [f"{r['label']}：爆火/潜力中 {r['count']}/{n}（{r['share']:.0%}），全部家具中 {r['baseline_share']:.0%}，"
                f"×{r['lift']}" for r in rows]

    out = {
        "样本说明": f"本期突然爆火 + 潜力 + 上升中（近 28 天增长 ≥30%）共 {n} 个，对比本月全部头部商品 {tr.get('n_baseline')} 个",
        "主材质构成（优先用亚马逊 Material 属性）": [
            f"{m['label']}：爆火/潜力/上升中 {m['count']}/{(tr.get('materials') or {}).get('n_focus')}（{m['share']:.0%}）"
            + (f"，持续热销 {m['reference']}/{tr['materials']['n_reference']}（{m['reference_share']:.0%}）"
               if m.get("reference_share") is not None else "")
            for m in (tr.get("materials") or {}).get("rows", [])],
        "外观与工艺": {dim: lines(rows) for dim, rows in design.items() if dim != "功能卖点"},
        "功能卖点": lines(design.get("功能卖点", [])),
        "其他结构特征": [f"{f['dimension']}={f['value']}：占爆火/潜力的 {f['share']:.0%}，全部家具 {f['baseline_share']:.0%}"
                   for f in tr.get("facts", [])[:4]],
        "备注": tr.get("note"),
    }
    v = tr.get("vision") or {}
    if v.get("n_focus"):
        ref = f"（持续热销对照 {v['n_reference']} 个）" if v.get("n_reference") else ""
        out[f"主图识别：爆火/潜力 {v['n_focus']} 个{ref}"] = {
            dim: [f"{r['label']} {r['focus']}/{v['n_focus']}"
                  + (f"，持续热销 {r['reference']}/{v['n_reference']}" if v.get("n_reference") else "")
                  + ("（明显多于持续热销）" if r.get("distinct") else "") for r in rows]
            for dim, rows in (v.get("dimensions") or {}).items()}
        out["外观要点"] = [f"{display_name(g.get('brand') or '', g.get('title') or '')[:30]}：{g['summary']}"
                       for g in v.get("gallery", [])[:10] if g.get("summary")]
    return out


def top_design_labels(tr: dict, k: int = 4) -> list[str]:
    """外观/工艺里提升最明显的几个特征名（钉钉卡片和模板总结用）。"""
    rows = [(dim, r) for dim, rs in (tr.get("design") or {}).items() if dim != "功能卖点" for r in rs]
    rows.sort(key=lambda x: -(x[1]["lift"] * x[1]["share"]))
    return [r["label"] for _, r in rows[:k]]


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


def template_summary(facts: dict) -> str:
    counts = facts["各类数量"]
    lines = ["### 本期结论",
             f"- 共追踪 {facts['追踪商品数']} 个家具 ASIN（数据截至 {facts['数据截至']}）："
             f"突然爆火 {counts['突然爆火']} 个、潜力 {counts['潜力']} 个、持续热销 {counts['持续热销']} 个、"
             f"异常信号 {counts['异常信号']} 个。"]
    for item in facts["突然爆火"][:3]:
        s = item.get("暴涨") or {}
        ratio = f"×{s['倍数']}" if s.get("倍数") else "从零起量"
        lines.append(f"- 爆火：{item['品名'][:36]}（{item['子类目']}），日均 {s.get('之前日均')} → "
                     f"{s.get('近期日均')} 件（{ratio}，{s.get('形态')}）。")
    lines.append("### 爆火产品的外观与工艺特点")
    trait = facts["共性特点"]
    if trait.get("备注"):
        lines.append(f"- {trait['备注']}")
    for dim, rows in trait["外观与工艺"].items():
        if rows:
            lines.append(f"- {dim}：{rows[0]}")
    for text in (trait.get("外观要点") or [])[:2]:
        lines.append(f"- 主图：{text}")
    if trait["功能卖点"]:
        lines.append(f"- 功能卖点：{trait['功能卖点'][0]}")
    if not trait["外观与工艺"] and not trait.get("备注"):
        lines.append("- 标题里没有明显偏多的外观/工艺特征。")
    lines.append("### 选品与开发建议")
    if facts["潜力"]:
        names = "、".join(i["品名"][:28] for i in facts["潜力"][:3])
        lines.append(f"- 优先研究潜力商品：{names}。")
    lines.append("- 带“季节性 / 大促 / 降价驱动”标签的爆火多为短期需求，备货前结合往年同期数据判断。")
    lines.append("### 风险提醒")
    if facts["异常信号"]:
        for item in facts["异常信号"][:3]:
            signal = (item.get("异常信号") or [""])[0]
            lines.append(f"- {item['品名'][:30]}：{signal}，疑似非自然增长，需人工核实。")
    else:
        lines.append("- 本期未发现明显的刷评/异常增长信号。")
    return "\n".join(lines)


def summary(ctx: dict, cfg: dict, api_key: str) -> tuple[str, str]:
    """返回 (markdown 文本, 来源 'llm'|'template')。"""
    facts = build_facts(ctx)
    text = llm_summary(facts, cfg, api_key)
    if text:
        return text, "llm"
    return template_summary(facts), "template"

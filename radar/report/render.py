"""生成报告 HTML（明文）。之后由 shell.py 加密包装再发布。

单文件、无外部脚本/字体，只有商品图片引用亚马逊图床；适配手机和深色模式。
主体只有：KPI 数字 → 本期要点 → 外观与工艺依据 → 与上期对比 → 数据说明。
商品清单（各板块、增长商品、全部追踪商品）都放在隐藏的小窗内容里，点 KPI 数字或链接才弹出。
"""
from __future__ import annotations

import html
import re

from ..analyze import display_name
from ..tracking import REASON_CN
from .sparkline import bars, sparkline

LABEL_COLOR = {"surge": "surge", "potential": "potential", "hot": "hot", "fake": "fake", "watch": "muted",
               "low": "muted", "rising": "accent"}


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def fmt_int(value) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}"


def fmt_num(value, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def fmt_pct(value, signed: bool = True) -> str:
    if value is None:
        return "—"
    return f"{value:+.0%}" if signed else f"{value:.0%}"


def fmt_price(value) -> str:
    return "—" if value is None else f"${value:,.2f}"


def markdown(text: str) -> str:
    """只支持 ### 标题、- 列表、**加粗**、段落：足够渲染总结文字。"""
    out: list[str] = []
    in_list = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc(line.lstrip("#-* ").strip()))
        if line.startswith("#"):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h3>{body}</h3>")
        elif line.startswith(("- ", "* ", "• ")) or re.match(r"^\d+[.、)]\s", line):
            if not in_list:
                out.append("<ul>")
                in_list = True
            body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>",
                          esc(re.sub(r"^(\d+[.、)]|[-*•])\s*", "", line)))
            out.append(f"<li>{body}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{body}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


CSS = """
:root{--bg:#f5f6f8;--card:#fff;--text:#1c2230;--muted:#667085;--line:#e4e7ec;--chip:#f0f2f5;
--surge:#e8590c;--potential:#2b8a3e;--hot:#1c6dd0;--fake:#c92a2a;--accent:#1c6dd0;--spark:#1c6dd0;--hl:rgba(232,89,12,.10)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1218;--card:#181c24;--text:#e6e8ec;
--muted:#9aa3b2;--line:#2a303b;--chip:#232833;--surge:#ff8a4c;--potential:#5cc477;--hot:#6aa8ff;--fake:#ff6b6b;
--accent:#6aa8ff;--spark:#6aa8ff;--hl:rgba(255,138,76,.14)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC",
"Microsoft YaHei","Segoe UI",Roboto,sans-serif;-webkit-text-size-adjust:100%}
.wrap{max-width:980px;margin:0 auto;padding:16px}
header h1{font-size:22px;margin:4px 0 2px}
.sub{color:var(--muted);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.chip{background:var(--chip);border-radius:999px;padding:2px 10px;font-size:12px;color:var(--muted)}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;border-top:3px solid var(--c);
font:inherit;color:inherit;text-align:left;width:100%}
.kpi b{display:block;font-size:26px;line-height:1.2;color:var(--c)}
.kpi span{font-size:13px;color:var(--muted)}
@media (max-width:560px){.kpis{grid-template-columns:repeat(2,1fr)}}
.kpi-hint{margin:-6px 0 0;font-size:12px}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:14px 0}
section>h2{font-size:18px;margin:0 0 4px;display:flex;align-items:center;gap:8px}
section>h2 .dot{width:10px;height:10px;border-radius:50%;background:var(--c)}
.hint{color:var(--muted);font-size:13px;margin:0 0 10px}
.summary h3{font-size:15px;margin:14px 0 4px}.summary ul{margin:4px 0;padding-left:20px}.summary p{margin:6px 0}
.cards{display:grid;gap:10px}
.card{display:grid;grid-template-columns:76px minmax(0,1fr);gap:12px;border:1px solid var(--line);border-radius:12px;
padding:10px;border-left:4px solid var(--c)}
.card>div{min-width:0}
.card img,.ph{width:76px;height:76px;object-fit:contain;background:#fff;border-radius:8px;display:block}
.ph{background:var(--chip);color:var(--muted);display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:600}
@media (max-width:420px){.card{grid-template-columns:56px minmax(0,1fr);gap:10px}.card img,.ph{width:56px;height:56px}}
.card .t{font-weight:600;font-size:14px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
overflow:hidden}
.card .t a{color:inherit;text-decoration:none}
.meta{color:var(--muted);font-size:12.5px;margin:2px 0}
.look{font-size:12.5px;margin:2px 0}.look em{font-style:normal;color:var(--muted);margin-right:6px}
.grp{font-size:11.5px;border-radius:6px;padding:1px 7px;background:var(--c);color:#fff}
.kv{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:13px;margin:4px 0}
.kv em{font-style:normal;color:var(--muted)}
.tags{display:flex;flex-wrap:wrap;gap:4px;margin:4px 0}
.tag{font-size:11.5px;border-radius:6px;padding:1px 7px;background:var(--chip);color:var(--muted)}
.tag.warn{background:rgba(201,42,42,.12);color:var(--fake)}
.tag.good{background:rgba(43,138,62,.12);color:var(--potential)}
.tag.info{background:rgba(28,109,208,.12);color:var(--hot)}
.signals{margin:4px 0 0;padding-left:18px;font-size:13px}
.signals li{margin:2px 0}
.score{display:inline-block;font-weight:700;color:var(--fake);margin-right:6px}
.spark{display:block;width:100%;max-width:260px;height:auto;margin-top:4px}
.spark-line{fill:none;stroke:var(--spark);stroke-width:1.6}
.spark-area{fill:var(--spark);opacity:.10}.spark-hl{fill:var(--hl)}
.bar{fill:var(--spark);opacity:.75}
.empty{color:var(--muted);font-size:14px;padding:6px 0}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.tbl{overflow-x:auto}
.meter{position:relative;height:8px;background:var(--chip);border-radius:4px;min-width:90px}
.meter i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:var(--potential)}
.meter u{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--muted)}
.kw{display:flex;flex-wrap:wrap;gap:6px}
.kw span{border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:13px}
.kw span b{color:var(--potential);font-weight:600;margin-left:4px}
details{margin-top:6px}summary{cursor:pointer;color:var(--accent)}
.hist a{margin-left:8px}
a{color:var(--accent)}
footer{color:var(--muted);font-size:12px;text-align:center;padding:16px 0 28px}
.kpi{cursor:pointer;-webkit-tap-highlight-color:transparent}
.kpi:hover{box-shadow:0 2px 10px rgba(0,0,0,.08)}
.pop{position:fixed;top:0;right:0;bottom:0;left:0;z-index:50;display:flex;align-items:center;justify-content:center;padding:16px}
.pop[hidden]{display:none}
.pop-mask{position:absolute;top:0;right:0;bottom:0;left:0;background:rgba(15,18,24,.55)}
.pop-panel{position:relative;background:var(--bg);border-radius:14px;width:100%;max-width:820px;max-height:86vh;
display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,.35);border-top:4px solid var(--c);overflow:hidden}
.pop-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 16px;
border-bottom:1px solid var(--line);background:var(--card)}
.pop-head b{font-size:17px}
.pop-x{border:0;background:var(--chip);border-radius:8px;width:34px;height:34px;font-size:22px;line-height:1;
cursor:pointer;color:var(--text);flex:none}
.pop-body{overflow-y:auto;padding:12px 16px 18px;-webkit-overflow-scrolling:touch}
html.noscroll,html.noscroll body{overflow:hidden}
@media (max-width:560px){.pop{padding:0;align-items:flex-end}.pop-panel{max-height:92vh;border-radius:16px 16px 0 0}}
.dgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px;margin:8px 0 4px}
.dbox{border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.dbox h4{margin:0 0 4px;font-size:14px}
.drow{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 8px;font-size:13px;margin:7px 0}
.drow .m{grid-column:1/-1}
.drow em{font-style:normal;color:var(--muted);font-size:12px}
.common{font-size:12.5px;margin:10px 0 2px;padding-top:8px;border-top:1px dashed var(--line)}
.common em{font-style:normal;color:var(--muted);display:block}
.drow span:nth-child(2){font-variant-numeric:tabular-nums;color:var(--potential);font-weight:600}
"""



# 主体只放结论和依据；商品清单都是隐藏的“小窗内容”，点 KPI 数字或链接时弹出，不跳转页面
POPUP = """<div class="pop" id="pop" hidden><div class="pop-mask" data-close></div>
<div class="pop-panel" role="dialog" aria-modal="true" aria-labelledby="pop-title">
<div class="pop-head"><b id="pop-title"></b><button type="button" class="pop-x" data-close aria-label="关闭">×</button></div>
<div class="pop-body" id="pop-body"></div></div></div>
<script>(function(){
var pop=document.getElementById('pop'),body=document.getElementById('pop-body'),title=document.getElementById('pop-title');
function closest(el,sel){while(el&&el.nodeType===1){if(el.matches?el.matches(sel):el.msMatchesSelector(sel))return el;el=el.parentNode;}return null;}
function open(key){var src=document.getElementById('pop-'+key);if(!src)return false;
title.textContent=src.getAttribute('data-title');body.innerHTML=src.innerHTML;
pop.querySelector('.pop-panel').style.setProperty('--c','var(--'+src.getAttribute('data-c')+')');
pop.hidden=false;document.documentElement.className+=' noscroll';body.scrollTop=0;return true;}
function close(){pop.hidden=true;document.documentElement.className=document.documentElement.className.replace(/\\s*noscroll/g,'');}
document.addEventListener('click',function(e){var t=closest(e.target,'[data-pop]');
if(t){if(open(t.getAttribute('data-pop')))e.preventDefault();return;}
if(closest(e.target,'[data-close]'))close();});
document.addEventListener('keydown',function(e){if(e.key==='Escape'||e.keyCode===27)close();});
})();</script>"""

GROUP_CN = {"surge": "突然爆火", "potential": "潜力", "rising": "上升中"}


def _tags(item: dict) -> str:
    out = []
    for tag in item["tags"]:
        cls = "warn" if any(k in tag for k in ("假爆火", "降价", "陈旧", "脉冲", "断货", "评分", "回落", "不稳定")) else \
            "good" if tag in ("稳在高位", "仍在上涨", "新品爆发", "潜力") or tag.startswith("上升中") else \
            "info" if any(k in tag for k in ("季节", "大促", "头部", "变体")) else ""
        out.append(f'<span class="tag {cls}">{esc(tag)}</span>')
    return f'<div class="tags">{"".join(out)}</div>' if out else ""


def _head(item: dict) -> str:
    url = f"https://www.amazon.com/dp/{esc(item['asin'])}"
    initial = esc((item.get("brand") or item["asin"])[:1].upper())
    img = (f'<img src="{esc(item["image"])}" alt="" loading="lazy" referrerpolicy="no-referrer">'
           if item.get("image") else f'<span class="ph">{initial}</span>')
    node = item.get("node_cn") or item.get("node_name") or ""
    rating = f"★{item['rating']}（{fmt_int(item['ratings'])}）" if item.get("rating") is not None else ""
    age = f"上架 {item['age_days']} 天" if item.get("age_days") is not None else ""
    material = item.get("material") if item.get("material") not in (None, "未注明") else ""
    meta = " · ".join(x for x in (esc(item.get("brand")), esc(node), esc(material), fmt_price(item.get("price")),
                                  rating, age, f"ASIN {esc(item['asin'])}") if x)
    return (f'<a href="{url}" target="_blank" rel="noopener noreferrer">{img}</a><div>'
            f'<div class="t"><a href="{url}" target="_blank" rel="noopener noreferrer">{esc(item["title"])}</a></div>'
            f'<div class="meta">{meta}</div>')


def card(item: dict, kind: str, group: str = "") -> str:
    """kind：surge / potential / hot / fake / rising（决定展示哪些数字和走势图）；group：增长商品小窗里的归类。"""
    c = LABEL_COLOR.get(kind, "muted")
    kv: list[str] = [f'<span class="grp">{esc(group)}</span>'] if group else []
    s = item.get("surge")
    if kind == "surge" or (kind == "fake" and s):
        ratio = f"×{s['sales_ratio']:.1f}" if s.get("sales_ratio") else "从零起量"
        span = f"之前{s.get('base_days', 28)}天 → 近{s['window']}天"
        kv.append(f"<span><em>日均（{span}）</em> {fmt_num(s['base_avg'])} → <b>{fmt_num(s['recent_avg'])}</b> 件"
                  f"（{ratio}）</span>")
        if s.get("base_bsr") and s.get("recent_bsr"):
            kv.append(f"<span><em>BSR 中位数</em> {fmt_int(s['base_bsr'])} → <b>{fmt_int(s['recent_bsr'])}</b></span>")
        if s.get("last_year_ratio"):
            kv.append(f"<span><em>去年同期</em> ×{s['last_year_ratio']:.1f}</span>")
    if kind == "potential" and item.get("potential"):
        p = item["potential"]
        if p.get("growth") is not None:
            kv.append(f"<span><b>{esc(growth_text(item))}</b></span>")
        kv.append(f"<span><em>近28天日均</em> {fmt_num(p.get('avg28'))} 件</span>")
        kv.append(f"<span><em>近7天日均</em> {fmt_num(item.get('recent_avg7'))} 件</span>")
        if p.get("recent_ratio"):
            kv.append(f"<span><em>较前28天</em> <b>×{p['recent_ratio']:.2f}</b></span>")
    if kind == "hot" and item.get("hot"):
        h = item["hot"]
        kv.append(f"<span><em>近6月月均</em> <b>{fmt_int(h['avg_monthly'])}</b> 件</span>")
        kv.append(f"<span><em>达标</em> {h['months_hit']}/{h['lookback']} 个月</span>")
        kv.append(f"<span><em>波动系数</em> {h['cv']:.2f}</span>")
        if h.get("trend") is not None:
            kv.append(f"<span><em>近3月趋势</em> {fmt_pct(h['trend'])}/月</span>")
    if kind == "fake":
        level = "高度疑似" if item["fake"]["level"] == "high" else "疑似"
        kv.insert(0, f'<span><span class="score">异常分 {item["fake"]["score"]}</span>{level}</span>')
    if kind in ("hot", "fake", "rising") and item.get("recent_avg28") is not None:
        kv.append(f"<span><em>近28天日均</em> {fmt_num(item['recent_avg28'])} 件</span>")

    chart = bars(item["monthly"][-12:]) if kind == "hot" else sparkline(item.get("spark") or [])
    look = f'<div class="look"><em>外观</em>{esc(item["look"])}</div>' if item.get("look") else ""
    signals = ""
    if item["fake"]["signals"] and kind in ("fake", "surge", "potential", "rising"):
        signals = "<ul class=\"signals\">" + "".join(
            f"<li>{esc(sig['text'])}（+{sig['points']}）</li>" for sig in item["fake"]["signals"]) + "</ul>"
    return (f'<article class="card" style="--c:var(--{c})">{_head(item)}'
            f'<div class="kv">{"".join(kv)}</div>{look}{_tags(item)}{signals}{chart}</div></article>')


def _pop_src(key: str, title: str, color: str, inner: str) -> str:
    return f'<div class="pop-src" id="pop-{key}" data-title="{esc(title)}" data-c="{color}" hidden>{inner}</div>'


def _section_pop(kind: str, title: str, hint: str, items: list[dict], total: int) -> str:
    more = f"（共 {total} 个，展示前 {len(items)} 个）" if total > len(items) else ""
    body = "".join(card(i, kind) for i in items) if items else '<div class="empty">本期没有符合条件的商品。</div>'
    return _pop_src(kind, title, LABEL_COLOR[kind], f'<p class="hint">{esc(hint)}{more}</p><div class="cards">{body}</div>')


def _growth_pop(focus: list[dict], hint: str) -> str:
    cards = []
    for i in focus:
        kind = i["label"] if i["label"] in ("surge", "potential") else "rising"
        cards.append(card(i, kind, GROUP_CN[kind]))
    body = "".join(cards) or '<div class="empty">本期没有增长商品。</div>'
    return _pop_src("growth", "增长商品", "potential", f'<p class="hint">{esc(hint)}</p><div class="cards">{body}</div>')


def _meter(share: float, base: float | None) -> str:
    tick = f'<u style="left:{min(100, base * 100):.0f}%"></u>' if base is not None else ""
    return f'<div class="meter m"><i style="width:{min(100, share * 100):.0f}%"></i>{tick}</div>'


def growth_text(item: dict) -> str:
    """潜力商品的增长：月环比过大（新品首月很少）时直接写月销量，比百分比直观。"""
    p = item.get("potential") or {}
    months = [m for m in item.get("monthly") or [] if m[1]]
    if p.get("growth") is not None and p["growth"] > 2 and len(months) >= 2:
        return f"月销 {months[-2][1]:,.0f} → {months[-1][1]:,.0f} 件"
    if p.get("growth") is not None:
        return f"月环比 {p['growth']:+.0%}"
    if p.get("recent_ratio"):
        return f"近28天 ×{p['recent_ratio']:.1f}"
    return ""


def _material_box(m: dict) -> str:
    rows = [r for r in m.get("rows") or [] if r["count"] or r["reference"]]
    if not rows:
        return ""
    nf, nr = m["n_focus"], m["n_reference"]
    lines = []
    for r in rows:
        ref = f" · 持续热销对照组 {r['reference']}/{nr}" if nr else ""
        lines.append(f'<div class="drow"><span>{esc(r["label"])}</span><span>{r["share"]:.0%}</span>'
                     f'<em>增长商品 {r["count"]}/{nf}{ref}</em>{_meter(r["share"], r["reference_share"])}</div>')
    return f'<div class="dbox"><h4>主材质</h4>{"".join(lines)}</div>'


def _evidence_lines(f: dict, vision: dict) -> list[str]:
    t, i = f.get("title"), f.get("image")
    ev = []
    if t:
        ev.append(f"标题：增长商品 {t['share']:.0%} · 全部头部商品 {t['baseline_share']:.0%}")
    if i:
        ref = f" · 持续热销对照组 {i['reference']}/{vision['n_reference']}" if vision.get("n_reference") else ""
        ev.append(f"主图：增长商品 {i['focus']}/{vision['n_focus']}{ref}")
    return ev


def _feature_box(dim: str, rows: dict, vision: dict) -> str:
    """一个维度：先列“更多”的特征（带依据和进度条），最后一行是“常见”的特征。"""
    lines = []
    for f in rows.get("more") or []:
        t, i = f.get("title"), f.get("image")
        src, share, base = ("主图", i["focus_share"], i["reference_share"]) if i else ("标题", t["share"], t["baseline_share"])
        lines.append(f'<div class="drow"><span>{esc(f["label"])}</span><span>{src} {share:.0%}</span>'
                     f'<em>{"<br>".join(esc(e) for e in _evidence_lines(f, vision))}</em>{_meter(share, base)}</div>')
    if not lines:
        lines.append('<p class="empty">没有比对照组明显更多的特征。</p>')
    common = []
    for f in rows.get("common") or []:
        t, i = f.get("title"), f.get("image")
        if i:
            ref = f"，对照组 {i['reference']}/{vision['n_reference']}" if vision.get("n_reference") else ""
            common.append(f"{f['label']} {i['focus']}/{vision['n_focus']}（{ref.lstrip('，')}）" if ref else
                          f"{f['label']} {i['focus']}/{vision['n_focus']}")
        else:
            common.append(f"{f['label']} {t['share']:.0%}（全部头部商品 {t['baseline_share']:.0%}）")
    tail = (f'<div class="common"><em>常见但不比对照组多</em>{esc("；".join(common))}</div>' if common else "")
    return f'<div class="dbox"><h4>{esc(dim)}</h4>{"".join(lines)}{tail}</div>'


def _structure(tr: dict) -> str:
    rows = []
    for f in tr.get("facts", []):
        rows.append(
            f"<tr><td>{esc(f['dimension'])}</td><td>{esc(f['value'])}</td>"
            f"<td class=n>{f['share']:.0%}</td><td class=n>{f['baseline_share']:.0%}</td></tr>")
    table = ("<div class=tbl><table><thead><tr><th>维度</th><th>特征</th><th class=n>增长商品</th>"
             "<th class=n>全部头部商品</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table></div>") if rows else '<p class="empty">没有明显偏多的结构特征。</p>'
    kws = "".join(f"<span title=\"增长商品 {k['share']:.0%}，全部头部商品 {k['baseline_share']:.0%}\">"
                  f"{esc(k['term'])}<b>{k['share']:.0%}</b></span>" for k in tr.get("keywords", []))
    numbers = []
    for name, v in (tr.get("numbers") or {}).items():
        if v.get("focus") is not None and v.get("baseline") is not None:
            fmt = fmt_price if "价格" in name else (lambda x: fmt_num(x, 1))
            numbers.append(f"<span class=chip>{esc(name)}：增长商品 {fmt(v['focus'])} · 全部头部商品 {fmt(v['baseline'])}</span>")
    return (f"<details><summary>其他特征：子类目、价格段、上架时长、标题高频词</summary>{table}"
            + (f"<div class=kw style=\"margin-top:10px\">{kws}</div>" if kws else "")
            + (f"<div class=chips style=\"margin-top:10px\">{''.join(numbers)}</div>" if numbers else "")
            + "</details>")


def _traits(tr: dict, counts: dict, cfg: dict) -> str:
    if tr.get("note"):
        return f'<p class="empty">{esc(tr["note"])}</p>'
    vision = tr.get("vision") or {}
    mats = tr.get("materials") or {}
    sample = (f'<a href="#" data-pop="growth">增长商品 {tr["n_focus"]} 个</a>（突然爆火 {counts["surge"]} + 潜力 {counts["potential"]}'
              f' + 上升中 {counts.get("rising", 0)}）。标题统计对比全部头部商品 {tr["n_baseline"]} 个；')
    if vision.get("n_focus"):
        sample += f'主图识别看增长商品里势头最强的 {vision["n_focus"]} 个；'
    a = cfg["thresholds"]["appearance"]
    sample += (f'主材质看全部增长商品（每个商品一类，依次取亚马逊 Material 属性、标题、主图识别）；'
               f'后两者都对比持续热销对照组（月销量最高的 {mats.get("n_reference") or 0} 个）。'
               f'列出的特征至少 {a["min_count"]} 个增长商品有，且标题里占比 ≥{a["title_min_share"]:.0%}、是全部头部商品的 '
               f'{a["title_min_lift"]:g} 倍以上，或主图里比对照组多 {a["image_min_gap"] * 100:.0f} 个百分点以上；两种依据方向相反的不列。'
               f'“常见”= 增长商品里 ≥{a["common_share"]:.0%} 有、但不比对照组多。'
               '右侧数字是增长商品中的占比（两种依据都有时取主图），进度条竖线是对比对象中的占比。')
    boxes = [_material_box(mats)]
    for dim, rows in (tr.get("appearance") or {}).items():
        boxes.append(_feature_box(dim, rows, vision))
    body = "".join(b for b in boxes if b)
    if not tr.get("appearance"):
        body += '<p class="empty">增长商品没有明显偏多或常见的外观/工艺特征。</p>'
    return f'<p class="hint">{sample}</p><div class="dgrid">{body}</div>{_structure(tr)}'


def _changes(diff: dict, by_asin: dict) -> str:
    if diff.get("first"):
        return '<p class="empty">本期没有可对比的上一期（首期或监控范围刚调整），从下一期开始显示变化。</p>'
    names = {"surge": "突然爆火", "potential": "潜力", "hot": "持续热销", "fake": "异常信号", "watch": "观察",
             "low": "评分偏低"}

    def name(asin: str) -> str:
        item = by_asin.get(asin)
        title = (item or {}).get("title", asin)
        name = display_name((item or {}).get("brand", ""), title)
        return f"<a href=\"https://www.amazon.com/dp/{esc(asin)}\" target=_blank rel=\"noopener noreferrer\">" \
               f"{esc(name[:40])}</a>"

    parts = []
    for label in ("surge", "potential", "fake", "hot"):
        asins = diff["new"].get(label) or []
        if asins:
            parts.append(f"<li>新进入「{names[label]}」{len(asins)} 个：" + "、".join(name(a) for a in asins[:5])
                         + ("…" if len(asins) > 5 else "") + "</li>")
    moves = [t for t in diff.get("transitions", []) if t["from"] != "watch" and t["to"] != "watch"]
    for t in moves[:8]:
        parts.append(f"<li>{name(t['asin'])}：{names.get(t['from'], t['from'])} → <b>{names.get(t['to'], t['to'])}</b></li>")
    if diff.get("cooled"):
        parts.append(f"<li>不再爆火 {len(diff['cooled'])} 个：" + "、".join(name(a) for a in diff["cooled"][:5])
                     + ("…" if len(diff["cooled"]) > 5 else "") + "</li>")
    if not parts:
        return '<p class="empty">与上期相比没有明显变化。</p>'
    return f'<p class="hint">对比上期 {esc(diff.get("prev_id", ""))}</p><ul>{"".join(parts)}</ul>'


def _all_table(items: list[dict]) -> str:
    order = {"fake": 0, "surge": 1, "potential": 2, "hot": 3, "watch": 4, "low": 5}
    rows = []
    for i in sorted(items, key=lambda x: (order[x["label"]], -(x.get("recent_avg28") or 0))):
        rows.append(
            f"<tr><td><a href=\"https://www.amazon.com/dp/{esc(i['asin'])}\" target=_blank rel=\"noopener noreferrer\">"
            f"{esc(i['asin'])}</a></td><td>{esc(i['brand'])}</td><td>{esc(i['node_cn'] or i['node_name'])}</td>"
            f"<td>{esc(i['label_cn'])}</td><td>{esc(REASON_CN.get(i.get('source') or '', ''))}</td>"
            f"<td class=n>{fmt_num(i.get('recent_avg28'))}</td>"
            f"<td class=n>{fmt_int(i.get('bsr'))}</td><td class=n>{fmt_price(i.get('price'))}</td>"
            f"<td class=n>{fmt_num(i.get('rating'))}</td><td class=n>{fmt_int(i.get('ratings'))}</td>"
            f"<td class=n>{i['fake']['score']}</td>"
            f"<td>{esc('、'.join(i['tags']))}</td></tr>")
    return ("<div class=tbl><table><thead><tr><th>ASIN</th><th>品牌</th><th>子类目</th><th>板块</th><th>来源</th>"
            "<th class=n>近28天日均</th><th class=n>BSR</th><th class=n>价格</th><th class=n>评分</th><th class=n>评论</th>"
            "<th class=n>异常分</th><th>标签</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def section_hints(cfg: dict) -> dict[str, str]:
    """每个小窗顶部的入选条件（和判定代码一一对应，数字直接取自 config.yaml）。共用概念见 _method。"""
    th = cfg["thresholds"]
    s, h, p, f, r = th["surge"], th["hot"], th["potential"], th["fake"], th["rising"]
    windows = " 或 ".join(map(str, s["windows"]))
    return {
        "surge": f"近 {windows} 天对比之前 {s['base_days']} 天：BSR 中位数降到 {s['bsr_ratio']} 倍以下（排名提升一倍以上），"
                 f"日均销量 ≥{s['sales_ratio']:g} 倍且 ≥{s['min_daily_sales']} 件；形态是新品爆发 / 稳在高位 / 仍在上涨之一；"
                 f"没有回落；异常分 <{f['suspect_score']}（轻微信号列在卡片上）。按爆发强度排序。",
        "potential": f"上架 {p['min_age_days']}~{p['max_age_days']} 天、评论 <{p['max_ratings']} 条；近 28 天日均 ≥{p['min_daily_sales']} 件、"
                     f"≥ 之前 28 天的 {p['min_recent_ratio']} 倍；有两个以上完整月份的，月销量平均每月增长 ≥{p['min_monthly_growth']:.0%}；"
                     f"没有回落；异常分 <{f['suspect_score']}。按月销量增长排序（不满两个完整月份的按近 28 天增长）。",
        "hot": f"近 {h['lookback_months']} 个月里至少 {h['months_required']} 个月的月销量达到所在子类目第 {h['rank_in_node']} 名的水平；"
               f"近 6 个月波动系数 ≤{h['max_cv']}；近 3 个月平均每月跌幅 ≤{-h['min_trend']:.0%}；"
               f"近 28 天日均 ≥ 近 6 个月日均的 {h['min_recent_ratio']:.0%}；异常分 <{f['suspect_score']}。按近 6 个月月均销量排序。",
        "fake": f"异常分 ≥{f['suspect_score']} 的商品，不论是否在增长；同时满足爆火条件的标“假爆火嫌疑”。"
                f"仅为数据异常提示，需人工核实。按异常分排序。",
        "growth": f"增长商品 = 突然爆火 + 潜力 + 上升中。上升中 = 不在前两个板块、近 28 天日均 ≥ 之前 28 天的 {r['min_ratio']} 倍"
                  f"且 ≥{r['min_daily_sales']} 件、没有回落。“外观与工艺依据”就是从这些商品统计的。按势头排序。",
        "all": "本期追踪的全部商品。“板块”是本期归入的板块（观察 = 不属于任何板块），“来源”是它被选进追踪的原因。",
    }


def _method(cfg: dict) -> str:
    """共用概念的定义。各板块自己的入选条件写在对应小窗顶部，这里不重复。"""
    th = cfg["thresholds"]
    s, f, m = th["surge"], th["fake"], th["momentum"]
    return f"""<details><summary>判定方法与数据口径</summary><ul class="hint">
<li><b>数据</b>：卖家精灵的月度头部名单（product_research，已结束月份）、近 30 天榜单（不传月份，截至当天）、
每个追踪商品约 400 天的日销量 / BSR / 价格（asin_prediction）。日销量是卖家精灵按 BSR 估算的，所以以 BSR 中位数为主信号。
所有判定都基于每个商品截至最近一天的日数据；日数据超过 {th['stale_days']} 天没更新的标“数据陈旧”，不做爆火/潜力判断。</li>
<li><b>追踪名额</b>：依次给必看（往期爆火/潜力/异常 + 每个子类目销量前 {cfg['pool']['per_node_top']} + 全部类目里销量最大的）、
上期爆款的相似款、机会候选（近 30 天榜单和月度名单里增长快或上架半年内的；机会分最高的 {cfg['pool']['opportunity_every_run']} 个每期都看，其余按最久没查轮流查）。
机会分综合销量增长率、近 7 天 BSR 改善、子类目内销量位次、是否上架半年内。</li>
<li><b>板块归属</b>：每个商品只归入一个板块，优先级：评分不达标 &gt; 异常信号 &gt; 突然爆火 &gt; 潜力 &gt; 持续热销；都不属于的为“观察”。
同时也符合潜力 / 持续热销条件的，在卡片上标“潜力”/“长期头部”。各板块的入选条件见点开后小窗顶部。</li>
<li><b>评分门槛</b>：评分低于 {th['min_rating']}、或评论不到 {th['few_ratings']} 条且评分低于 {th['few_ratings_min_rating']}（评论少时几条差评就会跌破
{th['min_rating']}，卖家精灵的评分也比亚马逊晚几天）的，不进任何板块和增长商品，也不占追踪名额，由后面的商品依次补位。
评分优先取最新日数据；还没有评论的新品不受限。</li>
<li><b>回落</b>：近 7 天日均比最近 4 周里最高的一周低 {1 - m['min_vs_peak_week']:.0%} 以上，或最近 3 天日均比近 7 天低 {1 - m['min_last3_vs_7d']:.0%} 以上。
突然爆火、潜力、上升中都要求没有回落，有回落的标“最近回落”。</li>
<li><b>突然爆火的形态</b>（依次判断）：<b>新品爆发</b> = 之前 {s['base_days']} 天几乎没卖（日均 &lt;0.5 件），且日均 ≥{s['new_listing_min_daily']} 件、
≥ 所在子类目第 20 名水平的 {s['new_listing_node_share']:.0%}（量不够的标“新品起量（未达爆火门槛）”）；
<b>稳在高位</b> = 近 7 天里 ≥{s['sustained_days']} 天达到之前水平的 {s['sustained_mult']} 倍，且最近 3 天日均与近 7 天相差 ≤{s['steady_band']:.0%}；
<b>仍在上涨</b> = 近 7 天日均比前 7 天高 ≥{s['climb_ratio'] - 1:.0%}，且最近 3 天日均高于近 7 天。
近 7 天的增量 ≥{s['pulse_share']:.0%} 来自 ≤2 天的是“短时脉冲”，其余都不符合的是“上涨形态不稳定”，都不算爆火。
<b>爆发强度</b> = 销量倍数（最多按 10 倍算）× ln(1 + 近期日均)。</li>
<li><b>爆火的附加标签</b>：季节性 = 去年同期也涨了 ≥{s['seasonal_ratio']} 倍；降价驱动 = 价格中位数下降 ≥{s['price_drop']:.0%}；
大促 = 上涨期间与大促日期重叠。对比期里大部分天没有销量、而更早时卖得和现在差不多的是“断货恢复”，不算爆火。</li>
<li><b>外观与工艺依据</b>：材质只有“主材质”一种口径，每个商品一类（铁木 / 实木 / 板材 / 金属…），依次取亚马逊商品详情的 Material 属性、
标题里写明的材质、AI 主图识别，都没有的按标题记为“木质（未注明）”或“未注明”。风格、造型、工艺、颜色有两种依据——
标题统计（增长商品 vs 全部头部商品）和 AI 主图识别（增长商品里势头最强的 {cfg['llm']['vision_focus']} 个 vs 持续热销对照组），
同一特征合并成一行；入选门槛写在该板块顶部，两种依据方向相反的不算趋势、不列出；“常见”只说明增长商品里多数有，不代表比对照组多。</li>
<li><b>异常信号</b>：留评率 ≥ 子类目中位数的 {f['review_rate_mult']} 倍、新品评论/销量比过高、两期之间评论增速远超销量、评分短期跳升、
近 30 天的短时脉冲（1~3 天冲到基线 {f['pulse_mult']} 倍后迅速回落）、评论集中在少数几天、非验证购买占比高，各记 1~2 分；
累计 ≥{f['suspect_score']} 分为疑似、≥{f['high_score']} 分为高度疑似。变体多的商品评论为父体共享，不算评论/销量比；
评论暴增且父体/变体变化的标“变体合并”、短时脉冲落在大促里的标“大促脉冲”、抽查评论里 Vine 评论 ≥30% 的标“Vine 评论多”，这三项不计分。</li>
<li><b>与上期对比</b>：“不再爆火”= 上期爆火、本期留在追踪里但不在爆火 / 潜力 / 异常信号板块。</li>
<li>阈值都可以在仓库的 config.yaml 中调整。</li></ul></details>"""


def render(ctx: dict) -> str:
    sec = ctx["sections"]
    counts = sec["counts"]
    cfg = ctx["cfg"]
    by_asin = {i["asin"]: i for i in ctx["items"]}
    hints = section_hints(cfg)
    kpi = "".join(
        f'<button type="button" class="kpi" data-pop="{k}" style="--c:var(--{k})"><b>{counts[k]}</b><span>{label}</span></button>'
        for k, label in (("surge", "突然爆火"), ("potential", "潜力产品"), ("hot", "持续热销"), ("fake", "异常信号")))
    cov = ctx["coverage"]
    facts = [f"月度名单 {ctx['period_text']}", f"日数据截至 {ctx['as_of']}",
             f"追踪 {counts['total']} 个（本期刷新 {cov['refreshed']} 个）"]
    if cov.get("candidates"):
        facts.append(f"机会候选 {cov['candidates']} 个，本期查了 {cov.get('opportunity') or 0} 个")
    if counts.get("low"):
        facts.append(f"评分不达标未上榜 {counts['low']} 个")
    facts += [f"卖家精灵调用 {fmt_int(cov.get('calls'))} 次",
              f"本期要点：{'AI 生成' if ctx['summary_source'] == 'llm' else '模板'}"]
    history = ""
    if ctx.get("history"):
        history = " · 往期 " + " ".join(f'<a href="{esc(h["href"])}">{esc(h["id"])}</a>' for h in ctx["history"])
    pops = "".join([
        _section_pop("surge", "突然爆火", hints["surge"], sec["surge"], counts["surge"]),
        _section_pop("potential", "潜力产品", hints["potential"], sec["potential"], counts["potential"]),
        _section_pop("hot", "持续热销", hints["hot"], sec["hot"], counts["hot"]),
        _section_pop("fake", "异常信号", hints["fake"], sec["fake"], counts["fake"]),
        _growth_pop(ctx.get("focus") or [], hints["growth"]),
        _pop_src("all", f"全部追踪商品（{counts['total']} 个）", "muted",
                 f'<p class="hint">{esc(hints["all"])}</p>{_all_table(ctx["items"])}'),
    ])
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer"><title>{esc(ctx['title'])} {esc(ctx['report_id'])}</title>
<style>{CSS}</style></head><body><div class="wrap">
<header><div class="sub">{esc(cfg['report'].get('subtitle') or '亚马逊美国站 · 家具')}</div><h1>{esc(ctx['title'])}</h1>
<div class="sub hist">{esc(ctx['generated_at'])} 生成 · 第 {ctx['run_seq']} 期 · 日数据截至 {esc(ctx['as_of'])}{history}</div></header>
<div class="kpis">{kpi}</div><p class="sub kpi-hint">点数字查看商品清单</p>
<section class="summary" style="--c:var(--accent)"><h2><span class="dot"></span>本期要点</h2>{markdown(ctx['summary'])}</section>
<section id="traits" style="--c:var(--potential)"><h2><span class="dot"></span>外观与工艺依据</h2>{_traits(ctx['traits'], counts, cfg)}</section>
<section id="changes" style="--c:var(--hot)"><h2><span class="dot"></span>与上期对比</h2>{_changes(ctx['diff'], by_asin)}</section>
<section id="about" style="--c:var(--muted)"><h2><span class="dot"></span>数据说明</h2>
<p class="sub">{' · '.join(esc(x) for x in facts)}</p>
<p class="sub"><a href="#" data-pop="all">查看全部 {counts['total']} 个追踪商品</a></p>{_method(cfg)}</section>
<footer>{esc(ctx['title'])} · 数据来源：卖家精灵 · 报告已加密，仅持有链接的人可查看</footer>
</div>{pops}{POPUP}</body></html>"""

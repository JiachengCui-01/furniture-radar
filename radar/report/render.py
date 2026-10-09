"""生成报告 HTML（明文）。之后由 shell.py 加密包装再发布。

单文件、无外部脚本/字体，只有商品图片引用亚马逊图床；适配手机和深色模式。
"""
from __future__ import annotations

import html
import re

from ..analyze import display_name
from .sparkline import bars, sparkline

LABEL_COLOR = {"surge": "surge", "potential": "potential", "hot": "hot", "fake": "fake", "watch": "muted"}


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
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;border-top:3px solid var(--c)}
.kpi b{display:block;font-size:26px;line-height:1.2;color:var(--c)}
.kpi span{font-size:13px;color:var(--muted)}
@media (max-width:560px){.kpis{grid-template-columns:repeat(2,1fr)}}
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
.hist a{margin-right:10px}
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
.drow b{color:var(--potential)}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px;margin:8px 0}
.gitem{border:1px solid var(--line);border-radius:10px;padding:8px;font-size:12.5px;border-top:3px solid var(--c)}
.gitem img{width:100%;height:140px;object-fit:contain;background:#fff;border-radius:8px;display:block}
.gitem p{margin:6px 0 4px;line-height:1.45}
.gitem .gt{font-weight:600;display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden}
.vchips{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0}
.vchips span{border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:13px}
.vchips span i{font-style:normal;color:var(--muted);font-size:12px;margin-left:4px}
.vchips span.hi{border-color:var(--potential);background:rgba(43,138,62,.08)}
h3.sub3{font-size:15px;margin:16px 0 6px}
"""


# 顶部四个数字点开后以小窗展示对应板块，不跳转页面；没有脚本时退回为页内跳转
POPUP = """<div class="pop" id="pop" hidden><div class="pop-mask" data-close></div>
<div class="pop-panel" role="dialog" aria-modal="true" aria-labelledby="pop-title">
<div class="pop-head"><b id="pop-title"></b><button type="button" class="pop-x" data-close aria-label="关闭">×</button></div>
<div class="pop-body" id="pop-body"></div></div></div>
<script>(function(){
var pop=document.getElementById('pop'),body=document.getElementById('pop-body'),title=document.getElementById('pop-title');
function closest(el,sel){while(el&&el.nodeType===1){if(el.matches?el.matches(sel):el.msMatchesSelector(sel))return el;el=el.parentNode;}return null;}
function open(id){var sec=document.getElementById(id);if(!sec)return false;var src=sec.querySelector('.pop-src');if(!src)return false;
title.textContent=sec.querySelector('h2').textContent;body.innerHTML=src.innerHTML;
pop.querySelector('.pop-panel').style.setProperty('--c',sec.style.getPropertyValue('--c'));
pop.hidden=false;document.documentElement.className+=' noscroll';body.scrollTop=0;return true;}
function close(){pop.hidden=true;document.documentElement.className=document.documentElement.className.replace(/\\s*noscroll/g,'');}
document.addEventListener('click',function(e){var t=closest(e.target,'[data-pop]');
if(t){if(open(t.getAttribute('data-pop')))e.preventDefault();return;}
if(closest(e.target,'[data-close]'))close();});
document.addEventListener('keydown',function(e){if(e.key==='Escape'||e.keyCode===27)close();});
})();</script>"""


def _tags(item: dict) -> str:
    out = []
    for tag in item["tags"]:
        cls = "warn" if any(k in tag for k in ("假爆火", "降价", "陈旧", "脉冲", "断货")) else \
            "good" if tag in ("持续型", "潜力", "爬升型", "新品起量") else \
            "info" if any(k in tag for k in ("季节", "大促", "热销", "变体")) else ""
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
    meta = " · ".join(x for x in (esc(item.get("brand")), esc(node), fmt_price(item.get("price")), rating, age,
                                  f"ASIN {esc(item['asin'])}") if x)
    return (f'<a href="{url}" target="_blank" rel="noopener noreferrer">{img}</a><div>'
            f'<div class="t"><a href="{url}" target="_blank" rel="noopener noreferrer">{esc(item["title"])}</a></div>'
            f'<div class="meta">{meta}</div>')


def card(item: dict, kind: str) -> str:
    c = LABEL_COLOR.get(kind, "muted")
    kv: list[str] = []
    s = item.get("surge")
    if kind == "surge" or (kind == "fake" and s):
        ratio = f"×{s['sales_ratio']:.1f}" if s.get("sales_ratio") else "从零起量"
        kv.append(f"<span><em>近{s['window']}天日均</em> {fmt_num(s['base_avg'])} → <b>{fmt_num(s['recent_avg'])}</b> 件"
                  f"（{ratio}）</span>")
        if s.get("base_bsr") and s.get("recent_bsr"):
            kv.append(f"<span><em>BSR</em> {fmt_int(s['base_bsr'])} → <b>{fmt_int(s['recent_bsr'])}</b></span>")
        if s.get("last_year_ratio"):
            kv.append(f"<span><em>去年同期</em> ×{s['last_year_ratio']:.1f}</span>")
    if kind == "potential" and item.get("potential"):
        p = item["potential"]
        if p.get("growth") is not None:
            kv.append(f"<span><em>月环比</em> <b>{fmt_pct(p['growth'])}</b></span>")
        kv.append(f"<span><em>近28天日均</em> {fmt_num(p.get('avg28'))} 件</span>")
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
    if kind not in ("surge",) and item.get("recent_avg28") is not None and kind != "potential":
        kv.append(f"<span><em>近28天日均</em> {fmt_num(item['recent_avg28'])} 件</span>")

    chart = bars(item["monthly"][-12:]) if kind == "hot" else sparkline(item.get("spark") or [])
    signals = ""
    if item["fake"]["signals"] and kind in ("fake", "surge", "potential"):
        signals = "<ul class=\"signals\">" + "".join(
            f"<li>{esc(sig['text'])}（+{sig['points']}）</li>" for sig in item["fake"]["signals"]) + "</ul>"
    return (f'<article class="card" style="--c:var(--{c})">{_head(item)}'
            f'<div class="kv">{"".join(kv)}</div>{_tags(item)}{signals}{chart}</div></article>')


def _section(kind: str, title: str, hint: str, items: list[dict], total: int) -> str:
    more = f"（共 {total} 个，展示前 {len(items)} 个）" if total > len(items) else ""
    body = "".join(card(i, kind) for i in items) if items else '<div class="empty">本期没有符合条件的商品。</div>'
    return (f'<section id="{kind}" style="--c:var(--{LABEL_COLOR[kind]})"><h2><span class="dot"></span>{esc(title)}</h2>'
            f'<div class="pop-src"><p class="hint">{esc(hint)}{more}</p><div class="cards">{body}</div></div></section>')


def _meter(share: float, base: float | None) -> str:
    tick = f'<u style="left:{min(100, base * 100):.0f}%"></u>' if base is not None else ""
    return f'<div class="meter m"><i style="width:{min(100, share * 100):.0f}%"></i>{tick}</div>'


def _design_boxes(design: dict, n_focus: int) -> str:
    boxes = []
    for dim, rows in design.items():
        lines = "".join(
            f'<div class="drow"><span>{esc(r["label"])}</span><span><b>×{r["lift"]}</b></span>'
            f'<em>爆火/潜力 {r["count"]}/{n_focus}（{r["share"]:.0%}）· 全部家具 {r["baseline_share"]:.0%}</em><span></span>'
            f'{_meter(r["share"], r["baseline_share"])}</div>' for r in rows)
        boxes.append(f'<div class="dbox"><h4>{esc(dim)}</h4>{lines}</div>')
    return f'<div class="dgrid">{"".join(boxes)}</div>'


def _vision_block(v: dict) -> str:
    if not v or not v.get("n_focus"):
        return ""
    parts = [f'<h3 class="sub3">主图识别：爆火/潜力商品长什么样</h3>'
             f'<p class="hint">AI 看了 {v["n_focus"]} 个爆火/潜力商品的主图'
             + (f'，并对照 {v["n_reference"]} 个持续热销商品' if v.get("n_reference") else "")
             + '。数字为“出现该特征的商品数 / 识别数”；绿框 = 在爆火/潜力里明显比持续热销更常见的外观。</p>']
    for dim, rows in (v.get("dimensions") or {}).items():
        chips = "".join(
            f'<span{" class=hi" if r.get("distinct") else ""}>{esc(r["label"])} <b>{r["focus"]}/{v["n_focus"]}</b>'
            + (f'<i>热销 {r["reference"]}/{v["n_reference"]}</i>' if v.get("n_reference") else "")
            + '</span>' for r in rows)
        parts.append(f'<div class="drow" style="margin:8px 0"><span><b style="color:var(--text)">{esc(dim)}</b></span>'
                     f'<span></span><div class="vchips m">{chips}</div></div>')
    gallery = []
    for g in v.get("gallery") or []:
        c = "surge" if g["label"] == "surge" else "potential" if g["label"] == "potential" else "hot"
        tags = [t for dim in ("风格", "材质", "造型", "工艺", "颜色") for t in (g["tags"].get(dim) or [])][:6]
        img = f'<img src="{esc(g["image"])}" alt="" loading="lazy" referrerpolicy="no-referrer">' if g.get("image") else ""
        gallery.append(
            f'<a class="gitem" style="--c:var(--{c});color:inherit;text-decoration:none" target="_blank" '
            f'rel="noopener noreferrer" href="https://www.amazon.com/dp/{esc(g["asin"])}">{img}'
            f'<p class="gt">{esc(display_name(g.get("brand") or "", g.get("title") or ""))}</p>'
            f'<p>{esc(g.get("summary") or "")}</p>'
            f'<div class="tags">{"".join(f"<span class=tag>{esc(t)}</span>" for t in tags)}</div></a>')
    if gallery:
        parts.append(f'<div class="gallery">{"".join(gallery)}</div>')
    return "".join(parts)


def _structure(tr: dict) -> str:
    rows = []
    for f in tr.get("facts", []):
        rows.append(
            f"<tr><td>{esc(f['dimension'])}</td><td>{esc(f['value'])}</td>"
            f"<td class=n>{f['share']:.0%}</td><td class=n>{f['baseline_share']:.0%}</td>"
            f"<td class=n>×{f['lift']}</td></tr>")
    table = ("<div class=tbl><table><thead><tr><th>维度</th><th>特征</th><th class=n>爆火/潜力中</th>"
             "<th class=n>全部家具中</th><th class=n>提升</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table></div>") if rows else '<p class="empty">没有显著偏高的结构特征。</p>'
    kws = "".join(f"<span title=\"爆火/潜力中 {k['share']:.0%}，全部家具中 {k['baseline_share']:.0%}\">"
                  f"{esc(k['term'])}<b>×{k['lift']}</b></span>" for k in tr.get("keywords", []))
    numbers = []
    for name, v in (tr.get("numbers") or {}).items():
        if v.get("focus") is not None and v.get("baseline") is not None:
            fmt = fmt_price if "价格" in name else (lambda x: fmt_num(x, 1))
            numbers.append(f"<span class=chip>{esc(name)}：{fmt(v['focus'])}（全部 {fmt(v['baseline'])}）</span>")
    return (f"<details><summary>其他特征：子类目、价格段、上架时长、标题高频词</summary>{table}"
            + (f"<div class=kw style=\"margin-top:10px\">{kws}</div>" if kws else "")
            + (f"<div class=chips style=\"margin-top:10px\">{''.join(numbers)}</div>" if numbers else "")
            + "</details>")


def _traits(tr: dict) -> str:
    if tr.get("note"):
        return f'<p class="empty">{esc(tr["note"])}</p>'
    design = tr.get("design") or {}
    appearance = {d: rows for d, rows in design.items() if d != "功能卖点"}
    parts = [f'<p class="hint">本期真爆火 + 潜力 {tr["n_focus"]} 个，对比全部家具头部商品 {tr["n_baseline"]} 个。'
             f'“×倍数”= 这个特征在爆火/潜力商品里出现的比例 ÷ 在全部家具里的比例；进度条竖线为全部家具中的占比。</p>']
    if appearance:
        parts.append('<h3 class="sub3">外观与工艺（来自商品标题）</h3>' + _design_boxes(appearance, tr["n_focus"]))
    else:
        parts.append('<p class="empty">标题里没有明显偏多的外观/工艺特征。</p>')
    parts.append(_vision_block(tr.get("vision") or {}))
    if design.get("功能卖点"):
        parts.append('<h3 class="sub3">功能卖点</h3>' + _design_boxes({"功能卖点": design["功能卖点"]}, tr["n_focus"]))
    parts.append(_structure(tr))
    return "".join(parts)


def _changes(diff: dict, by_asin: dict) -> str:
    if diff.get("first"):
        return '<p class="empty">这是第一期报告，从下一期开始显示与上期的对比。</p>'
    names = {"surge": "突然爆火", "potential": "潜力", "hot": "持续热销", "fake": "异常信号", "watch": "观察"}

    def name(asin: str) -> str:
        item = by_asin.get(asin)
        title = (item or {}).get("title", asin)
        name = display_name((item or {}).get("brand", ""), title)
        return f"<a href=\"https://www.amazon.com/dp/{esc(asin)}\" target=_blank rel=\"noopener noreferrer\">" \
               f"{esc(name[:48])}</a>"

    parts = []
    for label in ("surge", "potential", "fake", "hot"):
        asins = diff["new"].get(label) or []
        if asins:
            parts.append(f"<li>新进入「{names[label]}」{len(asins)} 个：" + "、".join(name(a) for a in asins[:8])
                         + ("…" if len(asins) > 8 else "") + "</li>")
    moves = [t for t in diff.get("transitions", []) if t["from"] != "watch" and t["to"] != "watch"]
    for t in moves[:12]:
        parts.append(f"<li>{name(t['asin'])}：{names.get(t['from'], t['from'])} → <b>{names.get(t['to'], t['to'])}</b></li>")
    if diff.get("cooled"):
        parts.append(f"<li>爆火回落 {len(diff['cooled'])} 个：" + "、".join(name(a) for a in diff["cooled"][:8]) + "</li>")
    if not parts:
        return '<p class="empty">与上期相比没有明显变化。</p>'
    return f'<p class="hint">对比上期 {esc(diff.get("prev_id", ""))}</p><ul>{"".join(parts)}</ul>'


def _all_table(items: list[dict]) -> str:
    order = {"fake": 0, "surge": 1, "potential": 2, "hot": 3, "watch": 4}
    rows = []
    for i in sorted(items, key=lambda x: (order[x["label"]], -(x.get("recent_avg28") or 0))):
        rows.append(
            f"<tr><td><a href=\"https://www.amazon.com/dp/{esc(i['asin'])}\" target=_blank rel=\"noopener noreferrer\">"
            f"{esc(i['asin'])}</a></td><td>{esc(i['brand'])}</td><td>{esc(i['node_cn'] or i['node_name'])}</td>"
            f"<td>{esc(i['label_cn'])}</td><td class=n>{fmt_num(i.get('recent_avg28'))}</td>"
            f"<td class=n>{fmt_int(i.get('bsr'))}</td><td class=n>{fmt_price(i.get('price'))}</td>"
            f"<td class=n>{fmt_int(i.get('ratings'))}</td><td class=n>{i['fake']['score']}</td>"
            f"<td>{esc('、'.join(i['tags']))}</td></tr>")
    return ("<div class=tbl><table><thead><tr><th>ASIN</th><th>品牌</th><th>子类目</th><th>判定</th>"
            "<th class=n>近28天日均</th><th class=n>BSR</th><th class=n>价格</th><th class=n>评论</th>"
            "<th class=n>异常分</th><th>标签</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def _method(cfg: dict) -> str:
    th = cfg["thresholds"]
    s, h, p, f = th["surge"], th["hot"], th["potential"], th["fake"]
    return f"""<details><summary>判定方法与数据口径</summary><ul class="hint">
<li>数据来自卖家精灵：月度头部商品（product_research，已结束月份）+ 每个 ASIN 约 400 天的日销量/BSR/价格（asin_prediction）。
日销量是卖家精灵根据 BSR 估算的，因此以 BSR 中位数为主信号，销量用于门槛和倍数。</li>
<li><b>突然爆火</b>：近 {'/'.join(map(str, s['windows']))} 天 BSR 中位数 ≤ 之前 {s['base_days']} 天的 {s['bsr_ratio']} 倍，
且日均销量 ≥ 之前的 {s['sales_ratio']} 倍、≥ {s['min_daily_sales']} 件。持续型 = 近 7 天 ≥{s['sustained_days']} 天达到基线 {s['sustained_mult']} 倍；
脉冲型 = ≤2 天贡献 ≥{s['pulse_share']:.0%} 增量；季节性 = 去年同期也涨了 ≥{s['seasonal_ratio']} 倍；降价驱动 = 价格下降 ≥{s['price_drop']:.0%}。
对比窗口里大部分天没有销量、而更早时卖得和现在差不多的，判为“断货恢复”，不计入爆火。</li>
<li><b>持续热销</b>：近 {h['lookback_months']} 个月中 ≥{h['months_required']} 个月月销量达到所在子类目 Top{h['rank_in_node']} 水平，
近 6 个月波动系数 ≤{h['max_cv']}，近 3 个月趋势不低于 {h['min_trend']:.0%}/月，且近 28 天没有明显下滑。</li>
<li><b>潜力</b>：上架 {p['min_age_days']}~{p['max_age_days']} 天，月销量增长 ≥{p['min_monthly_growth']:.0%}/月，
近 28 天日均 ≥ 再之前 28 天的 {p['min_recent_ratio']} 倍，评论 &lt;{p['max_ratings']}、评分 ≥{p['min_rating']}，且无异常信号。</li>
<li><b>异常信号（假爆火）</b>：留评率异常、新品评论/销量比过高、两期之间评论增速远超销量、评分短期跳升、短时脉冲、
评论集中在少数几天、非验证购买占比高等，累计 ≥{f['suspect_score']} 分为疑似、≥{f['high_score']} 分为高度疑似。
变体多的商品评论为父体共享，不计算评论/销量比；评论暴增且父体/变体变化标记为“变体合并”，不计分。异常信号仅供人工核实参考。</li>
<li>阈值都可以在仓库的 config.yaml 中调整。</li></ul></details>"""


def render(ctx: dict) -> str:
    sec = ctx["sections"]
    counts = sec["counts"]
    by_asin = {i["asin"]: i for i in ctx["items"]}
    kpi = "".join(
        f'<a class="kpi" href="#{k}" data-pop="{k}" role="button" style="--c:var(--{k});text-decoration:none;color:inherit">'
        f'<b>{counts[k]}</b><span>{label}</span></a>'
        for k, label in (("surge", "突然爆火"), ("potential", "潜力产品"), ("hot", "持续热销"), ("fake", "异常信号")))
    cov = ctx["coverage"]
    chips = "".join(f'<span class="chip">{esc(c)}</span>' for c in (
        f"数据月份 {ctx['period_text']}", f"日数据截至 {ctx['as_of']}",
        f"追踪 {counts['total']} 个 ASIN", f"本期刷新 {cov['refreshed']} 个",
        f"卖家精灵调用 {cov['calls']} 次", f"总结：{'AI 生成' if ctx['summary_source'] == 'llm' else '模板'}"))
    history = ""
    if ctx.get("history"):
        history = '<p class="hist sub">往期报告：' + "".join(
            f'<a href="{esc(h["href"])}">{esc(h["id"])}</a>' for h in ctx["history"]) + "</p>"
    top_n = ctx["top_n"]
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer"><title>{esc(ctx['title'])} {esc(ctx['report_id'])}</title>
<style>{CSS}</style></head><body><div class="wrap">
<header><div class="sub">亚马逊美国站 · 家具全类目</div><h1>{esc(ctx['title'])}</h1>
<div class="sub">{esc(ctx['generated_at'])} 生成 · 第 {ctx['run_seq']} 期</div><div class="chips">{chips}</div>{history}</header>
<div class="kpis">{kpi}</div>
<section class="summary" style="--c:var(--accent)"><h2><span class="dot"></span>本期简报</h2>{markdown(ctx['summary'])}</section>
{_section('surge', '突然爆火', '近期销量/排名明显跃升且没有异常信号的商品，按爆发强度排序。', sec['surge'], counts['surge'])}
<section id="traits" style="--c:var(--potential)"><h2><span class="dot"></span>爆火产品的外观与工艺特征</h2>{_traits(ctx['traits'])}</section>
{_section('potential', '潜力产品', '上架半年内、销量持续增长、评论还不多的商品，适合重点研究。', sec['potential'], counts['potential'])}
{_section('hot', '真正持续热销', '连续多个月保持子类目头部销量、波动小、没有异常信号的商品。', sec['hot'], counts['hot'])}
{_section('fake', '假爆火 / 异常信号', '增长伴随刷评、评论异常等信号的商品。仅为数据异常提示，需人工核实。', sec['fake'], counts['fake'])}
<section id="changes" style="--c:var(--hot)"><h2><span class="dot"></span>与上期对比</h2>{_changes(ctx['diff'], by_asin)}</section>
<section id="all" style="--c:var(--muted)"><h2><span class="dot"></span>全部追踪商品</h2>
<details><summary>展开 {counts['total']} 个商品明细</summary>{_all_table(ctx['items'])}</details>{_method(ctx['cfg'])}</section>
<footer>{esc(ctx['title'])} · 数据来源：卖家精灵 · 每个板块最多展示 {top_n} 个 · 报告已加密，仅持有链接的人可查看</footer>
</div>{POPUP}</body></html>"""

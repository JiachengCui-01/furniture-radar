"""生成报告 HTML（明文）。之后由 shell.py 加密包装再发布。

单文件、无外部脚本/字体，只有商品图片引用亚马逊图床；适配手机和深色模式。
主体只有：KPI 数字 → 本期要点 → 外观与工艺依据 → 与上期对比 → 数据说明。
商品清单（各板块、增长商品、全部追踪商品）都放在隐藏的小窗内容里，点 KPI 数字或链接才弹出。
"""
from __future__ import annotations

import html
import re

from .. import clock, shipping
from ..analyze import display_name
from ..detect import directions, events, seasonality
from ..tracking import REASON_CN
from .sparkline import HOVER_JS, bars, sparkline

LABEL_COLOR = {"surge": "surge", "potential": "potential", "hot": "hot", "fake": "fake", "watch": "muted",
               "low": "muted", "rising": "accent", "seasonal": "seasonal"}


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


PRODUCT_LINK = re.compile(r"\[([^\[\]]{1,80})\]\(([A-Z0-9]{10})\)")


def product_links(body: str, products: dict | None) -> str:
    """把（已转义的）文字里的 [品牌 简称](ASIN) 换成亚马逊商品链接，悬停显示主图。
    ASIN 必须是本期追踪的商品、方括号里必须写着它的品牌，否则只保留文字（大模型写错 ASIN 时不会链到别的商品）。"""
    def repl(m: re.Match) -> str:
        text, asin = m.group(1), m.group(2)
        item = (products or {}).get(asin)
        brand = ((item or {}).get("brand") or "").strip().lower()
        if not item or (brand and brand not in html.unescape(text).lower()):
            return text
        peek = (f'<img class="peek" src="{esc(item["image"])}" alt="" loading="lazy">'
                if item.get("image") else "")
        return (f'<a class="ref" href="https://www.amazon.com/dp/{asin}" target="_blank" '
                f'rel="noopener noreferrer">{text}{peek}</a>')
    return PRODUCT_LINK.sub(repl, body)


def markdown(text: str, products: dict | None = None) -> str:
    """只支持 ### 标题、- 列表、**加粗**、段落，以及 [品牌 简称](ASIN) 商品链接（见 product_links）：足够渲染总结文字。"""
    out: list[str] = []
    in_list = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        body = product_links(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc(line.lstrip("#-* ").strip())),
                             products)
        if line.startswith("#"):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h3>{body}</h3>")
        elif line.startswith(("- ", "* ", "• ")) or re.match(r"^\d+[.、)]\s", line):
            if not in_list:
                out.append("<ul>")
                in_list = True
            body = product_links(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>",
                                        esc(re.sub(r"^(\d+[.、)]|[-*•])\s*", "", line))), products)
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
--surge:#e8590c;--potential:#2b8a3e;--hot:#1c6dd0;--fake:#c92a2a;--seasonal:#0b7285;--accent:#1c6dd0;--spark:#1c6dd0;
--hl:rgba(232,89,12,.10)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1218;--card:#181c24;--text:#e6e8ec;
--muted:#9aa3b2;--line:#2a303b;--chip:#232833;--surge:#ff8a4c;--potential:#5cc477;--hot:#6aa8ff;--fake:#ff6b6b;
--seasonal:#3bc9db;--accent:#6aa8ff;--spark:#6aa8ff;--hl:rgba(255,138,76,.14)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC",
"Microsoft YaHei","Segoe UI",Roboto,sans-serif;-webkit-text-size-adjust:100%}
.wrap{max-width:980px;margin:0 auto;padding:16px}
header h1{font-size:22px;margin:4px 0 2px}
.sub{color:var(--muted);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.chip{background:var(--chip);border-radius:999px;padding:2px 10px;font-size:12px;color:var(--muted)}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:14px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;border-top:3px solid var(--c);
font:inherit;color:inherit;text-align:left;width:100%}
.kpi b{display:block;font-size:26px;line-height:1.2;color:var(--c)}
.kpi span{font-size:13px;color:var(--muted)}
@media (max-width:560px){.kpis{grid-template-columns:repeat(2,1fr)}.kpi:last-child:nth-child(odd){grid-column:span 2}}
.kpi-hint{margin:-6px 0 0;font-size:12px}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:14px 0}
section>h2{font-size:18px;margin:0 0 4px;display:flex;align-items:center;gap:8px}
section>h2 .dot{width:10px;height:10px;border-radius:50%;background:var(--c)}
.hint{color:var(--muted);font-size:13px;margin:0 0 10px}
.summary h3{font-size:15px;margin:14px 0 4px}.summary ul{margin:4px 0;padding-left:20px}.summary p{margin:6px 0}
.summary a.ref{position:relative;color:var(--accent);text-decoration:underline dotted;text-underline-offset:3px}
.summary a.ref .peek{display:none;position:absolute;left:0;top:1.6em;width:180px;height:180px;object-fit:contain;
background:#fff;border:1px solid var(--line);border-radius:10px;box-shadow:0 6px 20px rgba(0,0,0,.18);padding:6px;z-index:20}
.summary a.ref:hover .peek,.summary a.ref:focus .peek{display:block}
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
.chart{margin:6px 0 0;max-width:280px}
.chart figcaption{font-size:12px;line-height:1.4;color:var(--muted)}
.spark{display:block;width:100%;height:auto;margin-top:2px;overflow:visible}
.spark .grid{stroke:var(--line);stroke-width:1;vector-effect:non-scaling-stroke}
.spark .ax{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.spark-line{fill:none;stroke:var(--spark);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.spark-area{fill:var(--spark);opacity:.10}.spark-hl{fill:var(--hl)}
.bar{fill:var(--spark);opacity:.75}.bar.hi{fill:var(--seasonal);opacity:.95}.bar.lo{fill:var(--muted);opacity:.45}
.hov line{stroke:var(--muted);stroke-width:1;vector-effect:non-scaling-stroke}
.hov circle{fill:var(--spark);stroke:var(--card);stroke-width:2}
.hov text{fill:var(--text);font-size:11px;font-weight:600}
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
.dbox h4{margin:0 0 8px;font-size:15px}
.grp-h{font-size:12px;color:var(--muted);margin:10px 0 6px}
.grp-h:first-of-type{margin-top:0}
.feat{background:var(--bg);border-radius:8px;padding:7px 10px;margin:0 0 6px}
.feat b{display:block;font-size:14px;margin-bottom:3px}
.ev{display:grid;grid-template-columns:auto 4.6em minmax(40px,1fr) 4.6em;align-items:center;gap:6px;font-size:12px;
line-height:1.9;font-variant-numeric:tabular-nums;white-space:nowrap}
.ev.nopill{grid-template-columns:4.6em minmax(40px,1fr) 4.6em}
.ev .src{font-size:11px;border-radius:4px;padding:0 5px;line-height:1.6}
.src.img{background:rgba(43,138,62,.14);color:var(--potential)}
.src.ttl{background:rgba(28,109,208,.14);color:var(--hot)}
.ev .g{color:var(--potential);font-weight:600}
.ev .r{color:var(--muted);text-align:right}
.ev .meter{min-width:40px;background:var(--line);height:7px}
.ev .meter u{background:var(--text);width:2px}
.chips2{display:flex;flex-wrap:wrap;gap:6px}
.chips2 span{border:1px solid var(--line);border-radius:999px;padding:1px 9px;font-size:12px;white-space:nowrap}
.chips2 span em{font-style:normal;color:var(--muted);margin-left:4px}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--muted);margin:0 0 6px}
.legend .src{font-size:11px;border-radius:4px;padding:0 5px;margin-right:4px}
details.rules{margin:0 0 6px;font-size:12.5px}details.rules ul{margin:4px 0;padding-left:18px;color:var(--muted)}
#traits h3{font-size:15px;margin:16px 0 4px}
.dirs{grid-template-columns:repeat(auto-fill,minmax(min(290px,100%),1fr))}
.dir h4 .sub{font-weight:400;font-size:12px;margin-left:6px}
.spec{display:grid;grid-template-columns:3.6em minmax(0,1fr);gap:6px 8px;margin:0 0 4px;font-size:13px;align-items:baseline}
.spec dt{color:var(--muted);font-size:12px}.spec dd{margin:0;min-width:0}
.spec .none{color:var(--muted);font-size:12px}
.chips2 .up{color:var(--potential);font-weight:700;margin-left:2px}
.chips2 span.more{border-color:var(--potential);border-width:1.5px;font-weight:600}
.chips2 span.more em{font-weight:400}
.chips2 span.base{color:var(--muted)}
.dsale{margin:-4px 0 4px;font-size:13px;font-weight:600}
.dbasis{margin:0 0 8px;font-size:12px;color:var(--muted)}
ul.season{margin:0;padding-left:16px;font-size:12.5px}ul.season li{margin:1px 0}
.curve{font-size:11.5px;color:var(--muted);margin-top:2px}
.refs{margin:0;padding-left:18px;font-size:12.5px}.refs li{margin:3px 0}.refs em{font-style:normal;color:var(--muted)}
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
SEASON_GROUP = {"surge": "突然爆火", "potential": "潜力", "hot": "持续热销"}


def _tags(item: dict) -> str:
    out = []
    for tag in item["tags"]:
        cls = "warn" if any(k in tag for k in ("假爆火", "降价", "陈旧", "脉冲", "断货", "评分", "回落", "不稳定",
                                               "拉动", "还会被改")) else \
            "good" if tag in ("稳在高位", "仍在上涨", "新品爆发", "潜力") or tag.startswith("上升中") else \
            "info" if any(k in tag for k in ("季节", "大促", "头部", "变体", "换链接", "老链接", "对比期")) else ""
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


def card(item: dict, kind: str, group: str = "", notes: list[tuple[str, str]] = ()) -> str:
    """kind：surge / potential / hot / fake / rising（决定展示哪些数字和走势图）；group：增长商品、季节性商品小窗里的归类；
    notes：额外的 (小标题, 文字) 行，放在外观要点下面。"""
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
        if h.get("adjusted"):
            kv.append(f"<span><em>已按品类季节调整</em>（{h['season_n']} 年类目历史）</span>")
        if h.get("trend") is not None:
            kv.append(f"<span><em>近3月趋势</em> {fmt_pct(h['trend'])}/月</span>")
    if kind == "fake":
        level = "高度疑似" if item["fake"]["level"] == "high" else "疑似"
        kv.insert(0, f'<span><span class="score">异常分 {item["fake"]["score"]}</span>{level}</span>')
    season = item.get("item_season") if kind == "seasonal" else None
    if season:
        kv.append(f"<span><em>旺季（{esc(seasonality.months_text(season['peak_months']))}）日均</em> "
                  f"<b>{fmt_num(season['peak_avg'])}</b> 件</span>")
        kv.append(f"<span><em>淡季日均</em> {fmt_num(season['off_avg'])} 件</span>")
    if kind in ("hot", "fake", "rising", "seasonal") and item.get("recent_avg28") is not None:
        kv.append(f"<span><em>近28天日均</em> {fmt_num(item['recent_avg28'])} 件</span>")

    if season:
        chart = bars([(k, v) for k, v, _ in season["months"]], highlight={k for k, _, peak in season["months"] if peak},
                     caption="每月日均销量（件/天），深色 = 品类旺季月", unit="件/天")
    elif kind == "hot":
        chart = bars(item["monthly"][-12:])
    else:
        chart = sparkline(item.get("spark") or [], start=item.get("spark_start"))
    look = f'<div class="look"><em>外观</em>{esc(item["look"])}</div>' if item.get("look") else ""
    look += "".join(f'<div class="look"><em>{esc(head)}</em>{esc(text)}</div>' for head, text in notes)
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


def season_text(item: dict) -> str:
    """季节性商品的一句话（报告卡片和钉钉消息共用）：旺季月份、旺季比淡季高几倍、现在离旺季多远。"""
    s = item["item_season"]
    as_of = clock.parse_day(item.get("as_of"))
    gap = seasonality.months_to_peak(as_of, s["peak_months"]) if as_of else None
    when = "" if gap is None else "，现在是旺季" if gap == 0 else f"，离旺季还有 {gap} 个月"
    return (f"连续 {s['cycles']} 年 {seasonality.months_text(s['peak_months'])}旺，"
            f"每年旺季日均都至少是前后淡季的 {s['ratio']:.1f} 倍{when}")


def _seasonal_pop(items: list[dict], seasons: dict, hint: str, total: int) -> str:
    """季节性商品：卡片左上角写它本期所在的板块（不在任何板块的写“观察”），下面是每月日均（深色 = 品类旺季月）、
    这句季节结论和所在品类的季节（“每年”的依据）。"""
    cards = []
    for i in items:
        group = SEASON_GROUP.get(i["label"], "观察")
        if i["label"] in ("watch", "hot") and i.get("rising"):
            group = "上升中"
        cat = seasonality.verdicts(seasons.get(i.get("node"))) or ["还没有这个子类目的类目历史"]
        cards.append(card(i, "seasonal", group, [("季节", season_text(i)), ("品类", cat[0])]))
    more = f"（共 {total} 个，展示前 {len(items)} 个）" if total > len(items) else ""
    if cards:
        body = "".join(cards)
    elif not any((o or {}).get("ready") for o in seasons.values()):
        body = ('<div class="empty">类目历史还不够，认不出季节性品类，也就认不出季节性商品'
                '（每期用剩余预算补类目月度销量，补齐后自动出现）。</div>')
    else:
        body = '<div class="empty">本期没有符合条件的季节性商品。</div>'
    link = '<p class="sub"><a href="#" data-pop="season">查看季节日历（全部子类目的旺季、淡季和接下来 6 个月）</a></p>'
    return _pop_src("seasonal", "季节性商品", LABEL_COLOR["seasonal"],
                    f'<p class="hint">{esc(hint)}{more}</p>{link}<div class="cards">{body}</div>')


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


def _ev(src: str, growth: str, share: float, other: str, base: float | None) -> str:
    """一条依据：[来源] 增长商品的数字 | 进度条（绿 = 增长商品占比，竖线 = 对比对象占比）| 对比对象的数字。"""
    pill = {"主图": '<span class="src img">主图</span>', "标题": '<span class="src ttl">标题</span>'}.get(src, "")
    return (f'<div class="ev{"" if pill else " nopill"}">{pill}<span class="g">增长 {esc(growth)}</span>'
            f'{_meter(share, base)}<span class="r">{esc(other)}</span></div>')


def _material_box(m: dict) -> str:
    rows = [r for r in m.get("rows") or [] if r["count"] or r["reference"]]
    if not rows:
        return ""
    nf, nr = m["n_focus"], m["n_reference"]
    feats = "".join(
        f'<div class="feat"><b>{esc(r["label"])}</b>'
        + _ev("", f'{r["count"]}/{nf}', r["share"], f'对照 {r["reference"]}/{nr}' if nr else "", r["reference_share"])
        + "</div>" for r in rows)
    src = ""
    if "amazon_focus" in m:
        src = (f'<p class="hint">用亚马逊 Material 属性的：增长商品 {m["amazon_focus"]}/{nf}、对照 {m["amazon_reference"]}/{nr}，'
               f'其余看标题 / 主图（每期最多查 40 个，查过的永久缓存）</p>')
    return f'<div class="dbox"><h4>主材质</h4>{src}{feats}</div>'


def _evidence(f: dict, vision: dict) -> str:
    t, i = f.get("title"), f.get("image")
    out = []
    if i:
        other = f"对照 {i['reference']}/{vision['n_reference']}" if vision.get("n_reference") else ""
        out.append(_ev("主图", f"{i['focus']}/{vision['n_focus']}", i["focus_share"], other, i["reference_share"]))
    if t:
        out.append(_ev("标题", f"{t['share']:.0%}", t["share"], f"头部 {t['baseline_share']:.0%}", t["baseline_share"]))
    return "".join(out)


def _feature_box(dim: str, rows: dict, vision: dict) -> str:
    """一个维度：“比对照组更多”的特征各占一块（每种依据一行），下面是“常见但不比对照组多”和“也有”的标签。"""
    more = rows.get("more") or []
    parts = ['<div class="grp-h">比对照组更多</div>']
    parts += [f'<div class="feat"><b>{esc(f["label"])}</b>{_evidence(f, vision)}</div>' for f in more]
    if not more:
        parts.append('<p class="empty">没有比对照组明显更多的特征。</p>')
    def chip(f: dict) -> str:
        t, i = f.get("title"), f.get("image")
        if i:
            ref = f" · 对照 {i['reference']}/{vision['n_reference']}" if vision.get("n_reference") else ""
            return f'<span>{esc(f["label"])}<em>{i["focus"]}/{vision["n_focus"]}{ref}</em></span>'
        return f'<span>{esc(f["label"])}<em>{t["share"]:.0%} · 头部 {t["baseline_share"]:.0%}</em></span>'

    for key, head in (("common", "常见但不比对照组多"), ("also", "也有（不比对照组多）")):
        chips = [chip(f) for f in rows.get(key) or []]
        if chips:
            parts.append(f'<div class="grp-h">{head}</div><div class="chips2">{"".join(chips)}</div>')
    return f'<div class="dbox"><h4>{esc(dim)}</h4>{"".join(parts)}</div>'


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


DIRECTION_ROWS = (("主材质", None), ("风格", "风格"), ("造型", "造型"), ("工艺", "工艺"), ("颜色", "颜色"),
                  ("功能", "功能卖点"))


def _clip(text: str, limit: int) -> str:
    """截到 limit 个字符以内，不断在单词中间。"""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;-")
    return cut + "…"


def _curve_text(o: dict) -> str:
    return " · ".join(f"{seasonality.month_cn(m)} {r:.1f}" for m, r in o.get("curve") or [])


def _season_row(o: dict | None, cfg: dict) -> str:
    if o is None:
        return '<dt>季节</dt><dd><span class="none">还没有这个子类目的类目历史</span></dd>'
    lines = "".join(f"<li>{esc(v)}</li>" for v in seasonality.verdicts(o))
    curve = _curve_text(o) if o.get("seasonal") else ""
    body = (f'<ul class="season">{lines}</ul>'
            + (f'<div class="curve">按往年规律，接下来各月是现在（近 28 天）的：{esc(curve)}</div>' if curve else ""))
    return f"<dt>季节</dt><dd>{body}</dd>"


def _season_pop(ctx: dict) -> str:
    """季节日历：全部追踪子类目的季节画像和接下来 6 个月（按往年规律，相对现在）。"""
    seasons = ctx.get("seasons") or {}
    nodes = ctx.get("season_nodes") or []
    rows = []
    for node in nodes:
        o = seasons.get(node["path"])
        name = esc(node.get("name_cn") or node.get("name") or node["path"])
        if not o or not o.get("ready"):
            rows.append((2, 0, f"<tr><td>{name}</td><td colspan=6 class=\"sub\">类目历史 {(o or {}).get('months', 0)} 个月，还不够</td></tr>"))
            continue
        cons = "—" if o.get("consistency") is None else f"{o['consistency']:.2f}"
        err = "—" if o.get("error") is None else f"{o['error']:.0%}"
        if not o.get("seasonal"):
            rows.append((1, 0, f"<tr><td>{name}</td><td>没有固定季节</td><td colspan=2 class=\"sub\">{esc(o.get('why') or '')}</td>"
                               f"<td>{cons}</td><td></td><td>{err}</td></tr>"))
            continue
        rows.append((0, -(o.get("amplitude") or 0),
                     f"<tr><td>{name}</td><td><b>季节性</b></td><td>{esc(seasonality.months_text(o['peak_months']))}</td>"
                     f"<td>{esc(seasonality.months_text(o['low_months']))}</td><td>{cons}</td>"
                     f"<td>{esc(_curve_text(o))}</td><td>{err}</td></tr>"))
    rows.sort(key=lambda r: (r[0], r[1]))
    sc = ctx["cfg"]["thresholds"]["seasonality"]
    hint = (f"数据：卖家精灵每个子类目的月度销量合计（整个子类目所有商品，最近 {sc['history_months']} 个月）。"
            "季节指数 = 每个月的日均销量 ÷ 以它为中心的 12 个月移动平均，同一月份各年取中位数，归一到 12 个月平均为 1。"
            f"季节性品类 = 历史 ≥{sc['min_months']} 个月、每 12 个月一段的形状两两相关平均 ≥{sc['min_consistency']:g}、"
            f"最高月 ≥ 最低月的 {sc['min_amplitude']:g} 倍、按规律回测预测 1~6 个月后的误差中位数 ≤{sc['max_error']:.0%}。"
            f"旺季 = 指数 ≥{sc['peak_month']:g} 的月份，淡季 = ≤{sc['low_month']:g}。"
            "“接下来 6 个月”= 按往年规律（只是季节，不含品类自己的涨跌）各月是现在（近 28 天）的几倍；"
            "预测误差 = 只用当时以前的数据、按“最近 3 个月水平 × 季节指数”预测之后 1~6 个月，和实际比的误差中位数。")
    table = ("<table><thead><tr><th>子类目</th><th>季节</th><th>旺季</th><th>淡季</th><th>每年形状相关</th>"
             "<th>接下来 6 个月（÷ 现在）</th><th>预测误差</th></tr></thead><tbody>"
             + "".join(r[2] for r in rows) + "</tbody></table>")
    return _pop_src("season", "季节日历", "muted", f'<p class="hint">{esc(hint)}</p><div class=tbl>{table}</div>')


def _direction_box(d: dict, cfg: dict) -> str:
    """一个开发方向：每个维度“比头部多”和“基本款”两档特征（几个品牌有 / 品牌数 · 头部里的占比）、
    价格、包装、季节、参考商品。"""
    n = d.get("n_brands", d["n"])
    compared = bool((d.get("compare") or {}).get("mode"))

    def chip(r: dict, tier: str) -> str:
        head = f" · 头部 {r['head_share']:.0%}" if r.get("head_share") is not None else ""
        up = '<b class="up" title="在全部增长商品里也比对照组更多">↑</b>' if r.get("trend") else ""
        return f'<span class="{tier}">{esc(r["label"])}{up}<em>{r["count"]}/{n}{head}</em></span>'

    def chips(f) -> str:
        rows = f if isinstance(f, list) else None
        if rows is not None:  # 主材质：只有一档
            body = "".join(chip(r, "base") for r in rows)
        else:
            body = "".join(chip(r, "more") for r in f["more"]) + "".join(chip(r, "base") for r in f["base"])
        return f'<div class="chips2">{body}</div>' if body else '<span class="none">不统一</span>'

    spec = "".join(f"<dt>{label}</dt><dd>{chips(d['materials'] if dim is None else d['features'][dim])}</dd>"
                   for label, dim in DIRECTION_ROWS)
    price = d.get("price")
    if price:
        median = f"，中位 ${price['median']:,.0f}" if price.get("median") is not None else ""
        spec += f"<dt>价格</dt><dd>{esc(directions.price_text(price))}{median}</dd>"
    pkg = shipping.summary_text(d.get("pkg"))
    no_pkg = '<span class="none">这些增长商品还没有包装数据</span>'
    spec += f"<dt>包装</dt><dd>{esc(pkg) if pkg else no_pkg}</dd>"
    spec += _season_row(d.get("season"), cfg)
    refs = "".join(
        f'<li><a href="https://www.amazon.com/dp/{esc(r["asin"])}" target="_blank" rel="noopener noreferrer">'
        f'{esc(_clip(r["name"], 48))}</a> <em>{fmt_price(r.get("price"))}'
        + (f" · {esc(shipping.text(r.get('pkg'), r.get('price')))}" if r.get("pkg") else "") + "</em>"
        + (f"：{esc(r['look'])}" if r.get("look") else "") + "</li>" for r in d["refs"])
    surge = f"，其中突然爆火 {d['n_surge']} 个" if d["n_surge"] else ""
    refs = (f'<ul class="refs">{refs}</ul>' if refs
            else '<p class="none">势头靠前的都有异常信号，不列参考商品</p>')
    sales = d.get("sales") or {}
    money = (f'<p class="dsale">月销售额约 {esc(directions.money_text(sales.get("monthly")))}，'
             f'比之前 28 天多约 {esc(directions.money_text(sales.get("increment")))}</p>') if sales else ""
    cmp = d.get("compare") or {}
    if compared:
        src = "标题 + 主图" if cmp["mode"] == "image" else "只看标题"
        basis = f"对比同子类目头部商品 {cmp['n_head']} 个（{src}）"
    else:
        basis = f"同子类目头部商品只有 {cmp.get('n_head_all', 0)} 个，没有对比，只列共同特征"
    return (f'<div class="dbox dir"><h4>{esc(d["node"])}<span class="sub">增长商品 {d["n"]} 个、{n} 个品牌{surge}</span></h4>'
            f'{money}<p class="dbasis">{esc(basis)}</p>'
            f'<dl class="spec">{spec}</dl><div class="grp-h">参考商品</div>{refs}</div>')


def _directions(tr: dict, cfg: dict) -> str:
    c, sc = cfg["thresholds"]["directions"], cfg["thresholds"]["seasonality"]
    rule = (f"增长商品 ≥{c['min_items']} 个、来自 ≥{c.get('min_brands', 1)} 个品牌的子类目，按增量销售额从大到小"
            f"（同额时看品牌数、增长商品个数、突然爆火个数、势头）取前 {c['max']} 个，没有任何主材质或特征的跳过。"
            "月销售额 = 每个增长商品的近 28 天日均 × 当前售价 × 30 的合计；增量 = （近 28 天日均 − 之前 28 天在卖日子的日均 × 季节倍数）"
            "× 售价 × 30 的合计，之前没在卖的按 0、老品换链接之前没在卖的增量按 0、老链接重新起量的增量按 0（销量是卖家精灵按 BSR 估算的，大促日不算；"
            "本期测到了日数据修订时，近 28 天日均用最近几天按修订幅度校正后的）。"
            "特征按品牌计数，同一品牌的多个商品只算一次（该品牌任一增长商品的标题或主图有就算），数字 = 具备的品牌数 / 品牌数，"
            "“头部”= 同子类目头部商品里有这个特征的占比（月度名单里这个子类目、不在增长商品里的，按月销量取前 "
            f"{c.get('head_max', 30)} 个；有主图识别的 ≥{c.get('head_min', 10)} 个时两边都看标题 + 主图，头部只算识别过的，"
            f"否则两边都只看标题；头部不足 {c.get('head_min', 10)} 个时不对比）。"
            f"绿框 = 比头部多：≥2 个品牌有，占比比头部高 {c.get('head_min_gap', 0.2) * 100:.0f} 个百分点以上、"
            f"是头部的 {c.get('head_min_lift', 1.5):g} 倍以上；灰框 = 基本款：≥{c['feature_min_share']:.0%}（且 ≥2 个）品牌有，但不比头部多，"
            f"说明这个品类本来就都这样做；每档最多 {c['per_dim']} 个；主材质只看 ≥{c['feature_min_share']:.0%} 品牌，不和头部比；"
            "“不统一”= 两档都没有；↑ = 这个特征在全部增长商品里也比对照组更多。"
            "价格 = 最低价 ~ 最高价（3 个以上给中位数）；包装 = 有数据的增长商品的包装体积（卖家精灵给的包装长宽高换算）、"
            "包装重量、FBA 配送费（卖家精灵估算）的中位数。"
            f"季节：用卖家精灵的子类目月度销量（最近 {sc['history_months']} 个月）算季节指数，只对季节性品类下结论（定义见“季节日历”）："
            f"按往年规律近 28 天品类本来就是之前 28 天的 ≥{sc['tag_ratio']:g} 倍 = 本期增长有季节因素；"
            f"按开发周期 {sc['lead_days']} 天，到货前有哪个月按往年规律 ≥ 现在的 {sc['peak_ratio']:g} 倍 = 品类会先冲高、赶不上；"
            f"上架后 {sc['launch_days']} 天 ≥{sc['busier']:g} 倍更旺、≤{sc['quieter']:g} 倍转淡、之间差不多；"
            "增量里扣除季节：之前 28 天日均先乘以这个季节倍数再相减（不是季节性品类的倍数为 1）。"
            "参考商品 = 该子类目势头最强、没有任何异常信号的两个，来自不同品牌。")
    boxes = "".join(_direction_box(d, cfg) for d in tr.get("directions") or [])
    body = f'<div class="dgrid dirs">{boxes}</div>' if boxes else '<p class="empty">本期没有增长商品数达到门槛的子类目。</p>'
    link = '<p class="sub"><a href="#" data-pop="season">查看季节日历（全部子类目的旺季、淡季和接下来 6 个月）</a></p>'
    return f'<h3>开发方向依据</h3><p class="hint">{esc(rule)}</p>{link}{body}'


def _traits(tr: dict, counts: dict, cfg: dict) -> str:
    if tr.get("note"):
        return f'<p class="empty">{esc(tr["note"])}</p>' + (_directions(tr, cfg) if tr.get("directions") else "")
    vision = tr.get("vision") or {}
    mats = tr.get("materials") or {}
    a = cfg["thresholds"]["appearance"]
    sample = (f'样本：<a href="#" data-pop="growth">增长商品 {tr["n_focus"]} 个</a>（突然爆火 {counts["surge"]} + '
              f'潜力 {counts["potential"]} + 上升中 {counts.get("rising", 0)}）')
    legend = [f'<span><span class="src img">主图</span>AI 看主图：增长商品里势头最强的 {vision.get("n_focus") or 0} 个，'
              f'对比“对照”= 持续热销对照组（不在增长商品里的、月销量最高的 {mats.get("n_reference") or 0} 个）</span>',
              f'<span><span class="src ttl">标题</span>标题统计：增长商品，对比“头部”= 全部头部商品 {tr["n_baseline"]} 个</span>',
              '<span>进度条：绿色 = 增长商品中的占比，竖线 = 对比对象中的占比</span>']
    rules = [f'主材质：每个商品一类，依次取亚马逊 Material 属性、标题、主图识别；看全部增长商品，对比对照组。',
             f'比对照组更多：至少 {a["min_count"]} 个增长商品有（且来自至少 {a.get("min_brands", 1)} 个品牌），'
             f'标题里占比 ≥{a["title_min_share"]:.0%}、是头部的 '
             f'{a["title_min_lift"]:g} 倍以上，或主图里比对照多 {a["image_min_gap"] * 100:.0f} 个百分点以上；'
             '两种依据方向相反（一种说更多、一种说更少）的，哪一档都不列。',
             f'常见但不比对照组多：增长商品里 ≥{a["common_share"]:.0%} 有（有主图识别看主图，没有看标题），只说明是基本盘，不代表趋势。',
             f'也有（不比对照组多）：增长商品里 ≥{a.get("also_share", 0):.0%}、不到 {a["common_share"]:.0%} 有（同样至少 {a["min_count"]} 个、'
             f'{a.get("min_brands", 1)} 个品牌），只说明有人在做，不代表趋势。',
             '颜色以主图识别为准：标题里的颜色常是把可选变体都列出来（Walnut/Black/White），不代表商品本身；没有主图识别时才看标题。']
    head = (f'<p class="hint">{sample}</p><div class="legend">{"".join(legend)}</div>'
            f'<details class="rules"><summary>入选规则</summary><ul>{"".join(f"<li>{esc(r)}</li>" for r in rules)}</ul></details>')
    boxes = [_material_box(mats)]
    for dim, rows in (tr.get("appearance") or {}).items():
        boxes.append(_feature_box(dim, rows, vision))
    body = "".join(b for b in boxes if b)
    if not tr.get("appearance"):
        body += '<p class="empty">增长商品没有明显偏多或常见的外观/工艺特征。</p>'
    return f'{head}<div class="dgrid">{body}</div>{_directions(tr, cfg)}{_structure(tr)}'


def _changes(diff: dict, by_asin: dict) -> str:
    if diff.get("first"):
        return '<p class="empty">本期没有可对比的上一期（首期或监控范围刚调整），从下一期开始显示变化。</p>'
    names = {"surge": "突然爆火", "potential": "潜力", "hot": "持续热销", "fake": "异常信号"}
    moved_from = diff.get("moved_from") or {}

    def name(asin: str) -> str:
        item = by_asin.get(asin)
        title = (item or {}).get("title", asin)
        name = display_name((item or {}).get("brand", ""), title)
        was = f'<span class="sub">（上期{names[moved_from[asin]]}）</span>' if asin in moved_from else ""
        return f"<a href=\"https://www.amazon.com/dp/{esc(asin)}\" target=_blank rel=\"noopener noreferrer\">" \
               f"{esc(name[:40])}</a>{was}"

    def line(head: str, asins: list[str]) -> str:
        return f"<li>{head}{len(asins)} 个：" + "、".join(name(a) for a in asins[:5]) \
            + ("…" if len(asins) > 5 else "") + "</li>"

    parts = [line("依旧爆火 ", diff["still"])] if diff.get("still") else []
    parts += [line(f"新进入「{names[label]}」", diff["new"][label])
              for label in ("surge", "potential", "fake", "hot") if diff["new"].get(label)]
    if diff.get("cooled"):
        parts.append(line("不再爆火 ", diff["cooled"]))
    if diff.get("covered"):
        parts.append(line("持续热销本期起也用月度名单补判（上期没判过，不算新进入）", diff["covered"]))
    if diff.get("retro"):
        parts.append(f"<li>上面有 {len(diff['retro'])} 个商品上期没追踪：它们上期在哪个板块，是用本期拉到的日数据回看上期那天判的"
                     "（同样的规则；那几天的数据已经过卖家精灵修订），回看上期就已经是的算“依旧”，不算新进入</li>")
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


def season_rule(sc: dict) -> str:
    """季节性商品的四条（seasonality.item_season），小窗顶部和“判定方法”共用。"""
    return ("“每年”要商品自己证明：日数据只有约 400 天、只看得到一个旺季，分不清是每年都这样还是那一年碰巧那段时间卖得好，"
            "所以看卖家精灵的月度销量趋势（父体月销量，约 2 年），四条都满足：① 所在子类目是季节性品类（3 年类目销量），旺季 = 品类的旺季月；"
            f"② 到最近一个月为止连续每月都有销量，其中至少有 {sc['item_min_cycles']} 年的旺季（只经历过一个旺季的不列）；"
            f"③ 每一年的旺季日均 ≥ 紧挨着它的前一段、后一段淡季日均的 {sc['item_ratio']:g} 倍，"
            "而且看得到一次从淡季涨上去、一次从旺季掉下来（一直在涨或一直在跌的只有一边低，不算）；"
            f"④ 淡季里每个月的日均都 ≤ 最低那一年旺季日均的 {sc['item_off_max']:g} 倍（淡季里冲高过的不算，例如 Prime Day 那个月）。"
            f"月度销量是整月合计，含大促日和断货日。只查季节性品类里上架满 {sc.get('item_min_age_days', 365)} 天的追踪商品。")


def section_hints(cfg: dict, universe: bool = True) -> dict[str, str]:
    """每个小窗顶部的入选条件（和判定代码一一对应，数字直接取自 config.yaml）。共用概念见 _method。
    universe：本期翻成了全市场名单；没翻成（预算不够）时机会候选退回月度名单和近 30 天榜单，范围的说法跟着变。"""
    th = cfg["thresholds"]
    s, h, p, f, r = th["surge"], th["hot"], th["potential"], th["fake"], th["rising"]
    sc = th.get("seasonality") or {}
    pool = cfg["pool"]
    floor = f"近 30 天销量 ≥{pool['opportunity_min_units']} 件或销售额 ≥${pool.get('opportunity_min_revenue') or 0:,.0f}"
    scope = (f"范围：全部子类目里{floor}的商品（全市场名单，每期都翻一遍，初筛后拉日数据判定；量不够的不在范围内）。" if universe else
             f"范围：本期全市场名单没翻成（预算不够），机会候选退回月度名单和近 30 天榜单里{floor}的商品，"
             f"分数最高的 {pool.get('opportunity_every_run', 0)} 个每期都看、其余轮流查，不是全部量够的商品都看了。")
    windows = " 或 ".join(map(str, s["windows"]))
    tide = "（所在子类目整体在涨时，先除以品类大盘倍数，见“判定方法”）" if (th.get("tide") or {}).get("enabled", True) else ""
    short = (f"对比期在卖不到 {th['short_base_days']} 天的（刚开卖、断过货）照样判，卡片上写明“对比期只有 X 天在卖”。"
             if th.get("short_base_days") else "")
    adjust = ("。季节性品类（见“季节日历”）的商品，原始销量不达标的，再按季节调整后判一次：每个月的销量（和近 28 天、门槛线）"
              "乘以 这 6 个月季节指数的平均 ÷ 当月的指数，调整后达标的也算，卡片上注明“已按品类季节调整”"
              if h.get("deseasonalize") and sc else "")
    return {
        "surge": scope + f"近 {windows} 天对比之前 {s['base_days']} 天：BSR 中位数降到 {s['bsr_ratio']} 倍以下（排名提升一倍以上），"
                 f"日均销量 ≥{s['sales_ratio']:g} 倍且 ≥{s['min_daily_sales']} 件；形态是新品爆发 / 老链接重新起量 / 稳在高位 / 仍在上涨之一；"
                 f"没有回落；异常分 <{f['suspect_score']}（轻微信号列在卡片上）。{short}按爆发强度排序。",
        "potential": scope + f"上架 {p['min_age_days']}~{p['max_age_days']} 天、评论 <{p['max_ratings']} 条、不是老品换链接；近 28 天日均 ≥{p['min_daily_sales']} 件、"
                     f"≥ 之前 28 天的 {p['min_recent_ratio']} 倍{tide}；有两个以上完整月份的，月销量平均每月增长 ≥{p['min_monthly_growth']:.0%}；"
                     f"没有回落；异常分 <{f['suspect_score']}。{short}按月销量增长排序（不满两个完整月份的按近 28 天增长）。",
        "hot": f"近 {h['lookback_months']} 个月里至少 {h['months_required']} 个月的月销量达到所在子类目第 {h['rank_in_node']} 名的水平（按月销量排名，同一父体只算一次）、"
               f"且这个月量够（≥{h.get('min_units', 0):g} 件或销售额 ≥${h.get('min_revenue') or 0:,.0f}）；"
               f"近 6 个月波动系数 ≤{h['max_cv']}；近 3 个月平均每月跌幅 ≤{-h['min_trend']:.0%}；"
               f"近 28 天日均 ≥ 近 6 个月日均的 {h['min_recent_ratio']:.0%}；异常分 <{f['suspect_score']}{adjust}。"
               f"不在追踪池里的头部商品也判（月度名单补判）：近 {h.get('list_months', 6)} 个月每个月都在子类目销售额前 50、"
               f"近 30 天名单里也有它的，按同样的条件判，近 28 天日均用近 30 天的销量 ÷ 30（名单的销量含大促日，按本期追踪商品算的大促系数扣掉）；"
               f"没有日数据，卡片上标“只有月度数据”：没有走势图、不判回落，异常信号只看留评率一项（追踪商品还看评论增速、评分跳升、短时脉冲等）。正在回落的（规则见“判定方法”）标“最近回落”，不影响上榜。按近 6 个月月均销量排序。",
        "fake": f"异常分 ≥{f['suspect_score']} 的商品，不论是否在增长；同时满足爆火条件的标“假爆火嫌疑”。"
                f"仅为数据异常提示，需人工核实。按异常分排序。",
        "growth": scope + f"增长商品 = 突然爆火 + 潜力 + 上升中。上升中 = 不在前两个板块、近 28 天日均 ≥ 之前 28 天的 {r['min_ratio']} 倍{tide}"
                  f"且 ≥{r['min_daily_sales']} 件、没有回落。{short}“外观与工艺依据”就是从这些商品统计的。按势头排序。"
                  f"季节性品类里、按往年规律对比期间品类本来就会涨 ≥{sc.get('tag_ratio', 1.15):g} 倍的，标“品类季节性”并写明扣除季节后的倍数（见“数据说明”）。",
        "seasonal": "每年固定一段时间卖得特别好、其他时间反而不行的商品。不是板块：不论它本期在哪个板块、还是只在观察"
                    "（卡片左上角），都在这里再列一遍。" + season_rule(sc) +
                    "异常信号和评分不达标的不列。离旺季近的排前面（正在旺季的最前）。柱状图是这个商品每个月的日均，"
                    "深色是品类的旺季月；“品类”一行是所在子类目的季节结论（季节性品类的定义见“季节日历”）。"
                    "带“品类季节性”标签、或按季节调整后才算持续热销的，说的是品类，不等于商品自己是季节性的。",
        "all": "本期追踪（拉了日数据）的全部商品。“板块”是本期归入的板块（观察 = 不属于任何板块），“来源”是它被选进追踪的原因。"
               "月度名单补判出的持续热销不在这里（只在持续热销小窗里）。",
    }


def _method(cfg: dict) -> str:
    """共用概念的定义。各板块自己的入选条件写在对应小窗顶部，这里不重复。"""
    th = cfg["thresholds"]
    s, f, m = th["surge"], th["fake"], th["momentum"]
    return f"""<details><summary>判定方法与数据口径</summary><ul class="hint">
<li><b>数据</b>：卖家精灵的月度头部名单（product_research，已结束月份；月份结束后不到 {cfg['discovery'].get('settle_days', 20)} 天拉的过了这么多天重拉一次，
卖家精灵月初还没更新完；持续热销补判还用近 {th['hot'].get('list_months', 6)} 个月每个月的名单）、
近 30 天榜单（不传月份，截至当天；持续热销补判还查每个子类目的近 30 天名单）、
每个追踪商品约 400 天的日销量 / BSR / 价格（asin_prediction）。日销量是卖家精灵按 BSR 估算的，所以以 BSR 中位数为主信号。
所有判定都基于每个商品截至最近一天的日数据；比本期最新的一天晚 {th['stale_days']} 天以上的标“数据陈旧”，不做爆火 / 潜力 / 上升中判断。</li>
<li><b>大促日</b>：这几天全类目一起冲高（历史上大促当天约六七成商品销量翻倍，第二天就回到原样），
所以爆火 / 潜力 / 上升中 / 回落 / 季节性 / 持续热销的比较都去掉这些天：近 7 天、近 28 天、对比期都跳过大促日。
大促日 = 从追踪商品的日数据自动识别（某天 ≥{th['events']['core_share']:.0%} 的商品当天销量达到自己前
{th['events']['base_days']} 天中位数的 {th['events']['jump']:g} 倍，与它相连、比例 ≥{th['events']['edge_share']:.0%} 的日子也算）
+ config.yaml 登记的大促里、数据也印证（当天 ≥{th['events']['edge_share']:.0%} 的商品冲高）的日子。
自己在大促日冲高过、算上大促日能达到爆火 / 潜力 / 上升中而去掉后达不到的，标“大促拉动”，不进这几个板块。</li>
<li><b>最近几天的数还会被改</b>：卖家精灵会回头改最近几天的日销量（实测隔 2 天重拉，最近 9 天里 75% 的商品被改、中位数是原来的 85%）。
每期拿上期存的日数据和这期新拉的比，测出改了几天、改成原来的几成（写在“数据说明”）；判定用当期拉到的数，再把最近这几天按测到的幅度校正后重判一次，
原来成立、校正后不成立的突然爆火 / 潜力 / 上升中 / 持续热销 / 最近回落（比回落规则本身），卡片上标“最近 N 天的数还会被改：…校正后不算 X”，
钉钉消息里那一行也提醒，不排除（验证：标了的 4 个里 3 个修订后真的不成立，没标的 16 个里 2 个变了）。开发方向的增量和月销售额也用校正后的近 28 天日均。测到的商品不到 {th.get('revision_min_items', 30)} 个、或幅度不到 {th.get('revision_min_change', 0.02):.0%} 时不校正、不标。</li>
<li><b>品类大盘</b>：每个子类目里量够的商品（全市场名单）近 30 天销量是约 30 天前的几倍，取中位数（扣掉大促日；
商品不到 {(th.get('tide') or {}).get('min_items', 10)} 个的子类目不算）。潜力、上升中的增长倍数先除以它（大于 1 时）再比门槛——
整个子类目一起涨时随大流的商品不算，卡片上标“随大盘”。突然爆火不用：它要求 BSR 排名提升一倍，排名本来就是和整个市场比的。
回测（2025-11~2026-09 每周回放）：去掉的潜力之后 28 天 74% 保持住（其余 86%），上升中 79%（其余 81%）——主要是定义上的要求，不是预测更准。</li>
<li><b>断货</b>：BSR ≥{th['out_of_stock_bsr']:,} 或没有排名的日子（断货 / 停售 / 还没开卖）不算进对比期，
爆火、潜力、上升中都只和真正在卖的日子比{f"；潜力、上升中（和开发方向的增量）的对比期里，连着 ≥{th['out_of_stock_zero_run']} 天销量 0 的日子也算断货（断货头几天 BSR 还没涨上去）" if th.get('out_of_stock_zero_run') else ""}。</li>
<li><b>追踪名额</b>：必看（往期爆火/潜力/异常 + 每个子类目销量前 {cfg['pool']['per_node_top']}、销售额前 {cfg['pool'].get('per_node_top_revenue', 0)}
+ 全部类目里销量最大的）+ 机会候选。机会候选来自<b>全市场名单</b>：每期把全部子类目里近 30 天销量 ≥{cfg['pool']['opportunity_min_units']} 件
或销售额 ≥${cfg['pool'].get('opportunity_min_revenue') or 0:,.0f} 的商品都翻一遍，存下每个商品的近 30 天销量；名单里的增长率和日数据对不上，
所以用自己存的往期名单算：比约 30 天前那份高 ≥{cfg['pool']['screen_growth']:.0%}、半年内上架且没跌、近 6 / 12 天比 6 / 12 天前那份的日均高
{cfg['pool']['screen_short']:g} 倍以上，满足一个就拉日数据按上面的规则判；新进名单的都拉。头一个月还没有 30 天前的快照，
用上个月的月度名单当基准、门槛放宽，再加上近 7 天排名改善 ≥{cfg['pool']['screen_bsr_cr']:g}%。
回测（全覆盖试跑 2,039 个门槛以上商品）：初筛不漏增长商品，要拉日数据的约三成多（头一个月四到六成）。
突然爆火、潜力、上升中都只在这些量够的商品里找。</li>
<li><b>板块归属</b>：每个商品只归入一个板块，优先级：评分不达标 &gt; 异常信号 &gt; 突然爆火 &gt; 潜力 &gt; 持续热销；都不属于的为“观察”。
同时也符合潜力 / 持续热销条件的，在卡片上标“潜力”/“长期头部”。各板块的入选条件见点开后小窗顶部。</li>
<li><b>评分门槛</b>：评分低于 {th['min_rating']}、或评论不到 {th['few_ratings']} 条且评分低于 {th['few_ratings_min_rating']}（评论少时几条差评就会跌破
{th['min_rating']}，卖家精灵的评分也比亚马逊晚几天）的，不进任何板块和增长商品，也不占追踪名额，由后面的商品依次补位。
评分优先取最新日数据；还没有评论的新品不受限。</li>
<li><b>回落</b>（任一）：① 冲高后正在往下掉 = 最近 3 天日均比近 4 周里最高的 3 天日均低 {1 - m['min_vs_peak_3d']:.0%} 以上，
并且比再之前 3 天低 {1 - m['min_vs_prev_3d']:.0%} 以上；② 这一周整体变低 = 近 7 天销量的中位数比再之前 7 天的中位数低
{1 - m['min_vs_prev_week']:.0%} 以上；③ 冲高后连着往下掉 = 近 7 天里最高的那天之后、直到最近一天都没有回升过（至少 {m['slide_days']} 天），
且最近一天比最高那天低 {1 - m['slide_max_vs_peak']:.0%} 以上。冲高一两天后回到原来水平的不算回落（中位数不受一两天的尖峰影响）。
突然爆火、潜力、上升中都要求没有回落，有回落的标“最近回落”。</li>
<li><b>突然爆火的形态</b>（依次判断）：<b>新品爆发</b> = 上架 ≤{s.get('new_listing_max_age_days', 180)} 天、之前 {s['base_days']} 天几乎没卖（日均 &lt;0.5 件），且日均 ≥{s['new_listing_min_daily']} 件、
≥ 所在子类目第 20 名水平的 {s['new_listing_node_share']:.0%}（量不够的标“新品起量（未达爆火门槛）”）；
<b>老链接重新起量</b> = 条件同新品爆发，但上架超过 {s.get('new_listing_max_age_days', 180)} 天（停了很久又开始卖，不是新品；量不够的标“老链接重新起量（未达爆火门槛）”）；
<b>稳在高位</b> = 近 7 天里 ≥{s['sustained_days']} 天达到之前水平的 {s['sustained_mult']} 倍，且最近 3 天日均与近 7 天相差 ≤{s['steady_band']:.0%}；
<b>仍在上涨</b> = 近 7 天日均比前 7 天高 ≥{s['climb_ratio'] - 1:.0%}，且最近 3 天日均高于近 7 天。
近 7 天的增量 ≥{s['pulse_share']:.0%} 来自 ≤2 天的是“短时脉冲”，其余都不符合的是“上涨形态不稳定”，都不算爆火。
老品换链接的“从零起量”只是新子体开始卖，不算新品爆发、也不标新品起量。
和之前 {s['base_days']} 天比的（爆火、潜力、上升中），对比期只算在卖的日子；在卖不到 {th.get('short_base_days', 14)} 天的照样判，卡片上写明“对比期只有 X 天在卖”。
<b>爆发强度</b> = 销量倍数（最多按 10 倍算）× ln(1 + 近期日均)。</li>
<li><b>爆火的附加标签</b>：降价驱动 = 价格中位数下降 ≥{s['price_drop']:.0%}。
对比期里 ≥7 天（且 ≥25%）没有销量、而更早之前卖得和现在差不多的是“断货恢复”，不算爆火。</li>
<li><b>数据来源</b>：所有判定的数据都来自卖家精灵（月度名单、近 30 天榜单、每个商品的日数据、子类目月度销量），
不用人工填的日期或系数；大促日也只从日数据里认，config.yaml 的 deal_windows 只给认出来的日子起名字。</li>
<li><b>季节性品类</b>：用卖家精灵的子类目月度销量合计（整个子类目所有商品，最近 {th['seasonality']['history_months']} 个月）算季节指数
（每个月的日均 ÷ 以它为中心的 12 个月移动平均，同一月份各年取中位数，12 个月平均为 1）。四条都满足才算季节性品类：
历史 ≥{th['seasonality']['min_months']} 个月；每 12 个月切一段，各段形状两两相关平均 ≥{th['seasonality']['min_consistency']:g}（每年都是同样的月份高）；
最高月 ≥ 最低月的 {th['seasonality']['min_amplitude']:g} 倍；按“最近 3 个月水平 × 季节指数”回测预测 1~6 个月后，误差中位数 ≤{th['seasonality']['max_error']:.0%}。
不是季节性品类的不做任何季节判断。全部子类目见开发方向下面的“季节日历”。</li>
<li><b>品类季节性</b>：季节性品类里的突然爆火、潜力、上升中，按往年规律它的对比期间品类本来就会涨 ≥{th['seasonality']['tag_ratio']:g} 倍的
（爆火看它自己的近 7 / 14 天 vs 之前 28 天，潜力和上升中看近 28 天 vs 之前 28 天），标“品类季节性”，写明季节倍数和扣除后的倍数。
这种 28 天的短窗口，季节倍数 = (季节指数之比)^k：月度指数摊到 28 天会夸大，k 每期用卖家精灵数据现算（过去一年，季节性品类的指数之比 vs
该子类目追踪商品实际 28 天变化的中位数，k = ln(实际) ÷ ln(指数之比) 的中位数，只用季节起伏 ≥5% 的样本），本期的 k 写在上面。
只是标签，不影响上榜。回测：去年 12 月判出的突然爆火只有 52% 四周后还保持住，6~8 月是 84~86%。</li>
<li><b>季节性商品</b>（标签“季节性”，顶部的“季节性商品”小窗列出全部）：每年固定一段时间卖得特别好、其他时间反而不行。
{esc(season_rule(th['seasonality']))}不是板块，只是标签，不影响上榜。</li>
<li><b>老品换链接</b>：上架 ≤{th['relist']['max_age_days']} 天，且评论数 ≥ 这个 ASIN 自己累计销量（日数据里卖出的全部件数）的 {th['relist']['review_share']:.0%}，
或评论数 ≥ 累计销量的 {th['relist']['review_share_fast']:.0%} 且第一周（从第一天卖出东西算起）日均 ≥ 近 7 天日均的 {th['relist']['fast_share']:.0%}（且 ≥{th['relist']['fast_min_daily']} 件）。
多是新子体挂进老父体、或老品换了新 ASIN，继承了评论和排名，上架天数不代表商品是新的（正常留评率 1~3%，Vine 最多 30 条）。
这类商品不算潜力、新品爆发、新品起量，不计“新品评论/销量比”异常分；还可以是稳在高位、仍在上涨、上升中（这几个是和它自己之前比），
对比期还包括它开卖第一周（{th['relist'].get('ramp_days', 7)} 天）的，标“对比期包括它开卖的第一周”：起量里可能有老链接转过来的流量；
开发方向里，它之前 28 天没在卖时增量按 0（销量是从老链接转过来的）。</li>
<li><b>外观与工艺依据</b>：材质只有“主材质”一种口径，每个商品一类（铁木 / 实木 / 板材 / 金属…），依次取亚马逊商品详情的 Material 属性、
标题里写明的材质、AI 主图识别，都没有的按标题记为“木质（未注明）”或“未注明”。风格、造型、工艺、颜色、功能卖点有两种依据（功能只认图里看得见的，颜色以主图为准）——
标题统计（增长商品 vs 全部头部商品）和 AI 主图识别（增长商品里势头最强的 {cfg['llm']['vision_focus']} 个 vs 持续热销对照组，
对照组不含增长商品），同一特征合并成一行；入选门槛写在该板块顶部，两种依据方向相反的哪一档都不列；“常见”只说明增长商品里多数有，不代表比对照组多。</li>
<li><b>开发方向</b>：增长商品带来的增量销售额最大的几个子类目（增量 = （近 28 天日均 − 之前 28 天在卖日子的日均 × 季节倍数）× 售价 × 30 的合计；
季节倍数 = 季节性品类里按往年规律近 28 天品类本来就是之前 28 天的几倍，其余子类目为 1；老链接重新起量的增量按 0；
本期测到了日数据修订时，近 28 天日均用最近几天按修订幅度校正后的），
各给一个产品画像：主材质；风格、造型、工艺、颜色、功能分两档——“比头部多”（增长商品比同子类目头部商品明显多的）和
“基本款”（增长商品多数有、头部也一样多，说明这个品类本来就这样做）；价格；包装体积、包装重量和 FBA 配送费（卖家精灵的包装数据）；
季节（只对季节性品类下结论，依据是这个子类目 3 年的类目月度销量算出的季节指数：本期增长有没有季节因素、
按开发周期 {th['seasonality']['lead_days']} 天到货前会不会先冲高、上架后是更旺还是转淡）。每项的门槛、入选比例和商品数写在“开发方向依据”里。</li>
<li><b>异常信号</b>：留评率 ≥ 子类目中位数的 {f['review_rate_mult']} 倍、新品评论/销量比过高、两期之间评论增速远超销量、评分短期跳升、
近 30 天的短时脉冲（1~3 天冲到基线 {f['pulse_mult']} 倍后迅速回落）、评论集中在少数几天、非验证购买占比高，各记 1~2 分；
累计 ≥{f['suspect_score']} 分为疑似、≥{f['high_score']} 分为高度疑似。变体超过 {f['max_variations_for_ratio']} 个的商品评论为父体共享，
不算留评率、也不算评论/销量比（子类目的留评率中位数也只用变体不超过 {f['max_variations_for_ratio']} 个的商品算）；老品换链接不算新品评论/销量比；
评论暴增且父体/变体变化的标“变体合并”、短时脉冲落在大促日的标“大促脉冲”、抽查评论里 Vine 评论 ≥30% 的标“Vine 评论多”，这三项不计分。</li>
<li><b>与上期对比</b>：每个商品只出现一次。“依旧爆火”= 上期、本期都在突然爆火板块；“新进入「某板块」”= 本期在该板块、上期不在，
上期在别的板块的在括号里注明；“不再爆火”= 上期爆火、本期留在追踪里但不在任何板块。</li>
<li>阈值都可以在仓库的 config.yaml 中调整。</li></ul></details>"""


def render(ctx: dict) -> str:
    sec = ctx["sections"]
    counts = sec["counts"]
    cfg = ctx["cfg"]
    by_asin = {i["asin"]: i for i in ctx["items"]}
    hints = section_hints(cfg, universe=bool(ctx["coverage"].get("universe")))
    kpi = "".join(
        f'<button type="button" class="kpi" data-pop="{k}" style="--c:var(--{k})"><b>{counts[k]}</b><span>{label}</span></button>'
        for k, label in (("surge", "突然爆火"), ("potential", "潜力产品"), ("hot", "持续热销"), ("fake", "异常信号"),
                         ("seasonal", "季节性商品")))
    cov = ctx["coverage"]
    facts = [f"月度名单 {ctx['period_text']}", f"日数据截至 {ctx['as_of']}",
             f"追踪 {counts['total']} 个（本期刷新 {cov['refreshed']} 个）"]
    if cov.get("universe"):
        failed = f"；{cov['universe_failed']} 个子类目有页查询失败、没翻全" if cov.get("universe_failed") else ""
        facts.append(f"全市场名单：近 30 天量够的商品 {cov['universe']} 个（翻名单 {cov.get('universe_pages') or 0} 次调用{failed}），"
                     f"初筛出 {cov.get('candidates') or 0} 个可能在增长，本期拉了日数据的 {cov.get('opportunity') or 0} 个")
        if cov.get("young"):
            facts.append(f"早期名单：上架半年内、量还不够全市场门槛的商品 {cov['young'][0]} 个，近 7 天排名改善够多、拉了日数据的 {cov['young'][1]} 个")
    elif cov.get("candidates"):
        facts.append(f"机会候选 {cov['candidates']} 个，本期查了 {cov.get('opportunity') or 0} 个")
    if counts.get("low"):
        facts.append(f"评分不达标未上榜 {counts['low']} 个")
    tides = cov.get("tides") or {}
    if tides:
        names = {n["path"]: n.get("name_cn") or n.get("name") or n["path"] for n in ctx.get("season_nodes") or []}
        up = sorted(((t, p) for p, t in tides.items() if t > 1.0), reverse=True)
        facts.append(f"品类大盘（{len(tides)} 个子类目算得出）：" + ("、".join(f"{names.get(p, p)} ×{t:.2f}" for t, p in up[:6])
                     + ("…" if len(up) > 6 else "") + " 整体在涨，潜力 / 上升中要跑赢它" if up else "没有子类目整体在涨"))
    rev = cov.get("revision")
    if rev and rev.get("window"):
        facts.append(f"卖家精灵会回头改最近几天的日数据：上期拉到的数据，这期再拉时 {rev['changed']:.0%} 的商品被改过，"
                     f"上期最后 {rev['window']} 天平均是原来的 {rev['median']:.0%}（{rev['items']} 个商品的中位数）；"
                     f"本期的判定用的是这期拉到的数；把最近 {rev['window']} 天按这个幅度校正后不成立的判定，卡片上写明“最近 N 天的数还会被改”")
    mon = ctx.get("monthly") or {}
    if mon.get("need"):
        if mon["months"] < mon["need"]:
            facts.append(f"持续热销的月度名单补判：往月名单已齐 {mon['months']}/{mon['need']} 个月，补齐后开始（这期只判追踪的商品）")
        else:
            part = f"（近 30 天名单只查了 {mon['rolling']}/{mon['nodes']} 个子类目）" if mon["rolling"] < mon["nodes"] else ""
            deal = f"，近 30 天名单含大促日，按 ×{mon['deal']:.2f} 扣掉" if (mon.get("deal") or 1) > 1.001 else ""
            facts.append(f"持续热销里 {counts.get('hot_monthly', 0)} 个来自月度名单补判（不在追踪池的头部商品判了 {mon['judged']} 个{deal}）{part}")
    spans = (ctx.get("events") or {}).get("spans") or []
    if spans:
        facts.append("大促日（不参与比较）：" + "、".join(events.span_text(sp) for sp in spans))
    cal = ctx.get("season_calibration")
    if cal:
        facts.append(f"28 天季节倍数折扣 k={cal['k']:.2f}（" + (f"本期用 {cal['n']} 个样本现算" if cal["fitted"]
                     else f"样本只有 {cal['n']} 个，用默认值") + "）")
    facts += [f"卖家精灵调用 {fmt_int(cov.get('calls'))} 次",
              f"本期要点：{'AI 生成' if ctx['summary_source'] == 'llm' else '模板'}"]
    history = ""
    if ctx.get("history"):
        history = (" · 往期 " + " ".join(f'<a href="{esc(h["href"])}">{esc(h["id"])}</a>' for h in ctx["history"])
                   + '<span class="sub">（往期报告请从钉钉里那一期的消息打开）</span>')
    pops = "".join([
        _section_pop("surge", "突然爆火", hints["surge"], sec["surge"], counts["surge"]),
        _section_pop("potential", "潜力产品", hints["potential"], sec["potential"], counts["potential"]),
        _section_pop("hot", "持续热销", hints["hot"], sec["hot"], counts["hot"]),
        _section_pop("fake", "异常信号", hints["fake"], sec["fake"], counts["fake"]),
        _growth_pop(ctx.get("focus") or [], hints["growth"]),
        _seasonal_pop(sec.get("seasonal") or [], ctx.get("seasons") or {}, hints["seasonal"], counts.get("seasonal", 0)),
        _season_pop(ctx),
        _pop_src("all", f"全部追踪商品（{counts['total']} 个）", "muted",
                 f'<p class="hint">{esc(hints["all"])}</p>{_all_table([i for i in ctx["items"] if i.get("daily", True)])}'),
    ])
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer"><title>{esc(ctx['title'])} {esc(ctx['report_id'])}</title>
<style>{CSS}</style></head><body><div class="wrap">
<header><div class="sub">{esc(cfg['report'].get('subtitle') or '亚马逊美国站 · 家具')}</div><h1>{esc(ctx['title'])}</h1>
<div class="sub hist">{esc(ctx['generated_at'])} 生成 · 第 {ctx['run_seq']} 期 · 日数据截至 {esc(ctx['as_of'])}{history}</div></header>
<div class="kpis">{kpi}</div><p class="sub kpi-hint">点数字查看商品清单</p>
<section class="summary" style="--c:var(--accent)"><h2><span class="dot"></span>本期要点</h2>{markdown(ctx['summary'], by_asin)}</section>
<section id="traits" style="--c:var(--potential)"><h2><span class="dot"></span>外观与工艺依据</h2>{_traits(ctx['traits'], counts, cfg)}</section>
<section id="changes" style="--c:var(--hot)"><h2><span class="dot"></span>与上期对比</h2>{_changes(ctx['diff'], by_asin)}</section>
<section id="about" style="--c:var(--muted)"><h2><span class="dot"></span>数据说明</h2>
<p class="sub">{' · '.join(esc(x) for x in facts)}</p>
<p class="sub"><a href="#" data-pop="all">查看全部 {counts['total']} 个追踪商品</a></p>{_method(cfg)}</section>
<footer>{esc(ctx['title'])} · 数据来源：卖家精灵 · 报告已加密，仅持有链接的人可查看</footer>
</div>{pops}{POPUP}{HOVER_JS}</body></html>"""

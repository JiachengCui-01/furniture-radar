"""生成报告 HTML（明文）。之后由 shell.py 加密包装再发布。

单文件、无外部脚本/字体，只有商品图片引用亚马逊图床；适配手机和深色模式。
主体：KPI（带缩略图）→ 本期增长商品（图墙，带迷你走势）→ 本期要点 → 开发方向（一行一个子类目的一览：月增量、价格带、
新品占比、头部评论，可排序）→ 外观与工艺依据（哑铃图）→ 与上期对比（缩略图行）→ 数据说明（次要的折叠）。
完整的商品卡片（各板块、增长商品、全部追踪商品）和每个开发方向的画像放在隐藏的小窗内容里，点 KPI 数字、链接或一览的一行才弹出；
卡片多的小窗可按类别、子类目筛，按月增量 / 售价 / 上架天数 / 评分排。图表见 charts.py。
列出的商品去掉简单款、合并相近款（detect/showcase.py，pipeline.curated_lists）；报告上的规则只写一句话，完整口径见 README。
"""
from __future__ import annotations

import html
import re

from .. import shipping
from ..analyze import display_name, growth_kind, increment_note, usable_revision
from ..detect import blueocean, directions, events
from ..detect.market import EDGES as market_edges
from ..tracking import REASON_CN
from ..vision import small_image
from . import charts
from .sparkline import HOVER_JS, bars, sparkline

LABEL_COLOR = {"surge": "surge", "potential": "potential", "hot": "hot", "fake": "fake", "watch": "muted",
               "low": "muted", "rising": "accent", "ocean": "ocean"}
GROUP_CN = {"surge": "突然爆火", "potential": "潜力", "rising": "上升中"}
LINK = 'target="_blank" rel="noopener noreferrer"'


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


def _clip(text: str, limit: int) -> str:
    """截到 limit 个字符以内，不断在单词中间。"""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;-")
    return cut + "…"


def amazon(asin: str) -> str:
    return f"https://www.amazon.com/dp/{esc(asin)}"


def short_name(item: dict, limit: int = 42) -> str:
    return _clip(display_name(item.get("brand") or "", item.get("title") or item["asin"]), limit)


def _img(item: dict, size: int = 200) -> str:
    """商品主图（亚马逊图床，按需缩小）；没有图的用品牌首字母占位。"""
    if item.get("image"):
        return (f'<img src="{esc(small_image(item["image"], size))}" alt="" loading="lazy" '
                f'referrerpolicy="no-referrer">')
    return f'<span class="ph">{esc((item.get("brand") or item["asin"])[:1].upper())}</span>'


def thumb(item: dict, cls: str = "th") -> str:
    """小缩略图，点开是亚马逊商品页，悬停显示品名。"""
    return f'<a class="{cls}" href="{amazon(item["asin"])}" {LINK} title="{esc(short_name(item, 80))}">{_img(item, 160)}</a>'


def chip_link(item: dict, limit: int = 30) -> str:
    """带小图的商品名链接（本期要点、与上期对比里点名商品用）。"""
    img = (f'<img src="{esc(small_image(item["image"], 100))}" alt="" loading="lazy" referrerpolicy="no-referrer">'
           if item.get("image") else "")
    return f'<a class="ref" href="{amazon(item["asin"])}" {LINK}>{img}{esc(short_name(item, limit))}</a>'


PRODUCT_LINK = re.compile(r"\[([^\[\]]{1,80})\]\(([A-Z0-9]{10})\)")


def product_links(body: str, products: dict | None) -> str:
    """把（已转义的）文字里的 [品牌 简称](ASIN) 换成带小图的亚马逊商品链接。
    ASIN 必须是本期追踪的商品、方括号里必须写着它的品牌，否则只保留文字（大模型写错 ASIN 时不会链到别的商品）。"""
    def repl(m: re.Match) -> str:
        text, asin = m.group(1), m.group(2)
        item = (products or {}).get(asin)
        brand = ((item or {}).get("brand") or "").strip().lower()
        if not item or (brand and brand not in html.unescape(text).lower()):
            return text
        img = (f'<img src="{esc(small_image(item["image"], 100))}" alt="" loading="lazy" referrerpolicy="no-referrer">'
               if item.get("image") else "")
        return f'<a class="ref" href="https://www.amazon.com/dp/{asin}" {LINK}>{img}{text}</a>'
    return PRODUCT_LINK.sub(repl, body)


def markdown(text: str, products: dict | None = None) -> str:
    """只支持 ### 标题、- 列表、**加粗**、段落，以及 [品牌 简称](ASIN) 商品链接（见 product_links）：足够渲染总结文字。
    每个 ### 小节包成一个 <div class="sbox">，宽屏上并排。"""
    out: list[str] = []
    in_list = in_box = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            close_list()
            continue
        body = product_links(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc(line.lstrip("#-* ").strip())),
                             products)
        if line.startswith("#"):
            close_list()
            if in_box:
                out.append("</div>")
            out.append(f'<div class="sbox"><h3>{body}</h3>')
            in_box = True
        elif line.startswith(("- ", "* ", "• ")) or re.match(r"^\d+[.、)]\s", line):
            if not in_list:
                out.append("<ul>")
                in_list = True
            body = product_links(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>",
                                        esc(re.sub(r"^(\d+[.、)]|[-*•])\s*", "", line))), products)
            out.append(f"<li>{body}</li>")
        else:
            close_list()
            out.append(f"<p>{body}</p>")
    close_list()
    if in_box:
        out.append("</div>")
    return "\n".join(out)


CSS = """
:root{--bg:#f5f6f8;--card:#fff;--text:#1c2230;--muted:#667085;--line:#e4e7ec;--chip:#f0f2f5;
--surge:#e8590c;--potential:#2b8a3e;--hot:#1c6dd0;--fake:#c92a2a;--ocean:#0c8599;--accent:#1c6dd0;--spark:#1c6dd0;
--hl:rgba(232,89,12,.10);--imgbg:#fff;--row:#f8f9fb;
--k-surge:#eb6834;--k-potential:#1baf7a;--k-rising:#2a78d6;--ink-bar:#5b6578;--band:#d5d9e0}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1218;--card:#181c24;--text:#e6e8ec;
--muted:#9aa3b2;--line:#2a303b;--chip:#232833;--surge:#ff8a4c;--potential:#5cc477;--hot:#6aa8ff;--fake:#ff6b6b;--ocean:#3bc9db;
--accent:#6aa8ff;--spark:#6aa8ff;--hl:rgba(255,138,76,.14);--imgbg:#f4f4f2;--row:#1d222b;
--k-surge:#d95926;--k-potential:#199e70;--k-rising:#3987e5;--ink-bar:#aab3c2;--band:#3a4150}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC",
"Microsoft YaHei","Segoe UI",Roboto,sans-serif;-webkit-text-size-adjust:100%}
.wrap{max-width:1080px;margin:0 auto;padding:16px}
header h1{font-size:22px;margin:2px 0}
.sub{color:var(--muted);font-size:13px}
a{color:var(--accent)}
.hint{color:var(--muted);font-size:13px;margin:0 0 10px}
.empty{color:var(--muted);font-size:14px;padding:6px 0}
img{display:block}
.ph{display:flex;align-items:center;justify-content:center;background:var(--chip);color:var(--muted);font-weight:600;
width:100%;height:100%}
/* KPI */
.kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:14px 0 6px}
@media (max-width:900px){.kpis{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:640px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;border-top:3px solid var(--c);
font:inherit;color:inherit;text-align:left;width:100%;cursor:pointer;display:flex;flex-direction:column;gap:4px;
-webkit-tap-highlight-color:transparent}
.kpi:hover{box-shadow:0 2px 10px rgba(0,0,0,.08)}
.kpi-n{display:flex;align-items:baseline;gap:8px}
.kpi-n b{font-size:26px;line-height:1.1;color:var(--c);font-variant-numeric:tabular-nums}
.kpi-n span{font-size:14px;font-weight:600}
.kpi-d{font-size:12px;color:var(--muted);line-height:1.35}
.kpi-th{display:flex;gap:4px;margin-top:2px;min-height:36px;overflow:hidden}
.kpi-th img,.kpi-th .ph{width:36px;height:36px;object-fit:contain;background:var(--imgbg);border-radius:6px;border:1px solid var(--line)}
.kpi-x{font-size:11.5px;color:var(--muted)}
.nich{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 10px}.nich span{background:var(--chip);border-radius:8px;padding:3px 9px;font-size:12.5px}.nich b{color:var(--c);margin-right:2px}
.alert{border-left:3px solid var(--fake);background:rgba(201,42,42,.08);border-radius:8px;padding:8px 12px;margin:12px 0;font-size:14px}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin:14px 0}
section>h2{font-size:18px;margin:0 0 4px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
section>h2 .dot{width:10px;height:10px;border-radius:50%;background:var(--c)}
section>h2 .h-more{margin-left:auto;font-size:13px;font-weight:400}
/* 图墙 */
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(158px,1fr));gap:10px}
@media (max-width:560px){.gal{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}section{padding:12px}
.kpi-th img,.kpi-th .ph{width:30px;height:30px}}
.tile{position:relative;display:flex;flex-direction:column;border:1px solid var(--line);border-radius:12px;overflow:hidden;
text-decoration:none;color:inherit;background:var(--card)}
.tile:hover{box-shadow:0 2px 12px rgba(0,0,0,.10)}
.tile .im{position:relative;display:block;padding-top:100%;background:var(--imgbg)}
.tile .im img,.tile .im .ph{position:absolute;top:8px;left:8px;width:calc(100% - 16px);height:calc(100% - 16px);object-fit:contain}
.badge{position:absolute;top:6px;left:6px;font-size:11px;font-weight:600;color:#fff;background:var(--c);border-radius:6px;padding:1px 6px}
.tile .bd{padding:7px 9px 9px;display:flex;flex-direction:column;gap:3px;border-top:3px solid var(--c)}
.tile .tt{font-size:13px;font-weight:600;line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tile .mm{font-size:12px;color:var(--muted)}
.tile .kk{font-size:12.5px;font-variant-numeric:tabular-nums;display:flex;justify-content:space-between;gap:6px;flex-wrap:wrap}
.tile .kk b{color:var(--c)}
.tile .inc{font-size:12.5px;white-space:nowrap}.tile .inc b{color:var(--text)}
.tile .sim{font-size:11px;color:var(--muted)}
.fx{display:flex;flex-wrap:wrap;gap:3px;margin:2px 0}
.fx i{font-style:normal;font-size:11px;border-radius:5px;padding:0 6px;border:1px solid var(--line);color:var(--text);
background:var(--chip);white-space:nowrap}
/* 本期要点 */
.sgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(400px,100%),1fr));gap:10px}
.sbox{border:1px solid var(--line);border-radius:10px;padding:8px 12px}
.sbox h3{font-size:15px;margin:2px 0 4px}.sbox ul{margin:2px 0;padding-left:18px}.sbox li{margin:3px 0}.sbox p{margin:4px 0}
a.ref{display:inline-flex;align-items:center;gap:4px;vertical-align:middle;background:var(--chip);border-radius:6px;
padding:1px 6px 1px 2px;margin:1px 0;text-decoration:none;color:var(--accent);font-size:.95em;line-height:1.4}
a.ref img{width:22px;height:22px;object-fit:contain;background:var(--imgbg);border-radius:4px;flex:none}
/* 卡片（小窗里） */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(420px,100%),1fr));gap:10px}
.card{display:grid;grid-template-columns:96px minmax(0,1fr);gap:12px;border:1px solid var(--line);border-radius:12px;
padding:10px;border-left:4px solid var(--c);background:var(--card)}
.card>div{min-width:0}
.card .pic{width:96px;height:96px;background:var(--imgbg);border-radius:8px;overflow:hidden;display:block;padding:3px}
.card .pic img{width:100%;height:100%;object-fit:contain}
@media (max-width:420px){.card{grid-template-columns:64px minmax(0,1fr);gap:10px}.card .pic{width:64px;height:64px}}
.card .t{font-weight:600;font-size:14px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card .t a{color:inherit;text-decoration:none}
.meta{color:var(--muted);font-size:12px;margin:2px 0}
.grp{font-size:11px;border-radius:5px;padding:0 6px;background:var(--c);color:#fff;margin-right:4px;vertical-align:1px}
.kv{display:flex;flex-wrap:wrap;gap:4px;margin:5px 0 3px}
.pill{font-size:12.5px;background:var(--chip);border-radius:6px;padding:1px 7px;font-variant-numeric:tabular-nums;white-space:nowrap}
.pill em{font-style:normal;color:var(--muted);margin-right:3px}
.pill.hl{background:var(--hl)}
.note{font-size:12px;color:var(--muted);margin:1px 0}
.look{font-size:12.5px;margin:3px 0;color:var(--text)}
.tags{display:flex;flex-wrap:wrap;gap:4px;margin:4px 0}
.tag{font-size:11.5px;border-radius:6px;padding:1px 7px;background:var(--chip);color:var(--muted)}
.tag.warn{background:rgba(201,42,42,.12);color:var(--fake)}
.tag.good{background:rgba(43,138,62,.12);color:var(--potential)}
.tag.info{background:rgba(28,109,208,.12);color:var(--hot)}
details.why{font-size:12px;color:var(--muted);margin:2px 0}details.why summary{color:var(--muted);font-size:12px}
details.why ul{margin:2px 0;padding-left:16px}
.signals{margin:3px 0 0;padding-left:18px;font-size:12.5px}
.signals li{margin:1px 0}
.score{display:inline-block;font-weight:700;color:var(--fake);margin-right:4px}
.simrow{display:flex;align-items:center;gap:4px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin:4px 0}
a.th{display:block;width:40px;height:40px;border-radius:6px;overflow:hidden;background:var(--imgbg);border:1px solid var(--line);flex:none}
a.th img{width:100%;height:100%;object-fit:contain}
a.th.big{width:58px;height:58px}
.chart{margin:4px 0 0;max-width:300px}
.chart figcaption{font-size:11.5px;line-height:1.4;color:var(--muted)}
.spark{display:block;width:100%;height:auto;margin-top:2px;overflow:visible}
.spark .grid{stroke:var(--line);stroke-width:1;vector-effect:non-scaling-stroke}
.spark .ax{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.spark-line{fill:none;stroke:var(--spark);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.spark-area{fill:var(--spark);opacity:.10}.spark-hl{fill:var(--hl)}
.bar{fill:var(--spark);opacity:.75}.bar.hi{fill:var(--accent);opacity:.95}.bar.lo{fill:var(--muted);opacity:.45}
.hov line{stroke:var(--muted);stroke-width:1;vector-effect:non-scaling-stroke}
.hov circle{fill:var(--spark);stroke:var(--card);stroke-width:2}
.hov text{fill:var(--text);font-size:11px;font-weight:600}
details.skip{margin:12px 0 0;font-size:13px;border-top:1px dashed var(--line);padding-top:8px}
details.skip summary{color:var(--muted);cursor:pointer}
.mini{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.tbl{overflow-x:auto}
.kw{display:flex;flex-wrap:wrap;gap:6px}
.kw span{border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:13px}
.kw span b{color:var(--potential);font-weight:600;margin-left:4px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.chip{background:var(--chip);border-radius:999px;padding:2px 10px;font-size:12px;color:var(--muted)}
details{margin-top:6px}summary{cursor:pointer;color:var(--accent)}
details.rules{margin:0 0 8px;font-size:12.5px}
.hist a{margin-left:8px}
footer{color:var(--muted);font-size:12px;text-align:center;padding:16px 0 28px}
/* 小窗 */
.pop{position:fixed;top:0;right:0;bottom:0;left:0;z-index:50;display:flex;align-items:center;justify-content:center;padding:16px}
.pop[hidden]{display:none}
.pop-mask{position:absolute;top:0;right:0;bottom:0;left:0;background:rgba(15,18,24,.55)}
.pop-panel{position:relative;background:var(--bg);border-radius:14px;width:100%;max-width:1040px;max-height:88vh;
display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,.35);border-top:4px solid var(--c);overflow:hidden}
.pop-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 16px;
border-bottom:1px solid var(--line);background:var(--card)}
.pop-head b{font-size:17px}
.pop-x{border:0;background:var(--chip);border-radius:8px;width:34px;height:34px;font-size:22px;line-height:1;
cursor:pointer;color:var(--text);flex:none}
.pop-body{overflow-y:auto;padding:12px 16px 18px;-webkit-overflow-scrolling:touch}
html.noscroll,html.noscroll body{overflow:hidden}
@media (max-width:560px){.pop{padding:0;align-items:flex-end}.pop-panel{max-height:92vh;border-radius:16px 16px 0 0}}
/* 外观与工艺、开发方向 */
.dbox{border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.dbox h4{margin:0 0 6px;font-size:15px}
.grp-h{font-size:12px;color:var(--muted);margin:8px 0 4px}
.src.img{background:rgba(43,138,62,.14);color:var(--potential)}
.src.ttl{background:rgba(28,109,208,.14);color:var(--hot)}
.chips2{display:flex;flex-wrap:wrap;gap:5px}
.chips2 span{border:1px solid var(--line);border-radius:999px;padding:1px 8px;font-size:12px;white-space:nowrap}
.chips2 span em{font-style:normal;color:var(--muted);margin-left:4px}
.chips2 .up{color:var(--potential);font-weight:700;margin-left:2px}
.chips2 span.more{border-color:var(--potential);border-width:1.5px;font-weight:600}
.chips2 span.more em{font-weight:400}
.chips2 span.base{color:var(--muted)}
.dir h4{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.dir .inc{color:var(--surge);font-size:15px}
.dir .sub{font-weight:400;font-size:12px}
.dir .ths{display:flex;gap:5px;margin:6px 0 8px;flex-wrap:wrap}
.spec{display:grid;grid-template-columns:3.2em minmax(0,1fr);gap:5px 8px;margin:0 0 4px;font-size:13px;align-items:baseline}
.spec dt{color:var(--muted);font-size:12px}.spec dd{margin:0;min-width:0}
.spec .none{color:var(--muted);font-size:12px}
.dir .chips2 span{white-space:normal;max-width:100%}
.refs{display:grid;gap:6px;margin:4px 0 0}
.refrow{display:grid;grid-template-columns:58px minmax(0,1fr);gap:8px;align-items:center;font-size:12.5px}
.refrow em{font-style:normal;color:var(--muted)}
/* 与上期对比、数据说明 */
.chg{margin:0;padding-left:18px}.chg li{margin:5px 0;line-height:1.9}
.facts{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 12px;font-size:13px;margin:0}
.facts dt{color:var(--muted)}.facts dd{margin:0}
@media (max-width:560px){.facts{grid-template-columns:1fr}.facts dt{margin-top:6px}}
ul.gloss{margin:6px 0;padding-left:18px;font-size:13px;color:var(--muted)}ul.gloss li{margin:3px 0}ul.gloss b{color:var(--text)}
/* 图上的数据标记（charts.py） */
.dot{stroke:var(--card);stroke-width:2}.k-surge{fill:var(--k-surge);background:var(--k-surge)}
.k-potential{fill:var(--k-potential);background:var(--k-potential)}.k-rising{fill:var(--k-rising);background:var(--k-rising)}
.pband{fill:var(--band)}.pmid{stroke:var(--muted);stroke-width:1.5}.hbar{fill:var(--band)}.hbar:hover{fill:var(--muted)}
.brk{fill:none;stroke:var(--muted);stroke-width:1}
.pstrip .grid,.phist .grid{stroke:var(--line);stroke-width:1}
.phist .ax{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.track{stroke:var(--line);stroke-width:2;stroke-linecap:round}.span{stroke:var(--potential);stroke-width:2;opacity:.35}
.gro{fill:var(--potential);stroke:var(--card);stroke-width:2}.oth{fill:var(--card);stroke:var(--muted);stroke-width:2}
.lv0{stroke:var(--muted);stroke-width:1.5}.lv1{stroke:var(--text);stroke-width:1.5}
svg.mini{display:block;width:100%;height:auto;margin:2px 0 0}
.tip{position:fixed;z-index:80;max-width:280px;background:var(--text);color:var(--card);font-size:12px;line-height:1.45;
border-radius:6px;padding:5px 8px;pointer-events:none;box-shadow:0 4px 14px rgba(0,0,0,.2)}
.tip[hidden]{display:none}
.kd{display:inline-block;width:8px;height:8px;border-radius:50%;margin:0 2px 0 6px;vertical-align:0}
/* 开发方向一览 */
.board{margin-top:8px;border-top:1px solid var(--line)}
.bhead,.brow{display:grid;grid-template-columns:62px minmax(120px,1.05fr) minmax(130px,1fr) minmax(170px,1.35fr) 62px 62px 12px;
column-gap:14px;align-items:center;grid-template-areas:"pic name inc price new rev go" "pic feat feat feat feat feat go"}
.bhead{grid-template-areas:"pic name inc price new rev go";font-size:12px;color:var(--muted);padding:6px 8px;
border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--card);z-index:2}
.bhead button{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer;text-align:left;white-space:nowrap}
.bhead button.on{color:var(--text);font-weight:600}.bhead button.on::after{content:" ↓"}.bhead button.on.asc::after{content:" ↑"}
.bhead .h-price{grid-area:price}.paxis{position:relative;height:15px;width:100%;max-width:260px}
.paxis span{position:absolute;top:0;transform:translateX(-50%);font-size:11px;font-variant-numeric:tabular-nums}
.brow{padding:9px 8px;border-bottom:1px solid var(--line);row-gap:5px;cursor:pointer;border-radius:8px}
.brow:hover,.brow:focus-visible{background:var(--row);outline:none}
.bpic{grid-area:pic;align-self:start;display:grid;grid-template-columns:1fr 1fr;gap:2px;width:62px;height:62px;
border-radius:8px;overflow:hidden;background:var(--imgbg);border:1px solid var(--line)}
.bpic img,.bpic .ph{width:100%;height:100%;object-fit:contain;background:var(--imgbg)}
.bpic.n1{grid-template-columns:1fr}.bpic.n1>*{grid-row:span 2}.bpic.n2>*{grid-row:span 2}
.bname{grid-area:name;min-width:0;line-height:1.35}.bname b{font-size:15px;margin-right:2px}
.bname .sub{display:block;font-size:12px}.bname .kc{font-size:12px;color:var(--muted);white-space:nowrap}
.binc{grid-area:inc;display:flex;align-items:center;gap:6px;font-variant-numeric:tabular-nums;min-width:0}
.ibar{flex:1;height:10px;min-width:30px}.ibar i{display:block;height:100%;background:var(--ink-bar);border-radius:0 4px 4px 0}
.binc b{font-size:13.5px;white-space:nowrap}
.bprice{grid-area:price;min-width:0;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.pstrip{display:block;width:100%;max-width:260px;height:auto;overflow:visible}
.bnew{grid-area:new}.brev{grid-area:rev}.bnew,.brev{font-variant-numeric:tabular-nums;font-size:13.5px;line-height:1.2}
.bnew b,.brev b{font-weight:600}.ml{display:none;font-style:normal;color:var(--muted);font-size:12px;margin-right:4px}
.m2{display:block;height:4px;background:var(--line);border-radius:2px;margin-top:4px;max-width:56px}
.m2 i{display:block;height:100%;background:var(--ink-bar);border-radius:2px}
.bgo{grid-area:go;color:var(--muted);font-size:18px}
.bfeat{grid-area:feat;display:flex;flex-wrap:wrap;gap:4px;min-width:0}
.bfeat span{font-size:12px;border-radius:999px;padding:0 8px;border:1px solid var(--line);white-space:nowrap;line-height:1.6}
.bfeat .more{border-color:var(--potential);color:var(--text);font-weight:600}.bfeat .mat{background:var(--chip)}
.bfeat .base{color:var(--muted)}.bfeat em{font-style:normal;color:var(--muted);font-size:11.5px}
@media (max-width:700px){.bhead{grid-template-columns:none;display:flex;flex-wrap:wrap;gap:4px 14px;position:static}
.bhead .h-pic,.bhead .h-price,.bhead .h-go{display:none}
.brow{grid-template-columns:52px minmax(0,1fr) minmax(0,1fr) 10px;column-gap:10px;
grid-template-areas:"pic name name go" "pic inc inc go" "price price price price" "new new rev rev" "feat feat feat feat"}
.bpic{width:52px;height:52px}.ml{display:inline}.m2{display:none}.pstrip{max-width:none}}
.dstats{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
.dstats span{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:4px 10px;font-size:12px;color:var(--muted)}
.dstats b{display:block;font-size:15px;color:var(--text);font-variant-numeric:tabular-nums}
.phist{display:block;width:100%;max-width:620px;height:auto;overflow:visible;margin:4px 0 2px}
.kleg{display:flex;flex-wrap:wrap;gap:2px 12px;font-size:12px;color:var(--muted);margin:2px 0 8px}
.kleg i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px;vertical-align:-1px}
.kleg i.band{border-radius:3px;width:18px;background:var(--band)}
.pop-body h5{font-size:14px;margin:14px 0 6px}
/* 外观与工艺：哑铃图 */
.tgrid{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1.95fr);gap:12px;margin-top:8px}
@media (max-width:860px){.tgrid{grid-template-columns:1fr}}
.tgrid>*,.flist>*{min-width:0}
@media (max-width:560px){.frow{grid-template-columns:2.4em minmax(0,1fr) 2.4em auto;grid-template-areas:"dim lab gap ths" "db db db db";row-gap:3px}
.fdim{grid-area:dim}.flab{grid-area:lab}.fgap{grid-area:gap}.frow .ths{grid-area:ths}.fdb{grid-area:db}
.mrow{grid-template-columns:minmax(6.5em,auto) minmax(0,1fr) auto;grid-template-areas:none}.mrow>*{grid-area:auto}.frow .ths a:nth-child(n+3){display:none}.dumb{max-width:none}}
.flist{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(440px,100%),1fr));column-gap:22px}
.frow{display:grid;grid-template-columns:2.6em minmax(5.5em,1fr) minmax(110px,190px) 2.6em auto;gap:8px;align-items:center;
padding:5px 0;border-bottom:1px solid var(--line);font-size:13px}
.fdim{font-size:11px;color:var(--muted);background:var(--chip);border-radius:4px;text-align:center;padding:0 2px}
.flab{font-weight:600;min-width:0}.fgap{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.fdb{display:flex;flex-direction:column;gap:2px;min-width:0}.fdb>span{display:flex;align-items:center;gap:4px}
.fdb .src{font-size:10px;padding:0 3px;line-height:1.5;border-radius:3px;flex:none}
.dumb{display:block;width:100%;min-width:90px;max-width:170px;height:auto;overflow:visible}
.frow .ths{display:flex;gap:2px}.frow a.th{width:28px;height:28px}
.mrow{grid-template-columns:minmax(7.5em,auto) minmax(90px,1fr) auto}
.mrow .flab{white-space:nowrap}
.mrow .cnt{font-size:11.5px;color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}
.leg2{display:flex;flex-wrap:wrap;gap:2px 14px;font-size:12px;color:var(--muted);margin:0 0 4px;align-items:center}
.leg2 svg{width:12px;height:12px;vertical-align:-2px;margin-right:3px}
.leg2 .src{font-size:10px;padding:0 3px;border-radius:3px;margin-right:3px;line-height:1.5}
.cline{font-size:12.5px;margin:4px 0}.cline b{color:var(--muted);font-weight:500;margin-right:4px}
.cline span{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:0 7px;margin:2px 2px 0 0;font-size:12px}
.cline em{font-style:normal;color:var(--muted);margin-left:3px;font-size:11px}
/* 与上期对比（缩略图行） */
.crow{display:grid;grid-template-columns:11em minmax(0,1fr);gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid var(--line)}
.crow:last-child{border-bottom:0}.ck{font-size:13px}.ck b{font-size:16px;margin-left:6px;font-variant-numeric:tabular-nums}
.cth{display:flex;flex-wrap:wrap;gap:4px;align-items:center}.cth .more{font-size:12px;color:var(--muted)}
.cth a.th{width:36px;height:36px}.cth a.mv{outline:2px solid var(--accent);outline-offset:-2px}
@media (max-width:560px){.crow{grid-template-columns:1fr;gap:4px}}
/* 小窗里的筛选、排序 */
.tools{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;margin:0 0 10px;font-size:13px;position:sticky;top:-12px;
z-index:3;background:var(--bg);padding:6px 0}
.tools select{font:inherit;font-size:13px;color:var(--text);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:3px 6px;max-width:46vw}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--card)}
.seg button{font:inherit;font-size:13px;border:0;background:none;color:var(--muted);padding:3px 10px;cursor:pointer}
.seg button.on{background:var(--chip);color:var(--text);font-weight:600}
.tools .cnt{color:var(--muted);margin-left:auto;font-size:12px}
details.more-facts{margin-top:4px}
"""


# 主体只放结论和图；完整的商品卡片都是隐藏的“小窗内容”，点 KPI 数字或链接时弹出，不跳转页面
POPUP = """<div class="pop" id="pop" hidden><div class="pop-mask" data-close></div>
<div class="pop-panel" role="dialog" aria-modal="true" aria-labelledby="pop-title">
<div class="pop-head"><b id="pop-title"></b><button type="button" class="pop-x" data-close aria-label="关闭">×</button></div>
<div class="pop-body" id="pop-body"></div></div></div>
<script>(function(){
var pop=document.getElementById('pop'),body=document.getElementById('pop-body'),title=document.getElementById('pop-title');
function closest(el,sel){while(el&&el.nodeType===1){if(el.matches?el.matches(sel):el.msMatchesSelector(sel))return el;el=el.parentNode;}return null;}
var KIND={surge:'突然爆火',potential:'潜力',rising:'上升中'};
function el(tag,cls,text){var x=document.createElement(tag);if(cls)x.className=cls;if(text!=null)x.textContent=text;return x;}
function num(c,k){var v=c.getAttribute('data-'+k);return v===null||v===''?NaN:+v;}
// 卡片多的小窗：按类别、子类目筛，按月增量 / 售价 / 上架天数 / 评分排（卡片上的 data-*，见 render.card）
function tools(root){var box=root.querySelector('.cards[data-tools]');if(!box)return;
var cards=[].slice.call(box.children);if(cards.length<6)return;
var nodes={},kinds={},order=[],hasInc=false;
cards.forEach(function(c,i){c.setAttribute('data-i',i);var n=c.getAttribute('data-node'),k=c.getAttribute('data-kind');
if(n){if(!nodes[n]){nodes[n]=0;order.push(n);}nodes[n]++;}if(k)kinds[k]=(kinds[k]||0)+1;if(!isNaN(num(c,'inc')))hasInc=true;});
var bar=el('div','tools'),st={k:'',n:'',s:''},cnt=el('span','cnt');
var kn=Object.keys(kinds);
if(kn.length>1){var seg=el('span','seg');[''].concat(kn).forEach(function(k){var b=el('button',k?'':'on',k?(KIND[k]||k)+' '+kinds[k]:'全部');
b.type='button';b.setAttribute('data-k',k);seg.appendChild(b);});bar.appendChild(seg);
seg.addEventListener('click',function(e){var b=closest(e.target,'button');if(!b)return;st.k=b.getAttribute('data-k');
[].forEach.call(seg.children,function(x){x.className=x===b?'on':'';});apply();});}
if(order.length>1){var sel=el('select');sel.appendChild(new Option('全部'+(box.getAttribute('data-group')||'子类目')+'（'+cards.length+'）',''));
order.sort(function(a,b){return nodes[b]-nodes[a];}).forEach(function(n){sel.appendChild(new Option(n+'（'+nodes[n]+'）',n));});
sel.addEventListener('change',function(){st.n=sel.value;apply();});bar.appendChild(sel);}
var so=el('select'),opts=[['','默认排序']];if(hasInc)opts.push(['-inc','月增量 高→低']);
opts.push(['price','售价 低→高'],['-price','售价 高→低'],['age','上架天数 少→多'],['-rating','评分 高→低']);
opts.forEach(function(o){so.appendChild(new Option(o[1],o[0]));});so.addEventListener('change',function(){st.s=so.value;apply();});
bar.appendChild(so);bar.appendChild(cnt);box.parentNode.insertBefore(bar,box);
function apply(){var key=st.s.replace('-',''),dir=st.s.charAt(0)==='-'?-1:1,shown=0;
var list=cards.slice().sort(function(a,b){if(!key)return num(a,'i')-num(b,'i');var x=num(a,key),y=num(b,key);
if(isNaN(x)&&isNaN(y))return num(a,'i')-num(b,'i');if(isNaN(x))return 1;if(isNaN(y))return -1;return (x-y)*dir||num(a,'i')-num(b,'i');});
list.forEach(function(c){var ok=(!st.k||c.getAttribute('data-kind')===st.k)&&(!st.n||c.getAttribute('data-node')===st.n);
c.style.display=ok?'':'none';if(ok)shown++;box.appendChild(c);});cnt.textContent='显示 '+shown+' / '+cards.length;}
apply();}
function open(key){var src=document.getElementById('pop-'+key);if(!src)return false;
title.textContent=src.getAttribute('data-title');body.innerHTML=src.innerHTML;
pop.querySelector('.pop-panel').style.setProperty('--c','var(--'+src.getAttribute('data-c')+')');
pop.hidden=false;document.documentElement.className+=' noscroll';body.scrollTop=0;tools(body);return true;}
function close(){pop.hidden=true;document.documentElement.className=document.documentElement.className.replace(/\\s*noscroll/g,'');}
document.addEventListener('click',function(e){var t=closest(e.target,'[data-pop]');
if(t){if(open(t.getAttribute('data-pop')))e.preventDefault();return;}
if(closest(e.target,'[data-close]'))close();});
document.addEventListener('keydown',function(e){if(e.key==='Escape'||e.keyCode===27){close();return;}
var t=e.target;if((e.key==='Enter'||e.key===' ')&&t&&t.getAttribute&&t.getAttribute('role')==='button'&&t.getAttribute('data-pop')){
e.preventDefault();open(t.getAttribute('data-pop'));}});
})();</script>"""

# 悬停 / 点按显示读数：带 data-tip 的元素（图上的点、柱、条），小窗里也能用
TIP_JS = """<script>(function(){var tip=document.createElement('div');tip.className='tip';tip.hidden=true;document.body.appendChild(tip);
function find(el){while(el&&el.nodeType===1){if(el.getAttribute('data-tip'))return el;el=el.parentNode;}return null;}
function show(e){var t=find(e.target);if(!t){tip.hidden=true;return;}var p=e.touches?e.touches[0]:e;
tip.textContent=t.getAttribute('data-tip');tip.hidden=false;var w=tip.offsetWidth,h=tip.offsetHeight,x=p.clientX+12,y=p.clientY+14;
if(x+w>window.innerWidth-8)x=p.clientX-w-12;if(y+h>window.innerHeight-8)y=p.clientY-h-14;
tip.style.left=Math.max(8,x)+'px';tip.style.top=Math.max(8,y)+'px';}
document.addEventListener('mousemove',show);document.addEventListener('touchstart',show,{passive:true});
window.addEventListener('scroll',function(){tip.hidden=true;},true);})();</script>"""

# 开发方向一览：点表头按那一列排（data-sort；data-asc = 从小到大）
BOARD_JS = """<script>(function(){document.addEventListener('click',function(e){var b=e.target;
while(b&&b.nodeType===1&&!(b.getAttribute('data-sort')&&b.tagName==='BUTTON'))b=b.parentNode;if(!b||b.nodeType!==1)return;
var board=b.parentNode.parentNode,key=b.getAttribute('data-sort'),asc=b.getAttribute('data-asc')==='1';
var rows=[].slice.call(board.querySelectorAll('.brow'));
rows.sort(function(x,y){var u=+x.getAttribute('data-'+key),v=+y.getAttribute('data-'+key);
if(isNaN(u)&&isNaN(v))return 0;if(isNaN(u))return 1;if(isNaN(v))return -1;return asc?u-v:v-u;});
rows.forEach(function(r){board.appendChild(r);});
[].forEach.call(b.parentNode.querySelectorAll('button'),function(x){x.className=x===b?'on'+(asc?' asc':''):'';});});})();</script>"""


def _tag_class(tag: str) -> str:
    if any(k in tag for k in ("假爆火", "降价", "陈旧", "脉冲", "断货", "评分", "回落", "不稳定", "拉动", "还会被改",
                              "变体变多", "数据可能有误", "没有日数据", "没涨够")):
        return "warn"
    if tag in ("稳在高位", "仍在上涨", "新品爆发", "潜力") or tag.startswith("上升中"):
        return "good"
    if any(k in tag for k in ("大促", "头部", "变体", "换链接", "老链接", "对比期", "随大盘")):
        return "info"
    return ""


def tag_head(tag: str) -> str:
    """标签的短名：括号、冒号、逗号之前的部分（卡片上只放短名，完整说明折叠在“标签说明”里）。"""
    return re.split(r"[（(：:，,]", tag, maxsplit=1)[0].strip() or tag


def _tags(item: dict) -> str:
    chips, long, seen = [], [], set()
    for tag in item["tags"]:
        head = tag_head(tag)
        if head != tag:
            long.append(tag)
        if head in seen:
            continue
        seen.add(head)
        title = f' title="{esc(tag)}"' if head != tag else ""
        chips.append(f'<span class="tag {_tag_class(tag)}"{title}>{esc(head)}</span>')
    out = f'<div class="tags">{"".join(chips)}</div>' if chips else ""
    if long:
        out += ('<details class="why"><summary>标签说明</summary><ul>'
                + "".join(f"<li>{esc(t)}</li>" for t in long) + "</ul></details>")
    return out


def _features(item: dict, n: int = 5) -> str:
    feats = (item.get("features") or [])[:n]
    return f'<div class="fx">{"".join(f"<i>{esc(f)}</i>" for f in feats)}</div>' if feats else ""


def _head(item: dict, group: str = "") -> str:
    url = amazon(item["asin"])
    node = item.get("node_cn") or item.get("node_name") or ""
    rating = f"★{item['rating']}（{fmt_int(item['ratings'])}）" if item.get("rating") is not None else ""
    age = f"上架 {item['age_days']} 天" if item.get("age_days") is not None else ""
    material = item.get("material") if item.get("material") not in (None, "未注明") else ""
    meta = " · ".join(x for x in (esc(item.get("brand")), esc(node), esc(material), fmt_price(item.get("price")),
                                  rating, age, esc(item["asin"])) if x)
    badge = f'<span class="grp">{esc(group)}</span>' if group else ""
    return (f'<a class="pic" href="{url}" {LINK}>{_img(item, 300)}</a><div>'
            f'<div class="t">{badge}<a href="{url}" {LINK}>{esc(item["title"])}</a></div>'
            f'<div class="meta">{meta}</div>')


def _pill(label: str, value: str, hl: bool = False) -> str:
    return f'<span class="pill{" hl" if hl else ""}"><em>{esc(label)}</em>{value}</span>'


def increment_text(item: dict) -> str:
    """卡片 / 图墙上的“月增量”数：按 0 算的写“按 0”（原因另写），基准未知的写“无法估算”。"""
    if item.get("baseline_unknown"):
        return "无法估算"
    if increment_note(item).startswith("按 0"):
        return "按 0"
    return directions.money_text(directions.increment(item))


def _card_data(item: dict, kind: str) -> str:
    """小窗筛选、排序用（POPUP 的 tools）：子类目、类别、月增量（增长类才有，按 0 和无法估算的不参与排序）、售价、上架天数、评分。"""
    inc = ""
    if kind in GROUP_CN and not item.get("baseline_unknown") and not increment_note(item).startswith("按 0"):
        inc = f"{directions.increment(item):.0f}"
    node = (item.get("ocean") or {}).get("niche") if kind == "ocean" else None
    vals = {"node": node or item.get("node_cn") or item.get("node_name") or "", "kind": kind, "inc": inc,
            "price": item.get("price"), "age": item.get("age_days"), "rating": item.get("rating")}
    return "".join(f' data-{k}="{esc(v)}"' for k, v in vals.items() if v not in (None, ""))


def card(item: dict, kind: str, group: str = "", similar: list[dict] = ()) -> str:
    """kind：surge / potential / hot / fake / rising（决定展示哪些数字和走势图）；group：增长商品小窗里的归类；
    similar：并到这张卡片上的相近款（detect.showcase）。"""
    c = LABEL_COLOR.get(kind, "muted")
    kv: list[str] = []
    s = item.get("surge")
    if kind == "fake":
        level = "高度疑似" if item["fake"]["level"] == "high" else "疑似"
        kv.append(f'<span class="pill"><span class="score">异常分 {item["fake"]["score"]}</span>{level}</span>')
    if kind == "surge" or (kind == "fake" and s):
        ratio = f" ×{s['sales_ratio']:.1f}" if s.get("sales_ratio") else " 从零起量"
        kv.append(_pill(f"日均 前{s.get('base_days', 28)}天→近{s['window']}天",
                        f"{fmt_num(s['base_avg'])}→<b>{fmt_num(s['recent_avg'])}</b>{ratio}"))
        if s.get("base_bsr") and s.get("recent_bsr"):
            kv.append(_pill("BSR", f"{fmt_int(s['base_bsr'])}→<b>{fmt_int(s['recent_bsr'])}</b>"))
    if kind == "potential" and item.get("potential"):
        p = item["potential"]
        if p.get("growth") is not None:
            kv.append(f'<span class="pill"><b>{esc(growth_text(item))}</b></span>')
        kv.append(_pill("近28天日均", fmt_num(p.get("avg28"))))
        kv.append(_pill("近7天", fmt_num(item.get("recent_avg7"))))
        if p.get("recent_ratio"):
            kv.append(_pill("较前28天", f"<b>×{p['recent_ratio']:.2f}</b>"))
    if kind == "rising" and item.get("rising"):
        kv.append(_pill("较前28天", f"<b>×{item['rising']['ratio']:.2f}</b>"))
    if kind == "hot" and item.get("hot"):
        h = item["hot"]
        kv.append(_pill("近6月月均", f"<b>{fmt_int(h['avg_monthly'])}</b> 件"))
        kv.append(_pill("达标", f"{h['months_hit']}/{h['lookback']} 月"))
        kv.append(_pill("波动", f"{h['cv']:.2f}"))
        if h.get("trend") is not None:
            kv.append(_pill("近3月", f"{fmt_pct(h['trend'])}/月"))
    if kind == "ocean" and item.get("ocean"):
        o = item["ocean"]
        kv.append(_pill("细分", f"<b>{esc(o['niche'])}</b> · 全市场 {fmt_int(o['n'])} 个"))
        kv.append(_pill("近30天", f"<b>{esc(directions.money_text(o['revenue']))}</b>"))
    if kind in ("hot", "fake", "rising", "ocean") and item.get("recent_avg28") is not None:
        kv.append(_pill("近28天日均", fmt_num(item["recent_avg28"])))
    note = ""
    if kind in ("surge", "potential", "rising"):  # 爆火、潜力两个板块按它排（analyze.sections），和开发方向同一个口径
        kv.append(_pill("月增量", f"<b>{esc(increment_text(item))}</b>", hl=True))
        text = increment_note(item)
        note = f'<div class="note">{esc(text)}</div>' if text else ""  # 紧跟在月增量后面：按 0 的原因、近 28 天没多卖等

    chart = bars(item["monthly"][-12:]) if kind == "hot" else sparkline(item.get("spark") or [], start=item.get("spark_start"))
    look = f'<div class="look">{esc(item["look"])}</div>' if item.get("look") else ""
    signals = ""
    if item["fake"]["signals"] and kind in ("fake", "surge", "potential", "rising"):
        signals = "<ul class=\"signals\">" + "".join(
            f"<li>{esc(sig['text'])}（+{sig['points']}）</li>" for sig in item["fake"]["signals"]) + "</ul>"
    sim = ""
    if similar:
        sim = (f'<div class="simrow">相近款 {len(similar)} 个：' + "".join(thumb(x) for x in similar[:8])
               + ("…" if len(similar) > 8 else "") + "</div>")
    return (f'<article class="card" style="--c:var(--{c})"{_card_data(item, kind)}>{_head(item, group)}'
            f'<div class="kv">{"".join(kv)}</div>{note}{_features(item)}{look}{_tags(item)}{signals}{sim}{chart}</div></article>')


def _pop_src(key: str, title: str, color: str, inner: str) -> str:
    return f'<div class="pop-src" id="pop-{key}" data-title="{esc(title)}" data-c="{color}" hidden>{inner}</div>'


def _skipped(cur: dict) -> str:
    """小窗底部：没列出的简单款（折叠，点开才看得到）。"""
    plain = cur.get("plain") or []
    if not plain:
        return ""
    mini = "".join(f'<span class="simrow">{thumb(i)}<a href="{amazon(i["asin"])}" {LINK}>{esc(short_name(i, 34))}</a></span>'
                   for i in plain)
    return (f'<details class="skip"><summary>另有 {len(plain)} 个简单款没列出（主图和标题都没有任何特色）</summary>'
            f'<div class="mini">{mini}</div></details>')


def _count_text(cur: dict, total: int) -> str:
    shown = len(cur.get("shown") or [])
    folded = sum(len(v) for v in (cur.get("similar") or {}).values())
    plain = len(cur.get("plain") or [])
    if shown == total:
        return f"共 {total} 个。"
    parts = [f"简单款 {plain} 个没列"] if plain else []
    if folded:
        parts.append(f"相近款 {folded} 个记在相似的卡片上")
    return f"判定 {total} 个，列出 {shown} 个（{'，'.join(parts)}）。"


def _section_pop(kind: str, title: str, hint: str, cur: dict, total: int) -> str:
    similar = cur.get("similar") or {}
    body = "".join(card(i, kind, similar=similar.get(i["asin"], [])) for i in cur["shown"]) \
        or '<div class="empty">本期没有符合条件的商品。</div>'
    return _pop_src(kind, title, LABEL_COLOR[kind],
                    f'<p class="hint">{esc(hint)} {esc(_count_text(cur, total))}</p><div class="cards" data-tools>{body}</div>{_skipped(cur)}')


def _growth_pop(cur: dict, hint: str, total: int) -> str:
    similar = cur.get("similar") or {}
    cards = []
    for i in cur["shown"]:
        kind = i["label"] if i["label"] in ("surge", "potential") else "rising"
        cards.append(card(i, kind, GROUP_CN[kind], similar=similar.get(i["asin"], [])))
    body = "".join(cards) or '<div class="empty">本期没有增长商品。</div>'
    return _pop_src("growth", "增长商品", "potential",
                    f'<p class="hint">{esc(hint)} {esc(_count_text(cur, total))}</p><div class="cards" data-tools>{body}</div>{_skipped(cur)}')


def ocean_group(item: dict) -> str:
    """蓝海产品卡片上写它本来在哪个板块（蓝海是另一份名单，商品照样留在自己的板块里）。"""
    kind = growth_kind(item)
    if kind:
        return GROUP_CN[kind]
    return "持续热销" if item["label"] == "hot" else ""


def _ocean_note(cov: dict) -> str:
    """全市场名单上符合的几个，其中有日数据的、只有名单数据的几个，核对没通过的原因（数据说明里也有这一行）。"""
    o = cov.get("ocean") or {}
    if not o.get("candidates"):
        return ""
    missed = "、".join(f"{k} {v}" for k, v in sorted((o.get("missed") or {}).items(), key=lambda kv: -kv[1]))
    big = [k for k, v in (o.get("niches") or {}).items() if not v.get("small")]
    simple = sum(int(v.get("simple") or 0) for v in (o.get("niches") or {}).values())
    return (f"名单上符合 {o['candidates']} 个：有日数据 {o.get('daily', 0)} 个、月度补判 {o.get('monthly', 0)} 个、"
            f"只有名单数据 {o.get('list_only', 0)} 个" + (f"（没列：{missed}）" if missed else "")
            + (f"；太小、结构太简单的 {simple} 个不算" if simple else "")
            + (f"；{'、'.join(big)} 圈子太大，不算" if big else "") + "。")


def _niche_row(cov: dict) -> str:
    """小窗顶部：每一类在全市场名单里有几个够门槛的商品、合计月销售额、几个品牌（圈子有多大）。"""
    niches = (cov.get("ocean") or {}).get("niches") or {}
    rows = sorted(((k, v) for k, v in niches.items() if v.get("small")), key=lambda kv: -kv[1]["revenue"])
    if not rows:
        return ""
    return '<div class="nich">' + "".join(
        f'<span title="{esc("、".join(v["nodes"]))}"><b>{esc(k)}</b> {v["n"]} 个 · {esc(directions.money_text(v["revenue"]))}/月'
        f' · {v["brands"]} 个品牌</span>' for k, v in rows) + "</div>"


def _ocean_pop(cur: dict, hint: str, total: int, cov: dict) -> str:
    similar = cur.get("similar") or {}
    body = "".join(card(i, "ocean", ocean_group(i), similar=similar.get(i["asin"], [])) for i in cur["shown"])         or '<div class="empty">本期没有符合条件的商品。</div>'
    return _pop_src("ocean", "蓝海产品", "ocean",
                    f'<p class="hint">{esc(hint)} {esc(_ocean_note(cov))}{esc(_count_text(cur, total))}</p>{_niche_row(cov)}'
                    f'<div class="cards" data-tools data-group="细分">{body}</div>{_skipped(cur)}')


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


def _tile_metric(item: dict, kind: str) -> str:
    if kind == "surge" and item.get("surge"):
        s = item["surge"]
        ratio = f" <b>×{s['sales_ratio']:.1f}</b>" if s.get("sales_ratio") else " <b>从零起量</b>"
        return f"日均 {s['base_avg']:.0f}→{s['recent_avg']:.0f}{ratio}"
    if kind == "potential":
        return f"<b>{esc(growth_text(item))}</b>"
    if item.get("rising"):
        return f"近 28 天 <b>×{item['rising']['ratio']:.2f}</b>"
    return ""


def _trend(item: dict, kind: str) -> str:
    """图墙上的迷你走势：阴影 = 判定比的最近几天（突然爆火 7 / 14 天，其余 28 天），两段横线 = 两段比较期各自的日均。"""
    s = item.get("surge") or {}
    if kind == "surge" and s:
        recent, levels = int(s.get("window") or 7), (s.get("base_avg"), s.get("recent_avg"))
    else:
        recent, levels = 28, (item.get("prev_avg28"), item.get("recent_avg28"))
    return charts.mini_spark(item.get("spark") or [], item.get("spark_start"), recent, levels)


def _tile(item: dict, similar: list[dict]) -> str:
    kind = growth_kind(item) or item["label"]
    node = item.get("node_cn") or item.get("node_name") or ""
    feats = "".join(f"<i>{esc(f)}</i>" for f in (item.get("features") or [])[:3])
    sim = f'<span class="sim">另有相近款 {len(similar)} 个</span>' if similar else ""
    inc = increment_text(item)
    inc = f"月增量 {inc}" if inc in ("按 0", "无法估算") else f"<b>+{esc(inc)}</b>/月"
    return (f'<a class="tile" href="{amazon(item["asin"])}" {LINK} style="--c:var(--{LABEL_COLOR.get(kind, "muted")})">'
            f'<span class="im">{_img(item, 400)}</span><span class="badge">{esc(GROUP_CN.get(kind, item["label_cn"]))}</span>'
            f'<span class="bd"><span class="tt">{esc(short_name(item, 60))}</span>'
            f'<span class="mm">{esc(node)} · {fmt_price(item.get("price"))}</span>'
            f'<span class="kk"><span>{_tile_metric(item, kind)}</span><span class="inc">{inc}</span></span>{_trend(item, kind)}'
            + (f'<span class="fx">{feats}</span>' if feats else "") + f"{sim}</span></a>")


def _gallery(cur: dict, cfg: dict) -> str:
    rows = cur.get("gallery") or []
    growth = cur["growth"]
    similar = growth.get("similar") or {}
    per_node = int((cfg["report"].get("showcase") or {}).get("gallery_per_node", 1))
    body = "".join(_tile(i, similar.get(i["asin"], [])) for i in rows) or '<p class="empty">本期没有增长商品。</p>'
    return (f'<section id="picks" style="--c:var(--surge)"><h2><span class="dot"></span>本期增长商品'
            f'<a class="h-more" href="#" data-pop="growth">全部 {len(growth["shown"])} 个 ›</a></h2>'
            f'<p class="hint">有特色的增长商品按月增量排，每个子类目 {per_node} 个。走势：近 56 天日销量，'
            f'阴影是判定比的最近几天，两段横线是前后两段的日均。点图打开亚马逊。</p><div class="gal">{body}</div></section>')


def _kpi(key: str, label: str, desc: str, rows: list[dict], total: int, folded: str = "简单款、相近款") -> str:
    thumbs = "".join(_img(i, 100) for i in rows[:5])
    hidden = total - len(rows)
    extra = f'<span class="kpi-x">另 {hidden} 个{folded}没列</span>' if hidden > 0 else ""
    return (f'<button type="button" class="kpi" data-pop="{key}" style="--c:var(--{key})">'
            f'<span class="kpi-n"><b>{len(rows)}</b><span>{label}</span></span><span class="kpi-d">{esc(desc)}</span>'
            f'<span class="kpi-th">{thumbs}</span>{extra}</button>')


def _examples(pool: list[dict], test, n: int = 3) -> str:
    """有这个特征的增长商品，取前 n 个的缩略图（pool = 列出的增长商品，按势头）。"""
    picked = [i for i in pool if test(i)][:n]
    return f'<div class="ths">{"".join(thumb(i) for i in picked)}</div>' if picked else ""


def _material_box(m: dict, lookups: int) -> str:
    """主材质：每类一行，实心点 = 增长商品里的占比，空心圈 = 持续热销对照组，右边是个数（增长 · 对照）。"""
    rows = [r for r in m.get("rows") or [] if r["count"] or r["reference"]]
    if not rows:
        return ""
    nf, nr = m["n_focus"], m["n_reference"]
    lines = []
    for r in rows:
        tip = f"{r['label']}：增长商品 {r['count']}/{nf}（{r['share']:.0%}）" + (
            f" · 对照组 {r['reference']}/{nr}（{r['reference_share']:.0%}）" if nr else "")
        lines.append(f'<div class="frow mrow"><b class="flab">{esc(r["label"])}</b>'
                     f'<span class="fdb">{charts.dumbbell(r["share"], r["reference_share"] if nr else None, tip)}</span>'
                     f'<span class="cnt">{r["count"]}/{nf}' + (f' · {r["reference"]}/{nr}' if nr else "") + "</span></div>")
    src = ""
    if "amazon_focus" in m:
        src = (f'<p class="hint">亚马逊 Material 属性：增长 {m["amazon_focus"]}/{nf}、对照 {m["amazon_reference"]}/{nr}，'
               f'其余看标题 / 主图（每期最多查 {lookups} 个，查过的缓存）</p>')
    return f'<div class="dbox"><h4>主材质</h4>{src}{"".join(lines)}</div>'


DIM_SHORT = {"功能卖点": "功能"}


def _tracks(f: dict, vision: dict) -> list[tuple[str, float, float | None, str]]:
    """一个特征的依据：主图（增长势头前 N vs 持续热销对照组）、标题（全部增长商品 vs 全部头部商品），有几条画几条。"""
    out = []
    if f.get("image"):
        i, nr = f["image"], vision.get("n_reference")
        out.append(("img", i["focus_share"], i["reference_share"] if nr else None,
                    f"主图：增长商品 {i['focus']}/{vision['n_focus']}（{i['focus_share']:.0%}）"
                    + (f" · 对照组 {i['reference']}/{nr}（{i['reference_share']:.0%}）" if nr else "")))
    if f.get("title"):
        t = f["title"]
        out.append(("ttl", t["share"], t["baseline_share"], f"标题：增长商品 {t['share']:.0%} · 全部头部商品 {t['baseline_share']:.0%}"))
    return out


def _feature_rows(appearance: dict, vision: dict, pool: list[dict]) -> str:
    """“比对照组更多”的特征，所有维度放在一起按差距（第一条依据的百分点）从大到小，一行一个。"""
    rows = []
    for dim, groups in appearance.items():
        for f in groups.get("more") or []:
            tracks = _tracks(f, vision)
            if tracks:
                gap = tracks[0][1] - (tracks[0][2] or 0)
                rows.append((gap, dim, f, tracks))
    rows.sort(key=lambda r: -r[0])
    out = []
    for gap, dim, f, tracks in rows:
        bells = "".join(f'<span><span class="src {src}">{"图" if src == "img" else "题"}</span>{charts.dumbbell(g, o, tip)}</span>'
                        for src, g, o, tip in tracks)
        out.append(f'<div class="frow"><span class="fdim">{esc(DIM_SHORT.get(dim, dim))}</span><b class="flab">{esc(f["label"])}</b>'
                   f'<span class="fdb">{bells}</span><span class="fgap">+{gap * 100:.0f}</span>'
                   + _examples(pool, lambda i, label=f["label"], dim=dim: label in (i.get("design") or {}).get(dim, ())) + "</div>")
    return "".join(out)


def _common_lines(appearance: dict, vision: dict) -> str:
    """常见但不比对照组多、也有：品类本来就这样做的，按维度一行，折叠起来。"""
    def chip(f: dict) -> str:
        t, i = f.get("title"), f.get("image")
        if i:
            ref = f" · {i['reference']}/{vision['n_reference']}" if vision.get("n_reference") else ""
            return f'<span>{esc(f["label"])}<em>{i["focus"]}/{vision["n_focus"]}{ref}</em></span>'
        return f'<span>{esc(f["label"])}<em>{t["share"]:.0%} · {t["baseline_share"]:.0%}</em></span>'
    lines = []
    for dim, groups in appearance.items():
        chips = [chip(f) for f in (groups.get("common") or []) + (groups.get("also") or [])]
        if chips:
            lines.append(f'<div class="cline"><b>{esc(DIM_SHORT.get(dim, dim))}</b>{"".join(chips)}</div>')
    if not lines:
        return ""
    return ("<details><summary>常见但不比对照组多的特征（品类本来就这样做，不算趋势）</summary>"
            '<p class="hint">数字 = 增长商品 · 对照组</p>' + "".join(lines) + "</details>")


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


def _direction_box(d: dict, pool: list[dict], head: bool = True) -> str:
    """一个开发方向：月增量、这个子类目列出的增长商品缩略图、每个维度“比头部多”（绿框）和“基本款”（灰框）、
    价格、包装、参考商品（带图）。head=False：小窗里用，月增量、缩略图已在小窗顶部，只留画像和参考商品。"""
    n = d.get("n_brands", d["n"])

    def chip(r: dict, tier: str) -> str:
        head = f"｜头部 {r['head_share']:.0%}" if r.get("head_share") is not None else ""
        share = f"{r['share']:.0%}" if r.get("share") is not None else f"{r['count']}/{n} 品牌"
        up = '<b class="up" title="在全部增长商品里也比对照组更多">↑</b>' if r.get("trend") else ""
        title = f"{r['count']}/{n} 个品牌有" + (f"，品牌均占比 {r['share']:.0%}" if r.get("share") is not None else "") \
            + (f"，同子类目头部 {r['head_share']:.0%}" if r.get("head_share") is not None else "")
        return f'<span class="{tier}" title="{esc(title)}">{esc(r["label"])}{up}<em>{share}{head}</em></span>'

    def chips(f) -> str:
        if isinstance(f, list):  # 主材质：只有一档
            body = "".join(f'<span class="base">{esc(r["label"])}<em>{r["count"]}/{n} 品牌</em></span>' for r in f)
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
    spec += f"<dt>包装</dt><dd>{esc(pkg) if pkg else '<span class=none>还没有包装数据</span>'}</dd>"
    by_asin = {i["asin"]: i for i in pool}
    refs = []
    for r in d["refs"]:
        item = by_asin.get(r["asin"]) or {"asin": r["asin"], "brand": r.get("brand"), "title": r["name"], "image": ""}
        extra = (f" · {esc(shipping.text(r.get('pkg'), r.get('price')))}" if r.get("pkg") else "")
        refs.append(f'<div class="refrow">{thumb(item, "th big")}<div><a href="{amazon(r["asin"])}" {LINK}>'
                    f'{esc(_clip(r["name"], 48))}</a> <em>{fmt_price(r.get("price"))}{extra}</em>'
                    + (f'<div>{esc(r["look"])}</div>' if r.get("look") else "") + "</div></div>")
    refs_html = (f'<div class="grp-h">参考商品</div><div class="refs">{"".join(refs)}</div>' if refs
                 else '<p class="none">势头靠前的都有异常信号或是简单款，不列参考商品</p>')
    ref_asins = {r["asin"] for r in d["refs"]}
    others = [i for i in pool if directions.node_of(i) == d["node"] and i["asin"] not in ref_asins][:6]
    ths = f'<div class="ths">{"".join(thumb(i) for i in others)}</div>' if others else ""
    sales = d.get("sales") or {}
    inc = (f'<span class="inc">+{esc(directions.money_text(sales.get("increment")))}/月</span>') if sales else ""
    surge = f" · 突然爆火 {d['n_surge']}" if d["n_surge"] else ""
    money = (f'月销售额约 {esc(directions.money_text(sales.get("monthly")))}，'
             f'比之前 28 天多约 {esc(directions.money_text(sales.get("increment")))}') if sales else ""
    cmp = d.get("compare") or {}
    basis = (f"对比同子类目头部 {cmp['n_head']} 个（{'标题 + 主图' if cmp['mode'] == 'image' else '只看标题'}）"
             if cmp.get("mode") else f"同子类目头部已知品牌只有 {cmp.get('n_head_brands_all', 0)} 个，只列共同特征")
    if not head:
        return (f'<div class="dbox dir"><h4>产品画像<span class="sub">{esc(basis)}；绿框 = 比头部多，灰框 = 品类多数都这样；'
                f'数字 = 品牌均占比｜头部</span></h4><dl class="spec">{spec}</dl>{refs_html}</div>')
    return (f'<div class="dbox dir"><h4>{esc(d["node"])}{inc}<span class="sub">增长商品 {d["n"]} 个 · {n} 个品牌{surge}</span></h4>'
            f'<div class="sub">{money}；{esc(basis)}</div>{ths}'
            f'<dl class="spec">{spec}</dl>{refs_html}</div>')


def _direction_overview(rows: list[dict], cfg: dict) -> str:
    """全部子类目一览（directions.overview）：每个子类目的追踪商品、增长商品、增量、品类大盘，有没有画像、没有的原因。"""
    if not rows:
        return ""
    has_tide = any(r["tide"] is not None for r in rows)
    reasons: dict[str, int] = {}
    for r in rows:
        if not r["shown"]:
            reasons[r["status"]] = reasons.get(r["status"], 0) + 1

    def money(r: dict, key: str) -> str:
        return esc(directions.money_text(r[key])) if r["n"] else "—"

    body = []
    for r in rows:
        growth = (f'{r["n"]} 个 · {r["n_brands"]} 个品牌' + (f' · 突然爆火 {r["n_surge"]}' if r["n_surge"] else "")
                  if r["n"] else "—")
        tide_text = "—" if r["tide"] is None else f"×{r['tide']:.2f}"
        tide = f"<td class=n>{tide_text}</td>" if has_tide else ""
        status = "有（见上表）" if r["shown"] else f'<span class="none">{esc(r["status"])}</span>'
        body.append(f'<tr><td>{esc(r["node"])}</td><td class=n>{r["n_tracked"]}</td><td>{growth}</td>'
                    f'<td class=n>{money(r, "monthly")}</td><td class=n>{money(r, "increment")}</td>{tide}<td>{status}</td></tr>')
    head = ("<tr><th>子类目</th><th class=n>追踪商品</th><th>增长商品</th><th class=n>月销售额</th><th class=n>比之前 28 天多</th>"
            + ("<th class=n>品类大盘</th>" if has_tide else "") + "<th>画像</th></tr>")
    shown = sum(1 for r in rows if r["shown"])
    summary = (f"全部 {len(rows)} 个子类目一览：{shown} 个有画像（见上表）"
               + "".join(f"，{k} 个{reason}" for reason, k in sorted(reasons.items(), key=lambda x: -x[1])))
    return (f'<details class="rules"><summary>{esc(summary)}</summary>'
            f'<div class=tbl><table><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div></details>')


def _compact(n: float | None) -> str:
    if n is None:
        return "—"
    return f"{n / 1000:.1f}k" if n >= 1000 else f"{n:.0f}"


def _kind_dots(rows: list[dict]) -> str:
    """●7 ●3 ●3：这个子类目的增长商品里突然爆火 / 潜力 / 上升中各几个（颜色和价格带上的点一致）。"""
    counts: dict[str, int] = {}
    for i in rows:
        k = growth_kind(i) or "rising"
        counts[k] = counts.get(k, 0) + 1
    return "".join(f'<i class="kd k-{k}" title="{GROUP_CN[k]}"></i>{counts[k]}' for k in GROUP_CN if counts.get(k))


def _price_dots(rows: list[dict]) -> list[tuple[float, str, str]]:
    return [(float(i["price"]), growth_kind(i) or "rising",
             f"{fmt_price(i['price'])} · {GROUP_CN[growth_kind(i) or 'rising']} · {short_name(i, 40)}")
            for i in rows if i.get("price")]


KIND_LEGEND = "".join(f'<span><i class="k-{k}"></i>{v}</span>' for k, v in GROUP_CN.items())


def _row_feats(d: dict) -> str:
    """一行画像：主材质 + 比头部多的特征（按差距，最多 4 个）；没有比头部多的写基本款（最多 3 个）。"""
    out = [f'<span class="mat">{esc(m["label"])}</span>' for m in d["materials"][:1]
           if m["label"] not in directions.VAGUE_MATERIALS]
    more = sorted((f for dim in directions.DIMENSIONS for f in d["features"][dim]["more"]),
                  key=lambda f: -((f.get("share") or 0) - (f.get("head_share") or 0)))
    for f in more[:4]:
        tip = f"{f['count']} 个品牌有，品牌均占比 {f['share']:.0%}" + (
            f"，同子类目头部 {f['head_share']:.0%}" if f.get("head_share") is not None else "")
        out.append(f'<span class="more" data-tip="{esc(tip)}">{esc(f["label"])}'
                   f'<em> {f["share"]:.0%}｜{(f.get("head_share") or 0):.0%}</em></span>')
    if not more:
        base = [f for dim in directions.DIMENSIONS for f in d["features"][dim]["base"]][:3]
        out += [f'<span class="base">{esc(f["label"])}</span>' for f in base]
    return "".join(out)


def _board_row(k: int, d: dict, m: dict | None, rows: list[dict], shown: list[dict], dom, inc_max: float) -> str:
    sales = d.get("sales") or {}
    inc = sales.get("increment") or 0
    pics = (shown or rows)[:4]
    collage = (f'<span class="bpic n{max(1, len(pics))}">' + "".join(_img(i, 100) for i in pics) + "</span>")
    width = 100 * max(0, inc) / inc_max if inc_max > 0 else 0
    inc_tip = (f"月销售额约 {directions.money_text(sales.get('monthly'))}，比之前 28 天多约 {directions.money_text(inc)}"
               f"（增长商品 {d['n']} 个）")
    band = (m or {}).get("price")
    price_text = (f"主力 ${band['p25']:,.0f}–{band['p75']:,.0f}" if band else
                  f"增长商品 {directions.price_text(d.get('price'))}" if d.get("price") else "")
    data = {"inc": inc, "n": d["n"], "new": (m or {}).get("new_share"), "rev": (m or {}).get("head_reviews"),
            "price": (band or {}).get("p50")}
    attrs = "".join(f' data-{key}="{v:.6g}"' for key, v in data.items() if v is not None)
    if m:
        new = (f'<span class="bnew" data-tip="{esc(f"""近 30 天量够的 {m["n"]} 个里，上架 ≤{m["new_days"]} 天的 {m["n_new"]} 个占销售额 {m["new_share"]:.0%}""")}">'
               f'<em class="ml">新品占比</em><b>{m["new_share"]:.0%}</b><span class="m2"><i style="width:{min(100, m["new_share"] * 200):.0f}%"></i></span></span>')
        rev = (f'<span class="brev" data-tip="{esc(f"""销售额前 {m["head_n"]} 名的评论数中位数；前 {m["top_brands"]} 个品牌占销售额 {m["brand_share"]:.0%}""")}">'
               f'<em class="ml">头部评论</em><b>{_compact(m["head_reviews"])}</b></span>')
    else:
        new, rev = '<span class="bnew">—</span>', '<span class="brev">—</span>'
    return (f'<div class="brow" role="button" tabindex="0" data-pop="dir{k}"{attrs}>{collage}'
            f'<span class="bname"><b>{esc(d["node"])}</b><span class="kc">{_kind_dots(rows)}</span>'
            f'<span class="sub">增长 {d["n"]} 个 · {d.get("n_brands", d["n"])} 个品牌</span></span>'
            f'<span class="binc" data-tip="{esc(inc_tip)}"><span class="ibar"><i style="width:{width:.0f}%"></i></span>'
            f'<b>{"+" if inc > 0 else ""}{esc(directions.money_text(inc))}</b></span>'
            f'<span class="bprice">{charts.price_strip(band, _price_dots(rows), dom)}{esc(price_text)}</span>'
            f'{new}{rev}<span class="bfeat">{_row_feats(d)}</span><span class="bgo">›</span></div>')


def _direction_pop(k: int, d: dict, m: dict | None, rows: list[dict], cur: dict, pool: list[dict]) -> str:
    """点一行弹出的小窗：数字、价格分布（全市场名单 vs 增长商品）、产品画像和参考商品、这个子类目列出的增长商品卡片。"""
    sales = d.get("sales") or {}
    stats = [("月增量", f"+{directions.money_text(sales.get('increment'))}" if (sales.get("increment") or 0) > 0
              else directions.money_text(sales.get("increment"))),
             ("增长商品月销售额", directions.money_text(sales.get("monthly"))),
             ("增长商品", f"{d['n']} 个 · {d.get('n_brands', d['n'])} 品牌")]
    if d.get("price"):
        stats.append(("增长商品售价", directions.price_text(d["price"])
                      + (f"（中位 ${d['price']['median']:,.0f}）" if d["price"].get("median") is not None else "")))
    chart = ""
    if m:
        if m.get("price"):
            stats.append(("全市场主力价格带", f"${m['price']['p25']:,.0f}–{m['price']['p75']:,.0f}"))
        stats += [("新品占比", f"{m['new_share']:.0%}（{m['n_new']}/{m['n']} 个）"),
                  ("头部评论", f"{_compact(m['head_reviews'])} 条"), (f"前 {m['top_brands']} 品牌", f"{m['brand_share']:.0%}")]
        dots = _price_dots(rows)
        hist_edges = [e for k, h in enumerate(m.get("hist") or []) if h >= 0.02  # 占 2% 以上的价格段都画进来
                      for e in (market_edges[k], market_edges[k + 1]) if 0 < e < float("inf")]
        dom = charts.price_domain([p for p, *_r in dots] + [v for v in (m.get("price") or {}).values() if v] + hist_edges)
        hist = charts.price_hist(m.get("hist") or [], market_edges, m.get("price"), dots, dom)
        if hist:
            chart = (f'<h5>价格带</h5><div class="kleg"><span><i class="band"></i>各价格段占全市场名单销售额</span>{KIND_LEGEND}</div>'
                     f'{hist}<p class="hint">全市场名单 = 这个子类目近 30 天 ≥300 件或 ≥$4 万的 {m["n"]} 个商品；'
                     f'新品 = 上架 ≤{m["new_days"]} 天；头部评论 = 销售额前 {m["head_n"]} 名的评论数中位数。</p>')
    stat_html = "".join(f"<span>{esc(a)}<b>{esc(b)}</b></span>" for a, b in stats)
    similar = cur["growth"].get("similar") or {}
    listed = [i for i in cur["growth"]["shown"] if directions.node_of(i) == d["node"]]
    cards = "".join(card(i, growth_kind(i) or "rising", GROUP_CN[growth_kind(i) or "rising"],
                         similar=similar.get(i["asin"], [])) for i in listed)
    hidden = len(rows) - len(listed) - sum(len(similar.get(i["asin"], [])) for i in listed)
    more = f"（另 {hidden} 个简单款没列）" if hidden > 0 else ""
    cards_html = (f'<h5>这个子类目的增长商品 {len(listed)} 个{more}</h5><div class="cards">{cards}</div>' if cards else "")
    return _pop_src(f"dir{k}", f"开发方向 · {d['node']}", "surge",
                    f'<div class="dstats">{stat_html}</div>{chart}{_direction_box(d, pool, head=False)}{cards_html}')


def _directions(tr: dict, cfg: dict, cur: dict, focus: list[dict]) -> tuple[str, str]:
    """开发方向：一行一个子类目的一览（缩略图、月增量、价格带、新品占比、头部评论、比头部多的特征），点一行弹出完整画像。
    返回（主体里的板块，各子类目的小窗内容）。"""
    c = cfg["thresholds"]["directions"]
    dirs = tr.get("directions") or []
    mk = tr.get("market") or {}
    pool = cur["growth"]["shown"]
    by_node: dict[str, list[dict]] = {}
    for i in focus:
        by_node.setdefault(directions.node_of(i), []).append(i)
    shown_by: dict[str, list[dict]] = {}
    for i in pool:
        shown_by.setdefault(directions.node_of(i), []).append(i)
    prices = [i["price"] for d in dirs for i in by_node.get(d["node"], []) if i.get("price")]
    prices += [v for d in dirs for key, v in ((mk.get(d["node"]) or {}).get("price") or {}).items() if v and key != "p50"]
    dom = charts.price_domain(prices)
    inc_max = max([(d.get("sales") or {}).get("increment") or 0 for d in dirs] + [0])
    rows = "".join(_board_row(k, d, mk.get(d["node"]), by_node.get(d["node"], []), shown_by.get(d["node"], []), dom, inc_max)
                   for k, d in enumerate(dirs))
    pops = "".join(_direction_pop(k, d, mk.get(d["node"]), by_node.get(d["node"], []), cur, pool) for k, d in enumerate(dirs))
    rule = (f"增长商品 ≥{c['min_items']} 个、≥{c.get('min_brands', 1)} 个品牌的子类目，按月增量排；点表头换排序，点一行看画像、价格分布和商品。")
    mc = cfg["report"].get("market") or {}
    legend = (f'<div class="kleg">{KIND_LEGEND}<span><i class="band"></i>全市场主力价格带（一半销售额，竖线 = 中位）</span>'
              f'<span>新品占比 = 上架 ≤{int(mc.get("new_days", 180))} 天的占销售额</span>'
              f'<span>头部评论 = 销售额前 {int(mc.get("head_n", 20))} 名评论中位数</span>'
              '<span>特征后的数 = 增长商品｜头部的品牌均占比</span></div>') if mk else f'<div class="kleg">{KIND_LEGEND}</div>'
    head =('<div class="bhead"><span class="h-pic"></span><button type="button" data-sort="n">子类目 · 增长数</button>'
            '<button type="button" data-sort="inc" class="on">月增量</button>'
            f'<span class="h-price">{charts.price_axis(dom)}</span>'
            '<button type="button" data-sort="new">新品占比</button>'
            '<button type="button" data-sort="rev" data-asc="1">头部评论</button><span class="h-go"></span></div>')
    body = (f'<div class="board">{head}{rows}</div>' if rows else '<p class="empty">本期没有增长商品数达到门槛的子类目。</p>')
    return (f'<section id="dirs" style="--c:var(--surge)"><h2><span class="dot"></span>开发方向</h2>'
            f'<p class="hint">{esc(rule)}</p>{legend}{body}'
            f'{_direction_overview(tr.get("direction_overview") or [], cfg)}</section>', pops)


def _traits(tr: dict, cfg: dict, pool: list[dict]) -> str:
    if tr.get("note"):
        return f'<p class="empty">{esc(tr["note"])}</p>'
    vision = tr.get("vision") or {}
    mats = tr.get("materials") or {}
    appearance = tr.get("appearance") or {}
    dot = '<svg viewBox="0 0 12 12"><circle cx="6" cy="6" r="4" class="{}"/></svg>'
    legend = (f'<div class="leg2"><span>{dot.format("gro")}增长商品</span><span>{dot.format("oth")}对照组</span>'
              f'<span><span class="src img">图</span>主图：增长势头前 {vision.get("n_focus") or 0} 个 vs 持续热销对照 '
              f'{vision.get("n_reference") or mats.get("n_reference") or 0} 个</span>'
              f'<span><span class="src ttl">题</span>标题：增长商品 vs 全部头部商品 {tr["n_baseline"]} 个</span>'
              '<span>+N = 差几个百分点</span></div>')
    head = (f'<p class="hint">样本：<a href="#" data-pop="growth">全部增长商品 {tr["n_focus"]} 个</a>（含没列出的简单款、相近款）；'
            '只列增长商品明显更多的特征，右边是带这个特征的增长商品。</p>')
    rows = _feature_rows(appearance, vision, pool)
    feats = (f'<div class="dbox"><h4>比对照组更多的外观与工艺</h4>{legend}<div class="flist">{rows}</div>'
             f'{_common_lines(appearance, vision)}</div>') if rows else (
        f'<div class="dbox"><h4>外观与工艺</h4><p class="empty">增长商品没有比对照组明显更多的特征。</p>'
        f'{_common_lines(appearance, vision)}</div>')
    if not appearance:
        feats = '<div class="dbox"><p class="empty">增长商品没有明显偏多或常见的外观/工艺特征。</p></div>'
    mat = _material_box(mats, int(cfg["budget"].get("material_lookups", 0)))
    return f'{head}<div class="tgrid">{mat or "<div></div>"}{feats}</div>{_structure(tr)}'


def _changes(diff: dict, by_asin: dict) -> str:
    if diff.get("first"):
        return '<p class="empty">本期没有可对比的上一期（首期或监控范围刚调整），从下一期开始显示变化。</p>'
    names = {"surge": "突然爆火", "potential": "潜力", "hot": "持续热销", "fake": "异常信号"}
    moved_from = diff.get("moved_from") or {}

    def link(asin: str) -> str:
        item = by_asin.get(asin)
        if item:
            return chip_link(item, 28)
        return f'<a href="{amazon(asin)}" {LINK}>{esc(asin)}</a>'

    def pic(asin: str) -> str:
        item = by_asin.get(asin)
        was = f"（上期{names[moved_from[asin]]}）" if asin in moved_from else ""
        if not item:
            return f'<a href="{amazon(asin)}" {LINK} title="{esc(asin + was)}">{esc(asin)}</a>'
        cls = "th mv" if was else "th"
        return f'<a class="{cls}" href="{amazon(item["asin"])}" {LINK} title="{esc(short_name(item, 80) + was)}">{_img(item, 100)}</a>'

    def line(head: str, asins: list[str], k: int = 14) -> str:
        # 有特色的排前面：简单款也算进个数，但不占前几张图
        ordered = sorted(asins, key=lambda a: bool((by_asin.get(a) or {}).get("plain")))
        moved = sum(1 for a in asins if a in moved_from)
        extra = f'<span class="more">+{len(asins) - k}</span>' if len(asins) > k else ""
        note = f'<span class="more">（{moved} 个上期在别的板块，蓝框）</span>' if moved else ""
        return (f'<div class="crow"><span class="ck">{esc(head.strip())}<b>{len(asins)}</b></span>'
                f'<span class="cth">{"".join(pic(a) for a in ordered[:k])}{extra}{note}</span></div>')

    parts = [line("依旧爆火 ", diff["still"])] if diff.get("still") else []
    rc = diff.get("recheck")
    if rc:  # 上期提示了大促后数据待修订（“本期新出现的增长请等下期确认”）：放在最前面
        kinds = {"surge": "突然爆火", "potential": "潜力", "rising": "上升中"}
        lost = list(rc["lost"].items())
        text = (f"<li>上期提示了大促后数据待修订：上期的 {rc['total']} 个增长商品（突然爆火 / 潜力 / 上升中），"
                f"本期仍是增长商品 {len(rc['held'])} 个，不再是 {len(lost)} 个")
        if lost:
            text += "：" + " ".join(f'{link(a)}<span class="sub">（上期{kinds[k]}）</span>' for a, k in lost[:10]) \
                + ("…" if len(lost) > 10 else "")
        if rc.get("gone"):
            text += f"；另有 {len(rc['gone'])} 个本期没追踪"
        recheck = f'<ul class="chg">{text}</li></ul>'
    else:
        recheck = ""
    parts += [line(f"新进入「{names[label]}」", diff["new"][label])
              for label in ("surge", "potential", "fake", "hot") if diff["new"].get(label)]
    if diff.get("cooled"):
        parts.append(line("不再爆火 ", diff["cooled"]))
    if diff.get("covered"):
        parts.append(line("热销改用月度名单补判", diff["covered"]))
    notes = []
    if diff.get("covered"):
        notes.append("“热销改用月度名单补判”是上期没判过的持续热销，不算新进入")
    if diff.get("retro"):
        notes.append(f"{len(diff['retro'])} 个上期没追踪的，按本期拉到的日数据回看上期那天判（同样的规则）")
    if not parts and not recheck:
        return '<p class="empty">与上期相比没有明显变化。</p>'
    return (f'<p class="hint">对比上期 {esc(diff.get("prev_id", ""))}；悬停看品名，点图打开亚马逊'
            + (f"；{'；'.join(notes)}" if notes else "") + f'</p>{recheck}<div class="chgs">{"".join(parts)}</div>')


def _all_table(items: list[dict]) -> str:
    order = {"fake": 0, "surge": 1, "potential": 2, "hot": 3, "watch": 4, "low": 5}
    rows = []
    for i in sorted(items, key=lambda x: (order[x["label"]], -(x.get("recent_avg28") or 0))):
        rows.append(
            f"<tr><td><a href=\"{amazon(i['asin'])}\" {LINK}>"
            f"{esc(i['asin'])}</a></td><td>{esc(i['brand'])}</td><td>{esc(i['node_cn'] or i['node_name'])}</td>"
            f"<td>{esc(i['label_cn'])}</td><td>{esc(REASON_CN.get(i.get('source') or '', ''))}</td>"
            f"<td class=n>{fmt_num(i.get('recent_avg28'))}</td>"
            f"<td class=n>{fmt_int(i.get('bsr'))}</td><td class=n>{fmt_price(i.get('price'))}</td>"
            f"<td class=n>{fmt_num(i.get('rating'))}</td><td class=n>{fmt_int(i.get('ratings'))}</td>"
            f"<td class=n>{i['fake']['score']}</td>"
            f"<td>{esc('、'.join(tag_head(t) for t in i['tags']))}</td></tr>")
    return ("<div class=tbl><table><thead><tr><th>ASIN</th><th>品牌</th><th>子类目</th><th>板块</th><th>来源</th>"
            "<th class=n>近28天日均</th><th class=n>BSR</th><th class=n>价格</th><th class=n>评分</th><th class=n>评论</th>"
            "<th class=n>异常分</th><th>标签</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def section_hints(cfg: dict, universe: bool = True) -> dict[str, str]:
    """每个小窗顶部的一句话入选条件（数字直接取自 config.yaml，和判定代码一一对应；完整口径见 README）。
    universe：本期翻成了全市场名单；没翻成（预算不够）时说明机会候选退回月度名单和近 30 天榜单。"""
    th = cfg["thresholds"]
    s, h, p, f, r = th["surge"], th["hot"], th["potential"], th["fake"], th["rising"]
    o = blueocean.settings(cfg)
    windows = " 或 ".join(map(str, s["windows"]))
    tide = "，子类目整体在涨时先扣掉大盘" if (th.get("tide") or {}).get("enabled", True) else ""
    scope = "" if universe else "（本期全市场名单没翻成，候选退回月度名单和近 30 天榜单）"
    return {
        "surge": f"近 {windows} 天对比之前 {s['base_days']} 天：排名升一倍以上、日均 ≥{s['sales_ratio']:g} 倍且 ≥{s['min_daily_sales']} 件{tide}；"
                 f"稳在高位 / 仍在上涨 / 新品爆发，没有回落、不是短时脉冲。按月增量排。{scope}",
        "potential": f"上架 {p['min_age_days']}~{p['max_age_days']} 天、评论 <{p['max_ratings']} 条：近 28 天和近 7 天日均都 ≥ 之前 28 天的 "
                     f"{p['min_recent_ratio']} 倍{tide}、≥{p['min_daily_sales']} 件，近 3 个完整月月销平均每月涨 ≥{p['min_monthly_growth']:.0%}。按月增量排。{scope}",
        "hot": f"近 {h['lookback_months']} 个月里 ≥{h['months_required']} 个月达到子类目第 {h['rank_in_node']} 名的月销量，波动小、没在跌，"
               f"近 28 天还有近 6 个月日均的 {h['min_recent_ratio']:.0%}。按近 6 个月月均排。",
        "fake": f"异常分 ≥{f['suspect_score']}（留评率过高、评论暴增、评分跳升、短时脉冲等累计），只是提示，需人工核实。按异常分排。",
        "ocean": (f"标题写明是给特定人群 / 用途专门设计的（缝纫、卧床护理、黑胶唱片、蒙氏幼儿……，词表在 config.yaml），"
                  f"近 30 天量够全市场门槛，这一类全市场不超过 {o['max_products']} 个；太小、结构太简单的不算"
                  f"（包装不到 {o['tiny_weight']:g} 磅又没有升降折叠这类机构的，不到 {o['simple_weight']:g} 磅的开放书架、置物架）。"
                  "同一类排在一起，商品照样留在原板块。"
                  if universe else "本期没翻成全市场名单，不判蓝海产品。"),
        "growth": f"增长商品 = 突然爆火 + 潜力 + 上升中（近 28 天和近 7 天日均都 ≥ 之前 28 天的 {r['min_ratio']} 倍且 ≥{r['min_daily_sales']} 件{tide}）。按势头排。{scope}",
        "all": "本期拉了日数据的全部商品。板块 = 本期归入的板块（观察 = 都不属于），来源 = 被选进追踪的原因；月度名单补判的持续热销不在这里。",
    }


GLOSSARY_README = "完整的判定规则、阈值和回测依据见仓库 README"


def _method(cfg: dict, repo_url: str = "") -> str:
    """共用概念，各一句话。完整口径在 README（报告上不放大段规则）。"""
    th = cfg["thresholds"]
    sc = cfg["report"].get("showcase") or {}
    rows = [
        ("数据", "全部来自卖家精灵：月度头部名单、近 30 天全市场名单、每个追踪商品约 400 天的日销量 / BSR / 价格；销量是按 BSR 估算的"),
        ("大促日", "全类目一起冲高的日子，所有比较都跳过这几天"),
        ("断货", f"BSR ≥{th['out_of_stock_bsr']:,} 或没有排名的日子不算对比期"),
        ("回落", "最近几天明显低于近期高点或上一周；突然爆火、潜力、上升中都要求没有回落"),
        ("月增量", "（近 28 天日均 − 之前 28 天在卖日子的日均）× 售价 × 30；老品换链接、断货很久后恢复、跌下去又涨回来的按 0"),
        ("品类大盘", "子类目整体在涨时，增长倍数先除以大盘倍数再比门槛"),
        ("评分门槛", f"评分 <{th['min_rating']}（评论不到 {th['few_ratings']} 条时 <{th['few_ratings_min_rating']}）的不进任何板块"),
    ]
    small = cfg["scope"].get("small_item") or {}
    if small:
        dims = " × ".join(f"{v:g}" for v in small.get("max_dims") or ())
        rows.append(("小件", f"包装在亚马逊标准尺寸以内（≤{dims} 英寸、≤{float(small.get('max_weight', 0)):g} 磅）的商品不在监控范围；"
                            "衣帽架整个子类目不追踪"))
    ob = blueocean.settings(cfg)
    if ob["enabled"]:
        rows.append(("蓝海产品", f"针对特定小圈子人群 / 用途专门设计的（缝纫机桌、卧床的人用的床边桌、黑胶唱片机柜……），"
                              f"卖得够量，这一类全市场不超过 {ob['max_products']} 个，而且不是太小、结构太简单的：和常规款不同，做的卖家少"))
    if sc.get("enabled", True):
        rows += [("简单款", "主图识别过、主图和标题都没有任何特色（只有开放格、平板框架、普通抽屉这类基本结构，"
                            "或只有这个子类目多数商品都有的造型、功能，如衣帽架的挂钩）的，数据达标也不列"),
                 ("相近款", f"同子类目里和已列出的一款主图特征、标题用词平均 ≥{float(sc.get('similar_min', 0.5)):.0%} 相同的，"
                            "只列排在前面的那款，其余记在它的卡片上")]
    link = f'<a href="{esc(repo_url)}" {LINK}>{GLOSSARY_README}</a>' if repo_url else GLOSSARY_README
    return ('<details><summary>名词说明</summary><ul class="gloss">'
            + "".join(f"<li><b>{esc(k)}</b>：{esc(v)}</li>" for k, v in rows)
            + f"<li>{link}；阈值都在 config.yaml</li></ul></details>")


def lift_text(lift: dict | None, cfg: dict) -> str:
    """大促后数据待修订的提示（events.bsr_lift），没有返回空串。报告顶部和钉钉共用。"""
    if not lift:
        return ""
    ev = cfg["thresholds"]["events"]
    when = lift["start"][5:] if lift["start"] == lift["end"] else f"{lift['start'][5:]}~{lift['end'][5:]}"
    return (f"{when} 最多 {lift['peak']:.0%} 的商品排名同时好到自己前 {ev['base_days']} 天的 {ev['jump']:g} 倍以上（多是大促或圣诞后）。"
            "这几天的销量卖家精灵大约一周后才修订，修订前大促后的几天容易看成“冲上去后走平”"
            "（Prime Big Deal Days 后那期，增长判定约三分之一修订后不成立），本期新出现的突然爆火、潜力、上升中请等下期确认（下期的“与上期对比”会写明哪些仍成立）")


def _lift_alert(ctx: dict, cfg: dict) -> str:
    text = lift_text((ctx.get("events") or {}).get("lift"), cfg)
    return f'<p class="alert"><b>大促后数据待修订</b>：{esc(text)}。</p>' if text else ""


def _revision_fact(cov: dict, th: dict) -> str | None:
    rev = cov.get("revision")
    if not (rev and rev.get("window")):
        return None
    check = cov.get("revision_check") or {}
    measured = f"上期最后 {rev['window']} 天现在是原来的 {rev['median']:.0%}（{rev['items']} 个商品中位数，{rev['changed']:.0%} 被改过）"
    if usable_revision(rev, th) is None:
        how = "测到的太少或不是有效下调，本期不校正"
    elif check.get("state") == "uncertain" or (not check.get("applied") and check.get("inflation") is None):
        how = "本期是否偏高还不能确认，保留原始判定，只在卡片上提示"
    elif str(th.get("revision_mode") or "correct") == "correct" and not check.get("applied"):
        need = f"（×{check['inflation']:.2f}，要 ≥×{check['need']:.2f}）" if check.get("inflation") is not None else ""
        how = f"本期最近几天并不偏高{need}，按拉到的数判、不校正"
    elif str(th.get("revision_mode") or "correct") == "correct":
        how = "本期最近几天也偏高，已按这个幅度校正后再判（卡片上的数和走势图也是校正后的）"
    else:
        how = "判定用拉到的数，校正后不成立的在卡片上标出"
    return f"{measured}；{how}"


def _facts(ctx: dict, counts: dict, cur: dict) -> list[tuple[str, str]]:
    """数据说明：一行一项。"""
    cov, cfg = ctx["coverage"], ctx["cfg"]
    th = cfg["thresholds"]
    names = {n["path"]: n.get("name_cn") or n.get("name") or n["path"] for n in ctx.get("nodes") or []}
    out = [("数据", f"月度名单 {ctx['period_text']} · 日数据截至 {ctx['as_of']} · 追踪 {counts['total']} 个（本期刷新 {cov['refreshed']} 个）")]
    if cov.get("universe"):
        failed = f"（{cov['universe_failed']} 个子类目没翻全）" if cov.get("universe_failed") else ""
        out.append(("全市场名单", f"近 30 天量够的 {cov['universe']} 个{failed} → 初筛可能在增长 {cov.get('candidates') or 0} 个 "
                                f"→ 拉日数据 {cov.get('opportunity') or 0} 个"))
        if cov.get("young"):
            out.append(("早期名单", f"上架半年内、量还不够门槛的 {cov['young'][0]} 个，拉日数据 {cov['young'][1]} 个"))
    elif cov.get("candidates"):
        out.append(("机会候选", f"{cov['candidates']} 个，本期查了 {cov.get('opportunity') or 0} 个"))
    listed = [(label, len(cur[k]["shown"]), counts[k]) for k, label in (("surge", "突然爆火"), ("potential", "潜力"), ("hot", "持续热销"),
                                                                       ("ocean", "蓝海产品")) if k in cur]
    if any(n != total for _l, n, total in listed):
        out.append(("列出", "、".join(f"{label} {n}/{total}" for label, n, total in listed)
                    + "（其余是简单款或相近款，见各小窗底部 / 卡片上）"))
    if counts.get("low"):
        out.append(("评分不达标", f"{counts['low']} 个，没上榜"))
    note = _ocean_note(cov)
    if note:
        out.append(("蓝海产品", note.rstrip("。")))
    tides = cov.get("tides") or {}
    if tides:
        up = sorted(((t, p) for p, t in tides.items() if t > 1.0), reverse=True)
        out.append(("品类大盘", ("、".join(f"{names.get(p, p)} ×{t:.2f}" for t, p in up[:6]) + ("…" if len(up) > 6 else "")
                             + " 整体在涨，增长商品要跑赢它") if up else f"{len(tides)} 个子类目算得出，没有整体在涨的"))
    rev = _revision_fact(cov, th)
    if rev:
        out.append(("数据回改", f"卖家精灵会回头改最近几天的日数据：{rev}"))
    mon = ctx.get("monthly") or {}
    if mon.get("need"):
        if mon["months"] < mon["need"]:
            text = f"往月名单已齐 {mon['months']}/{mon['need']} 个月，补齐后开始（这期只判追踪的商品）"
        elif not mon["rolling"]:
            text = "这期没有当期的近 30 天销量，不拿旧名单判，只判追踪的商品"
        else:
            part = f"（{mon['rolling']}/{mon['nodes']} 个子类目有当期销量）" if mon["rolling"] < mon["nodes"] else ""
            deal = f"，名单含大促日按 ×{mon['deal']:.2f} 扣掉" if (mon.get("deal") or 1) > 1.001 else ""
            units = (f"；按件数的往月名单已齐 {mon['units_months']}/{mon['need']} 个月"
                     if mon.get("units_months") is not None and mon["units_months"] < mon["need"] else "")
            text = f"持续热销里 {counts.get('hot_monthly', 0)} 个来自月度名单补判（判了 {mon['judged']} 个不在追踪池的头部{deal}）{part}{units}"
        out.append(("月度补判", text))
    stale = cov.get("unsettled") or {}
    if stale:
        share = float(cfg["discovery"].get("settle_same_share", 0.5))
        out.append(("名单未更新完", f"月度名单卖家精灵还没更新完（≥{share:.0%} 的商品和上个月一模一样），下期重拉："
                                 + "；".join(f"{int(k[5:])} 月 " + "、".join(names.get(p, p) for p in v) for k, v in sorted(stale.items()))))
    spans = (ctx.get("events") or {}).get("spans") or []
    if spans:
        out.append(("大促日", "、".join(events.span_text(sp) for sp in spans) + "（不参与比较）"))
    out.append(("调用", f"卖家精灵 {fmt_int(cov.get('calls'))} 次 · 本期要点{'由 AI 生成' if ctx['summary_source'] == 'llm' else '为模板文字'}"))
    return out


FACTS_SHOWN = ("数据", "全市场名单", "机会候选", "列出", "调用")  # 其余折叠在“更多数据说明”里


def _curated(ctx: dict) -> dict:
    """ctx["curated"]（pipeline.curated_lists）；没有时（旧的调用方）按全部列出。"""
    if ctx.get("curated"):
        return ctx["curated"]
    sec = ctx["sections"]

    def every(rows):
        return {"shown": list(rows), "plain": [], "similar": {}}
    growth = ctx.get("focus") or []
    return {**{k: every(sec.get(k) or []) for k in ("surge", "potential", "hot", "ocean")}, "growth": every(growth),
            "gallery": growth[:12]}


def render(ctx: dict) -> str:
    sec = ctx["sections"]
    counts = sec["counts"]
    cfg = ctx["cfg"]
    cur = _curated(ctx)
    by_asin = {i["asin"]: i for i in ctx["items"]}
    pool = cur["growth"]["shown"]
    hints = section_hints(cfg, universe=bool(ctx["coverage"].get("universe")))
    kpi = "".join([
        _kpi("surge", "突然爆火", "近 1~2 周销量和排名翻倍", cur["surge"]["shown"], counts["surge"]),
        _kpi("potential", "潜力产品", "新品，近 28 天和月销都在涨", cur["potential"]["shown"], counts["potential"]),
        _kpi("hot", "持续热销", "近半年稳定在子类目头部", cur["hot"]["shown"], counts["hot"]),
        _kpi("ocean", "蓝海产品", "给特定人群 / 用途专门设计的", cur["ocean"]["shown"], counts.get("ocean", 0), "相近款"),
        _kpi("fake", "异常信号", "疑似刷评或数据异常，需核实", sec["fake"], counts["fake"]),
    ])
    facts = _facts(ctx, counts, cur)
    history = ""
    if ctx.get("history"):
        history = (" · 往期 " + " ".join(f'<a href="{esc(h["href"])}">{esc(h["id"])}</a>' for h in ctx["history"])
                   + '<span class="sub">（往期报告请从钉钉里那一期的消息打开）</span>')
    pops = "".join([
        _section_pop("surge", "突然爆火", hints["surge"], cur["surge"], counts["surge"]),
        _section_pop("potential", "潜力产品", hints["potential"], cur["potential"], counts["potential"]),
        _section_pop("hot", "持续热销", hints["hot"], cur["hot"], counts["hot"]),
        _ocean_pop(cur["ocean"], hints["ocean"], counts.get("ocean", 0), ctx["coverage"]),
        _section_pop("fake", "异常信号", hints["fake"], {"shown": sec["fake"]}, counts["fake"]),
        _growth_pop(cur["growth"], hints["growth"], len(ctx.get("focus") or [])),
        _pop_src("all", f"全部追踪商品（{counts['total']} 个）", "muted",
                 f'<p class="hint">{esc(hints["all"])}</p>{_all_table([i for i in ctx["items"] if i.get("daily", True)])}'),
    ])
    main_facts = [f for f in facts if f[0] in FACTS_SHOWN]
    rest = [f for f in facts if f[0] not in FACTS_SHOWN]
    facts_html = f'<dl class="facts">{"".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in main_facts)}</dl>'
    if rest:
        facts_html += (f'<details class="more-facts"><summary>更多数据说明：{esc("、".join(k for k, _v in rest))}</summary>'
                       f'<dl class="facts">{"".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in rest)}</dl></details>')
    dirs_html, dir_pops = _directions(ctx["traits"], cfg, cur, ctx.get("focus") or [])
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer"><title>{esc(ctx['title'])} {esc(ctx['report_id'])}</title>
<style>{CSS}</style></head><body><div class="wrap">
<header><div class="sub">{esc(cfg['report'].get('subtitle') or '亚马逊美国站 · 家具')}</div><h1>{esc(ctx['title'])}</h1>
<div class="sub hist">{esc(ctx['generated_at'])} 生成 · 第 {ctx['run_seq']} 期 · 日数据截至 {esc(ctx['as_of'])}{history}</div></header>
<div class="kpis">{kpi}</div>{_lift_alert(ctx, cfg)}
{_gallery(cur, cfg)}
<section class="summary" style="--c:var(--accent)"><h2><span class="dot"></span>本期要点</h2><div class="sgrid">{markdown(ctx['summary'], by_asin)}</div></section>
{dirs_html}
<section id="traits" style="--c:var(--potential)"><h2><span class="dot"></span>外观与工艺依据</h2>{_traits(ctx['traits'], cfg, pool)}</section>
<section id="changes" style="--c:var(--hot)"><h2><span class="dot"></span>与上期对比</h2>{_changes(ctx['diff'], by_asin)}</section>
<section id="about" style="--c:var(--muted)"><h2><span class="dot"></span>数据说明<a class="h-more" href="#" data-pop="all">全部 {counts['total']} 个追踪商品 ›</a></h2>
{facts_html}{_method(cfg, ctx.get('repo_url') or '')}</section>
<footer>{esc(ctx['title'])} · 数据来源：卖家精灵 · 报告已加密，仅持有链接的人可查看</footer>
</div>{pops}{dir_pops}{POPUP}{HOVER_JS}{TIP_JS}{BOARD_JS}</body></html>"""

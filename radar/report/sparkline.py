"""内联 SVG 走势图：不依赖任何 CDN，钉钉内置浏览器和国内网络都能直接显示。

图上方一行文字说明画的是什么，左边是纵轴刻度（件），下边是起止日期；
鼠标悬停（手机上按住滑动）显示当天 / 当月的销量，脚本见 HOVER_JS。"""
from __future__ import annotations

import math
from datetime import date, timedelta

W, T, B, R = 280, 13, 15, 4  # 宽；上边留给悬停读数，下边留给日期，右边留白
PLOT_H = 54
H = T + PLOT_H + B
BAR_GAP = 2


def _ticks(peak: float) -> tuple[float, int]:
    """纵轴刻度：步长取 1 / 2 / 2.5 / 5 × 10ⁿ，分 2~4 格，顶端尽量贴近最大值。返回（步长，格数）。"""
    if peak <= 0:
        return 1, 1
    best = None
    e = math.floor(math.log10(peak))
    for exp in range(e - 2, e + 1):
        for m in (1, 2, 2.5, 5):
            if m == 2.5 and exp < 1:
                continue  # 0.25、2.5 这类小数刻度不好读
            step = m * 10 ** exp
            k = math.ceil(round(peak / step, 9))
            if 2 <= k <= 4 and (best is None or (k * step, -step) < (best[0] * best[1], -best[0])):
                best = (step, k)
    return best or (peak, 1)


def _num(v: float) -> str:
    """显示用：千分位。"""
    v = round(v, 6)
    return f"{v:,.0f}" if v == int(v) else f"{v:g}"


def _raw(v: float) -> str:
    """写进 data-* 给脚本用：不带千分位（逗号是分隔符）。"""
    return f"{round(v, 2):.12g}"


def _text_w(s: str) -> float:
    return sum(3 if ch in ",." else 6.2 for ch in s)


def _frame(peak: float, x_labels: tuple[str, str]) -> tuple[str, float, float, float]:
    """网格线 + 纵轴刻度 + 起止日期。返回（SVG 片段，绘图区左边 x，绘图区宽，纵轴顶端值）。"""
    step, k = _ticks(peak)
    labels = [_num(step * j) for j in range(k + 1)]
    left = round(max(_text_w(s) for s in labels) + 6, 1)
    width = round(W - left - R, 1)
    parts = []
    for j, s in enumerate(labels):
        y = round(T + PLOT_H * (1 - j / k), 1)
        parts.append(f'<line x1="{left}" x2="{W - R}" y1="{y}" y2="{y}" class="grid"/>'
                     f'<text x="{left - 4}" y="{y}" dy=".35em" text-anchor="end" class="ax">{s}</text>')
    first, last = x_labels
    if first:
        parts.append(f'<text x="{left}" y="{H - 2}" class="ax">{first}</text>')
    if last:
        parts.append(f'<text x="{W - R}" y="{H - 2}" text-anchor="end" class="ax">{last}</text>')
    return "".join(parts), left, width, step * k


def _hover_layer(left: float) -> str:
    return (f'<g class="hov" visibility="hidden"><line x1="{left}" x2="{left}" y1="{T}" y2="{T + PLOT_H}"/>'
            f'<circle r="4" cx="{left}" cy="{T + PLOT_H}"/><text x="{left}" y="{T - 3}"></text></g>')


def _figure(caption: str, svg: str) -> str:
    return f'<figure class="chart"><figcaption>{caption}</figcaption>{svg}</figure>'


def sparkline(values: list, *, start: str | None = None, highlight_last: int = 7) -> str:
    """日销量折线图。start：第一个值对应的日期（YYYY-MM-DD）。"""
    points = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(points) < 2:
        return ""
    n = len(values)
    peak = max(v for _, v in points)
    first_day = date.fromisoformat(start) if start else None
    x_labels = (first_day.strftime("%m-%d"), (first_day + timedelta(days=n - 1)).strftime("%m-%d")) \
        if first_day else ("", "")
    frame, left, width, top = _frame(peak, x_labels)

    def xy(i: int, v: float) -> tuple[float, float]:
        return round(left + width * i / max(1, n - 1), 1), round(T + PLOT_H * (1 - v / top), 1)

    line = " ".join(f"{'M' if k == 0 else 'L'}{x},{y}" for k, (x, y) in enumerate(xy(i, v) for i, v in points))
    base = T + PLOT_H
    area = f"{line} L{xy(points[-1][0], 0)[0]},{base} L{xy(points[0][0], 0)[0]},{base} Z"
    hl_x = xy(max(0, n - highlight_last), 0)[0]
    caption = f"日销量（件/天）· 近 {n} 天，阴影为最近 {min(highlight_last, n)} 天"
    data = ",".join("" if v is None else _raw(v) for v in values)
    summary = f"{caption}；最高 {_num(peak)} 件/天，最近一天 {_num(points[-1][1])} 件"
    day_attr = f' data-d="{start}"' if start else ""
    svg = (f'<svg class="spark" viewBox="0 0 {W} {H}" role="img" aria-label="{summary}" data-k="line" '
           f'data-v="{data}" data-g="{left},{width},{T},{PLOT_H},{_raw(top)}" data-u="件"{day_attr}>'
           f'<rect x="{hl_x}" y="{T}" width="{round(left + width - hl_x, 1)}" height="{PLOT_H}" class="spark-hl"/>'
           f'{frame}<path d="{area}" class="spark-area"/><path d="{line}" class="spark-line"/>'
           f'{_hover_layer(left)}</svg>')
    return _figure(caption, svg)


def bars(values: list[tuple[str, float]], *, highlight=(), caption: str = "", unit: str = "件") -> str:
    """月销量柱状图。values：[(YYYY-MM, 件数)]；highlight：用深色画的月份（季节性商品的旺季月）；
    caption / unit：图题和读数单位（默认按月销量）。"""
    if not values:
        return ""
    n = len(values)
    peak = max(v for _, v in values)
    frame, left, width, top = _frame(peak, (values[0][0], values[-1][0]) if n > 1 else (values[0][0], ""))
    slot = (width + BAR_GAP) / n
    bar_w = min(24.0, slot - BAR_GAP)
    rects = []
    for k, (_, v) in enumerate(values):
        h = max(1.0, PLOT_H * v / top)
        x = left + k * slot + (slot - BAR_GAP - bar_w) / 2
        y, base, r = T + PLOT_H - h, T + PLOT_H, min(3.0, bar_w / 2, h)
        # 顶端圆角、底部方角
        rects.append(f'<path d="M{x:.1f},{base} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} '
                     f'H{x + bar_w - r:.1f} Q{x + bar_w:.1f},{y:.1f} {x + bar_w:.1f},{y + r:.1f} V{base} Z" '
                     f'class="bar{(" hi" if values[k][0] in highlight else " lo") if highlight else ""}"/>')
    caption = caption or f"月销量（件）· 近 {n} 个月"
    summary = f"{caption}；最高 {_num(peak)} {unit}，最近一个月 {_num(values[-1][1])} {unit}"
    svg = (f'<svg class="spark" viewBox="0 0 {W} {H}" role="img" aria-label="{summary}" data-k="bar" '
           f'data-v="{",".join(_raw(v) for _, v in values)}" data-m="{",".join(m for m, _ in values)}" '
           f'data-g="{left},{width},{T},{PLOT_H},{_raw(top)}" data-u="{unit}">'
           f'{frame}{"".join(rects)}{_hover_layer(left)}</svg>')
    return _figure(caption, svg)


# 悬停 / 按住显示读数。卡片在小窗里是复制出来的，所以用 document 上的事件委托
HOVER_JS = """<script>(function(){
function host(el){while(el&&el.nodeType===1){if(el.getAttribute('data-v')!==null&&el.getAttribute('data-g'))return el;el=el.parentNode;}return null;}
function day(s,i){var d=new Date(s+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+i);
return ('0'+(d.getUTCMonth()+1)).slice(-2)+'-'+('0'+d.getUTCDate()).slice(-2);}
var cur=null;
function hide(){if(cur){cur.querySelector('.hov').setAttribute('visibility','hidden');cur=null;}}
function move(e){var svg=host(e.target);if(svg!==cur)hide();if(!svg)return;
var p=e.touches?e.touches[0]:e,r=svg.getBoundingClientRect(),vb=svg.viewBox.baseVal;if(!r.width)return;
var x=(p.clientX-r.left)*vb.width/r.width,g=svg.getAttribute('data-g').split(','),L=+g[0],W=+g[1],T=+g[2],H=+g[3],top=+g[4];
var v=svg.getAttribute('data-v').split(','),n=v.length,i,cx;
if(svg.getAttribute('data-k')==='bar'){var slot=(W+2)/n;i=Math.max(0,Math.min(n-1,Math.floor((x-L)/slot)));cx=L+i*slot+(slot-2)/2;}
else{i=Math.max(0,Math.min(n-1,Math.round((x-L)/W*(n-1))));cx=L+W*i/Math.max(1,n-1);}
var has=v[i]!=='',val=+v[i],hov=svg.querySelector('.hov'),ln=hov.querySelector('line'),dot=hov.querySelector('circle'),t=hov.querySelector('text');
ln.setAttribute('x1',cx);ln.setAttribute('x2',cx);dot.setAttribute('cx',cx);
dot.setAttribute('cy',has?T+H*(1-val/top):T+H);dot.setAttribute('visibility',has?'inherit':'hidden');
var m=svg.getAttribute('data-m'),s=svg.getAttribute('data-d'),lab=m?m.split(',')[i]:s?day(s,i):'';
t.textContent=(lab?lab+'：':'')+(has?val.toLocaleString('en-US')+' '+svg.getAttribute('data-u'):'无数据');
var right=cx>L+W/2;t.setAttribute('x',right?cx-6:cx+6);t.setAttribute('text-anchor',right?'end':'start');
hov.setAttribute('visibility','visible');cur=svg;}
document.addEventListener('mousemove',move);
document.addEventListener('touchstart',move,{passive:true});
document.addEventListener('touchmove',move,{passive:true});
})();</script>"""

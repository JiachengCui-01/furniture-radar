"""报告里的小图（内联 SVG，不依赖 CDN，钉钉内置浏览器也能显示）：
价格带条（开发方向一览每行一条，各行同一条对数价格轴，能横向比）、价格分布柱（开发方向小窗）、
哑铃图（外观与工艺：增长商品 vs 对照组的占比）、图墙上的迷你走势。

颜色只用 CSS 变量（render.CSS）：增长商品三类 --k-surge / --k-potential / --k-rising（按 dataviz 校验过色盲可分，
和卡片徽章的颜色同色系），量用 --ink-bar，网格 --line。每个数据标记带 data-tip，悬停 / 点按显示读数（render.TIP_JS）。"""
from __future__ import annotations

import html
import math
from datetime import date, timedelta

from .sparkline import _raw

NICE = (10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 120, 150, 200, 250, 300, 400, 500, 600, 800, 1000, 1500, 2000, 3000)
TICKS = (25, 50, 100, 200, 400, 800, 1600)
KIND_CN = {"surge": "突然爆火", "potential": "潜力", "rising": "上升中"}


def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def money(value: float) -> str:
    return f"${value:,.0f}"


def price_domain(values) -> tuple[float, float]:
    """对数价格轴的两端：取到 NICE 里刚好包住全部值的两档。"""
    vals = [float(v) for v in values if v]
    if not vals:
        return 25.0, 400.0
    lo = max((n for n in NICE if n <= min(vals) * 0.95), default=NICE[0])
    hi = min((n for n in NICE if n >= max(vals) * 1.05), default=NICE[-1])
    return float(lo), float(hi if hi > lo else lo * 2)


def _x(price: float, dom: tuple[float, float], x0: float, width: float) -> float:
    lo, hi = dom
    p = min(max(float(price), lo), hi)
    return round(x0 + width * (math.log(p) - math.log(lo)) / (math.log(hi) - math.log(lo)), 1)


def _ticks(dom: tuple[float, float]) -> list[int]:
    return [t for t in TICKS if dom[0] < t < dom[1]]


SW, SH, PAD = 200, 22, 5  # 价格带条：宽、高、左右留白（点的半径 + 白边）


def price_axis(dom: tuple[float, float]) -> str:
    """价格带条上方的刻度（HTML，按 price_strip 的同一套横坐标折成百分比，放在同宽的格子里就对齐）。"""
    w = SW - 2 * PAD
    return ('<div class="paxis" aria-hidden="true">'
            + "".join(f'<span style="left:{100 * _x(t, dom, PAD, w) / SW:.1f}%">${t}</span>' for t in _ticks(dom)) + "</div>")


def _spread(xs: list[tuple[float, int]], gap: float = 7.0) -> dict[int, int]:
    """点挨得太近时上下错开一行：返回 {原序号: 行号 0 / 1 / 2}。"""
    rows: dict[int, int] = {}
    last = [-1e9, -1e9, -1e9]
    for x, k in sorted(xs):
        row = next((r for r in (0, 1, 2) if x - last[r] >= gap), min(range(3), key=lambda r: last[r]))
        rows[k] = row
        last[row] = x
    return rows


def price_strip(band: dict | None, dots: list[tuple[float, str, str]], dom: tuple[float, float]) -> str:
    """一行的价格带：灰条 = 全市场名单按销售额的 P25~P75（竖线 = 中位），点 = 这个子类目的增长商品（颜色 = 哪一类）。
    dots：[(售价, surge / potential / rising, 悬停文字)]。"""
    w = SW - 2 * PAD
    mid = SH / 2
    parts = [f'<line x1="{_x(t, dom, PAD, w)}" x2="{_x(t, dom, PAD, w)}" y1="1" y2="{SH - 1}" class="grid"/>' for t in _ticks(dom)]
    if band and band.get("p25") and band.get("p75"):
        x1, x2 = _x(band["p25"], dom, PAD, w), _x(band["p75"], dom, PAD, w)
        tip = f"全市场名单主力价格带 {money(band['p25'])}–{money(band['p75'])}（一半的销售额），中位 {money(band['p50'])}"
        parts.append(f'<rect x="{x1}" y="{mid - 4}" width="{max(2.0, round(x2 - x1, 1))}" height="8" rx="4" class="pband" '
                     f'data-tip="{_esc(tip)}"/>')
        xm = _x(band["p50"], dom, PAD, w)
        parts.append(f'<line x1="{xm}" x2="{xm}" y1="{mid - 6}" y2="{mid + 6}" class="pmid"/>')
    placed = [(_x(p, dom, PAD, w), k) for k, (p, _kind, _t) in enumerate(dots)]
    rows = _spread(placed)
    for k, (x, _k) in enumerate(placed):
        _p, kind, tip = dots[k]
        y = (mid, mid - 6, mid + 6)[rows[k]]
        parts.append(f'<circle cx="{x}" cy="{y}" r="4" class="dot k-{kind}" data-tip="{_esc(tip)}"/>')
    return f'<svg class="pstrip" viewBox="0 0 {SW} {SH}" role="img" aria-label="价格带">{"".join(parts)}</svg>'


HW, HH, HL, HB = 560, 140, 34, 52  # 价格分布：宽、高、左边（纵轴刻度）、下边（点和横轴）


def price_hist(hist: list[float], edges: tuple, band: dict | None, dots: list[tuple[float, str, str]],
               dom: tuple[float, float]) -> str:
    """开发方向小窗里的价格分布：柱 = 各价格段占全市场名单销售额的比例，下面一排点 = 增长商品的售价，括线 = 主力价格带。"""
    if not hist or not any(hist):
        return ""
    w, plot = HW - HL - 8, HH - HB - 14
    top = max(hist)
    step = 0.05 if top <= 0.2 else 0.1 if top <= 0.5 else 0.2
    top = math.ceil(top / step - 1e-9) * step
    parts = []
    for j in range(int(round(top / step)) + 1):
        y = round(14 + plot * (1 - j * step / top), 1)
        parts.append(f'<line x1="{HL}" x2="{HW - 8}" y1="{y}" y2="{y}" class="grid"/>'
                     f'<text x="{HL - 5}" y="{y}" dy=".35em" text-anchor="end" class="ax">{j * step:.0%}</text>')
    base = 14 + plot
    for k, share in enumerate(hist):
        lo_e, hi_e = edges[k], edges[k + 1]
        if not share or hi_e <= dom[0] or lo_e >= dom[1]:
            continue
        x1, x2 = _x(max(lo_e, dom[0]), dom, HL, w), _x(min(hi_e, dom[1]), dom, HL, w)
        bw, h = max(1.0, x2 - x1 - 2), max(1.0, plot * share / top)
        r = min(4.0, bw / 2, h)
        x, y = x1 + 1, base - h
        label = (f"{money(lo_e)} 以下" if not lo_e else f"{money(lo_e)} 以上" if math.isinf(hi_e) else f"{money(lo_e)}–{money(hi_e)}")
        parts.append(f'<path d="M{x:.1f},{base} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + bw - r:.1f} '
                     f'Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f} V{base} Z" class="hbar" '
                     f'data-tip="{_esc(f"{label}：占全市场名单销售额 {share:.0%}")}"/>')
    if band and band.get("p25") and band.get("p75"):
        x1, x2 = _x(band["p25"], dom, HL, w), _x(band["p75"], dom, HL, w)
        yb = base + 5
        parts.append(f'<path d="M{x1},{yb - 3} V{yb} H{x2} V{yb - 3}" class="brk"/>'
                     f'<text x="{round((x1 + x2) / 2, 1)}" y="{yb + 10}" text-anchor="middle" class="ax">主力 {money(band["p25"])}–{money(band["p75"])}</text>')
    placed = [(_x(p, dom, HL, w), k) for k, (p, _kind, _t) in enumerate(dots)]
    rows = _spread(placed, 9.0)
    for k, (x, _k) in enumerate(placed):
        _p, kind, tip = dots[k]
        y = base + 26 + 6 * rows[k]
        parts.append(f'<circle cx="{x}" cy="{y}" r="4" class="dot k-{kind}" data-tip="{_esc(tip)}"/>')
    parts += [f'<text x="{_x(t, dom, HL, w)}" y="{HH - 2}" text-anchor="middle" class="ax">${t}</text>' for t in _ticks(dom)]
    return (f'<svg class="phist" viewBox="0 0 {HW} {HH}" role="img" aria-label="价格分布：柱为各价格段占全市场名单销售额的比例，'
            f'点为增长商品售价">{"".join(parts)}</svg>')


DW, DH = 150, 12


def dumbbell(growth: float, other: float | None, tip: str) -> str:
    """0~100% 的一条：实心点 = 增长商品占比，空心圈 = 对照组占比，中间连线是差距。"""
    x0, w = 5, DW - 10

    def x(v: float) -> float:
        return round(x0 + w * min(1.0, max(0.0, v)), 1)
    parts = [f'<line x1="{x0}" x2="{x0 + w}" y1="{DH / 2}" y2="{DH / 2}" class="track"/>']
    if other is not None:
        parts.append(f'<line x1="{x(other)}" x2="{x(growth)}" y1="{DH / 2}" y2="{DH / 2}" class="span"/>'
                     f'<circle cx="{x(other)}" cy="{DH / 2}" r="4" class="oth"/>')
    parts.append(f'<circle cx="{x(growth)}" cy="{DH / 2}" r="4" class="gro"/>')
    return f'<svg class="dumb" viewBox="0 0 {DW} {DH}" data-tip="{_esc(tip)}" role="img" aria-label="{_esc(tip)}">{"".join(parts)}</svg>'


MW, MT, MP, MB = 150, 11, 24, 2  # 迷你走势：宽、上边（悬停读数）、绘图区高、下边


def mini_spark(values: list, start: str | None, recent: int, levels: tuple[float | None, float | None], days: int = 56) -> str:
    """图墙上的迷你走势：最近 days 天日销量，阴影 = 判定用的最近 recent 天；两段横线 = 之前 28 天和最近 recent 天的日均。
    和 sparkline.HOVER_JS 同一套 data-*，悬停显示当天销量。"""
    vals = list(values or [])[-days:]
    if start and values and len(values) > days:
        start = (date.fromisoformat(start) + timedelta(days=len(values) - days)).isoformat()
    points = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(points) < 2:
        return ""
    n = len(vals)
    top = max(max(v for _, v in points), *(lv for lv in levels if lv)) or 1
    w = MW - 2

    def xy(i: float, v: float) -> tuple[float, float]:
        return round(1 + w * i / max(1, n - 1), 1), round(MT + MP * (1 - v / top), 1)
    line = " ".join(f"{'M' if k == 0 else 'L'}{x},{y}" for k, (x, y) in enumerate(xy(i, v) for i, v in points))
    base = MT + MP
    area = f"{line} L{xy(points[-1][0], 0)[0]},{base} L{xy(points[0][0], 0)[0]},{base} Z"
    hl = xy(max(0, n - recent), 0)[0]
    parts = [f'<rect x="{hl}" y="{MT}" width="{round(1 + w - hl, 1)}" height="{MP}" class="spark-hl"/>',
             f'<path d="{area}" class="spark-area"/><path d="{line}" class="spark-line"/>']
    before, now = levels
    if before is not None:
        xa, xb = xy(max(0, n - recent - 28), 0)[0], xy(max(0, n - recent), 0)[0]
        y = xy(0, before)[1]
        parts.append(f'<line x1="{xa}" x2="{xb}" y1="{y}" y2="{y}" class="lv0"/>')
    if now is not None:
        y = xy(0, now)[1]
        parts.append(f'<line x1="{hl}" x2="{1 + w}" y1="{y}" y2="{y}" class="lv1"/>')
    data = ",".join("" if v is None else _raw(v) for v in vals)
    day = f' data-d="{start}"' if start else ""
    return (f'<svg class="spark mini" viewBox="0 0 {MW} {MT + MP + MB}" role="img" aria-label="近 {n} 天日销量" data-k="line" '
            f'data-v="{data}" data-g="1,{w},{MT},{MP},{_raw(top)}" data-u="件"{day}>{"".join(parts)}'
            f'<g class="hov" visibility="hidden"><line x1="1" x2="1" y1="{MT}" y2="{base}"/>'
            f'<circle r="3" cx="1" cy="{base}"/><text x="1" y="{MT - 2}"></text></g></svg>')

"""内联 SVG 迷你走势图：不依赖任何 CDN，钉钉内置浏览器和国内网络都能直接显示。"""
from __future__ import annotations


def sparkline(values: list, *, width: int = 220, height: int = 44, highlight_last: int = 7,
              label: str = "近 90 天日销量") -> str:
    points = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(points) < 2:
        return ""
    n = len(values)
    peak = max(v for _, v in points) or 1
    pad = 3

    def xy(i: int, v: float) -> tuple[float, float]:
        x = pad + (width - 2 * pad) * i / max(1, n - 1)
        y = height - pad - (height - 2 * pad) * (v / peak)
        return round(x, 1), round(y, 1)

    line = " ".join(f"{'M' if k == 0 else 'L'}{x},{y}" for k, (x, y) in enumerate(xy(i, v) for i, v in points))
    first_x, _ = xy(points[0][0], 0)
    last_x, _ = xy(points[-1][0], 0)
    area = f"{line} L{last_x},{height - pad} L{first_x},{height - pad} Z"
    hl_x = xy(max(0, n - highlight_last), 0)[0]
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="{label}"><title>{label}（峰值 {peak:g} 件/天）</title>'
        f'<rect x="{hl_x}" y="0" width="{width - hl_x}" height="{height}" class="spark-hl"/>'
        f'<path d="{area}" class="spark-area"/><path d="{line}" class="spark-line"/></svg>'
    )


def bars(values: list[tuple[str, float]], *, width: int = 220, height: int = 44) -> str:
    """月销量柱状图。"""
    if not values:
        return ""
    peak = max(v for _, v in values) or 1
    n = len(values)
    gap = 2
    bar_w = (width - gap * (n - 1)) / n
    rects = []
    for k, (month, v) in enumerate(values):
        h = max(1.0, (height - 4) * v / peak)
        x = k * (bar_w + gap)
        rects.append(f'<rect x="{x:.1f}" y="{height - h:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
                     f'class="bar"><title>{month}：{v:,.0f} 件</title></rect>')
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'role="img" aria-label="月销量">{"".join(rects)}</svg>')

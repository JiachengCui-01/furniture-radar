import re

from radar.report.sparkline import _ticks, bars, sparkline


def test_ticks_are_round_and_cover_peak():
    for peak, top in ((236, 300), (185, 200), (8, 8), (3, 3), (6094, 7500), (1, 1)):
        step, k = _ticks(peak)
        assert 2 <= k <= 4 and step * k == top and step * k >= peak


def test_sparkline_has_caption_axis_and_dates():
    html = sparkline([120, 1500, None, 900], start="2026-09-29")
    assert "<figcaption>日销量（件/天）· 近 4 天" in html
    assert re.findall(r'class="ax">([^<]+)<', html) == ["0", "500", "1,000", "1,500", "09-29", "10-02"]
    # data-v 给脚本按逗号拆分，不能带千分位；空值留空
    assert 'data-v="120,1500,,900"' in html and 'data-d="2026-09-29"' in html


def test_bars_label_months():
    html = bars([("2025-11", 4665), ("2025-12", 4399), ("2026-01", 4946)])
    assert "<figcaption>月销量（件）· 近 3 个月" in html
    assert 'data-m="2025-11,2025-12,2026-01"' in html
    assert ">2025-11<" in html and ">2026-01<" in html

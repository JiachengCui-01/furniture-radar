"""包装尺寸、包装重量和 FBA 配送费：判断一个开发方向做出来能不能赚钱、海运贵不贵。

数据来自卖家精灵 product_research / asin_competitor 的每一行（pkgDimensions、pkgWeight、fba），
这两个接口每期本来就在调用，只是多要几个字段，不多花调用次数。
  包装体积 = 包装长 × 宽 × 高（英寸）换算成立方英尺（÷1728）和立方米（× 0.0283168）；
  FBA 配送费 = 卖家精灵按包装尺寸和重量估算的亚马逊配送费，不含佣金、仓储费；
  都是“包装”的口径：板式家具拆装发货，包装比组装好的商品小得多，不能用商品尺寸代替。
"""
from __future__ import annotations

import re
import statistics

CUBIC_INCH_PER_FT3 = 1728
M3_PER_FT3 = 0.0283168

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_TO_INCH = {"inch": 1.0, "in": 1.0, "cm": 1 / 2.54, "centimeter": 1 / 2.54, "mm": 1 / 25.4, "millimeter": 1 / 25.4}
_TO_LB = {"pound": 1.0, "lb": 1.0, "ounce": 1 / 16, "oz": 1 / 16, "kilogram": 2.20462, "kg": 2.20462,
          "gram": 0.00220462, "g": 0.00220462}


def _unit(text: str, table: dict[str, float], default: float) -> float:
    words = re.findall(r"[a-z]+", text.lower())
    for word in words:
        for unit, factor in table.items():
            if word == unit or word == unit + "s" or word == unit + "es":
                return factor
    return default


def parse_dims(text) -> list[float] | None:
    """“38.4 x 19.3 x 9.8 inches” → [38.4, 19.3, 9.8]（英寸）。不是三个正数的返回 None。"""
    if not text or not isinstance(text, str):
        return None
    values = [float(v) for v in _NUMBER.findall(text)]
    if len(values) != 3 or min(values) <= 0:
        return None
    factor = _unit(text, _TO_INCH, 1.0)
    return [round(v * factor, 2) for v in values]


def parse_weight(text) -> float | None:
    """“82.9 pounds” → 82.9（磅）；ounces / kilograms / grams 换算成磅。"""
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None
    values = _NUMBER.findall(str(text))
    if not values:
        return None
    value = float(values[0]) * _unit(str(text), _TO_LB, 1.0)
    return round(value, 2) if value > 0 else None


def _money(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if number > 0 else None


def from_row(row: dict) -> dict | None:
    """卖家精灵一行 → {"dims": [长, 宽, 高] 英寸, "weight": 磅, "fba": 美元}；三项都没有的返回 None。"""
    out = {"dims": parse_dims(row.get("pkgDimensions")), "weight": parse_weight(row.get("pkgWeight")),
           "fba": _money(row.get("fba"))}
    return out if any(v is not None for v in out.values()) else None


def cubic_feet(dims: list[float] | None) -> float | None:
    if not dims:
        return None
    return round(dims[0] * dims[1] * dims[2] / CUBIC_INCH_PER_FT3, 2)


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def summarize(items: list[dict]) -> dict | None:
    """一组商品（item["pkg"]、item["price"]）的中位数：包装体积、包装重量、FBA 配送费、FBA 费占售价的比例。
    n = 有包装数据的商品数；没有任何商品有数据时返回 None。"""
    rows = [i for i in items if i.get("pkg")]
    if not rows:
        return None
    volume = [v for v in (cubic_feet(i["pkg"].get("dims")) for i in rows) if v is not None]
    weight = [i["pkg"]["weight"] for i in rows if i["pkg"].get("weight") is not None]
    fba = [i["pkg"]["fba"] for i in rows if i["pkg"].get("fba") is not None]
    share = [i["pkg"]["fba"] / i["price"] for i in rows if i["pkg"].get("fba") is not None and i.get("price")]
    ft3 = _median(volume)
    return {"n": len(rows), "ft3": None if ft3 is None else round(ft3, 1),
            "m3": None if ft3 is None else round(ft3 * M3_PER_FT3, 3),
            "weight": None if not weight else round(_median(weight), 1),
            "fba": None if not fba else round(_median(fba), 2),
            "fba_share": None if not share else round(_median(share), 3)}


def text(pkg: dict | None, price: float | None = None) -> str:
    """一个商品的包装：“包装 4.2 立方英尺（0.12 m³）· 45 磅 · FBA $38（售价的 28%）”。没有数据返回空串。"""
    if not pkg:
        return ""
    parts = []
    ft3 = cubic_feet(pkg.get("dims"))
    if ft3 is not None:
        parts.append(f"包装 {ft3:.1f} 立方英尺（{ft3 * M3_PER_FT3:.2f} m³）")
    if pkg.get("weight") is not None:
        parts.append(f"{pkg['weight']:g} 磅")
    if pkg.get("fba") is not None:
        share = f"（售价的 {pkg['fba'] / price:.0%}）" if price else ""
        parts.append(f"FBA ${pkg['fba']:.2f}{share}")
    return " · ".join(parts)


def summary_text(s: dict | None) -> str:
    """一组商品的中位数：“中位：包装 4.2 立方英尺（0.12 m³）· 45 磅 · FBA $38（售价的 28%）· 5 个有数据”。"""
    if not s:
        return ""
    parts = []
    if s.get("ft3") is not None:
        parts.append(f"包装 {s['ft3']:.1f} 立方英尺（{s['m3']:.2f} m³）")
    if s.get("weight") is not None:
        parts.append(f"{s['weight']:g} 磅")
    if s.get("fba") is not None:
        share = f"（售价的 {s['fba_share']:.0%}）" if s.get("fba_share") is not None else ""
        parts.append(f"FBA ${s['fba']:.2f}{share}")
    return "中位：" + " · ".join(parts) + f" · {s['n']} 个有数据" if parts else ""

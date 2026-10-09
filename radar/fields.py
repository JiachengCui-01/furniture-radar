"""``returnFields``：只让卖家精灵返回用得到的字段，显著缩小响应体。

字段名来自真实响应（见 tests/fixtures/sellersprite/），沿用 AI工作台 server/market/fields.py。
厂商改字段名时，vendor.Vendor 会在“带 returnFields 却返回空”时去掉它重试一次。
"""
from __future__ import annotations

FIELD_SETS: dict[str, tuple[str, ...]] = {
    "market_research": (
        "nodeId", "nodeIdPath", "nodeLabelPath", "nodeLabelLocale", "nodeLabelPathLocale",
        "totalProducts", "totalUnits", "totalRevenue", "avgPrice", "avgRating", "avgRatings",
    ),
    "product_research": (
        "asin", "brand", "title", "imageUrl", "parent", "nodeId", "nodeIdPath", "nodeLabelPath",
        "bsr", "bsrCv", "bsrCr", "units", "unitsGr", "revenue", "price", "averagePrice",
        "ratings", "ratingsRate", "rating", "ratingsCv", "ratingDelta", "availableDate",
        "fulfillment", "variations", "sellers", "sellerName", "sellerNation", "lqs",
    ),
    "asin_detail": (
        "asin", "title", "brand", "parent", "availableDate", "firstRatingDate", "bsrRank",
        "nodeIdPath", "nodeLabelPath", "price", "coupon", "rating", "ratings", "sellers",
        "sellerName", "fulfillment", "variations", "imageUrl", "overviews",
    ),
    # 嵌套结构 {asinDetail, dailyItemList[], monthItemList[]}，returnFields 管不到
    "asin_prediction": (),
    "review": ("date", "star", "verified", "vine", "title"),
    "product_node": ("nodeIdPath", "nodeLabelPath", "nodeLabelPathLocale", "products"),
}


def apply(tool: str, arguments: dict) -> dict:
    value = ",".join(FIELD_SETS.get(tool, ()))
    if not value:
        return arguments
    if isinstance(arguments.get("request"), dict):
        inner = dict(arguments["request"])
        inner.setdefault("returnFields", value)
        return {**arguments, "request": inner}
    out = dict(arguments)
    out.setdefault("returnFields", value)
    return out


def strip(arguments: dict) -> dict:
    if isinstance(arguments.get("request"), dict):
        inner = {k: v for k, v in arguments["request"].items() if k != "returnFields"}
        return {**arguments, "request": inner}
    return {k: v for k, v in arguments.items() if k != "returnFields"}


def has_fields(arguments: dict) -> bool:
    inner = arguments.get("request") if isinstance(arguments.get("request"), dict) else arguments
    return bool(inner.get("returnFields"))

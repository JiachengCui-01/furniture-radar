"""演示数据：模拟卖家精灵的响应，不花任何积分就能看到完整报告效果，也用于端到端测试。

内置几类典型商品：稳定热销、持续暴涨、脉冲、降价驱动、季节性、潜力新品、
刷评新品、评论集中爆发、变体合并、下滑、普通商品。
"""
from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta, timezone

ROOT_HOME, ROOT_OFFICE = "1055398:1063306", "1064954:1069102"

# 板材 / 实木 / 铁木为主的柜类、桌类、床架、置物架；沙发和办公椅用来验证“剔除”
NODES = [
    (ROOT_HOME, "1055398:1063306:1063318:1063310:3733691",
     "Home & Kitchen:Furniture:Living Room Furniture:TV & Media Furniture:Television Stands & Entertainment Centers",
     "电视柜", 4588),
    (ROOT_HOME, "1055398:1063306:1063308:3733101:3248801011",
     "Home & Kitchen:Furniture:Bedroom Furniture:Beds, Frames & Bases:Bed Frames", "床架", 11637),
    (ROOT_HOME, "1055398:1063306:1063312:3733671",
     "Home & Kitchen:Furniture:Home Office Furniture:Home Office Desks", "家庭办公桌", 6480),
    (ROOT_HOME, "1055398:1063306:3733781:3733831",
     "Home & Kitchen:Furniture:Kitchen & Dining Room Furniture:Buffets & Sideboards", "餐边柜", 4210),
    (ROOT_HOME, "1055398:1063306:1063312:3733641",
     "Home & Kitchen:Furniture:Home Office Furniture:Bookcases", "书架", 5244),
    (ROOT_HOME, "1055398:1063306:1063318:3733551",
     "Home & Kitchen:Furniture:Living Room Furniture:Sofas & Couches", "沙发", 9421),
    (ROOT_OFFICE, "1064954:1069102:1069130:1069140",
     "Office Products:Office Furniture & Lighting:Desks & Workstations:Computer Workstations", "计算机工作站", 1278),
    (ROOT_OFFICE, "1064954:1069102:1069129",
     "Office Products:Office Furniture & Lighting:Chairs & Sofas", "办公椅", 15200),
]

TITLES = {
    "电视柜": ["{s} TV Stand for 65 Inch TV, {m} Entertainment Center with Storage Cabinets",
            "{s} Media Console Table, {m} TV Cabinet with Open Shelves"],
    "床架": ["{s} Queen Bed Frame with Headboard, {m} Platform Bed with Storage Drawers",
           "{s} Full Size Bed Frame, {m} Noise-Free Mattress Foundation"],
    "家庭办公桌": ["{s} Computer Desk with Drawers, {m} Home Office Writing Desk",
              "{s} L Shaped Desk, {m} Corner Desk with Shelves"],
    "餐边柜": ["{s} Sideboard Buffet Cabinet with Storage, {m} Accent Credenza",
            "{s} Coffee Bar Cabinet, {m} Kitchen Storage Cabinet"],
    "书架": ["{s} 5 Tier Bookcase, {m} Open Display Shelves",
           "{s} Tall Bookshelf with Doors, {m} Storage Shelf"],
    "沙发": ["{s} Sectional Sofa Couch with Chaise, {m} L Shaped Sofa for Living Room"],
    "计算机工作站": ["{s} Computer Workstation with Monitor Shelf, {m} Gaming Desk",
               "{s} Writing Desk with Keyboard Tray, {m} Study Table"],
    "办公椅": ["{s} Ergonomic Office Chair, {m} Mesh Desk Chair with Lumbar Support"],
}
STYLES = ["Modern", "Mid-Century", "Farmhouse", "Industrial", "Rustic", "Minimalist"]
MATERIALS = ["Engineered Wood", "Solid Wood", "Metal Frame and Wood", "Acacia Wood", "Rustic Brown Wood", "MDF"]
TREND_WORDS = "Fluted Walnut"  # 爆火商品的共同外观，用来演示“外观与工艺特征”
BRANDS = ["Lumora", "Oakhaven", "Nestwell", "Vireo", "Casafin", "Haldor", "Moviq", "Sundry", "Brightoak", "Kelso"]


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _bsr(sales: float) -> int:
    return int(250000 / (max(sales, 0) + 0.3) ** 0.9)


class Product:
    def __init__(self, idx: int, node: tuple, kind: str, rng: random.Random, today: date):
        self.idx = idx
        self.root, self.node, self.label, self.cn, _ = node
        self.kind = kind
        self.rng = rng
        self.asin = f"B0DEMO{idx:04d}"
        self.parent = f"B0PAR{idx:05d}"
        self.brand = BRANDS[idx % len(BRANDS)]
        style, material = rng.choice(STYLES), rng.choice(MATERIALS)
        if kind in ("surge", "potential", "price_surge"):
            material = f"{TREND_WORDS} {material}"
        self.title = rng.choice(TITLES[self.cn]).format(s=style, m=material)
        if kind == "upholstered":  # 软包床：商品级过滤应剔除
            self.title = "Upholstered " + self.title
        self.price = round(rng.uniform(120, 700) if self.cn in ("电视柜", "床架", "餐边柜", "沙发") else rng.uniform(60, 320), 2)
        self.base = {"steady": rng.uniform(25, 45), "surge": rng.uniform(4, 7), "pulse": rng.uniform(5, 8),
                     "price_surge": rng.uniform(4, 6), "seasonal": rng.uniform(5, 8), "potential": 0.0,
                     "fake_new": 0.0, "review_burst": rng.uniform(10, 14), "merge": rng.uniform(12, 16),
                     "declining": rng.uniform(30, 40), "noise": rng.lognormvariate(1.0, 0.7),
                     "upholstered": rng.uniform(30, 40)}[kind]
        age = {"potential": 115, "fake_new": 70}.get(kind, rng.randint(250, 900))
        self.available = today - timedelta(days=age)
        self.variations = {"merge": 3, "fake_new": 2, "review_burst": 2}.get(kind, rng.choice([1, 2, 3, 6, 12]))
        self.review_rate = {"fake_new": 0.16}.get(kind, rng.uniform(0.01, 0.025))
        self.ratings0 = rng.randint(80, 2500) if age > 200 else 5
        self.rating = round(rng.uniform(4.1, 4.7), 1)
        self.fulfillment = "FBA" if kind in ("surge", "potential", "price_surge") or rng.random() < 0.5 else "FBM"
        self.nation = "CN" if kind in ("surge", "potential") or rng.random() < 0.45 else "US"
        self.extra_reviews: list[tuple[date, int]] = []
        self.merged_on: date | None = None

    def sales(self, day: date, today: date) -> float:
        if day < self.available:
            return 0.0
        k, b = self.kind, self.base
        days_ago = (today - day).days
        weekly = 1 + 0.12 * math.sin(day.toordinal() / 7 * 2 * math.pi)
        noise = 1 + 0.08 * math.sin(day.toordinal() * 1.7 + self.idx)
        if k == "surge":
            v = b * (3.8 if days_ago <= 11 else 1)
        elif k == "price_surge":
            v = b * (3.2 if days_ago <= 9 else 1)
        elif k == "pulse":
            v = b * (6 if days_ago in (10, 11) else 1)
        elif k == "seasonal":  # 今年和去年同期都在涨
            v = b * (3.5 if days_ago <= 9 or 364 <= days_ago <= 373 else 1)
        elif k in ("potential", "fake_new"):
            age = (day - self.available).days
            v = (1.2 * math.exp(age / 38) if k == "potential" else 4.5)
            v = min(v, 40)
        elif k == "declining":
            v = b * (1 - 0.6 * max(0, 1 - days_ago / 120))
        elif k == "noise":  # 普通商品：月度起伏较大
            v = b * (1 + 0.6 * math.sin(day.toordinal() / 30 + self.idx))
        else:
            v = b
        return max(0.0, v * weekly * noise)

    def price_on(self, day: date, today: date) -> float:
        if self.kind == "price_surge" and (today - day).days <= 9:
            return round(self.price * 0.72, 2)
        return self.price

    def ratings_on(self, day: date, today: date) -> int:
        total = sum(self.sales(self.available + timedelta(days=i), today)
                    for i in range(max(0, (day - self.available).days + 1)))
        extra = sum(n for d, n in self.extra_reviews if d <= day)
        if self.merged_on and day >= self.merged_on:
            extra += 2400
        return int(self.ratings0 + self.review_rate * total + extra)


class SyntheticWorld:
    """按工具名返回与卖家精灵同结构的 JSON。today 可推进，用来模拟多期运行。"""

    def __init__(self, today: date, seed: int = 7, lag_days: int = 2):
        self.today = today
        self.lag = lag_days
        rng = random.Random(seed)
        plan = {
            "电视柜": ["steady", "surge", "merge", "noise", "declining", "noise"],
            "床架": ["steady", "potential", "pulse", "upholstered", "steady", "noise"],
            "家庭办公桌": ["steady", "price_surge", "fake_new", "noise", "steady", "noise"],
            "餐边柜": ["steady", "surge", "potential", "review_burst", "noise"],
            "书架": ["seasonal", "steady", "potential", "noise"],
            "沙发": ["steady", "surge", "noise"],
            "计算机工作站": ["steady", "surge", "fake_new", "potential", "noise"],
            "办公椅": ["steady", "surge", "noise"],
        }
        self.products: list[Product] = []
        idx = 1
        for node in NODES:
            for kind in plan[node[3]]:
                self.products.append(Product(idx, node, kind, rng, today))
                idx += 1
        # 填充基线，让“共性特点”有对比对象
        for node in NODES:
            for _ in range(24):
                self.products.append(Product(idx, node, "noise", rng, today))
                idx += 1
        self.by_asin = {p.asin: p for p in self.products}
        self.calls: list[str] = []

    @property
    def data_end(self) -> date:
        return self.today - timedelta(days=self.lag)

    def advance(self, days: int) -> None:
        """推进时间；刷评商品在这段时间里多出一批评论，变体合并商品发生合并。"""
        start = self.today
        self.today += timedelta(days=days)
        for p in self.products:
            if p.kind == "review_burst":
                p.extra_reviews.append((start + timedelta(days=1), 60))
            if p.kind == "merge" and p.merged_on is None:
                p.merged_on = start + timedelta(days=1)
                p.variations = 24
                p.parent = "B0MERGED01"

    # ------------------------------------------------------------ 响应 ----

    @staticmethod
    def _ok(data) -> str:
        return json.dumps({"code": "OK", "message": "成功", "data": data}, ensure_ascii=False)

    @staticmethod
    def _page(items: list) -> dict:
        return {"page": 1, "size": len(items), "total": len(items), "items": items}

    def _month_bounds(self, period: str) -> tuple[date, date]:
        first = date(int(period[:4]), int(period[4:6]), 1)
        nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
        return first, nxt - timedelta(days=1)

    def _month_row(self, p: Product, period: str) -> dict:
        first, last = self._month_bounds(period)
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        units = sum(p.sales(d, self.today) for d in days)
        prev_first = first - timedelta(days=28)
        prev_units = sum(p.sales(prev_first + timedelta(days=i), self.today) for i in range(28))
        ratings = p.ratings_on(last, self.today)
        ratings_cv = ratings - p.ratings_on(first - timedelta(days=1), self.today)
        bsr, prev_bsr = _bsr(units / len(days)), _bsr(prev_units / 28)
        return {
            "asin": p.asin, "brand": p.brand, "title": p.title, "parent": p.parent,
            "imageUrl": "", "nodeIdPath": p.node, "nodeLabelPath": p.label,
            "bsr": bsr, "bsrCv": prev_bsr - bsr, "bsrCr": round((prev_bsr - bsr) / max(prev_bsr, 1) * 100, 2),
            "units": round(units), "unitsGr": None, "revenue": round(units * p.price, 2), "price": p.price,
            "ratings": ratings, "ratingsCv": ratings_cv,
            "ratingsRate": round(ratings_cv / units * 100, 2) if units else 0, "rating": p.rating,
            "availableDate": _ms(p.available),
            "fulfillment": p.fulfillment, "variations": p.variations, "sellers": 1,
            "sellerNation": p.nation,
        }

    def __call__(self, tool: str, arguments: dict) -> str:
        self.calls.append(tool)
        req = arguments.get("request") if isinstance(arguments.get("request"), dict) else arguments
        if tool == "market_research":
            root = req["nodeIdPath"]
            first, last = self._month_bounds(req["month"])
            if last >= self.today:
                return self._ok(self._page([]))  # 未结束的月份没有数据
            rows = [{"nodeIdPath": root, "nodeLabelPath": root, "totalProducts": 99999, "totalUnits": 1}]
            for r, path, label, cn, products in NODES:
                if r == root:
                    rows.append({"nodeIdPath": path, "nodeLabelPath": label, "nodeLabelLocale": cn,
                                 "totalProducts": products, "totalUnits": products * 3,
                                 "totalRevenue": products * 900})
            return self._ok(self._page(rows))
        if tool == "product_research":
            node, period = req["nodeIdPath"], req["month"]
            members = [p for p in self.products
                       if (p.node == node or p.node.startswith(node + ":"))
                       and p.available <= self._month_bounds(period)[1]]
            rows = [self._month_row(p, period) for p in members]
            rows = [r for r in rows if r["revenue"] >= req.get("minRevenue", 0) and r["units"] >= req.get("minUnits", 0)]
            order = (req.get("order") or {}).get("field")
            if order == "available_date":
                rows.sort(key=lambda r: -r["availableDate"])
            elif order == "bsr_rank_cr":
                rows.sort(key=lambda r: -r["bsrCr"])
            else:
                rows.sort(key=lambda r: -r["revenue"])
            return self._ok(self._page(rows[: int(req.get("size", 50))]))
        if tool == "asin_prediction":
            p = self.by_asin[req["asin"]]
            end = self.data_end
            start = end - timedelta(days=419)
            daily = []
            for i in range(420):
                d = start + timedelta(days=i)
                if d < p.available:
                    continue
                s = p.sales(d, self.today)
                daily.append({"date": d.isoformat(), "bsr": _bsr(s), "sales": round(s),
                              "amount": round(s * p.price_on(d, self.today)), "price": p.price_on(d, self.today)})
            months: dict[str, list] = {}
            for row in daily:
                months.setdefault(row["date"][:7], []).append(row)
            month_items = [{"date": k, "sales": sum(r["sales"] for r in v), "amount": sum(r["amount"] for r in v),
                            "price": p.price} for k, v in sorted(months.items())]
            detail = {"asin": p.asin, "title": p.title, "brand": p.brand, "availableDate": _ms(p.available),
                      "rating": p.rating, "ratings": p.ratings_on(end, self.today), "nodeIdPath": p.node,
                      "nodeLabelPath": p.label, "imageUrl": ""}
            return self._ok({"asinDetail": detail, "dailyItemList": daily, "monthItemList": month_items})
        if tool == "asin_detail":
            p = self.by_asin[req["asin"]]
            return self._ok({"asin": p.asin, "parent": p.parent, "variations": p.variations,
                             "ratings": p.ratings_on(self.data_end, self.today), "rating": p.rating})
        if tool == "review":
            p = self.by_asin[req["asin"]]
            rng = random.Random(p.idx)
            items = []
            for i in range(40):
                if p.kind in ("review_burst", "fake_new") and i < 22:
                    day = self.data_end - timedelta(days=[3, 4, 9][i % 3])
                else:
                    day = self.data_end - timedelta(days=rng.randint(0, 59))
                items.append({"date": str(_ms(day)), "star": "5" if i % 5 else "4",
                              "verified": "False" if p.kind == "fake_new" and i % 3 else "True", "vine": "False"})
            return self._ok(self._page(items))
        if tool == "product_node":
            return self._ok([{"nodeIdPath": path, "nodeLabelPath": label, "nodeLabelPathLocale": cn,
                              "products": products} for _, path, label, cn, products in NODES])
        return json.dumps({"code": "ERROR", "message": f"unknown tool {tool}"})

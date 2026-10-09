"""发现：每个已结束的自然月跑一次，找出全部家具子类目的头部商品与新品。

1. market_research（nodeIdPathEqual=false）列出每个根类目下的子类目 —— 每个根 1 次调用；
2. 过滤（商品数、排除非家具、去掉祖孙重叠），最多 max_nodes 个子类目；
3. 每个子类目 product_research 取头部 —— 每个 1 次调用；
4. 销量最大的若干子类目再查一次新品（按上架时间倒序 + 最低销售额）。

结果约 1500~2000 个 ASIN 的月度指标，作为“基线”与追踪池的来源。
另外每期都会刷新近 30 天榜单（find_risers）和爆款相似款（find_similar），见各函数说明。
预算中途用完时保存进度，下期接着跑同一个月份。
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone

from . import clock, log
from .vendor import BudgetExhausted, Vendor, request


def _variation(cfg: dict) -> str | None:
    """variation=Y：同一父体只返回一条。实测不设置时，子类目头部 50 名里常常只有 1~5 个不同的父体。"""
    return "Y" if cfg["discovery"].get("merge_variations", True) else None


def clean_text(value) -> str:
    return " ".join(html.unescape(str(value or "")).split())


def scope_key(cfg: dict) -> str:
    """监控范围的指纹：范围或发现参数一改，就在本期重新发现，而不是等到下个月。"""
    keys = ("top_per_node", "order_field", "merge_variations", "newcomer_max_nodes", "newcomers_per_node",
            "newcomer_min_revenue")
    body = {"scope": cfg["scope"], "discovery": {k: cfg["discovery"].get(k) for k in keys}}
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def keep_product(item: dict | None, cfg: dict) -> bool:
    """商品级过滤：标题里是软包、布艺抽屉、塑料等不在监控范围内的，直接剔除。"""
    if not item:
        return False
    pattern = cfg["scope"].get("exclude_product_regex")
    return not (pattern and re.search(pattern, item.get("title") or ""))


def depth(path: str) -> int:
    return path.count(":") + 1


def is_ancestor(a: str, b: str) -> bool:
    return b.startswith(a + ":")


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_node(row: dict, root: dict) -> dict | None:
    path = str(row.get("nodeIdPath") or "").strip()
    if not path:
        return None
    label = str(row.get("nodeLabelPath") or "").strip()
    cn_path = str(row.get("nodeLabelPathLocale") or "").strip()
    label_cn = str(row.get("nodeLabelLocale") or "").strip() or (cn_path.split(":")[-1] if cn_path else "")
    return {
        "path": path,
        "label": label,
        "name": label.split(":")[-1] if label else path,
        "name_cn": label_cn,
        "root": root["path"],
        "root_label": root.get("label", ""),
        "products": _num(row.get("totalProducts")) or 0,
        "units": _num(row.get("totalUnits")) or 0,
        "revenue": _num(row.get("totalRevenue")) or 0,
        "avg_price": _num(row.get("avgPrice")),
    }


def _relative_label(node: dict) -> str:
    """根类目以下的部分。根名里本身含 Lighting / Accessories，不能拿整条路径去匹配排除词。"""
    parts = node["label"].split(":")
    return ":".join(parts[depth(node["root"]):]) or node["name"]


def select_nodes(raw: list[dict], cfg: dict) -> list[dict]:
    scope = cfg["scope"]
    roots = {r["path"] for r in scope["roots"]}
    exclude = re.compile(scope["exclude_label_regex"]) if scope.get("exclude_label_regex") else None
    include = re.compile(scope["include_label_regex"]) if scope.get("include_label_regex") else None

    seen: dict[str, dict] = {}
    for node in raw:
        if node["path"] in roots or node["path"] in seen:
            continue
        if node["products"] < scope["min_node_products"]:
            continue
        # 只看子类目自己的名字：父类目名会误伤（如 Kitchen Islands 的父类目叫 Storage Islands & Carts）
        name = node["name"] or _relative_label(node)
        if exclude and exclude.search(name):
            continue
        if include and not include.search(name):
            continue
        seen[node["path"]] = node

    # 先选最深的节点，祖先/后代重叠的不再选（nodeIdPathEqual=false 会包含后代，重叠会重复计数）
    candidates = sorted(seen.values(), key=lambda n: (-depth(n["path"]), -n["units"]))
    chosen: list[dict] = []
    for node in candidates:
        if any(is_ancestor(c["path"], node["path"]) or is_ancestor(node["path"], c["path"]) for c in chosen):
            continue
        chosen.append(node)
    chosen.sort(key=lambda n: -n["units"])
    chosen = chosen[: scope["max_nodes"]]

    for extra in scope.get("extra_nodes") or []:
        path = str(extra.get("path") or "").strip()
        if path and all(c["path"] != path for c in chosen):
            chosen.append({
                "path": path, "label": extra.get("label", path), "name": extra.get("label", path),
                "name_cn": extra.get("label", ""), "root": path, "root_label": extra.get("label", ""),
                "products": 0, "units": 0, "revenue": 0, "avg_price": None,
            })
    return chosen


def normalize_product(row: dict, node: dict, source: str, rank: int) -> dict | None:
    asin = str(row.get("asin") or "").strip()
    if not asin:
        return None
    available = clock.ms_to_day(row.get("availableDate"))
    return {
        "asin": asin,
        "parent": str(row.get("parent") or "").strip() or None,
        "title": clean_text(row.get("title")),
        "brand": clean_text(row.get("brand")),
        "image": str(row.get("imageUrl") or "").strip(),
        "node": node["path"],
        "node_name": node["name"],
        "node_cn": node.get("name_cn") or "",
        "own_node": str(row.get("nodeLabelPath") or ""),
        "bsr": _num(row.get("bsr")),
        "bsr_cr": _num(row.get("bsrCr")),
        "units": _num(row.get("units")) or 0,
        "units_gr": _num(row.get("unitsGr")),
        "revenue": _num(row.get("revenue")) or 0,
        "price": _num(row.get("price")),
        "ratings": _num(row.get("ratings")),
        "ratings_rate": _num(row.get("ratingsRate")),
        "rating": _num(row.get("rating")),
        "ratings_cv": _num(row.get("ratingsCv")),
        "available": available.isoformat() if available else None,
        "fulfillment": str(row.get("fulfillment") or "").strip(),
        "variations": _num(row.get("variations")),
        "sellers": _num(row.get("sellers")),
        "seller_nation": str(row.get("sellerNation") or "").strip(),
        "sources": [source],
        "rank": rank,
    }


def _add(products: dict, item: dict | None) -> None:
    if not item:
        return
    existing = products.get(item["asin"])
    if existing is None:
        products[item["asin"]] = item
    elif item["sources"][0] not in existing["sources"]:
        existing["sources"].append(item["sources"][0])


def discover(vendor: Vendor, cfg: dict, period: str, previous: dict | None = None) -> dict | None:
    """返回发现结果；该月份数据还没发布时返回 None。预算不够时返回 complete=False 的部分结果。"""
    mp = cfg["marketplace"]
    disc_cfg = cfg["discovery"]
    key = scope_key(cfg)
    if (previous and previous.get("period") == period and not previous.get("complete")
            and previous.get("scope_key") == key):
        disc = previous
    else:
        disc = {"period": period, "complete": False, "nodes": [], "done_top": [], "done_new": [],
                "products": {}, "fetched_at": None, "scope_key": key}

    try:
        if not disc["nodes"]:
            raw: list[dict] = []
            for root in cfg["scope"]["roots"]:
                # 子类目按销售额排序，取前几页（范围收窄后，柜类/桌类可能排在 50 名以后）
                for page in range(1, int(cfg["scope"].get("rollup_pages", 1)) + 1):
                    reply = vendor.call("market_research", request(
                        marketplace=mp, nodeIdPath=root["path"], nodeIdPathEqual="false", month=period,
                        size=50, page=page, order={"field": "total_amount", "desc": True}), purpose="子类目")
                    for row in reply.rows:
                        node = parse_node(row, root)
                        if node:
                            raw.append(node)
                    if len(reply.rows) < 50:
                        break
            if not raw:
                return None
            disc["nodes"] = select_nodes(raw, cfg)
            log.info(f"发现：{period} 选出 {len(disc['nodes'])} 个子类目")

        for node in disc["nodes"]:
            if node["path"] in disc["done_top"]:
                continue
            reply = vendor.call("product_research", request(
                marketplace=mp, nodeIdPath=node["path"], nodeIdPathEqual="false", month=period,
                size=disc_cfg["top_per_node"], page=1, variation=_variation(cfg),
                order={"field": disc_cfg["order_field"], "desc": True}), purpose="头部商品")
            for rank, row in enumerate(reply.rows, start=1):
                item = normalize_product(row, node, "top", rank)
                if keep_product(item, cfg):
                    _add(disc["products"], item)
            disc["done_top"].append(node["path"])

        newcomer_nodes = sorted(disc["nodes"], key=lambda n: -n["units"])[: disc_cfg["newcomer_max_nodes"]]
        for node in newcomer_nodes:
            if node["path"] in disc["done_new"]:
                continue
            reply = vendor.call("product_research", request(
                marketplace=mp, nodeIdPath=node["path"], nodeIdPathEqual="false", month=period,
                size=disc_cfg["newcomers_per_node"], minRevenue=disc_cfg["newcomer_min_revenue"],
                variation=_variation(cfg), order={"field": "available_date", "desc": True}), purpose="新品")
            for rank, row in enumerate(reply.rows, start=1):
                item = normalize_product(row, node, "new", rank)
                if keep_product(item, cfg):
                    _add(disc["products"], item)
            disc["done_new"].append(node["path"])
        disc["complete"] = True
    except BudgetExhausted:
        log.warn("发现阶段预算用完，下期继续")
    disc["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info(f"发现：基线商品 {len(disc['products'])} 个（{'完整' if disc['complete'] else '部分'}）")
    return disc


def owning_node(path: str, nodes: list[dict]) -> dict | None:
    """商品所属的、已选中的最深子类目；不在任何选中子类目下（例如被排除的遮阳伞）则返回 None。"""
    best = None
    for node in nodes:
        if path == node["path"] or is_ancestor(node["path"], path):
            if best is None or depth(node["path"]) > depth(best["path"]):
                best = node
    return best


def _fresh_row(row: dict, nodes: list[dict], source: str, rank: int, cfg: dict) -> dict | None:
    """近 30 天榜单里的一行：归到已选中的子类目，剔除范围外的和断货恢复的（之前 BSR 几百万）。"""
    max_prev = float(cfg["discovery"].get("risers_max_prev_bsr", 1_000_000))
    prev_bsr = (_num(row.get("bsr")) or 0) + (_num(row.get("bsrCv")) or 0)
    if prev_bsr > max_prev:
        return None
    node = owning_node(str(row.get("nodeIdPath") or ""), nodes)
    item = normalize_product(row, node, source, rank) if node else None
    return item if keep_product(item, cfg) else None


def find_risers(vendor: Vendor, cfg: dict, nodes: list[dict]) -> list[dict]:
    """近 30 天榜单（每期刷新）：product_research 不传 month 时，返回的是截至当天的近 30 天数据
    （传 month 只有已结束月份的月末快照）。每个根类目查：
      - 按“销量增长率”“近 7 天 BSR 增长率”排序的上升榜；
      - 按上架时间倒序、近 30 天销售额达标的新品榜。

    实测两种上升排序都有噪音：BSR 增长率榜前排多是断货后恢复的商品（之前 BSR 几百万），
    销量增长率榜会有整组变体同时出现。所以去掉“之前 BSR > risers_max_prev_bsr”的，
    并按父体去重；最终是否爆火由日数据判定。"""
    dcfg = cfg["discovery"]
    size = int(dcfg.get("risers_per_root", 0))
    new_size = int(dcfg.get("risers_new_per_root", 0))
    if (size <= 0 and new_size <= 0) or not nodes:
        return []
    found: dict[str, dict] = {}
    parents: set[str] = set()

    def keep(item: dict | None) -> None:
        if not item:
            return
        key = item["parent"] or item["asin"]
        if item["asin"] in found or key in parents:
            return
        found[item["asin"]] = item
        parents.add(key)

    lists = [(order, size, {"minUnits": dcfg.get("risers_min_units")}, "riser")
             for order in (dcfg.get("risers_orders") or ["total_units_growth", "bsr_rank_cr"])] if size > 0 else []
    if new_size > 0:
        lists.append(("available_date", new_size, {"minRevenue": dcfg.get("newcomer_min_revenue")}, "fresh_new"))
    for order_field, n, limits, source in lists:
        for root in cfg["scope"]["roots"]:
            reply = vendor.call("product_research", request(
                marketplace=cfg["marketplace"], nodeIdPath=root["path"], nodeIdPathEqual="false",
                size=n, page=1, variation=_variation(cfg), order={"field": order_field, "desc": True},
                **limits), purpose="近30天榜单")
            for rank, row in enumerate(reply.rows, start=1):
                keep(_fresh_row(row, nodes, source, rank, cfg))
    log.info(f"近 30 天榜单：{len(found)} 个候选")
    return list(found.values())


def find_similar(vendor: Vendor, cfg: dict, seeds: list[str], nodes: list[dict]) -> list[dict]:
    """相似款：asin_competitor 返回与种子商品同类的竞品（约 15 个，近 30 天数据，与 product_research 同结构）。
    种子是上期的爆火/潜力商品，用来顺藤摸瓜找同一波趋势里的其他商品。"""
    found: dict[str, dict] = {}
    for seed in seeds:
        reply = vendor.call("asin_competitor", vendor.args(
            "asin_competitor", nested=False, marketplace=cfg["marketplace"], asin=seed,
            size=int(cfg["discovery"].get("similar_size", 20))), purpose="相似款")
        for rank, row in enumerate(reply.rows, start=1):
            item = _fresh_row(row, nodes, "similar", rank, cfg)
            if item and item["asin"] != seed and item["asin"] not in found:
                item["seed"] = seed
                found[item["asin"]] = item
    return list(found.values())

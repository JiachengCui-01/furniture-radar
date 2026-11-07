"""新上架的早期名单先占名额、主图预筛（简单款不拉日数据）（2026-11-07 用户要求：尽量搜集新上架的大件家具，
不符合要求的不再花调用）。"""
from __future__ import annotations

import copy
from datetime import date

import httpx
import pytest

from radar import tracking, vision
from radar.detect import showcase

TODAY = date(2026, 11, 20)
NODE = "1055398:1063306:1063308:3733251"


@pytest.fixture
def pcfg(cfg):
    out = copy.deepcopy(cfg)
    out["pool"]["young"] = {**(out["pool"].get("young") or {}), "enabled": True, "max": 2, "bsr_cr": 50}
    out["pool"]["prescreen"] = {"enabled": True, "max_images": 50, "look_ahead": 1.5}
    out["llm"]["vision"] = "auto"
    out["report"].setdefault("showcase", {})["common_min_items"] = 99  # 不按子类目扣基本结构，只看本身有没有特色
    return out


def _row(asin, *, units=400, bsr_cr=0.0, title="Wood Nightstand", available="2026-03-01", weight=40.0):
    return {"asin": asin, "parent": None, "node": NODE, "node_name": "Nightstands", "node_cn": "床头柜", "title": title,
            "brand": f"B{asin}", "image": f"https://m.media-amazon.com/images/I/{asin}x._AC_US200_.jpg", "units": units,
            "revenue": units * 150.0, "price": 150.0, "rating": 4.6, "ratings": 300, "available": available,
            "bsr_cr": bsr_cr, "sources": ["universe"], "pkg": {"dims": [30.0, 20.0, 10.0], "weight": weight}}


def _seen(asin, tags):
    return {"image": f"https://m.media-amazon.com/images/I/{asin}x._AC_US600_.jpg", "vocab": vision.VOCAB_VERSION,
            "protocol": vision.PROTOCOL, "tags": tags, "summary": "", "where": {}}


def _state(rows, young):
    return {"asins": {}, "runs": [], "universe": {"date": TODAY.isoformat(), "rows": {r["asin"]: r for r in rows},
                                                  "young": {r["asin"]: r for r in young}, "snaps": [], "baseline": None}}


def test_young_listings_get_their_own_slots_before_the_market_screen(pcfg, monkeypatch):
    """初筛候选总比预算多：早期名单排在后面时一个都排不上；现在先占 young.max 个名额，名额以外的初筛之后还有预算再看。"""
    rows = [_row(f"M{i}") for i in range(10)]
    young = [_row(f"Y{i}", units=150, bsr_cr=80 - i, available="2026-09-01") for i in range(4)]
    state = _state(rows, young)
    monkeypatch.setattr(tracking.universe, "screen", lambda st, r, t, c: [(a, 1.0, "") for a in r])
    monkeypatch.setattr(tracking.blueocean, "candidates", lambda *a, **k: {})
    pcfg["pool"]["core_max"] = 0
    order, reason = tracking.select_pool({"nodes": [{"path": NODE}], "products": {}}, state, pcfg, TODAY, limit=5)
    assert order[:2] == ["Y0", "Y1"] and [reason[a] for a in order] == ["young", "young", "screen", "screen", "screen"]
    order, reason = tracking.select_pool({"nodes": [{"path": NODE}], "products": {}}, state, pcfg, TODAY, limit=20)
    assert [a for a in order if reason[a] == "young"] == ["Y0", "Y1", "Y2", "Y3"]  # 预算够时名额以外的也看
    # 主图预筛判成简单款的不以初筛 / 早期名单进池
    order, reason = tracking.select_pool({"nodes": [{"path": NODE}], "products": {}}, state, pcfg, TODAY, limit=5,
                                         skip={"Y0", "M0"})
    assert "Y0" not in order and "M0" not in order and order[:2] == ["Y1", "Y2"]


def test_prescreen_skips_simple_styles_using_cached_photos(pcfg, monkeypatch):
    """识别过的直接用缓存（不再调大模型）：只有基本结构（开放格、抽屉）的是简单款，跳过；有特色（凹槽竖纹）的照常拉；
    没识别成的照常拉。只看前 min(max_images, 名额 × look_ahead) 个。"""
    rows = [_row("PLAIN"), _row("FLUTED"), _row("UNSEEN")]
    state = _state(rows, [])
    state["vision_extra"] = {"PLAIN": {"vision": _seen("PLAIN", {"造型": ["开放格"], "功能卖点": ["带储物/抽屉"]})},
                             "FLUTED": {"vision": _seen("FLUTED", {"工艺": ["凹槽竖纹"], "功能卖点": ["带储物/抽屉"]})}}
    monkeypatch.setattr(tracking.universe, "screen", lambda st, r, t, c: [(a, 1.0, "") for a in r])
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500)))
    skip, stats = tracking.prescreen(state, {"products": {}}, pcfg, TODAY, budget=10, api_key="k", client=client)
    assert skip == {"PLAIN"} and stats["looked"] == 3 and stats["plain"] == 1
    assert showcase.is_plain(rows[0], state) and not showcase.is_plain(rows[1], state)
    # 只看前 min(max_images, 名额 × look_ahead) 个
    calls.clear()
    skip, stats = tracking.prescreen(state, {"products": {}}, pcfg, TODAY, budget=1, api_key="k", client=client)
    assert stats["looked"] == 1 and skip == {"PLAIN"} and not calls
    # 没开、没有大模型密钥、本期没翻成全市场名单：不预筛
    assert tracking.prescreen(state, {}, pcfg, TODAY, budget=10, api_key="") == (set(), None)
    off = copy.deepcopy(pcfg)
    off["pool"]["prescreen"]["enabled"] = False
    assert tracking.prescreen(state, {}, off, TODAY, budget=10, api_key="k") == (set(), None)
    assert tracking.prescreen(state, {}, pcfg, date(2026, 11, 21), budget=10, api_key="k") == (set(), None)

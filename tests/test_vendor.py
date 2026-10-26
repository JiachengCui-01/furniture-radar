import json

import pytest

from radar.mcp_client import McpTool, McpUnavailable
from radar.vendor import Budget, BudgetExhausted, QuotaExhausted, Vendor, request
from tests.conftest import fixture_text


class Recorder:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def __call__(self, tool, arguments):
        self.seen.append((tool, arguments))
        return self.replies.pop(0)


def test_budget_is_enforced_before_calling():
    transport = Recorder([fixture_text("product_research")] * 3)
    vendor = Vendor(transport, Budget(2), live=False)
    vendor.call("product_research", request(marketplace="US", nodeIdPath="1", page=1))
    vendor.call("product_research", request(marketplace="US", nodeIdPath="1", page=2))
    with pytest.raises(BudgetExhausted):
        vendor.call("product_research", request(marketplace="US", nodeIdPath="1", page=3))
    assert len(transport.seen) == 2


def test_identical_calls_are_billed_once():
    transport = Recorder([fixture_text("product_research")])
    vendor = Vendor(transport, Budget(5), live=False)
    a = vendor.call("product_research", request(marketplace="US", nodeIdPath="1"))
    b = vendor.call("product_research", request(marketplace="US", nodeIdPath="1"))
    assert a.ok and b.status == "cached" and vendor.billable_calls == 1


def test_return_fields_injected_and_dropped_on_empty_reply():
    empty = json.dumps({"code": "OK", "message": "成功", "data": {"items": []}})
    transport = Recorder([empty, fixture_text("product_research")])
    vendor = Vendor(transport, Budget(5), live=False)
    reply = vendor.call("product_research", request(marketplace="US", nodeIdPath="1"))
    first, second = transport.seen
    assert "returnFields" in first[1]["request"]
    assert "returnFields" not in second[1]["request"]
    assert reply.ok and len(reply.rows) == 3 and vendor.billable_calls == 2


def test_vendor_error_envelope_is_rejected_not_raised():
    transport = Recorder([json.dumps({"code": "ERROR_PARAM", "message": "参数错误"})])
    reply = Vendor(transport, Budget(5), live=False).call("review", {"asin": "X"})
    assert reply.status == "rejected" and "参数错误" in reply.detail


def test_quota_message_stops_the_run():
    transport = Recorder([json.dumps({"code": "ERROR", "message": "积分不足，请充值"})])
    with pytest.raises(QuotaExhausted):
        Vendor(transport, Budget(5), live=False).call("review", {"asin": "X"})


def test_live_calls_refused_under_pytest():
    vendor = Vendor(Recorder([]), Budget(5), live=True)
    with pytest.raises(McpUnavailable):
        vendor.call("review", {"asin": "X"})


def test_argument_shape_follows_tool_schema():
    tools = [McpTool("asin_detail", "", {"properties": {"request": {"type": "object"}}}),
             McpTool("review", "", {"properties": {"asin": {}, "marketplace": {}}})]
    vendor = Vendor(Recorder([]), Budget(1), live=False, schema_loader=lambda: tools)
    assert vendor.args("asin_detail", nested=False, asin="A") == {"request": {"asin": "A"}}
    assert vendor.args("review", nested=True, asin="A") == {"asin": "A"}
    assert vendor.args("unknown", nested=True, asin="A", page=None) == {"request": {"asin": "A"}}


def test_rate_limit_waits_retries_and_refunds_budget():
    limited = json.dumps({"code": "ERROR", "message": "每分钟访问已达上限"})
    transport = Recorder([limited, limited, fixture_text("product_research")])
    vendor = Vendor(transport, Budget(2), live=False, rate_limit_wait=0)
    reply = vendor.call("product_research", request(marketplace="US", nodeIdPath="1"))
    assert reply.ok and len(transport.seen) == 3
    assert vendor.budget.used == 1 and vendor.billable_calls == 1  # 限流的两次不占预算


def test_monthly_list_that_failed_is_fetched_again(cfg):
    """往月名单某次调用被拒（参数错、服务出错）时不记成“这个月名单是空的”：下期接着补，补齐之前不补判。"""
    from radar import monthly_lists

    cfg["thresholds"]["hot"]["list_months"] = 2
    nodes = [{"path": "1:A", "name": "A"}, {"path": "1:B", "name": "B"}]
    disc = {"period": "202609", "nodes": nodes, "products": {}}
    state = {"monthly_lists": {"months": {"2026-09": {"1:A": {}, "1:B": {}}}}}
    rejected = json.dumps({"code": "ERROR_SERVER", "message": "服务繁忙", "data": None})
    transport = Recorder([rejected, fixture_text("product_research"), fixture_text("product_research")])
    vendor = Vendor(transport, Budget(10), live=False)
    assert monthly_lists.backfill(vendor, cfg, state, disc, 10) == 2
    assert set(state["monthly_lists"]["months"]["2026-08"]) == {"1:B"}
    assert not monthly_lists.complete(state, nodes, "202609", 2)
    vendor = Vendor(transport, Budget(10), live=False)  # 下一期
    assert monthly_lists.backfill(vendor, cfg, state, disc, 10) == 1
    assert monthly_lists.complete(state, nodes, "202609", 2) and state["monthly_lists"]["months"]["2026-08"]["1:A"]


def test_account_limit_stops_without_waiting_and_blocks_later_calls():
    """“接口访问次数已达上限”是账户额度（实测过一小时仍然拒绝），不是“每分钟访问已达上限”的限流：
    不等待重试，直接停；之后的调用不再发出去。"""
    limit = json.dumps({"code": "ERROR", "message": "接口访问次数已达上限"})
    transport = Recorder([limit, fixture_text("product_research")])
    vendor = Vendor(transport, Budget(5), live=False, rate_limit_wait=3600)
    with pytest.raises(QuotaExhausted):
        vendor.call("asin_prediction", {"asin": "X"})
    with pytest.raises(QuotaExhausted):
        vendor.call("product_research", request(marketplace="US", nodeIdPath="1"))
    assert len(transport.seen) == 1


def test_connection_errors_fail_one_call_until_the_service_looks_down(monkeypatch):
    """连不上（网络 / 5xx）：同一个调用等一会儿重试，还不行就当这一个调用失败（status="error"，由调用方跳过）；
    连续 max_transport_failures 个调用都连不上才抛 McpUnavailable 中止本期。401/403 是密钥问题，直接抛。"""
    from radar import vendor as vendor_mod

    monkeypatch.setattr(vendor_mod.time, "sleep", lambda s: None)

    def down(tool, arguments):
        raise McpUnavailable("Could not reach the MCP server")

    vendor = Vendor(down, Budget(100), live=False, max_transport_failures=3)
    assert vendor.call("asin_prediction", {"asin": "A"}).status == "error"
    assert vendor.call("asin_prediction", {"asin": "B"}).status == "error"
    with pytest.raises(McpUnavailable):
        vendor.call("asin_prediction", {"asin": "C"})

    replies = [McpUnavailable("x"), fixture_text("product_research")]

    def once(tool, arguments):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    assert Vendor(once, Budget(100), live=False).call("product_research", {"page": 1}).ok  # 重试一次就好了

    def bad_key(tool, arguments):
        raise McpUnavailable("401", status_code=401)

    with pytest.raises(McpUnavailable):
        Vendor(bad_key, Budget(100), live=False).call("review", {"asin": "X"})

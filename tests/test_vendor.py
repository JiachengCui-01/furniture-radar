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

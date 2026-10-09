import base64
import hashlib
import hmac
import json
import urllib.parse

import httpx

from radar.notify import dingtalk


def test_signature_matches_dingtalk_spec():
    url = dingtalk.signed_url("https://oapi.dingtalk.com/robot/send?access_token=abc", "SECxyz", 1700000000000)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    expected = base64.b64encode(hmac.new(b"SECxyz", b"1700000000000\nSECxyz", hashlib.sha256).digest()).decode()
    assert query["timestamp"] == ["1700000000000"] and query["sign"] == [expected]
    assert query["access_token"] == ["abc"]


def test_link_keeps_the_key_fragment():
    url = "https://me.github.io/radar/reports/2026-10-08.html#k=abc-_123"
    link = dingtalk.open_link(url, True)
    assert link.startswith("dingtalk://dingtalkclient/page/link?url=") and link.endswith("&pc_slide=false")
    inner = urllib.parse.parse_qs(urllib.parse.urlparse(link.replace("dingtalk://", "https://")).query)["url"][0]
    assert inner == url
    assert dingtalk.open_link(url, False) == url


def test_send_to_multiple_robots():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        ok = "token=good" in str(request.url)
        return httpx.Response(200, json={"errcode": 0 if ok else 310000, "errmsg": "ok" if ok else "sign not match"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    payload = dingtalk.action_card("标题", "正文", "https://x/r.html#k=1")
    results = dingtalk.send(["https://oapi.dingtalk.com/robot/send?token=good",
                             "https://oapi.dingtalk.com/robot/send?token=bad"], ["S1", "S2"], payload, client=client)
    assert results == [True, False]
    body = json.loads(seen[0].content)
    assert body["msgtype"] == "actionCard" and body["actionCard"]["singleTitle"] == "查看完整报告"
    assert "sign=" in str(seen[0].url) and "timestamp=" in str(seen[0].url)


def test_each_group_can_be_its_own_secret():
    """新增钉钉群只要新建一个 DINGTALK_ROBOT_xxx，原来的 DINGTALK_WEBHOOK/SECRET 不用动。"""
    from radar.config import dingtalk_robots

    env = {
        "DINGTALK_WEBHOOK": "https://x/a", "DINGTALK_SECRET": "SECa",
        "DINGTALK_ROBOT_1": "https://x/b,SECb",
        "DINGTALK_ROBOT_2": "https://x/c",           # 关键词方式的机器人，只有 webhook
        "DINGTALK_ROBOT_3": "https://x/a SECa",      # 与 DINGTALK_WEBHOOK 重复，只推一次
        "DINGTALK_ROBOT_4": "",                      # 工作流里没建的 Secret 是空字符串
        "DINGTALK_ROBOT_LOCAL": "https://x/d\nSECd",  # 本地 .env 可以用任意名字
    }
    assert dingtalk_robots(env) == (["https://x/a", "https://x/b", "https://x/c", "https://x/d"],
                                    ["SECa", "SECb", "", "SECd"])
    legacy = {"DINGTALK_WEBHOOK": "https://x/a,https://x/b", "DINGTALK_SECRET": "SECa"}
    assert dingtalk_robots(legacy) == (["https://x/a", "https://x/b"], ["SECa", ""])

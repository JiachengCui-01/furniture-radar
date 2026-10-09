"""钉钉自定义群机器人：加签（HMAC-SHA256）+ ActionCard 卡片消息。

消息正文直接写出重点提醒，即使报告网页在国内打开较慢，看消息也能知道本期结论；
按钮“查看完整报告”打开带 #k= 密钥的加密报告。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse

import httpx

from .. import log
from ..analyze import display_name
from ..report.render import growth_text
from ..narrative import top_design_labels


def signed_url(webhook: str, secret: str, timestamp_ms: int | None = None) -> str:
    if not secret:
        return webhook
    ts = str(timestamp_ms if timestamp_ms is not None else int(time.time() * 1000))
    digest = hmac.new(secret.encode("utf-8"), f"{ts}\n{secret}".encode("utf-8"), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest).decode("ascii"))
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={ts}&sign={sign}"


def open_link(url: str, open_in_browser: bool) -> str:
    """PC 端钉钉默认在侧边栏打开链接；这个 scheme 让它用系统浏览器打开，手机端仍在钉钉内打开。
    整个 URL（含 #k=）都做了编码，片段不会丢。"""
    if not open_in_browser:
        return url
    return "dingtalk://dingtalkclient/page/link?url=" + urllib.parse.quote(url, safe="") + "&pc_slide=false"


def action_card(title: str, text: str, url: str, *, button: str = "查看完整报告",
                open_in_browser: bool = True) -> dict:
    return {
        "msgtype": "actionCard",
        "actionCard": {
            "title": title,
            "text": text,
            "btnOrientation": "0",
            "singleTitle": button,
            "singleURL": open_link(url, open_in_browser),
        },
    }


def text_message(content: str, at_all: bool = False) -> dict:
    return {"msgtype": "text", "text": {"content": content}, "at": {"isAtAll": at_all}}


def send(webhooks: list[str], secrets: list[str], payload: dict, *,
         client: httpx.Client | None = None) -> list[bool]:
    results = []
    own = client is None
    client = client or httpx.Client(timeout=15)
    try:
        for i, webhook in enumerate(webhooks):
            secret = secrets[i] if i < len(secrets) else ""
            try:
                response = client.post(signed_url(webhook, secret), json=payload)
                data = response.json()
                ok = response.status_code == 200 and data.get("errcode") == 0
                if not ok:
                    log.warn(f"钉钉第 {i + 1} 个机器人发送失败：errcode={data.get('errcode')} {data.get('errmsg')}")
                results.append(ok)
            except Exception as exc:  # noqa: BLE001
                log.warn(f"钉钉第 {i + 1} 个机器人发送异常：{type(exc).__name__}")
                results.append(False)
    finally:
        if own:
            client.close()
    return results


def _short(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def build_text(ctx: dict, max_items: int = 3) -> str:
    """ActionCard 正文（钉钉 markdown 子集）。"""
    sec = ctx["sections"]
    c = sec["counts"]
    lines = [
        f"### {ctx['title']} · {ctx['report_id']}",
        f"追踪 {c['total']} 个家具 ASIN · 日数据截至 {ctx['as_of']}",
        "",
        f"**🚀 突然爆火 {c['surge']}　🌱 潜力 {c['potential']}　🔥 持续热销 {c['hot']}　⚠️ 异常 {c['fake']}**",
        "",
    ]

    def item_line(item: dict, extra: str) -> str:
        node = item.get("node_cn") or item.get("node_name") or ""
        return f"- {_short(display_name(item['brand'], item['title']), 36)}（{node}）{extra}"

    if sec["surge"]:
        lines.append("**🚀 突然爆火**")
        for item in sec["surge"][:max_items]:
            s = item["surge"]
            ratio = f"×{s['sales_ratio']:.1f}" if s.get("sales_ratio") else "新起量"
            tags = "·".join(t for t in item["tags"][:2])
            lines.append(item_line(item, f" 日均{s['base_avg']:.0f}→{s['recent_avg']:.0f}（{ratio}）{tags}"))
        lines.append("")
    if sec["potential"]:
        lines.append("**🌱 潜力产品**")
        for item in sec["potential"][:max_items]:
            lines.append(item_line(item, " " + growth_text(item)))
        lines.append("")
    high = [i for i in sec["fake"] if i["fake"]["level"] == "high"] or sec["fake"]
    if high:
        lines.append("**⚠️ 假爆火 / 异常提醒**")
        for item in high[:max_items]:
            signal = item["fake"]["signals"][0]["text"] if item["fake"]["signals"] else ""
            lines.append(item_line(item, f" 异常分{item['fake']['score']}：{_short(signal, 34)}"))
        lines.append("")
    traits = ctx.get("traits") or {}
    looks = top_design_labels(traits)
    mats = [m for m in (traits.get("materials") or {}).get("rows", [])
            if m["count"] and m["label"] not in ("未注明", "其他")]
    mats.sort(key=lambda m: -m["share"])
    if mats:
        lines.append("**🪵 爆火/上升商品主材质**：" + "、".join(f"{m['label']} {m['share']:.0%}" for m in mats[:3]))
    if looks:
        lines.append(f"**🎨 爆火外观特点**：{'、'.join(looks)}")
    if mats or looks:
        lines.append("")
    diff = ctx["diff"]
    if not diff.get("first"):
        lines.append(f"较上期：新增爆火 {len(diff['new']['surge'])}、新增潜力 {len(diff['new']['potential'])}、"
                     f"新增异常 {len(diff['new']['fake'])}、爆火回落 {len(diff.get('cooled', []))}")
    return "\n".join(lines)


def failure_text(title: str, stage: str, run_url: str = "") -> str:
    tail = f"\n运行日志：{run_url}" if run_url else ""
    return f"【{title}】本期自动运行失败（{stage}），报告未更新，请检查 GitHub Actions。{tail}"

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
from ..detect.directions import headline, money_text
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


DATA_TAGS = ("变体变多", "名单和日数据对不上", "最近 28 天有")  # analyze.data_tags 打的“数据可能有问题”的标签


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
        # 按本期测到的修订幅度校正最近几天后不成立的判定：卡片上有完整说明，这里只提醒一句
        caveat = f"（最近几天的数待修订，校正后不算{'、'.join(item['revision_lost'])}）" if item.get("revision_lost") else ""
        if any(t.startswith(DATA_TAGS) for t in item.get("tags") or []):  # 变体变多、名单和日数据对不上、日数据有缺
            caveat += "（数据可能有问题，见报告卡片）"
        return f"- {_short(display_name(item['brand'], item['title']), 36)}（{node}）{extra}{caveat}"

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
    high =[i for i in sec["fake"] if i["fake"]["level"] == "high"] or sec["fake"]
    if high:
        lines.append("**⚠️ 异常信号**")
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
        lines.append("**🪵 增长商品主材质**：" + "、".join(f"{m['label']} {m['share']:.0%}" for m in mats[:3]))
    if looks:
        lines.append(f"**🎨 增长商品外观特点**：{'、'.join(looks)}")
    if mats or looks:
        lines.append("")
    if traits.get("directions"):
        dirs = traits["directions"]
        top = dirs[: int(ctx["cfg"]["thresholds"]["directions"].get("summary_max") or 5)]
        # 报告里够格的子类目全部列出，这里只写增量最大的几个
        lines.append("**🛠 开发方向**" + (f"（增量最大的 {len(top)} 个，共 {len(dirs)} 个见报告）" if len(dirs) > len(top) else ""))
        lines += [f"- {headline(d)}" + (f"（月增量约 {money_text(d['sales']['increment'])}）" if d.get("sales") else "")
                  for d in top]
        lines.append("")
    diff = ctx["diff"]
    if not diff.get("first"):
        lines.append(f"较上期：依旧爆火 {len(diff['still'])}、新增爆火 {len(diff['new']['surge'])}、"
                     f"新增潜力 {len(diff['new']['potential'])}、"
                     f"新增异常 {len(diff['new']['fake'])}、不再爆火 {len(diff.get('cooled', []))}")
    return "\n".join(lines)


def failure_text(title: str, stage: str, run_url: str = "", *, published: bool = False, saved: bool = False,
                 reason: str = "") -> str:
    """失败告警：按哪一步出的错说清楚报告到底更新了没有（以前一律写“报告未更新”，钉钉只是部分群没推送成功也这么说）。
    published：报告生成了；saved：没出报告，但中止前拉到的数据已保存。"""
    tail = f"\n运行日志：{run_url}" if run_url else ""
    if stage == "钉钉推送":
        what = "报告已更新，网站上能打开，但有钉钉群没推送成功，请检查这个群的机器人配置"
    elif stage == "网站部署":
        what = ("报告已生成并保存，但网站没部署成功，钉钉里的链接可能打不开；"
                "可在 GitHub Actions 里手动运行一次（mode 选 rerender）重新部署")
    elif published:
        what = "报告生成了，但没能保存到 site 分支（推送被拒），本期没有发布；下一次定时检查会重新运行"
    elif saved and reason == "quota":
        what = ("卖家精灵账户的接口调用次数用完了（卖家精灵返回“接口访问次数已达上限”，不是程序出错），本期没有生成报告。"
                "以前的数据和报告都在；额度恢复前每天的定时检查只试 1 次、7 天内不再重复提醒，恢复后自动出报告。"
                "请在卖家精灵后台确认套餐的调用额度和重置时间（和 AI工作台 共用一个 key）")
    elif saved and reason == "outage":
        what = "卖家精灵一直连不上，本期中途中止，没有生成报告；已拉到的数据和调用次数已保存，下一次定时检查会接着运行"
    elif saved and reason == "crash":
        what = ("这期拉完数据以后程序出错，没有生成报告；已拉到的数据和调用次数已保存，下一次定时检查接着用已拉到的日数据，"
                "但同样的错误会每天重复、每次都要花一些调用（重翻全市场名单），请尽快看运行日志里的出错位置")
    elif saved and reason == "thin":
        what = "这期拉到的日数据太少（不到计划的六成），没有生成报告，免得发一份旧数据的报告；已拉到的数据已保存，下一次定时检查会重新运行"
    elif saved:
        what = ("本期中途中止（卖家精灵额度用完、一直连不上，或拉到的日数据太少），没有生成报告；"
                "已拉到的数据和调用次数已保存，下一次定时检查会接着运行")
    else:
        what = "本期运行出错，没有生成报告"
    return f"【{title}】{what}。请检查 GitHub Actions。{tail}"

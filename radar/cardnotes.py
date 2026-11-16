"""商品卡片上大模型写的两句话（2026-11-16 用户：卡片要用文字说出这个商品和别的商品比为什么能脱颖而出，外观和功能都要说；
规则尽量靠大模型和数据，现有的套规则作为辅助）。

  对比 = 它的外观、功能和同子类目的同类比有什么不同、哪里更突出；
  原因 = 它可能为什么在涨（持续热销写为什么卖得稳）。
给大模型的只有系统已经算好的事实（detect/insights.py 的外观功能对比、可能原因、市场位置，主图识别的外观要点，走势数字），
不给它自己找数据。写回来以后逐条核对（check）：里面的每个数字、每个特征名都要在这个商品的事实里出现过，不超长、不写绝对化的话；
对不上的不用，这张卡片照常显示规则算的句子。按事实的指纹缓存（state["card_notes"]），事实没变的下次不再调用。
一次请求写 batch 个商品；连续 MAX_FAILED 次请求失败（接口挂了、余额不足）就不再写，剩下的用规则句子。"""
from __future__ import annotations

import hashlib
import json
import re

import httpx

from . import log
from .detect import design
from .detect.insights import SAY

STATE_KEY = "card_notes"
MAX_FAILED = 3
FORBIDDEN = ("一定", "保证", "必然", "肯定", "绝对", "100%", "秘诀", "稳赚", "最好的")
VOCAB = sorted({label for dims in design.LEXICON.values() for label, _rx in dims} | set(design.LABEL_DIM), key=len, reverse=True)
_NUM = re.compile(r"[$＄]?[0-9]+(?:[.][0-9]+)?[%％]?")

SYSTEM_PROMPT = """你是亚马逊美国站家具选品顾问。下面是一组商品（JSON），每个商品都有系统已经算好的事实：
它的外观和功能跟同子类目的同类比（同类多是什么、它不同在哪，括号里是同类里有这个特征的比例）、可能原因、市场位置、主图外观要点、走势。
为每个商品写两句中文：
1. 对比：一句话说清它的外观和功能跟同类比哪里不一样、哪里更突出（同类多是什么，它多了什么）。外观和功能都要说到（数据里只有一边的就说一边）。
2. 原因：一句话说它可能为什么在涨（板块是持续热销的写为什么卖得稳），从事实里挑最有说服力的一两点。
要求：
- 只用事实里出现过的特征名和数字，数字照抄事实里的写法，不编造事实里没有的卖点、数字、人群和场景；
- 不写“一定”“保证”“绝对”这类话，原因用“可能”“靠……”这类说法；
- 写得像跟同事说话一样自然、具体，比例和数字只在最关键的地方写一个，不要把括号里的比例全抄一遍；
- 和同类差不多的就直说差不多，再说它靠什么（价格、排名、同类爆品的共性……），不要硬找不同；
- 对比不超过 70 个字，原因不超过 45 个字；不要重复商品名。
只输出一个 JSON 对象，键是商品的 id：{"<id>": {"对比": "...", "原因": "..."}, ...}"""


def enabled(cfg: dict, api_key: str) -> bool:
    c = (cfg.get("report") or {}).get("card_notes") or {}
    flag = str((cfg.get("llm") or {}).get("enabled", "auto")).lower()
    return bool(api_key) and bool(c.get("enabled", True)) and flag not in ("false", "0", "no", "off")


def facts_of(item: dict, kind_cn: str) -> dict | None:
    """给大模型看的这个商品的事实（全部来自系统算好的结果）；没有和同类比的结果时返回 None（不写）。"""
    ins = item.get("insight") or {}
    if not ins.get("compare"):  # 没有外观、功能的对比（看过主图的同类太少）：说不出它哪里不一样，用规则句子
        return None
    trend = []
    for key, name in (("recent_avg7", "近 7 天日均"), ("recent_avg28", "近 28 天日均"), ("prev_avg28", "之前 28 天日均")):
        v = item.get(key)
        if v is not None:
            trend.append(f"{name} {round(v) if v >= 10 else round(v, 1):g} 件")
    return {"id": item["asin"], "子类目": item.get("node_cn") or item.get("node_name") or "", "板块": kind_cn,
            "外观要点": item.get("look") or "", "外观和功能和同类比": [t for t, _tip in ins.get("compare") or []],
            "可能原因": [t for t, _tip in ins.get("reasons") or []], "市场位置": [t for t, _tip in ins.get("findings") or []],
            "走势": "，".join(trend)}


def fingerprint(facts: dict) -> str:
    return hashlib.sha1(json.dumps(facts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _norm(text: str) -> str:
    """比数字用：去掉空格、千分位逗号，全角 ％ ＄ 换成半角。"""
    return text.replace(" ", "").replace(",", "").replace("％", "%").replace("＄", "$")


def check(note: dict | None, facts: dict, cfg: dict) -> tuple[dict | None, str | None]:
    """核对大模型写的（对比、原因）：返回 (能用的 {"compare", "why"}, 不能用的原因)。"""
    c = (cfg.get("report") or {}).get("card_notes") or {}
    if not isinstance(note, dict):
        return None, "格式不对"
    compare, why = str(note.get("对比") or "").strip(), str(note.get("原因") or "").strip()
    if not compare or not why:
        return None, "缺句子"
    if len(compare) > int(c.get("compare_max", 80)) or len(why) > int(c.get("why_max", 55)):
        return None, "太长"
    text = compare + why
    if any(w in text for w in FORBIDDEN):
        return None, "绝对化"
    source = json.dumps(facts, ensure_ascii=False)
    plain = _norm(source)
    for token in _NUM.findall(_norm(text)):
        # 百分比要对上事实里的百分比、金额要对上金额（“便宜 45%”不能拿事实里的“$45”凑数），其余数字在事实里出现过就行
        bare = token.strip("$%")
        if token.endswith("%") or token.startswith("$"):
            ok = token in plain
        else:
            ok = re.search(r"(?<![0-9.])" + re.escape(bare) + r"(?![0-9])", plain) is not None
        if not ok:
            return None, f"数字 {token} 不在事实里"
    rest = text
    for label in VOCAB:  # 先长后短：“L 形/U 形”“带储物/抽屉”不会被拆成短词
        if label in rest:
            if label not in source and SAY.get(label, label) not in source:  # “带储物/抽屉”和事实里的“储物/抽屉”是同一个
                return None, f"特征 {label} 不在事实里"
            rest = rest.replace(label, "")
    return {"compare": compare, "why": why}, None


def _ask(client: httpx.Client, cfg: dict, api_key: str, batch: list[dict]) -> dict:
    llm = cfg["llm"]
    body = {"model": llm["model"], "temperature": 0.3, "max_tokens": 220 * len(batch) + 200,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}],
            **(llm.get("extra_body") or {})}
    response = client.post(llm["base_url"].rstrip("/") + "/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                           timeout=float(llm.get("timeout_seconds", 120)))
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"] or ""
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1]) if start >= 0 and end > start else {}


def write(items: list[tuple[dict, str]], state: dict, cfg: dict, api_key: str, *,
          client: httpx.Client | None = None) -> dict:
    """items：[(商品, 板块中文名)]。写好、核对过的放进 item["insight"]["note"]。返回统计。"""
    c = (cfg.get("report") or {}).get("card_notes") or {}
    cache = state.setdefault(STATE_KEY, {})
    stats = {"cached": 0, "written": 0, "rejected": 0, "skipped": 0}
    todo: list[tuple[dict, dict, str]] = []
    keep = set()
    for item, kind_cn in items:
        facts = facts_of(item, kind_cn)
        if facts is None:
            continue
        key = fingerprint(facts)
        keep.add(item["asin"])
        hit = cache.get(item["asin"])
        if hit and hit.get("key") == key and hit.get("note"):
            item["insight"]["note"] = hit["note"]
            stats["cached"] += 1
        else:
            todo.append((item, facts, key))
    for asin in [a for a in cache if a not in keep]:  # 不再列出的商品不留
        del cache[asin]
    if not todo or not enabled(cfg, api_key):
        stats["skipped"] = len(todo)
        return stats
    size = max(1, int(c.get("batch", 10)))
    own = client is None
    client = client or httpx.Client()
    failed = 0
    try:
        for start in range(0, len(todo), size):
            chunk = todo[start:start + size]
            try:
                answer = _ask(client, cfg, api_key, [facts for _i, facts, _k in chunk])
                failed = 0
            except Exception as exc:  # noqa: BLE001 — 写不出来就用规则句子
                failed += 1
                stats["skipped"] += len(chunk)
                log.warn(f"卡片文字：大模型这批没写成（{type(exc).__name__}），用规则句子")
                if failed >= MAX_FAILED:
                    stats["skipped"] += len(todo) - start - len(chunk)
                    log.warn("卡片文字：连续几批失败，剩下的都用规则句子")
                    break
                continue
            for item, facts, key in chunk:
                note, why_not = check(answer.get(item["asin"]), facts, cfg)
                if note:
                    item["insight"]["note"] = note
                    cache[item["asin"]] = {"key": key, "note": note}
                    stats["written"] += 1
                else:
                    stats["rejected"] += 1
                    log.detail(f"卡片文字没通过核对（{why_not}）：{item['asin']}")
    finally:
        if own:
            client.close()
    log.info(f"卡片文字：大模型写了 {stats['written']} 张、沿用 {stats['cached']} 张；没通过核对 {stats['rejected']} 张、"
             f"没写成 {stats['skipped']} 张（这些用规则句子）")
    return stats

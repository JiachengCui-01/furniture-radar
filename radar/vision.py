"""商品主图外观识别（可选）：用视觉大模型读主图，提取风格、材质、造型、工艺、颜色、看得见的功能和一句外观要点。

默认用 DeepSeek 的视觉模型（与 AI工作台 相同），每张图约 300 tokens。
结果按“ASIN + 图片编号 + 词表版本”缓存在加密状态里，图片和词表都不变就不会重复调用（只是尺寸不同的地址算同一张图）。
识别失败不影响报告，只是少了“主图识别”这一块；单张失败会重试，还不行就跳过这张，不影响其他图片。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections import Counter

import httpx

from . import log
from .detect.design import DESIGN_DIMENSIONS, LEXICON, VISION_FUNCTIONS, known_brand_key


def _vocab() -> str:
    def labels(dim: str) -> list[str]:
        if dim == "功能卖点":
            return list(VISION_FUNCTIONS)
        return [label for label, _ in LEXICON[dim]]

    return "\n".join(f"{dim}：{'、'.join(labels(dim))}" for dim in DESIGN_DIMENSIONS)


PROMPT = f"""你是家具设计分析师。看这张亚马逊家具商品主图，只输出一个 JSON 对象，不要任何其他文字：
{{"风格":[],"材质":[],"造型":[],"工艺":[],"颜色":[],"主色":"","功能卖点":[],"外观要点":""}}
要求：
- 每个数组 0~4 项，优先使用下面词表里的说法，词表里没有的再用 2~6 个字的中文自拟；
- “颜色”按占画面面积从大到小排；“主色”只写一个：这件家具本体占面积最大的颜色（拉手、金属腿、床垫、摆件这些小面积的颜色不算），用颜色词表里的说法；
- 只写图片里看得出来的，看不清就留空，不要猜；“功能卖点”只写图里看得见的（抽屉、灯带、镜子、轮子、挂钩、插座口等）；
- “外观要点”用一句话（不超过 40 字）说清这件家具最抓眼的外观/工艺设计。
词表：
{_vocab()}"""


# 词表版本：词库（design.LEXICON）一改，缓存的识别结果就按新词表重看一遍
VOCAB_VERSION = hashlib.sha1(PROMPT.encode("utf-8")).hexdigest()[:8]


def small_image(url: str, size: int = 400) -> str:
    """亚马逊图片地址里的尺寸段换成 SL400，减少下载量和 token。"""
    return re.sub(r"\._[^/]*_\.(jpg|jpeg|png|webp)$", rf"._AC_SL{size}_.\1", url or "", flags=re.I)


_IMAGE_ID = re.compile(r"/images/I/([^./]+)")


def same_image(a: str | None, b: str | None) -> bool:
    """两个亚马逊图片地址是不是同一张图：比 /images/I/ 后面的图片编号，不管尺寸段（._AC_US200_ / ._AC_US600_）和域名。
    同一个 ASIN，月度名单和全市场名单 / 日数据给的地址常常只是尺寸不同（11-02 那期 102 个里 53 个），以前按整个地址比，
    每期都当成新图重看一遍。认不出编号的按整个地址比。"""
    ia, ib = _IMAGE_ID.search(a or ""), _IMAGE_ID.search(b or "")
    if ia and ib:
        return ia.group(1) == ib.group(1)
    return (a or "") == (b or "")


def _parse(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    tags = {}
    for dim in DESIGN_DIMENSIONS:
        values = data.get(dim) or []
        if isinstance(values, str):
            values = [values]
        tags[dim] = [str(v).strip() for v in values if str(v).strip()][:4]
    main = str(data.get("主色") or "").strip()
    out = {"tags": tags, "summary": str(data.get("外观要点") or "").strip()[:60]}
    if main:
        out["main_color"] = main[:8]
    return out


def _describe(client: httpx.Client, cfg: dict, api_key: str, image_url: str) -> dict | None:
    llm = cfg["llm"]
    image = client.get(small_image(image_url), timeout=20, follow_redirects=True)
    image.raise_for_status()
    mime = image.headers.get("content-type", "image/jpeg").split(";")[0]
    data_uri = f"data:{mime};base64,{base64.b64encode(image.content).decode('ascii')}"
    body = {
        "model": llm.get("vision_model") or "deepseek-v4-flash-vision-exp",
        "max_tokens": 400,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]}],
        **(llm.get("extra_body") or {}),
    }
    response = client.post(llm["base_url"].rstrip("/") + "/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {api_key}"},
                           timeout=float(llm.get("timeout_seconds", 120)))
    response.raise_for_status()
    return _parse(response.json()["choices"][0]["message"]["content"])


def enabled(cfg: dict, api_key: str) -> bool:
    flag = str(cfg["llm"].get("vision", "auto")).lower()
    return bool(api_key) and flag not in ("false", "0", "no", "off")


RETRY_WAITS = (2.0, 5.0)  # 单张失败后等几秒再试（共试 len+1 次）
MAX_FAILED_IN_A_ROW = 3     # 连续这么多张都失败，多半是接口挂了，本期不再识别


def _transient(exc: Exception) -> bool:
    """网络中断、超时、限流和服务端错误可以重试；鉴权失败、请求格式错误重试也没用。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def _describe_with_retry(client: httpx.Client, cfg: dict, api_key: str, image_url: str) -> dict | None:
    for wait in (*RETRY_WAITS, None):
        try:
            return _describe(client, cfg, api_key, image_url)
        except Exception as exc:  # noqa: BLE001
            if wait is None or not _transient(exc):
                raise
            time.sleep(wait)
    return None


EXTRA_KEY = "vision_extra"  # 不在追踪里的商品（开发方向对比用的同子类目头部商品）的识别结果，按 ASIN 存


def _color_names(label: str) -> list[str]:
    """颜色标签在外观要点里可能的写法：“灰褐/做旧灰” → 灰褐、做旧灰；“原木色” → 原木色、原木、木色。"""
    out = []
    for part in re.split(r"[/、]", label):
        part = part.strip()
        if part:
            out.append(part)
            if part.endswith("色") and len(part) > 2:
                out += [part[:-1], part[-2:]]
    return out


def main_color(v: dict) -> str | None:
    """主色：识别结果里写了“主色”（本体占面积最大的颜色）就用它；以前的结果没有这一项，取外观要点里最先说到的那个颜色标签，
    都没说到的取第一个。报告里“颜色”只按主色归类：原木色柜子配黑色滑轨的不算黑色（11-06 用户反馈：点“黑色”出来原木色的柜子）。"""
    colors = list(dict.fromkeys((v.get("tags") or {}).get("颜色") or []))
    if v.get("main_color"):
        return v["main_color"]
    if len(colors) <= 1:
        return colors[0] if colors else None
    look = v.get("summary") or ""
    hits = [(min(look.find(n) for n in _color_names(c) if n in look), k, c)
            for k, c in enumerate(colors) if any(n in look for n in _color_names(c))]
    return min(hits)[2] if hits else colors[0]


MAIN_ONLY = ("颜色",)  # 只按最主要的那一个归类的维度（一件家具只有一个主体颜色；造型、工艺、功能可以同时有几个）


def normalized(v: dict) -> dict:
    """识别结果里 MAIN_ONLY 的维度只留主色（main_color），其余照旧。报告、统计、开发方向都读这个（of、summarize）。"""
    if not v or not v.get("tags"):
        return v or {}
    main = main_color(v)
    return {**v, "tags": {**v["tags"], "颜色": [main] if main else []}}


def of(state: dict, asin: str) -> dict:
    """一个商品的主图识别结果（颜色只留主色，见 normalized）：追踪商品存在 state["asins"] 里，其余存在 state[EXTRA_KEY] 里。没有返回 {}。"""
    rec = (state.get("asins") or {}).get(asin) or {}
    return normalized(rec.get("vision") or ((state.get(EXTRA_KEY) or {}).get(asin) or {}).get("vision") or {})


def prune_extra(state: dict, keep: set[str]) -> None:
    """只留 keep 里的（当前月度名单里的商品），其余删掉，免得状态越积越大。"""
    extra = state.get(EXTRA_KEY)
    if extra:
        for asin in [a for a in extra if a not in keep]:
            del extra[asin]


def tag_items(items: list[dict], state: dict, cfg: dict, api_key: str, *,
              client: httpx.Client | None = None, untracked: bool = False) -> int:
    """给 items 的主图打标签，写入 state["asins"][asin]["vision"]；untracked=True 时，
    不在追踪里的商品写入 state[EXTRA_KEY][asin]["vision"]（否则跳过）。返回新识别的张数。
    单张失败先重试（网络中断、超时、限流、服务端错误），还不行就跳过这张、接着识别下一张；
    鉴权失败或连续 MAX_FAILED_IN_A_ROW 张失败才停（实测：一次连接中断就停掉了整批，56 个增长商品有 19 个没识别）。"""
    if not enabled(cfg, api_key):
        return 0
    own = client is None
    client = client or httpx.Client()
    done = failed = in_a_row = 0
    try:
        for item in items:
            url = item.get("image")
            rec = state["asins"].get(item["asin"])
            if url and rec is None and untracked:
                rec = state.setdefault(EXTRA_KEY, {}).setdefault(item["asin"], {})
            if not url or rec is None:
                continue
            cached = rec.get("vision")
            if cached and same_image(cached.get("image"), url) and cached.get("vocab") == VOCAB_VERSION:
                continue
            try:
                result = _describe_with_retry(client, cfg, api_key, url)
            except Exception as exc:  # noqa: BLE001 — 识别失败不影响报告
                failed += 1
                in_a_row += 1
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
                    log.warn("主图识别失败：密钥无效或没有权限，本期跳过剩余图片")
                    break
                if in_a_row >= MAX_FAILED_IN_A_ROW:
                    log.warn(f"主图识别连续 {in_a_row} 张失败（{type(exc).__name__}），本期跳过剩余图片")
                    break
                continue
            in_a_row = 0
            if result:
                rec["vision"] = {"image": url, "vocab": VOCAB_VERSION, **result}
                done += 1
    finally:
        if own:
            client.close()
    if failed:
        log.warn(f"主图识别：{failed} 张失败已跳过")
    return done


def summarize(focus: list[dict], reference: list[dict], state: dict, *, per_dim: int = 12,
              min_gap: float = 0.2) -> dict:
    """汇总主图标签：每个维度里，增长商品最常见的特征，并和持续热销对照组比较。
    每个商品自己的“外观要点”直接显示在商品卡片上（pipeline 里写到 item["look"]）。"""
    def tags_of(items):
        out = []
        for item in items:
            vision = (state["asins"].get(item["asin"]) or {}).get("vision")
            if vision:
                out.append((item, normalized(vision)))
        return out

    f_tagged, r_tagged = tags_of(focus), tags_of(reference)
    result = {"n_focus": len(f_tagged), "n_reference": len(r_tagged), "dimensions": {}}
    if not f_tagged:
        return result
    for dim in DESIGN_DIMENSIONS:
        cf = Counter(t for _, v in f_tagged for t in set(v["tags"].get(dim, [])))
        cr = Counter(t for _, v in r_tagged for t in set(v["tags"].get(dim, [])))
        rows = []
        for label, n in cf.most_common():
            if n < 2 and len(f_tagged) > 3:
                continue
            share = n / len(f_tagged)
            ref_share = cr.get(label, 0) / len(r_tagged) if r_tagged else None
            brands = len({key for item, v in f_tagged if label in set(v["tags"].get(dim, []))
                          and (key := known_brand_key(item)) is not None})
            rows.append({"label": label, "focus": n, "focus_share": round(share, 3), "brands": brands,
                         "reference": cr.get(label, 0),
                         "reference_share": None if ref_share is None else round(ref_share, 3),
                         # 增长商品里比持续热销对照组多 min_gap 以上的，才算增长商品特有的外观（留浮点误差余量）
                         "distinct": ref_share is not None and share - ref_share >= min_gap - 1e-9})
        # 有对照组时，按“比持续热销多出多少”排序，通用特征（如“现代”）排到后面
        rows.sort(key=lambda r: (-(r["focus_share"] - (r["reference_share"] or 0)) if r_tagged else 0,
                                 -r["focus"]))
        if rows:
            result["dimensions"][dim] = rows[:per_dim]
    return result

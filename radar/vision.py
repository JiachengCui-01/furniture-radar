"""商品主图外观识别（可选）：用视觉大模型读主图，提取风格、材质、造型、工艺、颜色和一句外观要点。

默认用 DeepSeek 的视觉模型（与 AI工作台 相同），每张图约 300 tokens。
结果按“ASIN + 图片地址”缓存在加密状态里，图片不变就不会重复调用。
识别失败不影响报告，只是少了“主图识别”这一块。
"""
from __future__ import annotations

import base64
import json
import re
from collections import Counter

import httpx

from . import log
from .detect.design import DESIGN_DIMENSIONS, LEXICON


def _vocab() -> str:
    return "\n".join(f"{dim}：{'、'.join(label for label, _ in LEXICON[dim])}" for dim in DESIGN_DIMENSIONS)


PROMPT = f"""你是家具设计分析师。看这张亚马逊家具商品主图，只输出一个 JSON 对象，不要任何其他文字：
{{"风格":[],"材质":[],"造型":[],"工艺":[],"颜色":[],"外观要点":""}}
要求：
- 每个数组 0~3 项，优先使用下面词表里的说法，词表里没有的再用 2~6 个字的中文自拟；
- 只写图片里看得出来的，看不清就留空，不要猜；
- “外观要点”用一句话（不超过 40 字）说清这件家具最抓眼的外观/工艺设计。
词表：
{_vocab()}"""


def small_image(url: str, size: int = 400) -> str:
    """亚马逊图片地址里的尺寸段换成 SL400，减少下载量和 token。"""
    return re.sub(r"\._[^/]*_\.(jpg|jpeg|png|webp)$", rf"._AC_SL{size}_.\1", url or "", flags=re.I)


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
        tags[dim] = [str(v).strip() for v in values if str(v).strip()][:3]
    return {"tags": tags, "summary": str(data.get("外观要点") or "").strip()[:60]}


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


def tag_items(items: list[dict], state: dict, cfg: dict, api_key: str, *,
              client: httpx.Client | None = None) -> int:
    """给 items 的主图打标签，写入 state["asins"][asin]["vision"]。返回新识别的张数。"""
    if not enabled(cfg, api_key):
        return 0
    own = client is None
    client = client or httpx.Client()
    done = 0
    try:
        for item in items:
            url = item.get("image")
            rec = state["asins"].get(item["asin"])
            if not url or rec is None:
                continue
            cached = rec.get("vision")
            if cached and cached.get("image") == url:
                continue
            try:
                result = _describe(client, cfg, api_key, url)
            except Exception as exc:  # noqa: BLE001 — 识别失败不影响报告
                log.warn(f"主图识别失败，本期跳过剩余图片：{type(exc).__name__}")
                break
            if result:
                rec["vision"] = {"image": url, **result}
                done += 1
    finally:
        if own:
            client.close()
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
                out.append((item, vision))
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
            rows.append({"label": label, "focus": n, "focus_share": round(share, 3),
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

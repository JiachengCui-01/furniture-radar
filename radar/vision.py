"""商品主图外观识别（可选）：用视觉大模型读主图，提取风格、材质、造型、工艺、颜色、看得见的功能和一句外观要点。

默认用 DeepSeek 的视觉模型（与 AI工作台 相同），每张图约 1500 tokens（提示词里带每个特征的定义）。
结果按“ASIN + 图片编号 + 词表版本”缓存在加密状态里，图片和词表都不变就不会重复调用（只是尺寸不同的地址算同一张图）。
识别失败不影响报告，只是少了“主图识别”这一块；单张失败会重试，还不行就跳过这张，不影响其他图片。

2026-11-07 起（PROTOCOL 2）：提示词给每个特征一句定义和“不算”的情况（design.DEFINITIONS），造型 / 工艺 / 功能要写出在哪个部位看到，
说不出部位的不算；只能用词表里的说法。以前的识别结果（只给词表）抽查误标很多，见 normalized 和 design.LEGACY_TRUSTED。
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
from .detect import design
from .detect.design import DESIGN_DIMENSIONS, known_brand_key

PROTOCOL = 2  # 识别结果的口径：1 = 只给词表（2026-11-07 以前），2 = 带定义、要写部位
EVIDENCE_DIMENSIONS = ("造型", "工艺", "功能卖点")  # 要写出在家具哪个部位看到的维度


def _vocab() -> str:
    def line(label: str) -> str:
        return f"{label}：{design.DEFINITIONS[label]}" if label in design.DEFINITIONS else label

    return "\n".join(f"【{dim}】\n" + ("、".join(design.VISION_VOCAB[dim]) if dim == "颜色"
                                      else "\n".join(line(label) for label in design.VISION_VOCAB[dim]))
                     for dim in DESIGN_DIMENSIONS)


PROMPT = f"""你是家具设计分析师。看这张亚马逊家具商品主图，只看这件家具本身（背景里的墙面、地毯、摆件、电视、床品都不算），
逐条对照下面词表里每个特征的定义，只输出一个 JSON 对象，不要任何其他文字：
{{"主风格":"","风格":[],"材质":[],"造型":[{{"特征":"","位置":""}}],"工艺":[{{"特征":"","位置":""}}],"主色":"","颜色":[],"功能卖点":[{{"特征":"","位置":""}}],"外观要点":""}}
要求：
- 只能用词表里的特征名（颜色词表里没有的可以写“X色”，如金色）；词表里没有的特征不写；
- 宁缺毋滥：完全符合定义、而且在图里指得出具体部位的才写；只是有点像、拿不准、被挡住看不清的都不写；不要因为词表里有就写；
- “造型”“工艺”“功能卖点”的每一项都写“位置”：在这件家具的哪个部位看到的（如“两扇柜门”“抽屉面”“桌腿”“床头”）；说不出位置的就不写这一项；
- “主风格”只写一个：最能代表这件家具的风格，看得出具体风格的就写具体的，看不出的普通款才写“现代”或“极简”；“风格”0~2 项，主风格放第一个；
- “主色”只写一个：家具本体占面积最大的颜色（拉手、金属腿、床垫、摆件这些小面积的不算）；“颜色”按面积从大到小 0~3 项；
- “材质”只写看得出的：木纹看不出是实木还是板材，一律写“木质”；小五金不算金属；
- “外观要点”用一句话（不超过 40 字）说清这件家具最抓眼的外观 / 工艺设计，只写图里确实有的。
词表（特征：定义）：
{_vocab()}"""




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
    tags, where = {}, {}
    for dim in DESIGN_DIMENSIONS:
        values = data.get(dim) or []
        if isinstance(values, (str, dict)):
            values = [values]
        labels = []
        for v in values:
            if isinstance(v, dict):
                label = str(v.get("特征") or v.get("label") or "").strip()
                spot = str(v.get("位置") or v.get("where") or "").strip()
                if dim in EVIDENCE_DIMENSIONS and not spot:
                    continue  # 说不出在哪个部位看到的不算
            else:
                label, spot = str(v).strip(), ""
            if label and label not in labels:
                labels.append(label)
                if spot:
                    where[label] = spot[:16]
        tags[dim] = labels[:6]
    out = {"tags": design.canonical_tags(tags), "summary": str(data.get("外观要点") or "").strip()[:60],
           "protocol": PROTOCOL}
    kept = {label for labels in out["tags"].values() for label in labels}
    where = {c: spot for label, spot in where.items() if (c := design.canonical_label(label)) in kept}
    if where:
        out["where"] = where  # 特征 → 在家具哪个部位看到的（只用来核对）
    for key, field in (("主色", "main_color"), ("主风格", "main_style")):
        main = design.canonical_label(str(data.get(key) or "").strip()[:8])
        if main:
            out[field] = main
    return out


def _chat(client: httpx.Client, cfg: dict, api_key: str, prompt: str, data_uri: str, *, max_tokens: int,
          temperature: float) -> str:
    llm = cfg["llm"]
    body = {
        "model": llm.get("vision_model") or "deepseek-v4-flash-vision-exp",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]}],
        **(llm.get("extra_body") or {}),
    }
    response = client.post(llm["base_url"].rstrip("/") + "/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {api_key}"},
                           timeout=float(llm.get("timeout_seconds", 120)))
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


# 复核：主图识别说有、标题里没写的特征，再单独问一次“是不是真有”（只看这几项、逐项给定义，答 true / false）。
# 11-07 在人工逐张核对过的 158 张图上：只识别一次，误标 22/87、认出 74/86；加复核后误标 0/87、认出 71/86
# （凹槽竖纹：认出 13/13、误标 0/21）。标题也写了的不复核（图文一致）；抽查一直可靠的、只说明有储物的不复核
VERIFY_DIMENSIONS = ("材质", "造型", "工艺", "功能卖点")
VERIFY_SKIP = frozenset({"开放格", "谷仓门/推拉门", "带镜子", "带储物/抽屉", "木质", "金属"})
VERIFY_PROMPT = ("下面几项是另一位分析师从这张家具主图里认出的特征，请你严格复核：只看这件家具本身，逐项对照定义，"
                 "图里清清楚楚、完全符合定义的才答 true；只是有点像、看不清、或者属于定义里“不算”的情况都答 false。"
                 "只输出一个 JSON 对象，键是特征名，值是 true 或 false。\n")


# 词表版本：识别、复核的提示词（词表、定义）一改，缓存的识别结果就按新提示词重看一遍
VOCAB_VERSION = hashlib.sha1((PROMPT + VERIFY_PROMPT).encode("utf-8")).hexdigest()[:8]


def to_verify(result: dict, title: str) -> list[str]:
    """要复核的特征：VERIFY_DIMENSIONS 里、标题没写的（design.extract），VERIFY_SKIP 以外的
    （design.TITLE_REQUIRED 不用复核：标题没写就不算）。"""
    said = design.extract(title or "")
    return [label for dim in VERIFY_DIMENSIONS for label in (result.get("tags") or {}).get(dim) or []
            if label not in VERIFY_SKIP and label not in design.TITLE_REQUIRED and label not in said.get(dim, set())]


def _verify(client: httpx.Client, cfg: dict, api_key: str, data_uri: str, labels: list[str]) -> dict[str, bool]:
    prompt = VERIFY_PROMPT + "\n".join(f"- {label}：{design.DEFINITIONS.get(label, label)}" for label in labels)
    text = _chat(client, cfg, api_key, prompt, data_uri, max_tokens=200, temperature=0.0)
    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        data = {}
    # 没答到的、答得不是 true 的都算没通过（拿不准就不算）
    return {label: data.get(label) is True for label in labels}


def _describe(client: httpx.Client, cfg: dict, api_key: str, image_url: str, title: str = "") -> dict | None:
    """识别一张主图：先按 PROMPT 认特征，再复核（to_verify / _verify），没通过复核的特征去掉，复核结果记在 checked 里。"""
    image = client.get(small_image(image_url), timeout=20, follow_redirects=True)
    image.raise_for_status()
    mime = image.headers.get("content-type", "image/jpeg").split(";")[0]
    data_uri = f"data:{mime};base64,{base64.b64encode(image.content).decode('ascii')}"
    result = _parse(_chat(client, cfg, api_key, PROMPT, data_uri, max_tokens=700, temperature=0.2))
    if not result:
        return result
    labels = to_verify(result, title)
    if labels:
        checked = _verify(client, cfg, api_key, data_uri, labels)
        result["tags"] = {dim: [label for label in values if checked.get(label, True)]
                          for dim, values in result["tags"].items()}
        result["checked"] = checked
        if result.get("where"):
            result["where"] = {label: spot for label, spot in result["where"].items() if checked.get(label, True)}
    return result


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


def _describe_with_retry(client: httpx.Client, cfg: dict, api_key: str, image_url: str, title: str = "") -> dict | None:
    for wait in (*RETRY_WAITS, None):
        try:
            return _describe(client, cfg, api_key, image_url, title)
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
    main = design.canonical_label(v.get("main_color") or "")
    if main and (design.LABEL_DIM.get(main) == "颜色" or main.endswith("色")):
        return main
    if len(colors) <= 1:
        return colors[0] if colors else None
    look = v.get("summary") or ""
    hits = [(min(look.find(n) for n in _color_names(c) if n in look), k, c)
            for k, c in enumerate(colors) if any(n in look for n in _color_names(c))]
    return min(hits)[2] if hits else colors[0]


GENERIC_STYLES = ("现代", "极简")  # 笼统的风格：几乎每件都会被标上，一起标了具体风格（农舍风、工业风……）时，主风格取具体的


def main_style(v: dict) -> str | None:
    """主风格：识别结果里写了“主风格”就用它；以前的结果没有这一项，取风格里第一个具体风格（不是“现代”“极简”的），
    没有具体风格的取第一个。报告里“风格”只按主风格归类：现代 + 农舍风的五斗橱只算农舍风（11-06 用户要求按最主要的特点归类）。"""
    styles = list(dict.fromkeys((v.get("tags") or {}).get("风格") or []))
    main = design.canonical_label(v.get("main_style") or "")
    if main and design.LABEL_DIM.get(main) == "风格":
        return main
    return next((s for s in styles if s not in GENERIC_STYLES), styles[0] if styles else None)


# 只按最主要的那一个归类的维度：一件家具只有一个主体颜色、一种主风格；造型、工艺、功能可以同时有几个
MAIN_ONLY = {"颜色": main_color, "风格": main_style}


def legacy(v: dict) -> bool:
    """旧提示词（只给词表、不给定义）的识别结果。"""
    return bool(v) and int(v.get("protocol") or 1) < PROTOCOL


def normalized(v: dict, title: str | None = "") -> dict:
    """报告、统计、开发方向读的识别结果（of、summarize 都经过这里）：
    ① 标签归位（design.canonical_tags：词表以外的写法换成词表里的、放错维度的挪回去、范围外的丢掉）；
    ② 颜色、风格（MAIN_ONLY）只留最主要的那一个（主色、主风格）；
    ③ 旧提示词的结果（legacy）：造型 / 工艺 / 功能 / 材质只有 design.LEGACY_TRUSTED 照用，其余的要商品标题也有这个特征
       （design.extract）才算——11-07 抽查旧结果，凹槽竖纹 36 张里只有 13 张真是，标题写 fluted 的则全是；
    ④ 看尺寸的特征（design.TITLE_REQUIRED：高柜/立式）不管新旧结果，都要标题也写了才算（照片没有参照物）；
    ⑤ 识别时写下的部位落在定义“不算”的地方（design.EXCLUDED_SPOTS：凹槽竖纹写在玻璃、软包上）的不算——标题写了也不算。"""
    if not v or not v.get("tags"):
        return v or {}
    tags = design.canonical_tags(v["tags"])
    picked = {**v, "tags": tags}
    for dim, pick in MAIN_ONLY.items():
        main = pick(picked)
        tags[dim] = [main] if main else []
    said = design.extract(title or "")
    old = legacy(v)
    where = v.get("where") or {}
    for dim in design.LEGACY_GATED:
        tags[dim] = [label for label in tags[dim]
                     if not any(word in where.get(label, "") for word in design.EXCLUDED_SPOTS.get(label, ()))
                     and (label in said.get(dim, ())
                          or (label not in design.TITLE_REQUIRED and (not old or label in design.LEGACY_TRUSTED)))]
    return picked


def title_of(state: dict, asin: str) -> str:
    """商品标题（核对旧识别结果用）：追踪商品记录 → 月度名单 → 全市场名单。"""
    for rows in ((state.get("asins") or {}), ((state.get("discovery") or {}).get("products") or {}),
                 ((state.get("universe") or {}).get("rows") or {})):
        title = (rows.get(asin) or {}).get("title")
        if title:
            return title
    return ""


def raw_of(state: dict, asin: str) -> dict:
    """存着的原始识别结果：追踪商品在 state["asins"] 里，其余在 state[EXTRA_KEY] 里。没有返回 {}。"""
    rec = (state.get("asins") or {}).get(asin) or {}
    return rec.get("vision") or ((state.get(EXTRA_KEY) or {}).get(asin) or {}).get("vision") or {}


def of(state: dict, asin: str, title: str | None = None) -> dict:
    """一个商品的主图识别结果（见 normalized：标签归位、颜色只留主色、风格只留主风格、旧结果按标题核对）。没有返回 {}。
    title 不给时从 state 里找（title_of）。"""
    v = raw_of(state, asin)
    if not v:
        return {}
    return normalized(v, title if title is not None else title_of(state, asin))


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
                result = _describe_with_retry(client, cfg, api_key, url,
                                              item.get("title") or title_of(state, item["asin"]))
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


def summarize(focus: list[dict], reference: list[dict], state: dict, *, per_dim: int = 20,
              min_gap: float = 0.2) -> dict:
    """汇总主图标签：每个维度里，增长商品最常见的特征，并和持续热销对照组比较。
    每个商品自己的“外观要点”直接显示在商品卡片上（pipeline 里写到 item["look"]）。"""
    def tags_of(items):
        out = []
        for item in items:
            vision = (state["asins"].get(item["asin"]) or {}).get("vision")
            if vision:
                out.append((item, normalized(vision, item.get("title") or title_of(state, item["asin"]))))
        return out

    f_tagged, r_tagged = tags_of(focus), tags_of(reference)
    # 两组里各有几张是旧提示词的结果（它们的造型 / 工艺 / 功能要标题也有才算，报告里写明）
    result = {"n_focus": len(f_tagged), "n_reference": len(r_tagged), "dimensions": {},
              "legacy_focus": sum(1 for _, v in f_tagged if legacy(v)),
              "legacy_reference": sum(1 for _, v in r_tagged if legacy(v))}
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

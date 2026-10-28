"""假爆火 / 异常信号评分。≥suspect_score 为“疑似”，≥high_score 为“高度疑似”。

报告里统一表述为“异常信号”，给出依据，由人判断，不直接下结论。

| 分 | 信号 | 首期可用 |
| +2 | 留评率 ≥ max(子类目中位数×3, 6%) 且本月新增评论 ≥10（当月新增多于商品总评论的是父体数据，不计） | ✓ |
| +2 | 新品（≤90 天）累计评论数 / 累计销量 ≥ 10%（和留评率同时成立时这一项只计 1 分：同一个证据） | ✓ |
| —  | Vine 评论先扣掉（抽查过的按抽到的比例；没抽查过的、上架 ≤90 天的新品，前 30 条当作可能是 Vine）；抽查到的近 60 天评论不到应有的一半 = 评论是继承来的，上面两项都不计 | ✓ |
| +2 | 两期之间新增评论 / 新增销量 ≥ 15%（或几乎无销量却涨评论） | 第 2 期起 |
| +1 | 45 天内评分上升 ≥0.2 且评论数 ≥50 | 第 2 期起 |
| +1 | 1~3 天脉冲后迅速回落（不在大促日，见 events.py） | ✓ |
| +2 | 评论核查：评论最多的 3 天 ≥30%，且比随机分布时 3 天最多的条数（99% 分位）还多 | ✓（需额外调用） |
| +1 | 评论核查：非验证购买占比 ≥50% | ✓（需额外调用） |
| 标签 | 评论数暴增 ≥30% 且父体/变体数变化 → 变体合并（不计分） | 第 2 期起 |

评论数和销量都是整个父体的（卖家精灵名单按父体一行，日数据也是父体的），变体多少都直接比，不除以变体数。
实测（2026-10-23 全覆盖试跑 7,536 个商品）：名单留评率的中位数按变体数 1 / 2~3 / 4~6 / 7~15 / 16+ 个是
1.7% / 3.2% / 3.0% / 2.8% / 2.3%，不随变体数变大；名单近 30 天销量 ÷ 日数据近 30 天合计各档都是 1.00~1.05。
如果销量只是一个子体的，16 个以上变体的比值应该高好几倍。以前按“销量是一个子体的”先除以变体数（再之前是整项不算），
4 个以上变体的商品留评率信号 208 个只剩 19 个（7~15 个变体 74 → 1，16 个以上 24 → 0），等于对多变体商品关掉了这几项。
变体合并（新子体带着评论挂进来）另有拦截：当月新增评论比总评论还多的是父体数据、抽查到的评论是继承来的、评论暴增且父体/变体变化。
老品换链接（rules.relisted）的评论是从老链接继承来的，不算“新品评论/销量比”。
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from functools import lru_cache

from .. import clock
from ..series import Daily, find_pulses, sum_sales_between


def previous_obs(rec: dict, as_of: date) -> dict | None:
    for entry in reversed(rec.get("obs") or []):
        day = clock.parse_day(entry.get("date"))
        if day and day < as_of and entry.get("ratings") is not None:
            return entry
    return None


def ratings_jump(rec: dict, as_of: date) -> float | None:
    obs = rec.get("obs") or []
    if not obs:
        return None
    current = obs[-1].get("ratings")
    prev = previous_obs(rec, as_of)
    if current is None or prev is None or not prev.get("ratings"):
        return None
    return (current - prev["ratings"]) / prev["ratings"]


def merge_detected(rec: dict, disc_row: dict | None) -> bool:
    """asin_detail 核查结果与之前记录相比，父体或变体数发生了变化。"""
    check = (rec.get("checks") or {}).get("detail")
    if not check:
        return False
    before_parent = (disc_row or {}).get("parent") or rec.get("parent")
    before_vars = (disc_row or {}).get("variations") or rec.get("variations")
    parent_changed = bool(check.get("parent") and before_parent and check["parent"] != before_parent)
    vars_changed = bool(check.get("variations") and before_vars and
                        abs(check["variations"] - before_vars) >= max(2, 0.3 * before_vars))
    return parent_changed or vars_changed


def score(rec: dict, d: Daily | None, as_of: date | None, disc_row: dict | None,
          node_median_rate: float | None, cfg: dict, event_days=(), *, relisted: bool = False) -> dict:
    """d 是完整的日数据（不去掉大促日）；event_days：大促日（events.detect），落在大促日的脉冲不计分；
    relisted：老品换链接，不算“新品评论/销量比”。"""
    signals: list[dict] = []
    tags: list[str] = []

    def add(code: str, points: int, text: str) -> None:
        signals.append({"code": code, "points": points, "text": text})

    check = (rec.get("checks") or {}).get("review")
    total = ((rec.get("obs") or [{}])[-1]).get("ratings")
    if total is None:
        total = (disc_row or {}).get("ratings")
    # Vine：亚马逊给新品安排的测评（每个商品最多 vine_allowance 条，都在上架头几个月）。抽查过评论的按抽到的 Vine 比例算；
    # 没抽查过的、上架不到 vine_days 天的新品，前 vine_allowance 条评论当作可能是 Vine，不算进评论/销量比。
    # 实测 2026-10-23 全覆盖试跑：8 个异常商品全是新品“留评率 + 新品评论比”，其中 3 个只有 28~48 条评论（Vine 就有 30 条），
    # 扣掉后都不到门槛；其余 5 个扣掉后仍是 7.5%~22%
    vine, vine_text = vine_reviews(check), "条 Vine 评论"
    age_now = clock.days_between(clock.parse_day(rec.get("available")), as_of) if as_of else None
    assumed = False  # 按“可能是 Vine”扣过（没抽查过评论）：扣之前够得上评论信号的，要去抽查评论，用抽到的 Vine 比例代替猜的
    if not (check and check.get("vine_share") is not None) and age_now is not None \
            and 0 <= age_now <= int(cfg.get("vine_days", 90)):
        vine, assumed = min(int(cfg.get("vine_allowance", 30)), int(total or 0)), True
        vine_text = f"条可能的 Vine 评论（新品最多 {int(cfg.get('vine_allowance', 30))} 条，没抽查过评论）"
    raw_flag = False  # 不扣 Vine 时评论信号是否成立

    # 1. 留评率（来自月度数据）：当月新增评论 ÷ 销量，都是整个父体的
    if disc_row and disc_row.get("ratings_rate") is not None:
        rate = disc_row["ratings_rate"]
        new_reviews = disc_row.get("ratings_cv") or 0
        # 当月新增评论比这个商品一共的评论还多：名单行里的评论数是整个父体的，不是它的（实测 1 条评论的商品“当月新增 403 条”）
        family = total is not None and new_reviews > total * 1.05 + 5
        bar = max(cfg["review_rate_floor"], cfg["review_rate_mult"] * (node_median_rate or 0))
        raw_flag |= bool(not family and rate >= bar and new_reviews >= cfg["min_new_reviews"])
        if vine and new_reviews and not family:  # Vine 评论是亚马逊安排的测评，不算刷评
            # 没抽查过时只知道“总评论里最多 vine 条是 Vine”：当月新增里按同样的比例扣（全扣会把 171 条评论的商品也扣没）
            if check and check.get("vine_share") is not None:
                month_vine = min(new_reviews, vine)
            else:
                month_vine = new_reviews * min(1.0, vine / max(1, total or 0))
            rate, new_reviews = rate * max(0, new_reviews - month_vine) / new_reviews, max(0, new_reviews - month_vine)
        if rate >= bar and new_reviews >= cfg["min_new_reviews"]:
            if family:
                tags.append("留评率数据是父体的，不计分")
            elif inherited(check, new_reviews):
                tags.append(f"评论多是继承来的（抽查近 60 天只有 {_actual(check)} 条），留评率不计分")
            else:
                add("review_rate", 2, f"留评率 {rate:.1f}%，远高于子类目中位数 {node_median_rate or 0:.1f}%"
                                      f"（当月新增评论 {int(new_reviews)} 条{f'，已按 {vine} {vine_text}扣过' if vine else ''}）")

    if d is None or as_of is None:
        return _finish(signals, tags, cfg)
    as_of_idx = d.index_of(as_of)
    current_obs = (rec.get("obs") or [{}])[-1]
    ratings = current_obs.get("ratings")

    # 2. 新品评论/销量比
    available = clock.parse_day(rec.get("available"))
    age = clock.days_between(available, as_of)
    if (age is not None and 0 <= age <= cfg["new_listing_days"] and ratings and ratings >= 20
            and not relisted):
        launch = clock.month_key(available)
        units = sum((m[1] or 0) for m in rec.get("months") or [] if m[0] >= launch)
        own = max(0, ratings - vine)
        raw_flag |= bool(units > 0 and ratings / units >= cfg["new_listing_review_ratio"])
        if units > 0 and own / units >= cfg["new_listing_review_ratio"]:
            ratio = own / units
            # 抽查评论能看到的近 60 天里，评论应该至少有（上架天数里平摊到 60 天的）一半；远少于这个数的，评论是继承来的
            if inherited(check, ratings * min(1.0, 60 / max(age, 1))):
                tags.append(f"评论多是继承来的（抽查近 60 天只有 {_actual(check)} 条），新品评论/销量比不计分")
            else:
                # 和留评率是同一个证据（评论相对销量太多），上架不到 90 天时两者几乎是同一段时间：两项都成立时这一项只计 1 分
                # （合计 3 分 = 疑似，会去抽查评论；Vine 评论多、评论是继承来的，抽查后就不计了），不再 2 + 2 = 4 分直接“高度疑似”
                once = any(sig["code"] == "review_rate" for sig in signals)
                add("new_listing_reviews", 1 if once else 2,
                    f"上架仅 {age} 天，累计评论 {int(own)} 条 / 累计销量约 {int(units)} 件"
                    f"（{ratio:.0%}）"
                    + (f"，已扣掉 {vine} {vine_text}" if vine else "")
                    + ("；和留评率是同一个证据，这一项只计 1 分" if once else ""))

    # 3. 两期之间的评论增速
    prev = previous_obs(rec, as_of)
    merged = merge_detected(rec, disc_row)
    if merged:
        tags.append("变体合并")
    if prev and ratings is not None:
        delta_ratings = ratings - prev["ratings"]
        prev_day = clock.parse_day(prev["date"])
        delta_units = sum_sales_between(d, prev_day, as_of) if prev_day else None
        if delta_ratings >= cfg["min_new_reviews"] and not merged:
            bar = max(cfg["run_review_ratio"], cfg["review_rate_mult"] * (node_median_rate or 0) / 100)
            if not delta_units:
                add("reviews_without_sales", 2,
                    f"{prev['date']} 以来评论 +{int(delta_ratings)} 条，同期几乎没有销量")
            elif delta_ratings / delta_units >= bar:
                add("review_velocity", 2,
                    f"{prev['date']} 以来评论 +{int(delta_ratings)} 条，销量约 {int(delta_units)} 件"
                    f"（{delta_ratings / delta_units:.0%}）")

        # 4. 评分跳升
        prev_rating, rating = prev.get("rating"), current_obs.get("rating")
        if (prev_rating is not None and rating is not None and prev_day
                and (as_of - prev_day).days <= 45
                and rating - prev_rating >= cfg["rating_jump"] - 1e-9
                and (ratings or 0) >= cfg["rating_jump_min_ratings"]):
            add("rating_jump", 1, f"{(as_of - prev_day).days} 天内评分 {prev_rating} → {rating}")

    # 5. 短时脉冲
    for pulse in find_pulses(d, as_of_idx, 30, cfg["pulse_mult"], cfg["pulse_min_sales"], cfg["pulse_revert"]):
        day = clock.parse_day(pulse["day"])
        if any(day + timedelta(days=k) in event_days for k in range(pulse["length"])):
            if "大促脉冲" not in tags:
                tags.append("大促脉冲")
            continue
        add("pulse", 1, f"{pulse['day']} 起 {pulse['length']} 天冲到 {int(pulse['peak'])} 件/天"
                        f"（基线约 {pulse['base']:.0f}），随后迅速回落")
        break

    # 6. 评论核查
    check = (rec.get("checks") or {}).get("review")
    if check and check.get("n", 0) >= 10:
        burst = burst_limit(check, cfg)
        if burst is not None:
            add("review_burst", 2, f"近 {burst['span']} 天抽到的 {check['n']} 条评论里 {burst['top3']} 条集中在 3 天"
                                   f"（{', '.join(check.get('burst_days', [])[:3])}），"
                                   f"随机分布时 3 天最多约 {burst['usual']} 条")
        if check.get("unverified_share", 0) >= cfg["unverified_share"]:
            add("unverified", 1, f"抽查评论中非验证购买占 {check['unverified_share']:.0%}")
        if check.get("vine_share", 0) >= 0.3:
            tags.append("Vine 评论多")

    out = _finish(signals, tags, cfg)
    out["check_reviews"] = bool(assumed and raw_flag)
    return out


def _actual(check: dict | None) -> int:
    """抽查评论：近 60 天一共有几条（接口给的 total；旧的核查结果没有 total 时用这一页的条数）。"""
    check = check or {}
    return int(check["total"] if check.get("total") is not None else check.get("n") or 0)


def inherited(check: dict | None, expected_60d: float) -> bool:
    """评论数是继承来的（变体合并 / 老品换链接）：抽查评论看到的近 60 天的评论，不到按评论数推算应有的一半。
    实测：上架 57 天、339 条评论、当月新增 332 条的商品，抽查近 60 天只有 4 条。没有核查过的判断不了，返回 False。"""
    if not check or check.get("n") is None or not expected_60d:
        return False
    if check.get("total") is None and (check.get("n") or 0) >= 20:
        return False  # 旧的核查结果：一页拿满了，看不出一共几条
    return _actual(check) < 0.5 * expected_60d


def vine_reviews(check: dict | None) -> int:
    """抽查评论里的 Vine 评论（亚马逊安排的测评，不算刷评）按比例折算成近 60 天的条数。没有核查过为 0。"""
    if not check or not check.get("vine_share"):
        return 0
    return int(round(float(check["vine_share"]) * _actual(check)))


@lru_cache(maxsize=None)
def uniform_top3(n: int, days: int, quantile: float = 0.99, sims: int = 4000) -> int:
    """n 条评论随机落在 days 天里时，评论最多的 3 天合计的 quantile 分位数（固定种子模拟，结果可复现）。"""
    rnd = random.Random(n * 1000 + days)
    tops = []
    for _ in range(sims):
        counts = [0] * days
        for _ in range(n):
            counts[rnd.randrange(days)] += 1
        tops.append(sum(sorted(counts, reverse=True)[:3]))
    tops.sort()
    return tops[min(sims - 1, int(quantile * sims))]


def burst_limit(check: dict, cfg: dict) -> dict | None:
    """评论集中：评论最多的 3 天 ≥ review_burst_share，而且比“随机分布时 3 天最多有几条”（review_burst_quantile 分位）还多。
    集中时返回 {"usual": 随机分布的上限, "top3", "span"}；不集中 / 旧的核查结果拿满一页又没记 top3、span（判不了）返回 None。
    以前只看“前 3 天占 ≥30%”：10 条评论分在 10 天，前 3 天正好 30%，一定达标；评论接口一页只给最新 20 条，
    覆盖的日子更短、更显得集中。实测 2026-10 核查过的 9 个（≥10 条）有 8 个被判集中，按随机分布比只剩 1~2 个。"""
    n, top3, span = check.get("n") or 0, check.get("top3"), check.get("span")
    if top3 is None and 1 <= n < 20 and check.get("burst_share") is not None:
        # 旧的核查结果没记 top3 / span：不到一页（20 条）说明近 60 天的都拿到了
        top3, span = int(round(check["burst_share"] * n)), 60
    if top3 is None or not span or n < 1:
        return None
    usual = uniform_top3(int(n), int(span), float(cfg.get("review_burst_quantile", 0.99)))
    if top3 / n >= cfg["review_burst_share"] - 1e-9 and top3 > usual:
        return {"usual": usual, "top3": top3, "span": span}
    return None


def _finish(signals: list[dict], tags: list[str], cfg: dict) -> dict:
    total = sum(s["points"] for s in signals)
    level = "high" if total >= cfg["high_score"] else "suspect" if total >= cfg["suspect_score"] else None
    return {"score": total, "level": level, "signals": signals, "tags": tags}

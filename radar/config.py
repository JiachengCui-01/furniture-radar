"""读取 config.yaml（非机密参数）和环境变量（密钥）。"""
from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MCP_URL = "https://mcp.sellersprite.com/mcp"

# config.yaml 缺省时的取值；与 config.yaml 中的注释保持一致
DEFAULTS: dict = {
    "marketplace": "US",
    "vendor_timezone": "America/Los_Angeles",
    "display_timezone": "Asia/Shanghai",
    "scope": {
        "roots": [
            {"path": "1055398:1063306", "label": "家具"},
            {"path": "1064954:1069102", "label": "办公家具"},
        ],
        "rollup_pages": 2,
        "min_node_products": 800,
        "max_nodes": 30,
        "include_label_regex": '(?i)\\bbeds?\\b|bed frames?|headboard|desk|workstation|dresser|\\bchests?\\b|bookcase|shel(f|ves|ving)|nightstand|night stand|tables?\\b|console|\\btv\\b|media|entertainment|cabinet|storage|pantr|buffet|sideboard|credenza|hutch|vanit|bench|rack|island|armoire|wardrobe|shoe|hall tree|filing|etagere',
        "exclude_label_regex": '(?i)sofas|couch|loveseat|sectional|futon|mattress|box spring|ottoman|bean bag|chair|stool|recliner|seating|canop|gazebo|pergola|tent|divider|screen|folding|adjustable base|massage|inflatable|air bed|light|lamp|umbrella|cushion|pillow|cover|replacement|parts|hardware|decor|cart|display|laborator|science',
        "exclude_product_regex": '(?i)upholster|tufted headboard|fabric (?:drawers?|bins?|storage|dresser|chest)|\\bplastic\\b|\\bresin\\b',
        "extra_nodes": [],
    },
    "discovery": {
        "top_per_node": 50,
        "order_field": "total_amount",
        "merge_variations": True,
        "newcomer_max_nodes": 12,
        "newcomers_per_node": 30,
        "newcomer_min_revenue": 20000,
        "risers_per_root": 50,
        "risers_new_per_root": 50,
        "risers_min_units": 300,
        "risers_orders": ["total_units_growth", "bsr_rank_cr"],
        "risers_max_prev_bsr": 1_000_000,
        "similar_seeds": 3,
        "similar_size": 20,
        "similar_cooldown_runs": 3,
    },
    "pool": {"max_size": 155, "core_max": 80, "per_node_top": 1, "watch_runs": 3, "similar_max": 10,
             "opportunity_every_run": 20, "opportunity_min_units": 300, "opportunity_min_growth": 20,
             "opportunity_max_age_days": 180},
    "budget": {
        "per_run": 175,
        "bootstrap_run": 230,
        "monthly_cap": 1800,
        "review_checks": 5,
        "material_lookups": 15,
        "stable_refresh_every": 1,
        "min_interval_seconds": 1.0,
        "rate_limit_wait_seconds": 60,
    },
    "schedule": {"min_days_between_runs": 3},
    "thresholds": {
        "stale_days": 4,
        "min_rating": 4.0,
        "few_ratings": 100,
        "few_ratings_min_rating": 4.2,
        "surge": {
            "windows": [7, 14], "base_days": 28, "bsr_ratio": 0.5, "sales_ratio": 2.0,
            "min_daily_sales": 3, "sustained_mult": 1.5, "sustained_days": 5, "steady_band": 0.15,
            "climb_ratio": 1.15,
            "pulse_share": 0.6, "seasonal_ratio": 1.5, "price_drop": 0.15, "restock_ratio": 0.5,
            "new_listing_min_daily": 30, "new_listing_node_share": 0.25,
        },
        "hot": {
            "rank_in_node": 20, "lookback_months": 4, "months_required": 3,
            "max_cv": 0.35, "min_trend": -0.15, "min_recent_ratio": 0.7,
        },
        "rising": {"min_ratio": 1.3, "min_daily_sales": 3},
        "momentum": {"min_vs_peak_week": 0.8, "min_last3_vs_7d": 0.8},
        "potential": {
            "min_age_days": 30, "max_age_days": 180, "min_monthly_growth": 0.15,
            "min_recent_ratio": 1.2, "max_ratings": 300, "min_daily_sales": 2,
        },
        "fake": {
            "suspect_score": 3, "high_score": 5, "review_rate_mult": 3,
            "review_rate_floor": 6, "min_new_reviews": 10, "new_listing_days": 90,
            "new_listing_review_ratio": 0.10, "run_review_ratio": 0.15,
            "max_variations_for_ratio": 3, "rating_jump": 0.2,
            "rating_jump_min_ratings": 50, "pulse_mult": 3, "pulse_min_sales": 6,
            "pulse_revert": 1.3, "merge_jump": 0.3, "review_burst_share": 0.3,
            "unverified_share": 0.5,
        },
    },
    "deal_windows": [],
    "report": {"title": "家具爆品雷达", "subtitle": "亚马逊美国站 · 板材 / 实木 / 铁木家具", "base_url": "",
               "keep_reports": 60, "top_n": 15},
    "notify": {"open_in_browser": True, "at_all": False, "max_items": 3},
    "llm": {
        "enabled": "auto",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "api_key_env": "DEEPSEEK_API_KEY",
        "timeout_seconds": 120,
        "extra_body": {"thinking": {"type": "disabled"}},
        "vision": "auto",
        "vision_model": "deepseek-v4-flash-vision-exp",
        "vision_focus": 20,
        "vision_reference": 10,
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(path: str | os.PathLike | None = None) -> dict:
    target = Path(path or os.environ.get("RADAR_CONFIG") or PROJECT_ROOT / "config.yaml")
    user: dict = {}
    if target.exists():
        with open(target, encoding="utf-8") as fh:
            user = yaml.safe_load(fh) or {}
    return deep_merge(DEFAULTS, user)


def _split(value: str | None) -> list[str]:
    return [part.strip() for part in re.split(r"[,\n]", value or "") if part.strip()]


@dataclass
class Secrets:
    sellersprite_key: str = ""
    sellersprite_url: str = DEFAULT_MCP_URL
    report_key: str = ""
    dingtalk_webhooks: list[str] = field(default_factory=list)
    dingtalk_secrets: list[str] = field(default_factory=list)
    llm_api_key: str = ""
    report_base_url: str = ""


def load_secrets(cfg: dict, *, dotenv: bool = True) -> Secrets:
    if dotenv:
        try:
            from dotenv import load_dotenv

            load_dotenv(PROJECT_ROOT / ".env", override=False)
        except ImportError:  # pragma: no cover
            pass
    env = os.environ
    webhooks, secrets = dingtalk_robots(env)
    return Secrets(
        sellersprite_key=env.get("SELLERSPRITE_SECRET_KEY", "").strip(),
        sellersprite_url=env.get("SELLERSPRITE_MCP_URL", "").strip() or DEFAULT_MCP_URL,
        report_key=env.get("REPORT_KEY", "").strip(),
        dingtalk_webhooks=webhooks,
        dingtalk_secrets=secrets,
        llm_api_key=env.get(cfg["llm"].get("api_key_env") or "DEEPSEEK_API_KEY", "").strip(),
        report_base_url=env.get("REPORT_BASE_URL", "").strip(),
    )


def parse_robot(value: str) -> tuple[str, str] | None:
    """一个群一个值：“webhook 地址” + 可选的 “SEC 加签密钥”，用逗号、空格或换行隔开都行。"""
    parts = [p for p in re.split(r"[\s,，]+", value or "") if p]
    url = next((p for p in parts if p.startswith("http")), "")
    secret = next((p for p in parts if p.startswith("SEC")), "")
    return (url, secret) if url else None


def dingtalk_robots(env) -> tuple[list[str], list[str]]:
    """所有要推送的钉钉群，返回 (webhook 列表, 加签密钥列表)，一一对应。

    两种写法可以同时用：
    * DINGTALK_WEBHOOK / DINGTALK_SECRET：最早的写法，多个群用英文逗号按顺序对应；
    * DINGTALK_ROBOT_<名字>：每个群单独一个，值是“webhook,SEC密钥”。新增群只要新建一个，不用改原来的。
      GitHub Actions 里工作流逐个传入 DINGTALK_ROBOT_1 ~ 5（不能用 toJSON(secrets)，会被 GitHub 判为可疑）。
    """
    webhooks = _split(env.get("DINGTALK_WEBHOOK"))
    secrets = _split(env.get("DINGTALK_SECRET"))
    secrets = (secrets + [""] * len(webhooks))[: len(webhooks)]

    named: dict[str, str] = {k: v for k, v in env.items() if k.startswith("DINGTALK_ROBOT_") and v}
    for name in sorted(named):
        robot = parse_robot(named[name])
        if robot and robot[0] not in webhooks:
            webhooks.append(robot[0])
            secrets.append(robot[1])
    return webhooks, secrets


def report_base_url(cfg: dict, secrets: Secrets) -> str:
    """报告网址前缀：环境变量 > config.yaml > 由 GITHUB_REPOSITORY 推导。"""
    base = secrets.report_base_url or cfg["report"].get("base_url") or ""
    if not base:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if "/" in repo:
            owner, name = repo.split("/", 1)
            if name.lower() == f"{owner.lower()}.github.io":
                base = f"https://{owner.lower()}.github.io/"
            else:
                base = f"https://{owner.lower()}.github.io/{name}/"
    if base and not base.endswith("/"):
        base += "/"
    return base

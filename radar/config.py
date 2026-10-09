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
            {"path": "2972638011:553824", "label": "庭院家具"},
            {"path": "1064954:1069102", "label": "办公家具"},
        ],
        "min_node_products": 800,
        "max_nodes": 30,
        "exclude_label_regex": "(?i)light|lamp|umbrella|shade|cover|cushion|pillow|accessor|replacement|parts|hardware|decor",
        "include_label_regex": "",
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
        "risers_min_units": 300,
        "risers_orders": ["total_units_growth", "bsr_rank_cr"],
        "risers_max_prev_bsr": 1_000_000,
        "risers_every_run": False,
    },
    "pool": {"max_size": 150, "per_node_top": 4, "momentum_top": 30, "watch_runs": 3,
             "risers_top": 15, "explore_per_run": 20, "explore_cooldown_runs": 6},
    "budget": {
        "per_run": 80,
        "bootstrap_run": 160,
        "monthly_cap": 900,
        "review_checks": 5,
        "stable_refresh_every": 3,
        "min_interval_seconds": 1.0,
        "rate_limit_wait_seconds": 60,
    },
    "schedule": {"min_days_between_runs": 3},
    "thresholds": {
        "stale_days": 4,
        "surge": {
            "windows": [7, 14], "base_days": 28, "bsr_ratio": 0.5, "sales_ratio": 2.0,
            "min_daily_sales": 3, "sustained_mult": 1.5, "sustained_days": 5,
            "pulse_share": 0.6, "seasonal_ratio": 1.5, "price_drop": 0.15, "restock_ratio": 0.5,
            "new_listing_min_daily": 30, "new_listing_node_share": 0.25,
        },
        "hot": {
            "rank_in_node": 20, "lookback_months": 4, "months_required": 3,
            "max_cv": 0.35, "min_trend": -0.15, "min_recent_ratio": 0.7,
        },
        "potential": {
            "min_age_days": 30, "max_age_days": 180, "min_monthly_growth": 0.15,
            "min_recent_ratio": 1.2, "max_ratings": 300, "min_rating": 4.0,
            "min_daily_sales": 2,
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
    "report": {"title": "家具爆品雷达", "base_url": "", "keep_reports": 60, "top_n": 15},
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
        "vision_focus": 12,
        "vision_reference": 8,
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
    return Secrets(
        sellersprite_key=env.get("SELLERSPRITE_SECRET_KEY", "").strip(),
        sellersprite_url=env.get("SELLERSPRITE_MCP_URL", "").strip() or DEFAULT_MCP_URL,
        report_key=env.get("REPORT_KEY", "").strip(),
        dingtalk_webhooks=_split(env.get("DINGTALK_WEBHOOK")),
        dingtalk_secrets=_split(env.get("DINGTALK_SECRET")),
        llm_api_key=env.get(cfg["llm"].get("api_key_env") or "DEEPSEEK_API_KEY", "").strip(),
        report_base_url=env.get("REPORT_BASE_URL", "").strip(),
    )


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

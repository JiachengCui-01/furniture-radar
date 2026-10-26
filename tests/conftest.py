import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / "tests" / "fixtures" / "sellersprite"

# 测试永远不能花积分，也不能往真实钉钉群发消息
for name in ("SELLERSPRITE_SECRET_KEY", "DINGTALK_WEBHOOK", "DINGTALK_SECRET", "DEEPSEEK_API_KEY",
             "REPORT_BASE_URL", "GITHUB_REPOSITORY", "GITHUB_ACTIONS", "CI",
             *[k for k in os.environ if k.startswith("DINGTALK_ROBOT_")]):
    os.environ.pop(name, None)


def fixture_text(tool: str) -> str:
    return (FIXTURES / f"{tool}.json").read_text(encoding="utf-8")


def fixture(tool: str) -> dict:
    return json.loads(fixture_text(tool))


@pytest.fixture
def cfg():
    """config.yaml 的参数，但不带登记的大促日期：合成数据的日期是随便取的，不能撞上真实的大促。
    需要大促日的测试自己写 deal_windows。"""
    from radar.config import load_config

    out = load_config()
    out["deal_windows"] = []
    # 合成世界（radar/demo.py）每期推进 3 天：测试里按每 3 天一期跑（线上是每周一期，见 test_weekly_schedule_counts_calls_in_the_quota_month）
    out["schedule"]["min_days_between_runs"] = 3
    return out


@pytest.fixture
def master_key():
    from radar import crypto

    return crypto.generate_master_key()

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
    from radar.config import load_config

    return load_config()


@pytest.fixture
def master_key():
    from radar import crypto

    return crypto.generate_master_key()

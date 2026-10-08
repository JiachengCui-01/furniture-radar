"""日志输出。公开仓库的 Actions 日志任何人可见，所以商品级信息只在本地打印。"""
from __future__ import annotations

import os
import sys


def in_ci() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CI") == "true"


def info(message: str) -> None:
    """不含商品数据的进度信息，CI 里也会打印。"""
    print(message, flush=True)


def detail(message: str) -> None:
    """可能含 ASIN / 标题 / 链接的信息：CI 中一律不打印。"""
    if not in_ci():
        print(message, flush=True)


def warn(message: str) -> None:
    print(f"[警告] {message}", file=sys.stderr, flush=True)


def mask(value: str) -> None:
    """让 GitHub Actions 在后续日志里把这个值替换成 ***。"""
    if value and os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{value}", flush=True)

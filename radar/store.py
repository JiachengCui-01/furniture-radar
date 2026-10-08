"""历史状态：一个加密的 JSON 文件 site/data/state.enc。

公开仓库里只有密文。结构：
{
  "schema": 1, "run_seq": 期数, "last_success": ISO 时间,
  "calls": {"2026-10": 本月已用调用数},
  "discovery": {"period", "complete", "nodes": [...], "done_nodes": [...], "products": {asin: 行}},
  "asins": {asin: {元数据, "series": {"start","bsr","sales","price"}, "months": [...], "obs": [...]}},
  "runs": [{"id","seq","date","labels": {asin: [label, score]}, "counts", "notify"}],
  "reports": ["2026-10-08", ...]
}
"""
from __future__ import annotations

import os
from pathlib import Path

from . import crypto

STATE_RELATIVE = Path("data") / "state.enc"
SCHEMA = 1


def empty_state() -> dict:
    return {
        "schema": SCHEMA,
        "run_seq": 0,
        "last_success": None,
        "calls": {},
        "discovery": None,
        "asins": {},
        "runs": [],
        "reports": [],
    }


def state_path(site_dir: str | os.PathLike) -> Path:
    return Path(site_dir) / STATE_RELATIVE


def load(site_dir: str | os.PathLike, master: bytes) -> dict:
    path = state_path(site_dir)
    if not path.exists():
        return empty_state()
    try:
        state = crypto.decrypt_json(crypto.data_key(master), path.read_bytes())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "历史数据解密失败：REPORT_KEY 与生成数据时用的不一致。"
            "如确需重置，请删除 site 分支上的 data/state.enc"
        ) from exc
    base = empty_state()
    base.update(state)
    return base


def save(site_dir: str | os.PathLike, master: bytes, state: dict) -> Path:
    path = state_path(site_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = crypto.encrypt_json(crypto.data_key(master), state)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, path)  # 原子替换，避免写一半
    return path

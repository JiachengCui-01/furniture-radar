"""对卖家精灵的唯一调用入口：预算、去重、returnFields、结果解析都在这里。

思路沿用 AI工作台 server/market/gateway.py：
* 预算在调用前扣减，用完抛 BudgetExhausted（这是“停止条件”，不是错误）。
* 同样的参数在本次运行内只计费一次。
* 带 returnFields 却返回空 → 去掉 returnFields 重试一次，防止厂商改字段名导致静默空数据。
* pytest 中拒绝真实调用，测试永远不会花积分。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import fields as field_sets
from .mcp_client import McpClient, McpToolError, McpUnavailable

Transport = Callable[[str, dict], str]

# 厂商返回这些字眼时视为积分/权限问题，整轮停止，而不是逐个失败
_QUOTA_HINTS = ("积分", "余额", "额度", "quota", "credit", "insufficient", "points", "次数")


class BudgetExhausted(RuntimeError):
    """本期调用预算已用完。"""


class QuotaExhausted(RuntimeError):
    """卖家精灵账户积分/额度不足。"""


@dataclass
class Reply:
    tool: str
    arguments: dict
    status: str = "ok"  # ok | empty | rejected | error | cached
    data: Any = None
    detail: str = ""
    billable: bool = False

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "cached")

    @property
    def rows(self) -> list:
        return rows_of(self.data)


def rows_of(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "records", "list", "rows"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _row_count(data: Any) -> int:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("items", "records", "list", "rows"):
            if isinstance(data.get(key), list):
                return len(data[key])
        return 1 if data else 0
    return 0 if data in (None, "") else 1


class Budget:
    def __init__(self, limit: int):
        self.limit = max(0, int(limit))
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def charge(self) -> None:
        if self.used >= self.limit:
            raise BudgetExhausted(f"本期调用预算 {self.limit} 次已用完")
        self.used += 1


def mcp_transport(url: str, secret_key: str, timeout: float = 60.0) -> tuple[Transport, McpClient]:
    client = McpClient(url, headers={"secret-key": secret_key}, timeout=timeout,
                       client_name="furniture-radar")
    return client.call_tool, client


def request(**kwargs) -> dict:
    """大部分工具的参数包在 {"request": {...}} 里。"""
    return {"request": {k: v for k, v in kwargs.items() if v is not None}}


@dataclass
class Vendor:
    transport: Transport
    budget: Budget
    live: bool = True
    min_interval: float = 0.0
    # 返回 McpTool 列表的函数（tools/list 不计费），用来判断参数要不要包一层 request
    schema_loader: Callable[[], list] | None = None
    calls: list[dict] = field(default_factory=list)
    _cache: dict = field(default_factory=dict)
    _last_call_at: float = 0.0
    _schemas: dict | None = None

    def wants_request(self, tool: str, default: bool) -> bool:
        if self._schemas is None:
            self._schemas = {}
            if self.schema_loader is not None:
                try:
                    self._schemas = {t.name: t.input_schema for t in self.schema_loader()}
                except Exception:  # noqa: BLE001 — 拿不到 schema 就用默认形状
                    self._schemas = {}
        schema = self._schemas.get(tool)
        if schema:
            return "request" in (schema.get("properties") or {})
        return default

    def args(self, tool: str, *, nested: bool, **kwargs) -> dict:
        """按工具 schema 组织参数；nested 是拿不到 schema 时的默认形状。"""
        clean = {k: v for k, v in kwargs.items() if v is not None}
        return {"request": clean} if self.wants_request(tool, nested) else clean

    def call(self, tool: str, arguments: dict, *, purpose: str = "", prune: bool = True,
             _drift_retry: bool = True) -> Reply:
        if self.live and os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("RADAR_TEST_LIVE"):
            raise McpUnavailable("pytest 中禁止真实调用卖家精灵（设置 RADAR_TEST_LIVE=1 可显式开启）")

        prepared = field_sets.apply(tool, arguments) if prune else arguments
        key = hashlib.sha1(
            f"{tool}|{json.dumps(prepared, sort_keys=True, ensure_ascii=False, default=str)}".encode()
        ).hexdigest()
        if key in self._cache:
            data = self._cache[key]
            self.calls.append({"tool": tool, "purpose": purpose, "status": "cached", "billable": False})
            return Reply(tool, prepared, "cached", data, billable=False)

        self.budget.charge()
        payload = self._send(tool, prepared)
        if isinstance(payload, Reply):  # 工具级错误
            self.calls.append({"tool": tool, "purpose": purpose, "status": payload.status,
                               "billable": True, "detail": payload.detail[:200]})
            return payload

        try:
            envelope = json.loads(payload) if payload.strip() else {}
        except ValueError:
            self.calls.append({"tool": tool, "purpose": purpose, "status": "error", "billable": True})
            return Reply(tool, prepared, "error", detail=f"非 JSON 响应: {payload[:200]}", billable=True)

        if isinstance(envelope, dict) and "code" in envelope and str(envelope.get("code")).upper() != "OK":
            message = str(envelope.get("message") or envelope.get("code"))
            self.calls.append({"tool": tool, "purpose": purpose, "status": "rejected",
                               "billable": True, "detail": message[:200]})
            if any(hint in message.lower() for hint in _QUOTA_HINTS):
                raise QuotaExhausted(f"卖家精灵拒绝调用：{message}")
            return Reply(tool, prepared, "rejected", detail=message, billable=True)

        data = envelope.get("data") if isinstance(envelope, dict) and "data" in envelope else envelope
        rows = _row_count(data)
        if rows == 0 and _drift_retry and field_sets.has_fields(prepared):
            self.calls.append({"tool": tool, "purpose": purpose, "status": "field_drift", "billable": True})
            return self.call(tool, field_sets.strip(prepared), purpose=purpose, prune=False,
                             _drift_retry=False)

        status = "ok" if rows else "empty"
        self.calls.append({"tool": tool, "purpose": purpose, "status": status, "billable": True})
        if rows:
            self._cache[key] = data
        return Reply(tool, prepared, status, data, billable=True)

    def _send(self, tool: str, arguments: dict) -> str | Reply:
        wait = self.min_interval - (time.monotonic() - self._last_call_at)
        if wait > 0:
            time.sleep(wait)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                self._last_call_at = time.monotonic()
                return self.transport(tool, arguments)
            except McpToolError as exc:
                message = str(exc)
                if any(hint in message.lower() for hint in _QUOTA_HINTS):
                    raise QuotaExhausted(f"卖家精灵拒绝调用：{message}") from exc
                return Reply(tool, arguments, "rejected", detail=message, billable=True)
            except McpUnavailable as exc:
                # 401/403 是密钥问题，重试无用
                if exc.status_code in (401, 403):
                    raise
                last_error = exc
                time.sleep(3 * (attempt + 1))
        assert last_error is not None
        raise last_error

    @property
    def billable_calls(self) -> int:
        return sum(1 for c in self.calls if c.get("billable"))

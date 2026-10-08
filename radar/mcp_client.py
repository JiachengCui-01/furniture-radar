"""Minimal synchronous MCP client for the streamable-HTTP transport.

Copied from AI工作台 (Marketing-team-AI-Agent/src/marketing_agent/tools/mcp_client.py)
so both projects talk to the SellerSprite MCP server the same way.

MCP on the wire is just JSON-RPC 2.0 over HTTP POST, so this module speaks it
directly with ``httpx`` instead of taking on the asyncio-only official SDK.

Implemented: ``initialize`` (with the ``Mcp-Session-Id`` handshake and the
``notifications/initialized`` follow-up), ``tools/list`` with cursor pagination,
and ``tools/call``. Deliberately not implemented: resources, prompts, sampling,
server-to-client requests, and the optional ``GET`` listening channel.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

# Revision this client is written against. The server answers ``initialize`` with
# the version it actually chose, and that value is echoed on later requests.
PROTOCOL_VERSION = "2025-06-18"

__all__ = ["McpClient", "McpTool", "McpUnavailable", "McpToolError", "text_from_content"]


class McpUnavailable(RuntimeError):
    """The MCP server could not be reached, or answered at the protocol level.

    ``status_code`` is set when the failure came from an HTTP response, which lets
    callers tell a bad key (401/403) from a transient outage (5xx).
    """

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class McpToolError(RuntimeError):
    """The server ran the tool and reported a tool-level failure.

    Distinct from ``McpUnavailable``: the transport is healthy, so the caller
    should hand the message back to the model rather than treat the whole data
    source as down.
    """


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict


def _sse_messages(body: str) -> list[Any]:
    """Pull JSON payloads out of a ``text/event-stream`` response body.

    The transport may answer a POST with either plain JSON or a short SSE stream
    carrying the reply as one ``message`` event, so both shapes have to work.
    """
    messages: list[Any] = []
    for frame in body.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(
            line[5:].lstrip() for line in frame.split("\n") if line.startswith("data:")
        )
        if not data.strip():
            continue
        try:
            messages.append(json.loads(data))
        except ValueError:
            continue
    return messages


def _pick(messages: list[Any], request_id: str) -> dict | None:
    """Find the reply to ``request_id``, tolerating batched or unlabeled replies."""
    flat: list[Any] = []
    for message in messages:
        if isinstance(message, list):
            flat.extend(message)
        else:
            flat.append(message)
    for message in flat:
        if isinstance(message, dict) and str(message.get("id")) == request_id:
            return message
    # Some servers omit the id on a single-reply stream; a lone response is
    # unambiguous, so accept it rather than failing the call.
    if len(flat) == 1 and isinstance(flat[0], dict):
        return flat[0]
    return None


def text_from_content(content: Any) -> str:
    """Flatten an MCP tool result's content blocks into one string.

    Non-text blocks are named rather than dropped silently, so a model reading
    the result can tell that something came back which it cannot see.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append(str(block.get("text") or ""))
        elif kind == "resource":
            resource = block.get("resource") or {}
            parts.append(str(resource.get("text") or resource.get("uri") or ""))
        else:
            parts.append(f"[{kind or 'unknown'} content omitted]")
    return "\n".join(part for part in parts if part)


class McpClient:
    """One long-lived session against a remote streamable-HTTP MCP server."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 60.0,
        client_name: str = "furniture-radar",
    ) -> None:
        self.url = url
        self._extra_headers = dict(headers or {})
        self._client_name = client_name
        self._lock = threading.Lock()
        self._session_id: str | None = None
        self._protocol_version = PROTOCOL_VERSION
        self._initialized = False
        self._http = httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0))

    # -- transport ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # Either shape is a legal reply to a POST, so both must be accepted.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self._protocol_version,
            **self._extra_headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: dict) -> httpx.Response:
        try:
            return self._http.post(self.url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise McpUnavailable(f"Could not reach the MCP server at {self.url}: {exc}") from exc

    def _notify(self, method: str, params: dict | None = None) -> None:
        """Fire a notification. A failure here does not invalidate the session."""
        try:
            self._post({"jsonrpc": "2.0", "method": method, "params": params or {}})
        except McpUnavailable:
            pass

    def _decode(self, response: httpx.Response, request_id: str, method: str) -> dict:
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type == "text/event-stream":
            messages = _sse_messages(response.text)
        elif not response.text.strip():
            messages = []
        else:
            try:
                messages = [response.json()]
            except ValueError as exc:
                raise McpUnavailable(
                    f"The MCP server returned a non-JSON reply: {response.text[:300]}"
                ) from exc

        message = _pick(messages, request_id)
        if message is None:
            raise McpUnavailable(f"The MCP server sent no reply to '{method}'.")
        error = message.get("error")
        if error:
            raise McpUnavailable(
                f"MCP error {error.get('code')} on '{method}': {error.get('message')}"
            )
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def _request(self, method: str, params: dict | None = None) -> dict:
        self._ensure_initialized()
        request_id = uuid.uuid4().hex
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        response = self._post(payload)

        if response.status_code == 404 and self._session_id:
            # The server dropped our session. Re-handshake once and retry, so an
            # idle-expired session surfaces as a slower call, not a data outage.
            self._session_id = None
            self._initialized = False
            self._ensure_initialized()
            response = self._post(payload)

        if response.status_code >= 400:
            hint = " (check the secret key)" if response.status_code in (401, 403) else ""
            raise McpUnavailable(
                f"The MCP server returned {response.status_code}{hint}: {response.text[:300]}",
                status_code=response.status_code,
            )
        return self._decode(response, request_id, method)

    # -- protocol ----------------------------------------------------------

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            request_id = uuid.uuid4().hex
            response = self._post({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": self._client_name, "version": "1.0.0"},
                },
            })
            if response.status_code >= 400:
                hint = " (check the secret key)" if response.status_code in (401, 403) else ""
                raise McpUnavailable(
                    f"MCP initialize failed with {response.status_code}{hint}: "
                    f"{response.text[:300]}",
                    status_code=response.status_code,
                )
            # Capture the session id before parsing the body: that header is what
            # keeps every later request on the same server-side session.
            session_id = response.headers.get("mcp-session-id")
            if session_id:
                self._session_id = session_id
            result = self._decode(response, request_id, "initialize")
            negotiated = result.get("protocolVersion")
            if isinstance(negotiated, str) and negotiated:
                self._protocol_version = negotiated
            self._initialized = True
        self._notify("notifications/initialized")

    def list_tools(self) -> list[McpTool]:
        """Enumerate every tool the server offers, following cursor pagination."""
        tools: list[McpTool] = []
        cursor: str | None = None
        # Bounded so a server that keeps handing back a cursor cannot spin forever.
        for _ in range(10):
            result = self._request("tools/list", {"cursor": cursor} if cursor else {})
            for raw in result.get("tools") or []:
                if not isinstance(raw, dict) or not raw.get("name"):
                    continue
                schema = raw.get("inputSchema") or raw.get("input_schema") or {}
                tools.append(
                    McpTool(
                        name=str(raw["name"]),
                        description=str(raw.get("description") or ""),
                        input_schema=schema if isinstance(schema, dict) else {},
                    )
                )
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        """Invoke one tool and return its content flattened to text."""
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        text = text_from_content(result.get("content"))
        structured = result.get("structuredContent")
        if structured is not None and not text:
            text = json.dumps(structured, ensure_ascii=False)
        if result.get("isError"):
            raise McpToolError(text or f"The MCP tool '{name}' reported an error.")
        return text

    def close(self) -> None:
        self._http.close()
        self._initialized = False
        self._session_id = None

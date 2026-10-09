"""Tripwire MCP server (E4): put any MCP-speaking agent behind the Tripwire checkpoint.

    uv run python -m tripwire.mcp_server            # stdio; add it to Claude Code / any MCP client

Dependency-free (no MCP SDK): JSON-RPC 2.0 over stdio, one JSON message per line, MCP protocol
version ``2025-06-18``. Implements ``initialize``, ``notifications/initialized``, ``ping``,
``tools/list`` and ``tools/call``; anything else gets a JSON-RPC error. stdout carries ONLY
protocol messages; every log line goes to stderr.

Tools (record-only: Tripwire decides and records, it never executes the action):

* ``tripwire_tool``      POST {checkpoint}/tool with ``agent_id`` forced to ``mcp:<name>``
                         (env ``TRIPWIRE_MCP_AGENT``, default ``mcp:agent``). A denial is a normal
                         tool result (isError false) whose first line is ``DENIED by Tripwire: ...``
                         so the calling agent stops; checkpoint down / HTTP errors are isError true.
* ``tripwire_status``    GET {checkpoint}/status summary (active, blocked, modes, open incidents).
* ``tripwire_incidents`` GET {checkpoint}/incidents, newest 10.

The checkpoint writes every ``tripwire_tool`` call to ClickHouse (``tripwire.events``) under the
``mcp:*`` agent id, exactly like a fleet agent. ``X-Tripwire-Token`` is sent when PUBLIC=1; the
token value is never logged or returned.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, BinaryIO, Optional

import httpx
from loguru import logger
from pydantic import ValidationError

from tripwire.config import Settings, get_settings
from tripwire.contracts import ACTIONS, Incident, StatusResponse, ToolCall, ToolResult

PROTOCOL_VERSION = "2025-06-18"
# Older revisions we can also speak (no feature we use differs); anything else gets ours.
SUPPORTED_VERSIONS = (PROTOCOL_VERSION, "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "tripwire", "title": "Tripwire checkpoint", "version": "0.1.0"}
INSTRUCTIONS = (
    "Tripwire is the security checkpoint for this agent. Before any risky action (reading files, "
    "running commands, HTTP requests, role changes, logging changes) call tripwire_tool with the "
    "action and target. If the result says DENIED, stop: do not retry or work around it. "
    "tripwire_tool is record-only: Tripwire decides and records the action, it does not perform it."
)

AGENT_ENV = "TRIPWIRE_MCP_AGENT"
AGENT_PREFIX = "mcp:"
DEFAULT_AGENT = "mcp:agent"
# Same rule as checkpoint/app.py check_agent_id (ids end up in URL paths).
AGENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.\-]{0,63}")

TOOL_TIMEOUT = httpx.Timeout(20.0, connect=3.0)  # hold mode may wait on a model verdict
READ_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
INCIDENTS_SHOWN = 10
DETAIL_MAX = 300
MAX_FIELD = 4096  # target / tainted_by length cap (payload: 64 KiB)
MAX_PAYLOAD = 64 * 1024

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# What each checkpoint denial reason means (checkpoint/service.py handle_tool, checkpoint/hold.py).
REASON_TEXT = {
    "blocked": "this agent is quarantined; every action is denied until a human restores it",
    "honeytoken": "the outbound payload carried a decoy credential (honeytoken)",
    "hold_policy": "policy: a fixed-deny action or a denylisted destination (no model call)",
    "hold_model": "hold mode: the model verdict said this send is not safe",
    "hold_rule": "hold mode: a detection rule flagged this send",
}


def mcp_agent_id(raw: Optional[str]) -> str:
    """Force an id into the mcp: namespace ('' -> mcp:agent) and validate it like the checkpoint."""
    aid = (raw or "").strip()
    if not aid:
        aid = DEFAULT_AGENT
    if not aid.startswith(AGENT_PREFIX):
        aid = AGENT_PREFIX + aid
    if len(aid) <= len(AGENT_PREFIX) or not AGENT_ID_RE.fullmatch(aid) or ".." in aid:
        raise ValueError(
            f"{AGENT_ENV} must give an agent id matching [A-Za-z0-9][A-Za-z0-9:_.-]{{0,63}} "
            "without '..' (after the mcp: prefix)"
        )
    return aid


TOOLS: list[dict[str, Any]] = [
    {
        "name": "tripwire_tool",
        "title": "Submit an action to Tripwire",
        "description": (
            "Submit ONE agent action to the Tripwire checkpoint for a decision before doing it. "
            "Record-only: Tripwire records and decides, it does not execute anything. Returns "
            "result ok / denied / error. If the text starts with 'DENIED by Tripwire', stop: do not "
            "retry or work around it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS), "description": "What the agent wants to do."},
                "target": {
                    "type": "string",
                    "description": "File path, shell command, URL, or role the action applies to.",
                },
                "payload": {
                    "type": "string",
                    "description": "Outbound body for http_post. Scanned for honeytokens, never stored raw.",
                },
                "tainted_by": {
                    "type": "string",
                    "description": "Id of the untrusted input read before this action, e.g. 'ticket:4821'.",
                },
            },
            "required": ["action", "target"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "result": {"type": "string", "enum": ["ok", "denied", "error"]},
                "reason": {"type": "string"},
                "incident_id": {"type": ["string", "null"]},
                "ts_ms": {"type": "integer"},
            },
            "required": ["agent_id", "result", "reason"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "tripwire_status",
        "title": "Tripwire fleet status",
        "description": (
            "Live checkpoint status: active and blocked agents, agent modes (heightened / quarantined), "
            "open incidents, hold mode, policy version, and this MCP agent's own state."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "tripwire_incidents",
        "title": "Recent Tripwire incidents",
        "description": (
            f"The newest {INCIDENTS_SHOWN} incidents: id, agent, rule, verdict, decision_source "
            "(who decided: policy, akashml, openai, rule_only, quorum, honeytoken) and open/closed."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]
TOOL_NAMES = {t["name"] for t in TOOLS}


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _text(text: str, *, is_error: bool = False, structured: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if structured is not None:
        out["structuredContent"] = structured
    return out


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        detail = body.get("detail", body) if isinstance(body, dict) else body
        text = detail if isinstance(detail, str) else json.dumps(detail)
    except ValueError:
        text = resp.text
    return text[:DETAIL_MAX]


def _dash(v: Any) -> str:
    return "—" if v is None or v == "" else str(v)


class TripwireMCP:
    """The protocol handler. ``handle(msg)`` returns the response dict, or None for notifications."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        agent_name: Optional[str] = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.agent_id = mcp_agent_id(agent_name if agent_name is not None else os.environ.get(AGENT_ENV))
        self.base = self.settings.checkpoint_url.rstrip("/")
        headers = {"X-Tripwire-Token": self.settings.tripwire_token} if self.settings.public else {}
        self.http = httpx.Client(base_url=self.base, headers=headers, transport=transport, timeout=READ_TIMEOUT)
        self.initialized = False
        self.client_info: dict[str, Any] = {}

    def close(self) -> None:
        self.http.close()

    # -- JSON-RPC framing ---------------------------------------------------------------------

    def handle_line(self, line: str) -> Optional[str]:
        line = line.strip()
        if not line:
            return None
        try:
            msg = json.loads(line)
        except ValueError:
            return json.dumps(_error(None, PARSE_ERROR, "parse error: not valid JSON"))
        resp = self.handle(msg)
        return None if resp is None else json.dumps(resp, ensure_ascii=False, separators=(",", ":"))

    def handle(self, msg: Any) -> Optional[dict[str, Any]]:
        if isinstance(msg, list):
            return _error(None, INVALID_REQUEST, "batch requests are not supported (MCP 2025-06-18)")
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _error(_id_of(msg), INVALID_REQUEST, "invalid request: expected a JSON-RPC 2.0 object")
        method = msg.get("method")
        is_notification = "id" not in msg
        if not isinstance(method, str):
            # A response from the client (we never send requests) or garbage: nothing to answer.
            return None if is_notification else _error(msg.get("id"), INVALID_REQUEST, "missing method")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return None if is_notification else _error(msg["id"], INVALID_PARAMS, "params must be an object")

        if is_notification:
            if method == "notifications/initialized":
                self.initialized = True
                logger.info("mcp: client initialized")
            return None  # notifications never get a response (incl. cancelled / unknown ones)

        rid = msg["id"]
        try:
            result = self.dispatch(method, params)
        except RpcError as exc:
            return _error(rid, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 - never crash the stdio loop
            logger.exception(f"mcp: {method} failed")
            return _error(rid, INTERNAL_ERROR, f"internal error: {type(exc).__name__}")
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return self.initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self.call_tool(params)
        raise RpcError(METHOD_NOT_FOUND, f"method not found: {method}")

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else PROTOCOL_VERSION
        info = params.get("clientInfo")
        self.client_info = info if isinstance(info, dict) else {}
        logger.info(
            f"mcp: initialize client={self.client_info.get('name', '?')} protocol={version} "
            f"agent_id={self.agent_id} checkpoint={self.base}"
        )
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        }

    def call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if name not in TOOL_NAMES:
            raise RpcError(INVALID_PARAMS, f"unknown tool: {name!r} (have {', '.join(sorted(TOOL_NAMES))})")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise RpcError(INVALID_PARAMS, "arguments must be an object")
        if name == "tripwire_tool":
            return self.tool_submit(args)
        if name == "tripwire_status":
            return self.tool_status()
        return self.tool_incidents()

    # -- checkpoint calls ---------------------------------------------------------------------

    def _unreachable(self, what: str, exc: Exception, *, action: bool = False) -> dict[str, Any]:
        logger.warning(f"mcp: checkpoint unreachable for {what}: {type(exc).__name__}")
        return _text(
            f"Tripwire checkpoint unreachable at {self.base} ({type(exc).__name__}): {what} did not reach "
            "Tripwire, so nothing was recorded or approved." + (" Treat the action as NOT allowed." if action else ""),
            is_error=True,
        )

    def _http_error(self, what: str, resp: httpx.Response) -> dict[str, Any]:
        logger.warning(f"mcp: {what} -> HTTP {resp.status_code}")
        return _text(
            f"Tripwire checkpoint answered HTTP {resp.status_code} for {what}: {_detail(resp)}",
            is_error=True,
        )

    def tool_submit(self, args: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(args) - {"action", "target", "payload", "tainted_by"})
        action, target = args.get("action"), args.get("target")
        payload, tainted_by = args.get("payload", ""), args.get("tainted_by", "")
        problems: list[str] = []
        if unknown:
            problems.append(f"unknown argument(s): {', '.join(unknown)}")
        if action not in ACTIONS:
            problems.append(f"action must be one of: {', '.join(ACTIONS)}")
        if not isinstance(target, str) or not target.strip() or len(target) > MAX_FIELD:
            problems.append(f"target must be a non-empty string (max {MAX_FIELD} chars)")
        if not isinstance(payload, str) or len(payload) > MAX_PAYLOAD:
            problems.append(f"payload must be a string (max {MAX_PAYLOAD} chars)")
        if not isinstance(tainted_by, str) or len(tainted_by) > MAX_FIELD:
            problems.append(f"tainted_by must be a string (max {MAX_FIELD} chars)")
        if problems:
            return _text("Invalid tripwire_tool call, nothing was submitted: " + "; ".join(problems), is_error=True)

        call = ToolCall(
            agent_id=self.agent_id,
            action=action,
            target=target,
            payload=payload,
            tainted_by=tainted_by,
            bytes=len(payload.encode("utf-8")),
        )
        what = f"{action} {target[:120]}"
        try:
            resp = self.http.post("/tool", json=call.model_dump(), timeout=TOOL_TIMEOUT)
        except httpx.HTTPError as exc:
            return self._unreachable(f"the action ({what})", exc, action=True)
        if resp.status_code >= 400:
            return self._http_error(f"the action ({what})", resp)
        try:
            res = ToolResult.model_validate(resp.json())
        except (ValueError, ValidationError):
            return _text("Tripwire checkpoint returned a response that is not a ToolResult.", is_error=True)

        data = res.model_dump()
        logger.info(f"mcp: {self.agent_id} {action} -> {res.result} {res.reason or ''}".rstrip())
        incident = f" Incident: {res.incident_id}." if res.incident_id else ""
        if res.result == "denied":
            why = REASON_TEXT.get(res.reason, "")
            text = (
                f"DENIED by Tripwire: {res.reason or 'denied'}" + (f" ({why})" if why else "") + "\n"
                f"Do not perform, retry or work around this action ({what}). "
                f"Recorded as {res.agent_id}.{incident}"
            )
            return _text(text, structured=data)
        if res.result == "error":
            text = f"Tripwire checkpoint returned result=error for {what} (reason: {_dash(res.reason)}).{incident}"
            return _text(text, is_error=True, structured=data)
        text = (
            f"ALLOWED by Tripwire: {what}\n"
            f"Recorded as {res.agent_id}. Record-only: Tripwire decided and recorded it, it did not execute it."
        )
        return _text(text, structured=data)

    def tool_status(self) -> dict[str, Any]:
        try:
            resp = self.http.get("/status")
        except httpx.HTTPError as exc:
            return self._unreachable("GET /status", exc)
        if resp.status_code >= 400:
            return self._http_error("GET /status", resp)
        try:
            st = StatusResponse.model_validate(resp.json())
        except (ValueError, ValidationError):
            return _text("Tripwire checkpoint returned a /status body that does not match StatusResponse.", is_error=True)
        modes = {a: m for a, m in st.modes.items() if m != "normal"}
        me_mode = st.modes.get(self.agent_id, "normal")
        data = {
            "checkpoint": self.base,
            "active": st.active,
            "blocked": st.blocked,
            "modes": modes,
            "open_incidents": len(st.open_incidents),
            "open_incident_ids": [i.id for i in st.open_incidents],
            "hold_enabled": st.hold_enabled,
            "policy_version": st.policy_version,
            "me": {"agent_id": self.agent_id, "blocked": self.agent_id in st.blocked, "mode": me_mode},
        }
        lines = [
            f"Tripwire checkpoint {self.base}",
            f"active agents ({len(st.active)}): {', '.join(st.active) or '—'}",
            f"blocked agents ({len(st.blocked)}): {', '.join(st.blocked) or '—'}",
            f"non-normal modes: {', '.join(f'{a}={m}' for a, m in modes.items()) or '—'}",
            f"open incidents: {len(st.open_incidents)}",
            f"hold mode: {'on' if st.hold_enabled else 'off'} · policy v{st.policy_version}",
            f"this agent: {self.agent_id} · {'BLOCKED' if data['me']['blocked'] else me_mode}",
        ]
        return _text("\n".join(lines), structured=data)

    def tool_incidents(self) -> dict[str, Any]:
        try:
            resp = self.http.get("/incidents")
        except httpx.HTTPError as exc:
            return self._unreachable("GET /incidents", exc)
        if resp.status_code >= 400:
            return self._http_error("GET /incidents", resp)
        try:
            body = resp.json()
            incidents = [Incident.model_validate(i) for i in body] if isinstance(body, list) else None
        except (ValueError, ValidationError):
            incidents = None
        if incidents is None:
            return _text("Tripwire checkpoint returned an /incidents body that is not a list of incidents.", is_error=True)
        newest = sorted(incidents, key=lambda i: (i.opened_ms, i.id), reverse=True)[:INCIDENTS_SHOWN]
        rows = [
            {
                "id": i.id,
                "agent_id": i.agent_id,
                "rule": i.rule,
                "verdict": i.verdict.verdict if i.verdict else None,
                "decision_source": i.verdict.decision_source if i.verdict else None,
                "open": i.closed_ms is None,
                "opened_ms": i.opened_ms,
            }
            for i in newest
        ]
        data = {"total": len(incidents), "shown": len(rows), "incidents": rows}
        if not rows:
            return _text("No incidents recorded by the checkpoint.", structured=data)
        lines = [f"Newest {len(rows)} of {len(incidents)} incidents (id · agent · rule · verdict · decided by · state):"]
        lines += [
            f"{r['id']} · {r['agent_id']} · {r['rule']} · {_dash(r['verdict'])} · {_dash(r['decision_source'])} · "
            f"{'open' if r['open'] else 'closed'}"
            for r in rows
        ]
        return _text("\n".join(lines), structured=data)


def _id_of(msg: Any) -> Any:
    return msg.get("id") if isinstance(msg, dict) else None


def _error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def serve(server: TripwireMCP, inp: BinaryIO, out: BinaryIO) -> None:
    """Newline-delimited JSON-RPC loop: one message per line in, one response per line out."""
    for raw in inp:
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            reply: Optional[str] = json.dumps(_error(None, PARSE_ERROR, "parse error: not UTF-8"))
        else:
            reply = server.handle_line(line)
        if reply is not None:
            out.write(reply.encode("utf-8") + b"\n")
            out.flush()


def main() -> int:
    logger.remove()
    logger.add(sys.stderr, level=os.environ.get("TRIPWIRE_MCP_LOG", "INFO"))  # stdout is the protocol
    try:
        server = TripwireMCP()
    except ValueError as exc:
        logger.error(f"mcp: {exc}")
        return 2
    logger.info(f"mcp: tripwire MCP server on stdio as {server.agent_id} -> {server.base}")
    try:
        serve(server, sys.stdin.buffer, sys.stdout.buffer)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

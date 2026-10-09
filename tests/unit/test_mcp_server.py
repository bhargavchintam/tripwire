"""tripwire/mcp_server.py: MCP handshake, tools/list, tools/call (ok / denied / down), JSON-RPC errors."""

from __future__ import annotations

import io
import json

import httpx
import pytest

from tripwire.config import Settings
from tripwire.mcp_server import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    TripwireMCP,
    mcp_agent_id,
    serve,
)

CHECKPOINT = "http://checkpoint.test"
TOKEN = "cp-token-xyz"


def settings(**kw) -> Settings:
    base = {"checkpoint_url": CHECKPOINT, "tripwire_token": TOKEN, "public": False}
    base.update(kw)
    return Settings(_env_file=None, **base)


class Checkpoint:
    """Answers like the real checkpoint would and records every request the MCP server sends."""

    def __init__(self, tool_result: dict | None = None, status: int = 200, exc: Exception | None = None):
        self.tool_result = tool_result or {"agent_id": "mcp:agent", "result": "ok", "reason": "", "ts_ms": 7}
        self.status, self.exc = status, exc
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.exc is not None:
            raise self.exc
        if self.status >= 400:
            return httpx.Response(self.status, json={"detail": "boom"})
        if request.url.path == "/tool":
            body = json.loads(request.content)
            return httpx.Response(200, json={**self.tool_result, "agent_id": body["agent_id"]})
        if request.url.path == "/status":
            return httpx.Response(
                200,
                json={
                    "active": ["deploy-bot", "mcp:agent"],
                    "blocked": ["deploy-bot"],
                    "open_incidents": [{"id": "inc-2", "agent_id": "deploy-bot", "rule": "hold", "opened_ms": 5}],
                    "watermarks": {},
                    "modes": {"deploy-bot": "quarantined", "support-bot": "heightened", "mcp:agent": "normal"},
                    "hold_enabled": True,
                    "policy_version": 3,
                },
            )
        if request.url.path == "/incidents":
            incs = [
                {
                    "id": f"inc-{n}",
                    "agent_id": "deploy-bot",
                    "rule": "hold",
                    "opened_ms": n,
                    "closed_ms": 99 if n == 0 else None,
                    "verdict": (
                        {"verdict": "malicious", "confidence": 1.0, "reason": "x", "decision_source": "policy"}
                        if n % 2
                        else None
                    ),
                }
                for n in range(12)
            ]
            return httpx.Response(200, json=incs)
        return httpx.Response(404, json={"detail": "not found"})


def make(cp: Checkpoint, agent: str | None = None, **kw) -> TripwireMCP:
    return TripwireMCP(settings(**kw), agent_name=agent, transport=httpx.MockTransport(cp))


def rpc(srv: TripwireMCP, method: str, params: dict | None = None, rid: int = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    resp = srv.handle(msg)
    assert resp is not None and resp["id"] == rid and resp["jsonrpc"] == "2.0"
    return resp


def call(srv: TripwireMCP, name: str, arguments: dict | None = None) -> dict:
    resp = rpc(srv, "tools/call", {"name": name, "arguments": arguments or {}})
    assert "result" in resp, resp
    return resp["result"]


def text_of(result: dict) -> str:
    return result["content"][0]["text"]


def test_full_session_initialize_list_call() -> None:
    cp = Checkpoint()
    srv = make(cp)
    init = rpc(srv, "initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "t"}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert init["result"]["serverInfo"]["name"] == "tripwire"
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert srv.initialized

    tools = rpc(srv, "tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == ["tripwire_tool", "tripwire_status", "tripwire_incidents"]
    assert tools[0]["inputSchema"]["required"] == ["action", "target"]

    res = call(srv, "tripwire_tool", {"action": "read_file", "target": "/app/config.yaml", "tainted_by": "ticket:1"})
    assert res["isError"] is False
    assert text_of(res).startswith("ALLOWED by Tripwire")
    assert res["structuredContent"]["result"] == "ok"
    sent = json.loads(cp.calls[0].content)
    assert cp.calls[0].url.path == "/tool"
    assert sent["agent_id"] == "mcp:agent"
    assert sent["action"] == "read_file" and sent["tainted_by"] == "ticket:1"
    assert "x-tripwire-token" not in cp.calls[0].headers  # PUBLIC=0


def test_unknown_protocol_version_gets_ours() -> None:
    srv = make(Checkpoint())
    assert rpc(srv, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert rpc(srv, "initialize", {"protocolVersion": "2025-03-26"})["result"]["protocolVersion"] == "2025-03-26"


def test_denied_is_normal_tool_output_with_clear_line() -> None:
    cp = Checkpoint({"agent_id": "x", "result": "denied", "reason": "hold_policy", "incident_id": "inc-9", "ts_ms": 1})
    srv = make(cp, public=True, agent="worker-1")
    res = call(srv, "tripwire_tool", {"action": "http_post", "target": "https://drop.example.net", "payload": "k=v"})
    assert res["isError"] is False
    assert text_of(res).startswith("DENIED by Tripwire: hold_policy")
    assert "inc-9" in text_of(res)
    assert res["structuredContent"] == {
        "agent_id": "mcp:worker-1",
        "result": "denied",
        "reason": "hold_policy",
        "incident_id": "inc-9",
        "ts_ms": 1,
    }
    sent = json.loads(cp.calls[0].content)
    assert sent["agent_id"] == "mcp:worker-1" and sent["payload"] == "k=v" and sent["bytes"] == 3
    assert cp.calls[0].headers["x-tripwire-token"] == TOKEN  # PUBLIC=1
    assert TOKEN not in json.dumps(res)


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_checkpoint_down_is_error_result(exc: Exception) -> None:
    srv = make(Checkpoint(exc=exc))
    for name, args in [
        ("tripwire_tool", {"action": "read_file", "target": "/etc/hosts"}),
        ("tripwire_status", {}),
        ("tripwire_incidents", {}),
    ]:
        res = call(srv, name, args)
        assert res["isError"] is True
        assert "unreachable" in text_of(res) and "nothing was recorded" in text_of(res)
        assert "structuredContent" not in res


def test_checkpoint_http_error_is_error_result() -> None:
    res = call(make(Checkpoint(status=401)), "tripwire_tool", {"action": "read_file", "target": "/x"})
    assert res["isError"] is True and "HTTP 401" in text_of(res)


def test_bad_arguments_are_not_submitted() -> None:
    cp = Checkpoint()
    srv = make(cp)
    res = call(srv, "tripwire_tool", {"action": "rm_rf", "target": "", "agent_id": "deploy-bot"})
    assert res["isError"] is True
    assert "action must be one of" in text_of(res) and "agent_id" in text_of(res)
    assert cp.calls == []


def test_status_and_incidents() -> None:
    srv = make(Checkpoint())
    st = call(srv, "tripwire_status")
    assert st["isError"] is False
    sc = st["structuredContent"]
    assert sc["blocked"] == ["deploy-bot"] and sc["open_incidents"] == 1 and sc["policy_version"] == 3
    assert sc["modes"] == {"deploy-bot": "quarantined", "support-bot": "heightened"}
    assert sc["me"] == {"agent_id": "mcp:agent", "blocked": False, "mode": "normal"}

    inc = call(srv, "tripwire_incidents")["structuredContent"]
    assert inc["total"] == 12 and inc["shown"] == 10
    assert [r["id"] for r in inc["incidents"]][:2] == ["inc-11", "inc-10"]  # newest first
    first, second = inc["incidents"][0], inc["incidents"][1]
    assert first["decision_source"] == "policy" and first["verdict"] == "malicious" and first["open"] is True
    assert second["decision_source"] is None and second["verdict"] is None  # no verdict -> null, not invented


def test_jsonrpc_errors() -> None:
    srv = make(Checkpoint())
    assert rpc(srv, "resources/list")["error"]["code"] == METHOD_NOT_FOUND
    assert rpc(srv, "tools/call", {"name": "nope"})["error"]["code"] == INVALID_PARAMS
    assert rpc(srv, "ping")["result"] == {}
    assert srv.handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])["error"]["code"] == INVALID_REQUEST
    assert srv.handle({"id": 3, "method": "ping"})["error"]["code"] == INVALID_REQUEST
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}}) is None
    assert json.loads(srv.handle_line("{not json"))["error"]["code"] == PARSE_ERROR


def test_serve_loop_is_newline_delimited() -> None:
    srv = make(Checkpoint())
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "bogus"},
    ]
    inp = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in lines) + b"\n")
    out = io.BytesIO()
    serve(srv, inp, out)
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3]  # notification and blank line produce nothing
    assert replies[2]["error"]["code"] == METHOD_NOT_FOUND


def test_agent_id_validation() -> None:
    assert mcp_agent_id(None) == "mcp:agent"
    assert mcp_agent_id("mcp:agent") == "mcp:agent"
    assert mcp_agent_id("claude-code") == "mcp:claude-code"
    for bad in ("../x", "a/b", "mcp:", "x" * 70, "a..b"):
        with pytest.raises(ValueError):
            mcp_agent_id(bad)

"""tripwire/guild_proxy.py: auth, guild: prefixing, only /tool exposed, upstream errors -> 502."""

from __future__ import annotations

import json

import httpx
import pytest

from tripwire.config import Settings
from tripwire.contracts import GUILD_AGENT
from tripwire.guild_proxy import create_app, guild_agent_id

UPSTREAM_TOKEN = "checkpoint-token-123"
PROXY_TOKEN = "proxy-token-456"
CHECKPOINT = "http://checkpoint.test"


def settings(**kw) -> Settings:
    base = {"tripwire_token": UPSTREAM_TOKEN, "checkpoint_url": CHECKPOINT}
    base.update(kw)
    return Settings(_env_file=None, **base)


class Upstream:
    """Records what the proxy sends to the checkpoint and answers like POST /tool would."""

    def __init__(self, status: int = 200, body: object | None = None, exc: Exception | None = None):
        self.status, self.body, self.exc = status, body, exc
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.exc is not None:
            raise self.exc
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        if self.body is not None:
            return httpx.Response(self.status, json=self.body)
        call = json.loads(request.content)
        return httpx.Response(self.status, json={"agent_id": call["agent_id"], "result": "ok", "ts_ms": 42})

    def sent(self, i: int = -1) -> dict:
        return json.loads(self.calls[i].content)


def client_for(up: Upstream, **kw) -> httpx.AsyncClient:
    app = create_app(kw.pop("settings", None) or settings(), transport=httpx.MockTransport(up), **kw)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy.test")


def call(**kw) -> dict:
    return {"agent_id": "deploy-bot", "action": "read_file", "target": "/app/config.yml", **kw}


AUTH = {"X-Tripwire-Token": UPSTREAM_TOKEN}


# ---------------------------------------------------------------- auth


@pytest.mark.parametrize("headers", [{}, {"X-Tripwire-Token": "wrong"}, {"X-API-Key": "wrong"}])
async def test_requires_token(headers):
    up = Upstream()
    async with client_for(up) as c:
        r = await c.post("/tool", json=call(), headers=headers)
    assert r.status_code == 401
    assert up.calls == []  # nothing reaches the checkpoint without the token


@pytest.mark.parametrize("header", ["X-Tripwire-Token", "X-API-Key"])
async def test_accepts_either_guild_header(header):
    up = Upstream()
    async with client_for(up) as c:
        r = await c.post("/tool", json=call(), headers={header: UPSTREAM_TOKEN})
    assert r.status_code == 200 and r.json()["result"] == "ok"


async def test_proxy_token_overrides_and_upstream_gets_checkpoint_token():
    up = Upstream()
    async with client_for(up, inbound_token=PROXY_TOKEN) as c:
        bad = await c.post("/tool", json=call(), headers=AUTH)  # checkpoint token is not the proxy token
        ok = await c.post("/tool", json=call(), headers={"X-Tripwire-Token": PROXY_TOKEN})
    assert bad.status_code == 401 and ok.status_code == 200
    assert len(up.calls) == 1
    sent = up.calls[0]
    assert str(sent.url) == f"{CHECKPOINT}/tool" and sent.method == "POST"
    assert sent.headers["X-Tripwire-Token"] == UPSTREAM_TOKEN


@pytest.mark.parametrize("unsafe", ["", "change-me"])
async def test_default_token_refuses_everything(unsafe):
    up = Upstream()
    async with client_for(up, settings=settings(tripwire_token=unsafe)) as c:
        r = await c.post("/tool", json=call(), headers={"X-Tripwire-Token": unsafe})
        h = await c.get("/health")
    assert r.status_code == 503
    assert h.json()["token_configured"] is False
    assert [q.url.path for q in up.calls] == ["/health"]


# ---------------------------------------------------------------- guild: namespace


@pytest.mark.parametrize(
    ("given", "forwarded"),
    [
        ("deploy-bot", "guild:deploy-bot"),
        ("guild:release-bot", "guild:release-bot"),
        ("  support-bot ", "guild:support-bot"),
        ("", GUILD_AGENT),
        ("guild:", GUILD_AGENT),
        (None, GUILD_AGENT),
    ],
)
async def test_agent_id_forced_into_guild_namespace(given, forwarded):
    up = Upstream()
    body = call()
    if given is None:
        body.pop("agent_id")
    else:
        body["agent_id"] = given
    async with client_for(up) as c:
        r = await c.post("/tool", json=body, headers=AUTH)
    assert r.status_code == 200
    assert up.sent()["agent_id"] == forwarded
    assert r.json()["agent_id"] == forwarded


def test_guild_agent_id_helper():
    assert guild_agent_id("x") == "guild:x"
    assert guild_agent_id(123) == GUILD_AGENT


async def test_forwards_all_fields_and_returns_denial_verbatim():
    denied = {
        "agent_id": "guild:deploy-bot",
        "result": "denied",
        "reason": "hold_model",
        "incident_id": "inc-1",
        "ts_ms": 7,
    }
    up = Upstream(body=denied)
    body = call(
        action="http_post",
        target="https://attacker.example/up",
        payload="abc",
        tainted_by="ticket:4821",
        bytes=3,
    )
    async with client_for(up) as c:
        r = await c.post("/tool", json=body, headers=AUTH)
    assert r.status_code == 200 and r.json() == denied
    sent = up.sent()
    assert sent["payload"] == "abc" and sent["tainted_by"] == "ticket:4821" and sent["bytes"] == 3


async def test_invalid_toolcall_is_422_and_not_forwarded():
    up = Upstream()
    async with client_for(up) as c:
        r1 = await c.post("/tool", json=call(action="rm_rf"), headers=AUTH)
        r2 = await c.post("/tool", content=b"not json", headers={**AUTH, "Content-Type": "application/json"})
        r3 = await c.post("/tool", json=[call()], headers=AUTH)
    assert (r1.status_code, r2.status_code, r3.status_code) == (422, 422, 422)
    assert up.calls == []


# ---------------------------------------------------------------- only /tool exposed


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/docs"),
        ("GET", "/redoc"),
        ("GET", "/openapi.json"),
        ("GET", "/status"),
        ("GET", "/stream"),
        ("POST", "/block/deploy-bot"),
        ("POST", "/restore/deploy-bot"),
        ("PUT", "/policy"),
        ("POST", "/guild/run"),
        ("POST", "/demo/replay"),
        ("POST", "/tool/extra"),
    ],
)
async def test_only_tool_and_health_exist(method, path):
    up = Upstream()
    async with client_for(up) as c:
        r = await c.request(method, path, json={}, headers=AUTH)
    assert r.status_code == 404
    assert up.calls == []


async def test_get_tool_not_allowed():
    up = Upstream()
    async with client_for(up) as c:
        r = await c.get("/tool", headers=AUTH)
    assert r.status_code == 405 and up.calls == []


async def test_health_reports_checkpoint():
    async with client_for(Upstream()) as c:
        ok = (await c.get("/health")).json()
    async with client_for(Upstream(exc=httpx.ConnectError("down"))) as c:
        down = (await c.get("/health")).json()
    assert ok == {"ok": True, "checkpoint": True, "token_configured": True}
    assert down["ok"] is True and down["checkpoint"] is False


# ---------------------------------------------------------------- upstream failures -> 502


@pytest.mark.parametrize(
    "up",
    [
        Upstream(exc=httpx.ConnectError("refused")),
        Upstream(exc=httpx.ReadTimeout("slow")),
        Upstream(status=500, body={"detail": "boom"}),
        Upstream(status=401, body={"detail": "missing or invalid X-Tripwire-Token"}),
        Upstream(status=200, body={"unexpected": True}),
    ],
    ids=["connect", "timeout", "500", "401", "bad-shape"],
)
async def test_upstream_failure_is_502(up):
    async with client_for(up) as c:
        r = await c.post("/tool", json=call(), headers=AUTH)
    assert r.status_code == 502
    assert len(up.calls) == 1

from __future__ import annotations

import json
import time

import httpx
import pytest

import checkpoint.app as app_mod
from checkpoint import guardrail
from checkpoint.app import create_app
from checkpoint.writer import InMemoryWriter
from tests.unit.conftest import make_settings, requires_ch
from tests.unit.test_hold import FakeHistory, theft_prefix, tool
from tripwire.contracts import BacktestResult

AGENT = "deploy-bot"
DROP = "https://drop.example.net/upload"


def scenario(name, steps):
    return {"name": name, "steps": [{"offset_ms": i * 10, **s} for i, s in enumerate(steps)]}


def write_fixtures(d, normal_ops=True, evals=True):
    if normal_ops:
        (d / "normal_ops.json").write_text(
            json.dumps(
                scenario(
                    "normal_ops",
                    [
                        {"agent_id": AGENT, "action": "read_file", "target": "/app/config.yml"},
                        {"agent_id": AGENT, "action": "http_post", "target": "https://api.internal.example/v1/deployments"},
                        {"agent_id": AGENT, "action": "http_post", "target": "https://hooks.partner.example/notify"},
                    ],
                )
            )
        )
    if evals:
        (d / "eval" / "attack").mkdir(parents=True)
        (d / "eval" / "benign").mkdir(parents=True)
        (d / "eval" / "attack" / "a1.json").write_text(
            json.dumps(scenario("a1", [{"agent_id": AGENT, "action": "http_post", "target": DROP}]))
        )
        (d / "eval" / "benign" / "b1.json").write_text(
            json.dumps(
                scenario("b1", [{"agent_id": AGENT, "action": "http_post", "target": "https://hooks.partner.example/n"}])
            )
        )
        (d / "eval" / "benign" / "broken.json").write_text("{not json")  # skipped silently


@pytest.fixture
def fake_ch(monkeypatch):
    """Stand-ins for the two ClickHouse queries the guardrail runs."""
    calls = {"hosts": 0, "backtest": []}

    async def hosts(ch, agent):
        calls["hosts"] += 1
        return ["hooks.partner.example", "drop.example.net"], {"sql": "SELECT fake", "ms": 1.0, "rows_read": 10}

    async def bt(ch, policy):
        calls["backtest"].append(policy)
        return 7, 30_000_000, 123.4, "SELECT count() ... SETTINGS log_comment = 'tripwire:backtest'"

    monkeypatch.setattr(guardrail, "agent_external_hosts", hosts)
    monkeypatch.setattr(guardrail, "backtest_history", bt)
    return calls


@pytest.fixture
async def gclient(tmp_path, fixtures_dir):
    writer = InMemoryWriter()
    app = create_app(
        writer=writer,
        settings=make_settings(),
        state_path=tmp_path / "state.json",
        ch_enabled=False,
        fixtures_dir=fixtures_dir,
        web_dist=None,
        history_lookup=FakeHistory(),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c, app.state.svc, writer


async def make_incident(c):
    await c.post("/config/hold", json={"enabled": True})
    await theft_prefix(c)
    r = (await c.post("/tool", json=tool(action="http_post", target=DROP))).json()
    assert r["reason"] == "hold_rule" and r["incident_id"]
    await c.post("/tool", json=tool("support-bot", "read_file", "/tickets/4821"))
    await c.post(
        f"/incidents/{r['incident_id']}/outbreak",
        json={"source_id": "ticket:4821", "exposed_agents": [AGENT, "support-bot"], "blocked_destinations": []},
    )
    return r["incident_id"]


async def test_prove_404_and_approve_without_proof_409(gclient):
    c, svc, _ = gclient
    assert (await c.post("/guardrail/nope/prove")).status_code == 404
    assert (await c.post("/guardrail/nope/approve")).status_code == 404
    inc = await make_incident(c)
    r = await c.post(f"/guardrail/{inc}/approve")
    assert r.status_code == 409 and r.json()["reason"] == "unproven"


async def test_prove_then_approve_end_to_end(gclient, fixtures_dir, fake_ch):
    write_fixtures(fixtures_dir)
    c, svc, writer = gclient
    inc_id = await make_incident(c)
    q = svc.bus.subscribe()
    rows_before = len(writer.rows)
    v0 = (await c.get("/status")).json()["policy_version"]

    proof = (await c.post(f"/guardrail/{inc_id}/prove")).json()
    assert [g["name"] for g in proof["gates"]] == ["replay_refused", "normal_ops_ok", "backtest", "policy_lint"]
    assert all(g["passed"] is True for g in proof["gates"]), proof["gates"]
    assert proof["all_passed"] is True and proof["incident_id"] == inc_id and proof["proved_at_ms"] > 0
    cand = proof["candidate"]
    assert "drop.example.net" in cand["denylist"] and proof["added_denylist"] == ["drop.example.net"]
    # history host that is the attacker destination is excluded from the allowlist
    assert cand["allowlists"][AGENT] == ["hooks.partner.example"]
    assert proof["added_allowlist"] == ["hooks.partner.example"]
    bt = BacktestResult.model_validate(proof["backtest"])
    assert bt.events_scanned == 30_000_000 and bt.would_block == 7
    assert bt.would_block_attack_cases == 1 and bt.would_block_normal_cases == 0
    assert all(g["ms"] >= 0 for g in proof["gates"])

    # Proving never touches live state: no verify rows, events or agents; real agent unchanged.
    assert len(writer.rows) == rows_before
    st = (await c.get("/status")).json()
    assert not any(a.startswith("verify:") for a in st["active"] + st["blocked"] + list(st["modes"]))
    assert st["blocked"] == [AGENT] and st["policy_version"] == v0
    assert not any(e["agent_id"].startswith("verify:") for e in svc.state.recent_events)
    types = [e.type for e in _drain(q)]
    assert "guardrail" in types and "backtest" in types and "tool_event" not in types

    r = await c.post(f"/guardrail/{inc_id}/approve")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["incident_id"] == inc_id and body["policy_version"] == v0 + 1
    assert body["restored"] == [AGENT, "support-bot"]
    st = (await c.get("/status")).json()
    assert st["blocked"] == [] and st["modes"][AGENT] == "normal" and st["modes"]["support-bot"] == "normal"
    assert st["policy_version"] == v0 + 1 and st["open_incidents"] == []
    assert not any(a.startswith("verify:") for a in st["blocked"])
    pol = (await c.get("/policy")).json()
    assert "drop.example.net" in pol["denylist"] and pol["allowlists"][AGENT] == ["hooks.partner.example"]
    evs = _drain(q)
    approved = [e for e in evs if e.type == "guardrail"]
    assert approved and approved[-1].data["phase"] == "approved"
    assert {e.data["agent_id"] for e in evs if e.type == "agent_state"} >= {AGENT, "support-bot"}
    assert (await c.post(f"/guardrail/{inc_id}/approve")).json()["reason"] == "already_approved"

    # Cured: normal work is allowed, the attacker destination is refused fleet-wide by policy.
    assert (await c.post("/tool", json=tool())).json()["result"] == "ok"
    r = (await c.post("/tool", json=tool("support-bot", "http_post", DROP))).json()
    assert r["result"] == "denied" and r["reason"] == "hold_policy"


async def test_prove_without_normal_ops_skips_that_gate(gclient, fixtures_dir, fake_ch):
    c, svc, _ = gclient
    inc_id = await make_incident(c)
    proof = (await c.post(f"/guardrail/{inc_id}/prove")).json()
    g = {x["name"]: x for x in proof["gates"]}
    assert g["normal_ops_ok"]["passed"] is None and "not present" in g["normal_ops_ok"]["detail"]
    assert g["backtest"]["passed"] is True and "no eval fixtures" in g["backtest"]["detail"]
    assert proof["all_passed"] is True
    assert (await c.post(f"/guardrail/{inc_id}/approve")).status_code == 200


async def test_prove_fails_when_clickhouse_down_and_approve_refused(gclient):
    c, svc, _ = gclient
    inc_id = await make_incident(c)
    proof = (await c.post(f"/guardrail/{inc_id}/prove")).json()
    g = {x["name"]: x for x in proof["gates"]}
    assert g["backtest"]["passed"] is False and "ClickHouse unavailable" in g["backtest"]["detail"]
    assert proof["backtest"] is None and proof["all_passed"] is False
    assert g["replay_refused"]["passed"] is True  # the sandbox replay needs no ClickHouse
    assert (await c.post(f"/guardrail/{inc_id}/approve")).status_code == 409
    assert (await c.get("/status")).json()["blocked"] == [AGENT]


async def test_backtest_endpoint_shape_and_sse(gclient, fixtures_dir, fake_ch):
    write_fixtures(fixtures_dir, normal_ops=False)
    c, svc, _ = gclient
    q = svc.bus.subscribe()
    r = await c.post("/policy/backtest", json={"denylist": ["drop.example.net"], "allowlists": {AGENT: ["x.example"]}})
    assert r.status_code == 200
    bt = BacktestResult.model_validate(r.json())
    assert bt.events_scanned == 30_000_000 and bt.query_ms == 123.4 and "tripwire:backtest" in bt.sql
    # attack posts to the denylisted host; the benign one posts outside deploy-bot's allowlist
    assert bt.would_block_attack_cases == 1 and bt.would_block_normal_cases == 1
    ev = [e for e in _drain(q) if e.type == "backtest"]
    assert ev and ev[0].data["would_block"] == 7 and ev[0].data["n_normal_cases"] == 1


async def test_backtest_heatmap_top_503_without_clickhouse(client):
    assert (await client.post("/policy/backtest", json={})).status_code == 503
    assert (await client.get("/fleet/heatmap?hours=72")).status_code == 503
    assert (await client.get("/fleet/top?minutes=60&limit=10")).status_code == 503


GUILD_URL = "https://guild.example/v1/workspaces/o~w/sessions"


def _guild_app(tmp_path, monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(app_mod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    app = create_app(
        writer=InMemoryWriter(),
        settings=make_settings(guild_trigger_url=GUILD_URL, guild_trigger_key="kid:ksecret"),
        state_path=tmp_path / "s.json",
        ch_enabled=False,
        web_dist=None,
    )
    return real(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_guild_run_starts_chat_session_for_first_installed_agent(tmp_path, monkeypatch):
    """Account key flow (docs.guild.ai): discover the installed agent, start a chat session."""
    seen: list[tuple[str, str, dict | None, str | None]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        seen.append((req.method, req.url.path, body, req.headers.get("authorization")))
        if req.url.path.endswith("/workspace_agents"):
            return httpx.Response(200, json={"items": [{"id": "wa-1", "agent": {"full_name": "o~release-bot"}}]})
        return httpx.Response(201, json={"id": "s-1", "session_url": "https://app.guild.ai/sessions/s-1"})

    async with _guild_app(tmp_path, monkeypatch, handler) as c:
        r = await c.post("/guild/run")
    out = r.json()
    assert r.status_code == 200 and out["status"] == 201 and out["ok"] is True
    assert out["agent_id"] == "o~release-bot" and out["session_url"].endswith("/s-1")
    (m1, p1, _, a1), (m2, p2, b2, a2) = seen
    assert (m1, p1) == ("GET", "/v1/workspaces/o~w/workspace_agents")
    assert (m2, p2) == ("POST", "/v1/workspaces/o~w/sessions")
    assert b2["session_type"] == "chat" and b2["agent_id"] == "o~release-bot" and b2["initial_prompt"]
    assert a1.startswith("Basic ") and a2.startswith("Basic ")


async def test_guild_run_falls_back_to_api_trigger_on_403(tmp_path, monkeypatch):
    """Trigger keys may not start chat sessions (403) — retry as api_trigger."""
    bodies: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        b = json.loads(req.content)
        bodies.append(b)
        return httpx.Response(403 if b["session_type"] == "chat" else 201, json={"session_url": "u"})

    async with _guild_app(tmp_path, monkeypatch, handler) as c:
        r = await c.post("/guild/run", json={"agent_id": "o~release-bot", "prompt": "go"})
    assert r.json()["status"] == 201
    assert [b["session_type"] for b in bodies] == ["chat", "api_trigger"]
    assert bodies[1]["agent_input"] == {"text": "go"}


async def test_guild_run_409_when_no_agent_installed(tmp_path, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    async with _guild_app(tmp_path, monkeypatch, handler) as c:
        r = await c.post("/guild/run")
    assert r.status_code == 409 and "no agent installed" in r.json()["detail"]


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ---------------------------------------------------------------- real ClickHouse (skips if down)


@pytest.fixture
async def chclient(tmp_path):
    app = create_app(
        writer=InMemoryWriter(),
        settings=make_settings(),
        state_path=tmp_path / "s.json",
        ch_enabled=True,
        web_dist=None,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


@requires_ch
async def test_heatmap_and_top_shapes_on_clickhouse(chclient):
    h = (await chclient.get("/fleet/heatmap?hours=72")).json()
    assert set(h) >= {"agents", "hours", "cells", "query_ms", "rows_read", "formula"}
    assert h["agents"][:2] == ["deploy-bot", "support-bot"] and len(h["agents"]) <= 42
    assert not any(a.startswith("verify:") for a in h["agents"])
    assert len(h["hours"]) == 72 and h["hours"] == sorted(h["hours"]) and h["hours"][-1] <= time.time() * 1000
    for ai, hi, score, n in h["cells"]:
        assert 0 <= ai < len(h["agents"]) and 0 <= hi < 72 and 0 <= score <= 100 and n > 0
    assert h["query_ms"] > 0
    top = (await chclient.get("/fleet/top?minutes=600&limit=5")).json()
    assert isinstance(top, list) and len(top) <= 5
    for row in top:
        assert set(row) == {"agent_id", "score", "events", "denied", "external_posts"}
    if top:
        assert top[0]["score"] == 100


@requires_ch
async def test_backtest_on_clickhouse(chclient):
    r = await chclient.post("/policy/backtest", json={"denylist": ["webhook.example.net"]})
    bt = BacktestResult.model_validate(r.json())
    assert bt.events_scanned > 0 and bt.query_ms > 0 and bt.would_block >= 0
    assert "tripwire:backtest" in bt.sql and "webhook.example.net" in bt.sql


async def test_guild_run_prefers_the_deploy_bot_over_the_responder(tmp_path, monkeypatch):
    """The workspace also holds the human-approval Responder; the Run button must start the governed worker."""
    posted: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/workspace_agents"):
            items = [{"id": "wa-1", "agent": {"full_name": "o~tripwire-responder"}},
                     {"id": "wa-2", "agent": {"full_name": "o~tripwire-deploy-bot"}}]
            return httpx.Response(200, json={"items": items})
        posted.append(json.loads(req.content))
        return httpx.Response(201, json={"id": "s-2"})

    async with _guild_app(tmp_path, monkeypatch, handler) as c:
        out = (await c.post("/guild/run")).json()
    assert out["agent_id"] == "o~tripwire-deploy-bot" and posted[0]["agent_id"] == "o~tripwire-deploy-bot"


async def test_snapshot_carries_last_eval_heartbeat(client):
    hb = {"source": "eval", "metrics": {"precision": 1.0, "recall": 0.9, "n_cases": 4, "tp": 2, "fp": 0, "tn": 1, "fn": 1}}
    assert (await client.post("/heartbeat", json=hb)).status_code == 200
    svc = client._transport.app.state.svc  # type: ignore[attr-defined]
    snap = svc.snapshot()
    assert snap["eval"]["source"] == "eval" and snap["eval"]["metrics"]["tp"] == 2

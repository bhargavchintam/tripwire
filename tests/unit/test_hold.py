from __future__ import annotations

from checkpoint import hold
from checkpoint.hold import HoldDecision, decide, get_classify, stub_classify
from checkpoint.state import State
from tests.unit.conftest import make_settings
from tripwire.contracts import Policy, QuickCheckInput, ToolCall, Verdict


def _ev(ts, action, target):
    return {"ts_ms": ts, "action": action, "target": target, "result": "ok", "reason": ""}


async def test_phase1_decide_allows():
    d = await decide(
        ToolCall(agent_id="a", action="http_post", target="https://x.example"), [], State(None), Policy()
    )
    assert d.allow is True and d.verdict is None


async def test_stub_malicious_on_env_read_then_base64_within_60s():
    v = await stub_classify(
        QuickCheckInput(
            agent_id="a",
            rule="hold",
            events=[_ev(1000, "read_file", "/app/.env"), _ev(5000, "run_command", "base64 /app/.env")],
        )
    )
    assert v.verdict == "malicious" and v.decision_source == "rule_only"


async def test_stub_benign_cases():
    far = [_ev(0, "read_file", "/app/.env"), _ev(61_000, "run_command", "base64 x")]
    reversed_order = [_ev(0, "run_command", "base64 x"), _ev(10, "read_file", "/app/.env")]
    no_env = [_ev(0, "read_file", "/app/config.yml"), _ev(10, "run_command", "base64 x")]
    for events in (far, reversed_order, no_env, []):
        v = await stub_classify(QuickCheckInput(agent_id="a", rule="hold", events=events))
        assert v.verdict == "benign" and v.decision_source == "rule_only", events


def test_get_classify_stub_and_real_fallback(monkeypatch):
    monkeypatch.setattr(hold, "get_settings", lambda: make_settings(hold_check="stub"))
    assert get_classify(force_reload=True) is stub_classify
    assert hold.classify_source == "stub"
    monkeypatch.setattr(hold, "get_settings", lambda: make_settings(hold_check="real"))
    fn = get_classify(force_reload=True)
    # Either Sripadha's real classify imported, or we fell back to the stub and said so.
    assert hold.classify_source == "real" or (
        fn is stub_classify and hold.classify_source.startswith("stub (real import failed")
    )
    monkeypatch.setattr(hold, "get_settings", lambda: make_settings(hold_check="stub"))
    get_classify(force_reload=True)


async def test_hold_not_called_when_disabled(client, monkeypatch):
    calls = []

    async def spy(*a, **k):
        calls.append(a)
        return HoldDecision()

    monkeypatch.setattr(hold, "decide", spy)
    r = await client.post("/tool", json={"agent_id": "a", "action": "http_post", "target": "https://x.example"})
    assert r.json()["result"] == "ok" and calls == []


async def test_hold_enabled_wiring_placeholder_allows(client):
    assert (await client.post("/config/hold", json={"enabled": True})).json() == {"hold_enabled": True}
    r = await client.post("/tool", json={"agent_id": "a", "action": "http_post", "target": "https://x.example"})
    assert r.json()["result"] == "ok"
    ev = (await client.get("/evidence")).json()
    assert ev["hold_decision_ms"] is None  # placeholder made no decision -> nothing measured


async def test_hold_deny_path_blocks_and_opens_incident(client, svc, monkeypatch):
    seen = {}

    async def deny(call, ring, state, policy):
        seen["ring"] = ring
        return HoldDecision(
            allow=False,
            reason="hold_model",
            verdict=Verdict(verdict="malicious", confidence=0.97, reason="exfil", decision_source="akashml"),
        )

    monkeypatch.setattr(hold, "decide", deny)
    await client.post("/config/hold", json={"enabled": True})
    await client.post("/tool", json={"agent_id": "a", "action": "read_file", "target": "/app/.env"})
    r = (await client.post("/tool", json={"agent_id": "a", "action": "http_post", "target": "https://x.example"})).json()
    assert r["result"] == "denied" and r["reason"] == "hold_model" and r["incident_id"]
    assert [e["action"] for e in seen["ring"]] == ["read_file"]  # ring before this step
    st = (await client.get("/status")).json()
    assert st["blocked"] == ["a"] and st["modes"]["a"] == "quarantined"
    inc = (await client.get(f"/incidents/{r['incident_id']}")).json()
    assert inc["rule"] == "hold" and inc["verdict"]["decision_source"] == "akashml"
    assert [s["action"] for s in inc["steps"]] == ["read_file", "http_post"]
    assert len(svc.state.hold_samples) == 1
    # read_file is not high-risk: never reaches decide(), but the agent is blocked now
    r2 = (await client.post("/tool", json={"agent_id": "a", "action": "read_file", "target": "/x"})).json()
    assert r2["reason"] == "blocked"

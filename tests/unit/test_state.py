from __future__ import annotations

import asyncio
import json
import re
from datetime import timezone
from pathlib import Path

import pytest

import checkpoint.state as state_mod
from checkpoint.audit import chain_hash, verify_chain
from checkpoint.bus import Bus
from checkpoint.state import State
from checkpoint.writer import COLUMNS, ClickHouseWriter, to_values, ts_from_ms
from tripwire.contracts import Incident, Verdict

ROOT = Path(__file__).resolve().parents[2]


def test_next_ts_monotonic_same_millisecond(monkeypatch):
    s = State(None)
    monkeypatch.setattr(state_mod, "now_ms", lambda: 1_000_000)
    ts = [s.next_ts("a") for _ in range(5)]
    assert ts == [1_000_000, 1_000_001, 1_000_002, 1_000_003, 1_000_004]
    assert s.next_ts("b") == 1_000_000  # per-agent
    assert s.modes["a"] == "normal"


def test_persistence_round_trip_keeps_agents_blocked(tmp_path):
    p = tmp_path / "state.json"
    s = State(p)
    s.next_ts("deploy-bot")
    s.blocked.add("deploy-bot")
    s.modes["deploy-bot"] = "quarantined"
    s.watermarks["support-bot"] = 123
    s.last_hash["deploy-bot"] = "abc"
    s.add_verdict_key("deploy-bot|secret_theft|99")
    s.add_incident(
        Incident(
            id="inc-1",
            agent_id="deploy-bot",
            rule="secret_theft",
            opened_ms=1,
            verdict=Verdict(verdict="malicious", confidence=0.9, reason="r", decision_source="rule_only"),
        )
    )
    s.push_ring("deploy-bot", {"ts_ms": 5, "action": "read_file", "target": "/x", "result": "ok", "reason": ""})
    s.hold_enabled = True
    s.ttd_samples.append(812.0)
    s.save()

    s2 = State(p)
    assert s2.load()
    assert "deploy-bot" in s2.blocked
    assert s2.modes["deploy-bot"] == "quarantined"
    assert s2.watermarks == {"support-bot": 123}
    assert s2.last_hash["deploy-bot"] == "abc"
    assert "deploy-bot|secret_theft|99" in s2.verdict_keys
    assert s2.incidents["inc-1"].verdict.decision_source == "rule_only"
    assert s2.hold_enabled is True
    assert list(s2.ttd_samples) == [812.0]
    assert len(s2.rings["deploy-bot"]) == 1


def test_blocked_agent_forced_quarantined_on_load(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"blocked": ["x"], "modes": {"x": "normal"}}))
    s = State(p)
    s.load()
    assert s.modes["x"] == "quarantined"


def test_corrupt_state_file_is_moved_aside_not_deleted(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    s = State(p)
    assert s.load() is False
    assert s.load_error and "moved to" in s.load_error
    assert any(f.name.startswith("state.json.corrupt-") for f in tmp_path.iterdir())


async def test_save_async_and_loop(tmp_path):
    p = tmp_path / "state.json"
    s = State(p)
    s.blocked.add("a")
    s.mark_dirty()
    task = asyncio.create_task(s.save_loop(interval=0.05))
    for _ in range(40):
        await asyncio.sleep(0.05)
        if p.exists():
            break
    task.cancel()
    assert json.loads(p.read_text())["blocked"] == ["a"]
    assert s.dirty is False


def test_ring_window_prunes_old_steps():
    s = State(None)
    s.push_ring("a", {"ts_ms": 0, "action": "read_file", "target": "", "result": "ok", "reason": ""})
    s.push_ring("a", {"ts_ms": 70_000, "action": "read_file", "target": "", "result": "ok", "reason": ""})
    assert [e["ts_ms"] for e in s.ring_window("a", now=70_000)] == [70_000]


# ---------------------------------------------------------------- writer


def test_columns_match_frozen_schema():
    sql = (ROOT / "data" / "schema.sql").read_text()
    body = sql[sql.index("(") + 1 :]
    cols = re.findall(r"^\s{4}([a-z_]+)\s+[A-Z]", body, flags=re.M)
    assert tuple(cols) == COLUMNS


def test_ts_is_tz_aware_utc_exact_ms():
    dt = ts_from_ms(1_760_000_000_123)
    assert dt.tzinfo == timezone.utc
    assert dt.microsecond == 123_000
    vals = to_values({"ts_ms": 1_760_000_000_123, "agent_id": "a", "bytes": 2**40, "payload": "SECRET"})
    assert vals[0] == dt
    assert vals[COLUMNS.index("bytes")] == 2**32 - 1
    assert "SECRET" not in [str(v) for v in vals]


class _FailingClient:
    async def insert(self, *a, **k):
        raise ConnectionError("clickhouse down")

    async def close(self):
        return None


class _OkClient:
    def __init__(self):
        self.batches = []

    async def insert(self, table, data, column_names):
        assert column_names == list(COLUMNS)
        self.batches.append(data)

    async def close(self):
        return None


async def test_ch_writer_keeps_rows_on_failure_and_caps_buffer():
    w = ClickHouseWriter(max_buffer=5)
    for i in range(8):
        w.enqueue({"ts_ms": i, "agent_id": "a"})
    assert w.stats()["queue"] == 5 and w.stats()["dropped"] == 3

    async def failing():
        return _FailingClient()

    w._get_client = failing  # type: ignore[method-assign]
    assert await w.flush_once() is False
    st = w.stats()
    assert st["queue"] == 5 and "clickhouse down" in st["last_error"]
    assert [r["ts_ms"] for r in w._buf] == [3, 4, 5, 6, 7]  # order kept, oldest dropped

    ok = _OkClient()

    async def good():
        return ok

    w._get_client = good  # type: ignore[method-assign]
    assert await w.flush_once() is True
    assert w.stats()["queue"] == 0 and w.stats()["last_error"] is None and w.inserted == 5
    assert len(ok.batches[0]) == 5


async def test_ch_writer_background_task_flushes_and_never_blocks():
    ok = _OkClient()
    w = ClickHouseWriter(flush_interval=0.02)

    async def good():
        return ok

    w._get_client = good  # type: ignore[method-assign]
    await w.start()
    w.enqueue({"ts_ms": 1, "agent_id": "a"})
    for _ in range(50):
        await asyncio.sleep(0.02)
        if w.inserted:
            break
    await w.stop()
    assert w.inserted == 1


# ---------------------------------------------------------------- bus + audit


async def test_bus_seq_and_drop_oldest():
    b = Bus(maxsize=2)
    q = b.subscribe()
    for i in range(3):
        b.publish("metrics", {"i": i})
    assert b.dropped == 1
    got = [q.get_nowait(), q.get_nowait()]
    assert [e.seq for e in got] == [2, 3]
    b.unsubscribe(q)
    assert not b.subscribers


def test_verify_chain_detects_tamper():
    rows, prev = [], ""
    for i in range(4):
        r = {"ts_ms": 10 + i, "agent_id": "a", "action": "read_file", "target": f"/f{i}", "result": "ok", "reason": ""}
        r["prev_hash"] = prev
        r["hash"] = prev = chain_hash(prev, r["ts_ms"], "a", "read_file", r["target"], "ok", "")
        rows.append(r)
    assert verify_chain(rows) == {"events": 4, "intact": True, "first_break_ts_ms": None}
    rows[2]["target"] = "/etc/shadow"
    assert verify_chain(rows) == {"events": 4, "intact": False, "first_break_ts_ms": 12}
    assert verify_chain([]) == {"events": 0, "intact": True, "first_break_ts_ms": None}


@pytest.mark.parametrize("n", [1, 200])
def test_verdict_keys_bounded(n, monkeypatch):
    monkeypatch.setattr(state_mod, "VERDICT_KEYS_MAX", 100)
    s = State(None)
    for i in range(n):
        s.add_verdict_key(f"k{i}")
    assert len(s.verdict_keys) == min(n, 100)

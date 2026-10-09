"""data/seed_live_agents.sql + data/background_data.sql produce the rows the plan promises.

Runs the real SQL through tripwire.loader into a scratch copy of tripwire.events (dropped after),
so the live table is never touched. Skips the whole module when ClickHouse is down."""

from __future__ import annotations

import pytest

from tripwire.loader import clickhouse_reachable, ping_url, run_load, run_seed, table_stats

if not clickhouse_reachable():
    pytest.skip(f"ClickHouse unreachable at {ping_url()}", allow_module_level=True)

TEN_MIN_MS = 10 * 60 * 1000
THREE_DAYS_MS = 3 * 24 * 3600 * 1000


def _one(c, sql: str) -> tuple:
    return tuple(c.query(sql).first_row)


def test_seed_live_agents(ch_client, scratch_table):
    t = scratch_table
    res = run_seed(ch_client, table=t)
    assert not res["skipped"] and res["rows_written"] == 540

    agents = {r[0] for r in ch_client.query(f"SELECT DISTINCT agent_id FROM {t}").result_rows}
    assert agents == {"deploy-bot", "support-bot"}

    min_age, max_age, not_synth, not_ok, chained = _one(ch_client, f"""
        SELECT min(dateDiff('millisecond', ts, now64(3))), max(dateDiff('millisecond', ts, now64(3))),
               countIf(synthetic != 1), countIf(result != 'ok' OR reason != ''),
               countIf(hash != '' OR prev_hash != '')
        FROM {t}""")
    # Rows keep aging after the insert (slow remote load + time until this check): allow 15 min
    # of slack on the upper bound. The invariant that matters is the lower bound (>= 10 min).
    assert min_age >= TEN_MIN_MS and max_age <= THREE_DAYS_MS + 15 * 60 * 1000
    assert (not_synth, not_ok, chained) == (0, 0, 0)
    assert table_stats(ch_client, t)["is_external_mismatch"] == 0

    env_reads, external_posts = _one(ch_client, f"""
        SELECT countIf(action = 'read_file' AND target = '/app/.env'),
               countIf(action = 'http_post' AND is_external = 1)
        FROM {t} WHERE agent_id = 'deploy-bot'""")
    assert env_reads > 0 and external_posts == 0  # legit .env reads, never an external post

    tickets, ticket_mismatch, untainted = _one(ch_client, f"""
        SELECT countIf(startsWith(target, 'ticket:')),
               countIf(startsWith(target, 'ticket:') AND tainted_by != target),
               countIf(NOT match(tainted_by, '^ticket:1[0-9]{{3}}$'))
        FROM {t} WHERE agent_id = 'support-bot'""")
    assert tickets == 60 and ticket_mismatch == 0 and untainted == 0

    again = run_seed(ch_client, table=t)
    assert again["skipped"] and again["existing_rows"] == 540
    replaced = run_seed(ch_client, table=t, replace=True)
    assert replaced["deleted_rows"] == 540 and replaced["rows_written"] == 540
    assert int(ch_client.command(f"SELECT count() FROM {t}")) == 540


def test_background_load_chunked(ch_client, scratch_table):
    t = scratch_table
    s = run_load(ch_client, rows=50_000, chunk=20_000, salt=7, table=t, echo=lambda *_: None)
    assert s["error"] is None
    assert [c["rows_requested"] for c in s["chunks"]] == [20_000, 20_000, 10_000]
    assert [c["rows_written"] for c in s["chunks"]] == [20_000, 20_000, 10_000]
    assert s["rows_written_total"] == 50_000 and s["events_total_after"] == 50_000
    assert all(c["wall_seconds"] > 0 for c in s["chunks"])

    total, agents, bad_agents, not_synth, min_age, max_age, hours, ext, internal_http, distinct = _one(
        ch_client, f"""
        SELECT count(), uniqExact(agent_id), countIf(NOT match(agent_id, '^agent-[0-3][0-9]$')),
               countIf(synthetic != 1),
               min(dateDiff('millisecond', ts, now64(3))), max(dateDiff('millisecond', ts, now64(3))),
               uniqExact(toStartOfHour(ts)),
               countIf(is_external = 1), countIf(action IN ('http_get', 'http_post') AND is_external = 0),
               uniqExact(agent_id, ts, action, target, bytes)
        FROM {t}""")
    assert total == 50_000 and agents == 40 and bad_agents == 0 and not_synth == 0
    # Rows keep aging after the insert (slow remote load + time until this check): allow 15 min
    # of slack on the upper bound. The invariant that matters is the lower bound (>= 10 min).
    assert min_age >= TEN_MIN_MS and max_age <= THREE_DAYS_MS + 15 * 60 * 1000
    assert hours <= 73  # stays under max_partitions_per_insert_block = 100
    assert ext > 0 and internal_http > 0
    assert distinct > 49_000  # chunks are not copies of each other
    assert table_stats(ch_client, t)["is_external_mismatch"] == 0

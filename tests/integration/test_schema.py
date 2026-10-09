"""tripwire.events matches data/schema.sql, ms timestamps round-trip, the RO user is read-only,
and the bloom-filter index on target exists. Skips the whole module when ClickHouse is down."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from tripwire.config import get_settings
from tripwire.loader import clickhouse_reachable, ping_url

if not clickhouse_reachable():
    pytest.skip(f"ClickHouse unreachable at {ping_url()}", allow_module_level=True)

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "data" / "schema.sql"

# Explicit UTC instant with a non-zero millisecond part.
TS = datetime(2026, 10, 1, 12, 34, 56, 789000, tzinfo=UTC)
TS_MS = (TS - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)

READBACK = (
    "SELECT toUnixTimestamp64Milli(ts), ts, action, target, bytes, is_external, result, reason, "
    "tainted_by, session_id, synthetic FROM events WHERE agent_id = {a:String} ORDER BY ts"
)


def schema_columns() -> list[tuple[str, str]]:
    """(name, type) pairs in declaration order, parsed from data/schema.sql."""
    text = SCHEMA_SQL.read_text()
    body = text[text.index("CREATE TABLE IF NOT EXISTS tripwire.events") :]
    body = body[body.index("(") + 1 : body.index("ENGINE")]
    cols: list[tuple[str, str]] = []
    for raw in body.splitlines():
        line = raw.split("--", 1)[0].strip().rstrip(",").strip()
        if not line or line == ")" or line.startswith("INDEX"):
            continue
        m = re.match(r"^(\w+)\s+(.+?)(?:\s+DEFAULT\s+.+)?$", line)
        assert m, f"cannot parse schema line: {raw!r}"
        cols.append((m.group(1), m.group(2).strip()))
    return cols


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def test_events_table_exists(ch_client):
    assert int(ch_client.command("EXISTS TABLE tripwire.events")) == 1
    row = ch_client.query(
        "SELECT engine, sorting_key, partition_key FROM system.tables "
        "WHERE database = 'tripwire' AND name = 'events'"
    ).first_row
    assert tuple(row) == ("MergeTree", "agent_id, ts", "toStartOfHour(ts)")


def test_columns_exactly_match_schema_sql(ch_client):
    expected = schema_columns()
    assert len(expected) == 15
    actual = ch_client.query(
        "SELECT name, type FROM system.columns WHERE database = 'tripwire' AND table = 'events' ORDER BY position"
    ).result_rows
    assert [tuple(r) for r in actual] == expected


def test_insert_sql_ms_timestamp_round_trip(ch_client, test_agent):
    ch_client.command(
        "INSERT INTO events (ts, agent_id, action, target, bytes, is_external, result, tainted_by, session_id) "
        "SELECT fromUnixTimestamp64Milli({ms:Int64}, 'UTC'), {a:String}, 'http_post', "
        "'https://api.internal.example/v1/x', 321, 0, 'ok', 'ticket:1001', 's-sql'",
        parameters={"ms": TS_MS, "a": test_agent},
    )
    rows = ch_client.query(READBACK, parameters={"a": test_agent}).result_rows
    assert len(rows) == 1
    ms, ts, *rest = rows[0]
    assert ms == TS_MS
    assert _utc(ts) == TS
    assert rest == ["http_post", "https://api.internal.example/v1/x", 321, 0, "ok", "", "ticket:1001", "s-sql", 0]


def test_insert_python_datetime_round_trip(ch_client, test_agent):
    """The path the checkpoint writer uses: client.insert() with an aware UTC datetime."""
    cols = ["ts", "agent_id", "action", "target", "bytes", "result", "session_id"]
    ch_client.insert("events", [[TS, test_agent, "read_file", "/app/config.yml", 1234, "ok", "s-py"]],
                     column_names=cols)
    rows = ch_client.query(READBACK, parameters={"a": test_agent}).result_rows
    assert len(rows) == 1
    assert rows[0][0] == TS_MS
    assert _utc(rows[0][1]) == TS
    assert list(rows[0][2:5]) == ["read_file", "/app/config.yml", 1234]


def _ro_http(sql: str) -> tuple[int, str]:
    """POST one statement as the read-only user from .env (CLICKHOUSE_RO_USER/PASSWORD)."""
    s = get_settings()
    req = urllib.request.Request(
        ping_url().removesuffix("/ping") + f"/?database={s.clickhouse_database}",
        data=sql.encode(),
        headers={"X-ClickHouse-User": s.clickhouse_ro_user, "X-ClickHouse-Key": s.clickhouse_ro_password},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")


def test_readonly_user_can_select_but_insert_raises(ch_client, test_agent):
    status, body = _ro_http("SELECT count() FROM events")
    assert status == 200, body[:300]
    assert int(body.strip()) >= 0

    status, body = _ro_http(
        "INSERT INTO events (ts, agent_id, action, target, result) "
        f"VALUES (now64(3), '{test_agent}', 'read_file', '/x', 'ok')"
    )
    assert status != 200
    assert re.search(r"(?i)readonly|not enough privileges|ACCESS_DENIED", body), body[:300]
    n = int(ch_client.command("SELECT count() FROM events WHERE agent_id = {a:String}", parameters={"a": test_agent}))
    assert n == 0


# Was xfail until 10:50: readonly_user.sql set max_result_rows=200 (throw), which broke
# clickhouse-connect's connect-time system.settings read. Fixed in readonly_user.sql (CCR).
def test_ro_client_from_tripwire_ch_connects_and_is_read_only(ch_client, test_agent):
    from tripwire.ch import ro_client

    ro = ro_client()
    assert int(ro.command("SELECT count() FROM events")) >= 0
    with pytest.raises(DatabaseError, match=r"(?i)readonly|not enough privileges|ACCESS_DENIED"):
        ro.command(
            "INSERT INTO events (ts, agent_id, action, target, result) "
            "VALUES (now64(3), {a:String}, 'read_file', '/x', 'ok')",
            parameters={"a": test_agent},
        )


def test_bloom_filter_index_on_target(ch_client):
    rows = ch_client.query(
        "SELECT name, type, expr, granularity FROM system.data_skipping_indices "
        "WHERE database = 'tripwire' AND table = 'events'"
    ).result_rows
    assert ("bf_target", "bloom_filter", "target", 4) in [tuple(r) for r in rows]

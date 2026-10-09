"""ai.sqlguard: the investigator's SQL guard (owner: Sripadha).

Run: uv run pytest tests/unit/test_sqlguard.py -q
Pure sqlglot cases need nothing; the two ClickHouse cases (the readonly user runs a guarded query, a write
through ro_client raises) skip when localhost:8123 does not answer /ping.
"""

from __future__ import annotations

import pytest

from ai.sqlguard import DEFAULT_LIMIT, SQLGuardError, _cli, guard
from tests.unit.conftest import requires_ch

# (sql, substring expected in the normalized output)
ALLOWED = [
    ("SELECT agent_id, count() FROM events GROUP BY agent_id", "GROUP BY agent_id LIMIT 200"),
    ("SELECT * FROM events WHERE agent_id = 'deploy-bot' ORDER BY ts DESC", "ORDER BY ts DESC LIMIT 200"),
    (
        "WITH recent AS (SELECT * FROM events WHERE ts > now() - INTERVAL 1 HOUR) "
        "SELECT agent_id, count() FROM recent GROUP BY agent_id",
        "WITH recent AS",
    ),
    (
        "SELECT agent_id FROM events WHERE agent_id IN (SELECT agent_id FROM events WHERE result = 'denied')",
        "IN (SELECT agent_id FROM events",
    ),
    ("SELECT count() FROM tripwire.events", "FROM tripwire.events LIMIT 200"),
    ("SELECT * FROM events LIMIT 10", "LIMIT 10"),
    ("SELECT * FROM events LIMIT 5000", "LIMIT 200"),
    ("SELECT * FROM events", "SELECT * FROM events LIMIT 200"),
    ("select * from events;", "SELECT * FROM events LIMIT 200"),
    (
        "SELECT toUnixTimestamp64Milli(ts) AS ts_ms, action, target FROM events "
        "WHERE agent_id = 'x' AND ts >= fromUnixTimestamp64Milli(1791569120000) ORDER BY ts",
        "toUnixTimestamp64Milli(ts) AS ts_ms",
    ),
    (
        "SELECT domain(target) AS host, countIf(result = 'denied') AS denied FROM events "
        "WHERE action IN ('http_post', 'http_get') GROUP BY host ORDER BY denied DESC",
        "countIf(result = 'denied')",
    ),
    ("SELECT * FROM events e1 JOIN events e2 ON e1.agent_id = e2.agent_id LIMIT 5", "JOIN events AS e2"),
    ("SELECT action FROM events UNION ALL SELECT action FROM tripwire.events", "UNION ALL"),
    ("SELECT * FROM events LIMIT 1 BY agent_id", "LIMIT 1 BY agent_id"),
    ("SELECT * FROM events /* harmless */ WHERE 1 -- trailing", "SELECT * FROM events WHERE 1 LIMIT 200"),
    ("SELECT * FROM `events` WHERE agent_id = 'a'", "WHERE agent_id = 'a' LIMIT 200"),
    (
        "SELECT * FROM events WHERE ts >= now64(3) - INTERVAL 72 HOUR AND (target = 'ticket:4821' OR tainted_by = 'ticket:4821')",
        "now64(3)",
    ),
    ("SELECT * FROM events WHERE arrayExists(p -> startsWith(target, p), ['ticket:', 'http_get:'])", "arrayExists"),
    ("SELECT * FROM events ORDER BY ts LIMIT 10 OFFSET 20", "LIMIT 10 OFFSET 20"),
    ("SELECT 1", "SELECT 1 LIMIT 200"),
]

# (sql, substring expected in the refusal reason, lower-case)
REJECTED = [
    ("INSERT INTO events (agent_id) VALUES ('x')", "only select"),
    ("DROP TABLE events", "only select"),
    ("ALTER TABLE events DELETE WHERE 1", "only select"),
    ("SYSTEM FLUSH LOGS", "only select"),
    ("KILL QUERY WHERE 1", ""),
    ("SET max_threads = 1", "only select"),
    ("SELECT * FROM system.tables", "system"),
    ("SELECT * FROM numbers(10)", "table functions"),
    ("SELECT * FROM url('http://x/y', CSV)", "table functions"),
    ("SELECT * FROM file('x.csv')", "table functions"),
    ("SELECT * FROM s3('http://b/x.csv')", "table functions"),
    ("SELECT * FROM remote('host', 'db', 'tbl')", "table functions"),
    ("SELECT * FROM information_schema.tables", "information_schema"),
    ("SELECT * FROM INFORMATION_SCHEMA.TABLES", "information_schema"),
    ("SELECT 1; SELECT 2", "exactly one statement"),
    ("SELECT 1; DROP TABLE events", "exactly one statement"),
    ("SELECT * FROM events SETTINGS max_threads = 1", "settings"),
    ("SELECT * FROM events FORMAT JSON", "format"),
    ("SELECT * FROM events INTO OUTFILE '/tmp/x.csv'", ""),
    ("SHOW TABLES", "only select"),
    ("SELECT * FROM events e JOIN other_table o ON e.agent_id = o.agent_id", "other_table"),
    ("SELECT action FROM events UNION ALL SELECT dummy FROM system.one", "system"),
    ("SELECT * FROM events; -- comment\nDROP TABLE events", "exactly one statement"),
    ("SELECT * FROM events /* ; */ ; DROP TABLE events", "exactly one statement"),
    ("SELECT sleep(3) FROM events", "sleep"),
    ("SELECT dictGet('d', 'a', 1) FROM events", "dictget"),
    ("SELECT * FROM events WHERE agent_id IN (SELECT name FROM system.users)", "system"),
    ("SELECT * FROM (SELECT * FROM numbers(10))", "table functions"),
    ("SELECT * FROM merge('tripwire', 'ev.*')", "table functions"),
    ("SELECT * FROM mysql('h:3306', 'db', 't', 'u', 'p')", "table functions"),
    ("SELECT * FROM input('a String')", "table functions"),
    ("SELECT * FROM other_db.events", "other_db.events"),
    ("SELECT * FROM a.b.c", "three-part"),
    ("DESCRIBE events", "only select"),
    ("EXPLAIN SELECT * FROM events", "only select"),
    ("CREATE TABLE t (a UInt8) ENGINE = Memory", "only select"),
    ("WITH x AS (SELECT 1) INSERT INTO events (agent_id) SELECT 'x'", "only select"),
    ("SELECT * FROM events LIMIT (SELECT 1)", "limit must be"),
    ("SELECT * FROM events_test_abc", "events_test_abc"),
    ("TRUNCATE TABLE events", "only select"),
    ("GRANT SELECT ON *.* TO x", "only select"),
    ("", "empty"),
    ("   ;  ", "empty"),
    # sqlglot parses these sources as Columns, not Tables: the structural check must catch them
    ("SELECT t FROM events ARRAY JOIN (SELECT groupArray(name) FROM system.tables) AS t", "from source"),
    ("SELECT x FROM events ARRAY JOIN (SELECT [1, 2]) AS x", "array join"),
    ("SELECT * FROM events e ARRAY JOIN (SELECT * FROM system.tables) t", "from source"),
    ("SELECT * FROM events ARRAY JOIN system.users", "system"),
    ("SELECT * FROM events WHERE agent_id IN system.users", "in table"),
    ("SELECT * FROM events t WHERE t.agent_id GLOBAL IN system.users", "in table"),
    ("SELECT * FROM events WHERE agent_id IN events", "in table"),
    ("SELECT system.users FROM events", "system"),
]


def test_array_join_over_a_column_or_literal_is_still_allowed():
    assert guard("SELECT x FROM events ARRAY JOIN [1, 2] AS x").endswith(f"LIMIT {DEFAULT_LIMIT}")
    assert "ARRAY JOIN" in guard("SELECT part FROM events ARRAY JOIN splitByChar('/', target) AS part")


@pytest.mark.parametrize("sql,expect", ALLOWED, ids=[s[:40] for s, _ in ALLOWED])
def test_allowed_queries_are_normalized_and_capped(sql, expect):
    out = guard(sql)
    assert expect in out, out
    assert "LIMIT" in out
    assert guard(out) == out  # idempotent: the emitted text is itself a guarded query


@pytest.mark.parametrize("sql,why", REJECTED, ids=[s[:40] or "empty" for s, _ in REJECTED])
def test_rejected_queries_raise(sql, why):
    with pytest.raises(SQLGuardError) as excinfo:
        guard(sql)
    assert why in str(excinfo.value).lower()


def test_limit_kept_clamped_injected_and_custom():
    assert guard("SELECT * FROM events LIMIT 10").endswith("LIMIT 10")
    assert guard("SELECT * FROM events LIMIT 5000").endswith(f"LIMIT {DEFAULT_LIMIT}")
    assert guard("SELECT * FROM events").endswith(f"LIMIT {DEFAULT_LIMIT}")
    assert guard("SELECT * FROM events", limit=50).endswith("LIMIT 50")
    assert guard("SELECT * FROM events LIMIT 60", limit=50).endswith("LIMIT 50")
    assert guard("SELECT * FROM events LIMIT 40", limit=50).endswith("LIMIT 40")
    assert guard("SELECT * FROM events LIMIT 5, 10") == "SELECT * FROM events LIMIT 10 OFFSET 5"
    with pytest.raises(ValueError):
        guard("SELECT 1", limit=0)
    with pytest.raises(ValueError):
        guard("SELECT 1", limit=True)  # type: ignore[arg-type]


def test_limit_by_and_set_operations_are_wrapped_for_a_total_cap():
    out = guard("SELECT * FROM events LIMIT 1 BY agent_id")
    assert out.startswith("SELECT * FROM (SELECT * FROM events LIMIT 1 BY agent_id)") and out.endswith("LIMIT 200")
    out = guard("SELECT action FROM events UNION ALL SELECT action FROM events LIMIT 5000")
    assert out.startswith("SELECT * FROM (SELECT action FROM events UNION ALL") and out.endswith("LIMIT 200")


def test_comments_are_stripped_from_the_receipt_text():
    out = guard("SELECT /* DROP TABLE events */ agent_id FROM events -- ; DROP TABLE events")
    assert out == "SELECT agent_id FROM events LIMIT 200"


def test_cte_alias_cannot_smuggle_another_table():
    with pytest.raises(SQLGuardError, match="not allowed"):
        guard("WITH x AS (SELECT * FROM system.one) SELECT * FROM x")
    with pytest.raises(SQLGuardError, match="not allowed"):
        guard("WITH x AS (SELECT * FROM events) SELECT * FROM x JOIN secrets s ON 1 = 1")


def test_cli(capsys):
    assert _cli(["SELECT agent_id FROM events"]) == 0
    assert capsys.readouterr().out.strip() == "SELECT agent_id FROM events LIMIT 200"
    assert _cli(["DROP TABLE events"]) == 1
    assert "refused: only SELECT" in capsys.readouterr().err
    assert _cli([]) == 2


# ---------------------------------------------------------------- against the real read-only user
@requires_ch
def test_readonly_user_runs_guarded_queries():
    from tripwire.ch import ro_client

    c = ro_client()
    sql = guard("SELECT agent_id, count() AS n FROM events GROUP BY agent_id ORDER BY n DESC")
    res = c.query(sql)
    assert len(res.result_rows) <= DEFAULT_LIMIT
    assert int(res.summary.get("read_rows", 0)) >= len(res.result_rows)
    # every ALLOWED case that is valid against the schema executes as emitted (receipts show what ran)
    for raw, _ in ALLOWED:
        if "JOIN events" in raw or "UNION" in raw:
            continue  # SELECT * self-join / union of one column: valid but pointless here
        rows = c.query(guard(raw)).result_rows
        assert len(rows) <= DEFAULT_LIMIT


@requires_ch
def test_write_and_settings_through_ro_client_raise():
    from tripwire.ch import ro_client

    c = ro_client()
    with pytest.raises(Exception) as excinfo:
        c.command("INSERT INTO events (agent_id, action, target) VALUES ('sqlguard-test', 'read_file', '/x')")
    assert "privileges" in str(excinfo.value).lower() or "readonly" in str(excinfo.value).lower()
    with pytest.raises(Exception) as excinfo:
        c.query("SELECT 1 SETTINGS max_threads = 1")
    assert "cannot modify" in str(excinfo.value).lower() or "readonly" in str(excinfo.value).lower()
    assert c.query("SELECT count() FROM events WHERE agent_id = 'sqlguard-test'").result_rows[0][0] == 0

"""Integration fixtures against a real ClickHouse. Owner: Bindu.

Every fixture here skips (never fails) when ClickHouse does not answer /ping — same check as
`curl -sf localhost:8123/ping`, against the host/port in .env. Rows and tables created by tests
are removed in teardown.
"""

from __future__ import annotations

import uuid

import pytest

from tripwire.loader import clickhouse_reachable, ping_url


def require_clickhouse() -> None:
    if not clickhouse_reachable():
        pytest.skip(f"ClickHouse unreachable at {ping_url()}")


@pytest.fixture(scope="session")
def ch_client():
    require_clickhouse()
    from tripwire.ch import client

    c = client()
    if not int(c.command("EXISTS TABLE events")):
        pytest.skip("tripwire.events missing — run `make db` first")
    return c


@pytest.fixture
def test_agent(ch_client):
    """A unique agent_id; every row with it is deleted (lightweight DELETE) after the test."""
    agent = f"test-{uuid.uuid4().hex[:12]}"
    yield agent

    def remaining() -> int:
        return int(ch_client.command("SELECT count() FROM events WHERE agent_id = {a:String}", parameters={"a": agent}))

    # A lightweight DELETE visits every part (seconds on a 30M-row table), so only run it when needed.
    if remaining():
        ch_client.command("DELETE FROM events WHERE agent_id = {a:String}", parameters={"a": agent})
    left = remaining()
    assert left == 0, f"cleanup left {left} rows for {agent}"


@pytest.fixture
def scratch_table(ch_client):
    """An empty copy of tripwire.events (same columns, engine, index); dropped after the test."""
    name = f"events_test_{uuid.uuid4().hex[:10]}"
    ch_client.command(f"CREATE TABLE {name} AS events")
    yield name
    ch_client.command(f"DROP TABLE IF EXISTS {name} SYNC")

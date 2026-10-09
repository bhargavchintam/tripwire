"""ClickHouse clients shared by both tracks. Owner: Bindu.

    from tripwire.ch import client, ro_client, async_client
    rows = client().query("SELECT count() FROM events").result_rows

CLI:
    uv run python -m tripwire.ch init      # apply data/schema.sql + read-only user
    uv run python -m tripwire.ch ping
"""

from __future__ import annotations

import sys
from pathlib import Path

import clickhouse_connect

from tripwire.config import get_settings

ROOT = Path(__file__).resolve().parent.parent


def _kwargs(readonly: bool = False, database: str | None = None) -> dict:
    s = get_settings()
    return dict(
        host=s.clickhouse_host,
        port=s.clickhouse_port,
        secure=s.clickhouse_secure,
        username=s.clickhouse_ro_user if readonly else s.clickhouse_user,
        password=s.clickhouse_ro_password if readonly else s.clickhouse_password,
        database=database if database is not None else s.clickhouse_database,
        connect_timeout=10,
        send_receive_timeout=30,
    )


def client(readonly: bool = False, **settings):
    """Sync client. Not safe to share across threads/tasks running concurrent queries."""
    return clickhouse_connect.get_client(**_kwargs(readonly), settings=settings or None)


def ro_client():
    """Read-only client for the investigator's run_sql (readonly=1, row/time limits)."""
    return client(readonly=True)


async def async_client(readonly: bool = False, **settings):
    """Async client (use one per task; clickhouse-connect async is a thread-pool wrapper)."""
    return await clickhouse_connect.get_async_client(**_kwargs(readonly), settings=settings or None)


def _split_sql(text: str) -> list[str]:
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def init() -> None:
    s = get_settings()
    admin = clickhouse_connect.get_client(**_kwargs(database=""))
    for stmt in _split_sql((ROOT / "data" / "schema.sql").read_text()):
        admin.command(stmt)
    ro_sql = (ROOT / "data" / "readonly_user.sql").read_text().format(
        ro_user=s.clickhouse_ro_user, ro_password=s.clickhouse_ro_password
    )
    for stmt in _split_sql(ro_sql):
        try:
            admin.command(stmt)
        except Exception as exc:  # user may already exist with other settings
            print(f"warn: {exc.__class__.__name__}: {str(exc)[:160]}")
    n = admin.command("SELECT count() FROM tripwire.events")
    print(f"schema ready: tripwire.events rows={n}")


def ping() -> None:
    print(client().command("SELECT version()"))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ping"
    {"init": init, "ping": ping}[cmd]()

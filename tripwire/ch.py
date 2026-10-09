"""ClickHouse clients shared by both tracks. Owner: Bindu.

    from tripwire.ch import client, ro_client, async_client
    rows = client().query("SELECT count() FROM events").result_rows

CLI:
    uv run python -m tripwire.ch init      # apply data/schema.sql + read-only user
    uv run python -m tripwire.ch ping
"""

from __future__ import annotations

import asyncio
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
    """Sync client. Not safe to share across threads/tasks running concurrent queries.

    On ClickHouse Cloud (secure=1, 2 replicas) a row written through one replica may not be
    visible yet on the replica serving the next read, so the read-write client defaults to
    select_sequential_consistency=1 (read-after-write for the detector and hold lookups).
    Not applied to the read-only client: readonly=1 forbids changing settings.
    """
    if get_settings().clickhouse_secure and not readonly:
        settings.setdefault("select_sequential_consistency", 1)
    return clickhouse_connect.get_client(**_kwargs(readonly), settings=settings or None)


def ro_client():
    """Read-only client for the investigator's run_sql (readonly=1, row/time limits)."""
    return client(readonly=True)


class ThreadedAsyncClient:
    """Async facade over the sync client. No aiohttp needed (deps are frozen).

    clickhouse-connect's own async client requires aiohttp, which is not in the dep list,
    so it raised ImportError (10:50 fix, CCR). Each awaited call runs in a worker thread;
    a lock serializes calls because one sync client must not run concurrent queries.
    For parallel queries, create one client per task.

        ch = await async_client()
        rows = (await ch.query("SELECT count() FROM events")).result_rows
    """

    def __init__(self, sync_client) -> None:
        self._c = sync_client
        self._lock = asyncio.Lock()

    async def _run(self, fn, *args, **kwargs):
        async with self._lock:
            return await asyncio.to_thread(fn, *args, **kwargs)

    async def query(self, *args, **kwargs):
        return await self._run(self._c.query, *args, **kwargs)

    async def command(self, *args, **kwargs):
        return await self._run(self._c.command, *args, **kwargs)

    async def insert(self, *args, **kwargs):
        return await self._run(self._c.insert, *args, **kwargs)

    async def close(self) -> None:
        await asyncio.to_thread(self._c.close)

    @property
    def sync(self):
        """The underlying sync client (use from threads only)."""
        return self._c


async def async_client(readonly: bool = False, **settings) -> ThreadedAsyncClient:
    """Async client: `await (await async_client()).query(...)`. One per task."""
    return ThreadedAsyncClient(await asyncio.to_thread(client, readonly, **settings))


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

"""Async batch writer to ClickHouse tripwire.events (master §5).

Rows are dicts keyed by the schema columns, except the timestamp, which is
passed as ``ts_ms`` (epoch ms) and converted to a timezone-aware UTC datetime
at insert time. ``payload`` is never a column.

enqueue() is synchronous and O(1): it never blocks a request. A background task
flushes every 200 ms or as soon as 500 rows are waiting. On failure, rows stay
buffered (cap 100k, oldest dropped and counted) and the insert is retried with
exponential backoff.
"""

from __future__ import annotations

import asyncio
import collections
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from loguru import logger

COLUMNS: tuple[str, ...] = (
    "ts",
    "agent_id",
    "action",
    "target",
    "bytes",
    "is_external",
    "result",
    "reason",
    "honeytoken_hit",
    "tainted_by",
    "code_ref",
    "session_id",
    "synthetic",
    "prev_hash",
    "hash",
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_UINT32_MAX = 2**32 - 1


def ts_from_ms(ts_ms: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=int(ts_ms))


def to_values(row: dict[str, Any]) -> list[Any]:
    """Row dict -> ordered values for COLUMNS (ts from ts_ms)."""
    out: list[Any] = []
    for col in COLUMNS:
        if col == "ts":
            out.append(ts_from_ms(row["ts_ms"]))
        elif col == "bytes":
            out.append(max(0, min(int(row.get("bytes", 0) or 0), _UINT32_MAX)))
        elif col in ("is_external", "honeytoken_hit", "synthetic"):
            out.append(1 if row.get(col) else 0)
        else:
            out.append(str(row.get(col, "") or ""))
    return out


class Writer(Protocol):
    def enqueue(self, row: dict[str, Any]) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def stats(self) -> dict[str, Any]: ...


class InMemoryWriter:
    """Test/offline writer: keeps every row in a list. Never fails."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def enqueue(self, row: dict[str, Any]) -> None:
        self.rows.append(dict(row))

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def stats(self) -> dict[str, Any]:
        return {"queue": 0, "dropped": 0, "last_error": None, "inserted": len(self.rows), "kind": "memory"}


class _ThreadedClient:
    """Async facade over a sync clickhouse-connect client (only the writer task uses it)."""

    def __init__(self, sync_client: Any) -> None:
        self._c = sync_client

    async def insert(self, table: str, data: list[list[Any]], column_names: list[str]) -> Any:
        return await asyncio.to_thread(self._c.insert, table, data, column_names=column_names)

    async def close(self) -> None:
        await asyncio.to_thread(self._c.close)


class ClickHouseWriter:
    def __init__(
        self,
        table: str = "events",
        flush_interval: float = 0.2,
        batch_rows: int = 500,
        max_buffer: int = 100_000,
        max_insert_rows: int = 10_000,
    ) -> None:
        self.table = table
        self.flush_interval = flush_interval
        self.batch_rows = batch_rows
        self.max_buffer = max_buffer
        self.max_insert_rows = max_insert_rows
        self._buf: collections.deque[dict[str, Any]] = collections.deque()
        self._inflight: list[dict[str, Any]] = []
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._client: Any = None
        self._stopping = False
        self.dropped = 0
        self.inserted = 0
        self.last_error: str | None = None
        self.last_flush_ms: int | None = None
        self._backoff = 0.0
        self._sync_fallback = False

    # ---- request path (sync, O(1)) -------------------------------------------------
    def enqueue(self, row: dict[str, Any]) -> None:
        self._buf.append(dict(row))
        self._trim()
        if self._wake is not None and len(self._buf) >= self.batch_rows:
            self._wake.set()

    def _trim(self) -> None:
        over = len(self._buf) + len(self._inflight) - self.max_buffer
        while over > 0 and self._buf:
            self._buf.popleft()
            self.dropped += 1
            over -= 1

    def stats(self) -> dict[str, Any]:
        return {
            "queue": len(self._buf) + len(self._inflight),
            "dropped": self.dropped,
            "last_error": self.last_error,
            "inserted": self.inserted,
            "last_flush_ms": self.last_flush_ms,
            "kind": "clickhouse",
        }

    # ---- background task -----------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="tripwire-ch-writer")

    async def stop(self, timeout: float = 3.0) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        # Last best-effort flush so a clean shutdown does not lose rows.
        try:
            await asyncio.wait_for(self.flush_once(), timeout)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"writer: final flush failed, {self.stats()['queue']} rows unsent: {exc!r}")
        await self._close_client()

    async def _run(self) -> None:
        assert self._wake is not None
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.flush_interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if not self._buf:
                continue
            ok = await self.flush_once()
            if ok:
                self._backoff = 0.0
            else:
                self._backoff = min(10.0, max(0.5, self._backoff * 2))
                await asyncio.sleep(self._backoff)

    async def _get_client(self) -> Any:
        if self._client is None:
            from tripwire.ch import async_client, client

            if not self._sync_fallback:
                try:
                    self._client = await async_client()
                    return self._client
                except ImportError as exc:
                    # clickhouse-connect's async client needs aiohttp, which is not in the
                    # frozen dep list. Use the sync client from a worker thread instead.
                    logger.warning(f"writer: async client unavailable ({exc}); using sync client in a thread")
                    self._sync_fallback = True
            self._client = _ThreadedClient(await asyncio.to_thread(client))
        return self._client

    async def _close_client(self) -> None:
        c, self._client = self._client, None
        if c is not None:
            try:
                await c.close()
            except Exception:  # noqa: BLE001
                pass

    async def flush_once(self) -> bool:
        """Insert up to max_insert_rows buffered rows. True on success (or nothing to do)."""
        if not self._buf:
            return True
        n = min(len(self._buf), self.max_insert_rows)
        self._inflight = [self._buf.popleft() for _ in range(n)]
        try:
            client = await self._get_client()
            data = [to_values(r) for r in self._inflight]
            await client.insert(self.table, data, column_names=list(COLUMNS))
        except asyncio.CancelledError:
            self._requeue()
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            logger.warning(f"writer: insert of {n} rows failed (will retry): {self.last_error}")
            self._requeue()
            await self._close_client()
            return False
        self.inserted += n
        self._inflight = []
        self.last_error = None
        self.last_flush_ms = time.time_ns() // 1_000_000
        return True

    def _requeue(self) -> None:
        if self._inflight:
            self._buf.extendleft(reversed(self._inflight))
            self._inflight = []
            self._trim()

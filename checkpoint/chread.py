"""Read-side ClickHouse access for the checkpoint (health ping, counts, audit, startup reconcile).

One cached sync client, used from a worker thread and serialised by an asyncio.Lock
(clickhouse-connect sync clients are not safe for concurrent queries). Every call
is bounded by a timeout and degrades to ``CHUnavailable`` when ClickHouse is down.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger


class CHUnavailable(RuntimeError):
    pass


class CHReader:
    def __init__(self, enabled: bool = True, table: str = "events", timeout: float = 5.0) -> None:
        self.enabled = enabled
        self.table = table
        self.timeout = timeout
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._ping_cache: tuple[float, bool] | None = None
        self._count_cache: tuple[float, int | None, dict[str, Any] | None] | None = None

    def _make_client(self) -> Any:
        from tripwire.ch import client

        return client()

    async def _run(self, fn_name: str, *args: Any, **kwargs: Any) -> Any:
        if not self.enabled:
            raise CHUnavailable("clickhouse disabled")
        async with self._lock:

            def work() -> Any:
                if self._client is None:
                    self._client = self._make_client()
                return getattr(self._client, fn_name)(*args, **kwargs)

            try:
                return await asyncio.wait_for(asyncio.to_thread(work), self.timeout)
            except Exception as exc:  # noqa: BLE001
                self._client = None  # reconnect next time (also abandons a stuck thread's client)
                raise CHUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}") from exc

    async def ping(self, max_age_s: float = 5.0) -> bool:
        now = time.monotonic()
        if self._ping_cache and now - self._ping_cache[0] < max_age_s:
            return self._ping_cache[1]
        try:
            ok = bool(await self._run("ping"))
        except CHUnavailable:
            ok = False
        self._ping_cache = (time.monotonic(), ok)
        return ok

    async def count_events(self, max_age_s: float = 2.0) -> tuple[int | None, dict[str, Any] | None]:
        """(count, receipt) with a 2 s cache. (None, None) when ClickHouse is down."""
        now = time.monotonic()
        if self._count_cache and now - self._count_cache[0] < max_age_s:
            return self._count_cache[1], self._count_cache[2]
        sql = f"SELECT count() FROM {self.table}"
        t0 = time.perf_counter()
        try:
            res = await self._run("query", sql)
            n: int | None = int(res.result_rows[0][0])
            ms = round((time.perf_counter() - t0) * 1000, 2)
            summary = getattr(res, "summary", {}) or {}
            receipt: dict[str, Any] | None = {
                "sql": sql,
                "ms": ms,
                "rows_read": int(summary["read_rows"]) if "read_rows" in summary else None,
            }
        except CHUnavailable:
            n, receipt = None, None
        self._count_cache = (time.monotonic(), n, receipt)
        return n, receipt

    async def agent_rows(self, agent_id: str) -> list[dict[str, Any]]:
        sql = (
            "SELECT toUnixTimestamp64Milli(ts) AS ts_ms, agent_id, action, target, result, reason, "
            f"prev_hash, hash FROM {self.table} "
            "WHERE agent_id = {agent:String} AND synthetic = 0 ORDER BY ts"
        )
        res = await self._run("query", sql, parameters={"agent": agent_id})
        cols = list(res.column_names)
        return [dict(zip(cols, r, strict=False)) for r in res.result_rows]

    async def chain_heads(self) -> dict[str, tuple[int, str]]:
        """agent_id -> (max ts_ms, hash of that row) for live (synthetic=0) rows."""
        sql = (
            "SELECT agent_id, max(toUnixTimestamp64Milli(ts)) AS m, argMax(hash, ts) AS h "
            f"FROM {self.table} WHERE synthetic = 0 GROUP BY agent_id"
        )
        try:
            res = await self._run("query", sql)
        except CHUnavailable as exc:
            logger.info(f"chread: chain reconcile skipped ({exc})")
            return {}
        return {str(a): (int(m), str(h)) for a, m, h in res.result_rows}

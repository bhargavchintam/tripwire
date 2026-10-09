"""Read-side ClickHouse access for the checkpoint (health ping, counts, audit, startup reconcile,
hold-mode history lookup, backtest, fleet heatmap).

One cached sync client per *slot*, used from a worker thread and serialised by that
slot's asyncio.Lock (clickhouse-connect sync clients are not safe for concurrent
queries). Slots keep the hot path (``hold``) from queueing behind a full-table scan
(``heavy``). Every call is bounded by a timeout and degrades to ``CHUnavailable``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger


class CHUnavailable(RuntimeError):
    pass


@dataclass
class QueryOut:
    rows: list[tuple[Any, ...]]
    columns: list[str]
    ms: float
    rows_read: int | None
    sql: str

    def receipt(self, **extra: Any) -> dict[str, Any]:
        return {"sql": self.sql, "ms": self.ms, "rows_read": self.rows_read, **extra}


class CHReader:
    def __init__(self, enabled: bool = True, table: str = "events", timeout: float = 5.0) -> None:
        self.enabled = enabled
        self.table = table
        self.timeout = timeout
        self._clients: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._ping_cache: tuple[float, bool] | None = None
        self._count_cache: tuple[float, int | None, dict[str, Any] | None] | None = None

    def _make_client(self) -> Any:
        from tripwire.ch import client

        return client()

    async def _run(self, fn_name: str, *args: Any, slot: str = "main", timeout: float | None = None, **kwargs: Any) -> Any:
        if not self.enabled:
            raise CHUnavailable("clickhouse disabled")
        lock = self._locks.setdefault(slot, asyncio.Lock())
        async with lock:

            def work() -> Any:
                c = self._clients.get(slot)
                if c is None:
                    c = self._clients[slot] = self._make_client()
                return getattr(c, fn_name)(*args, **kwargs)

            try:
                return await asyncio.wait_for(asyncio.to_thread(work), timeout or self.timeout)
            except Exception as exc:  # noqa: BLE001
                self._clients.pop(slot, None)  # reconnect next time (also abandons a stuck thread's client)
                raise CHUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}") from exc

    async def query(self, sql: str, *, slot: str = "main", timeout: float | None = None) -> QueryOut:
        """Run ``sql`` (already rendered with escaped literals) and time it; rows_read from the
        server's query summary (None when the server did not report it)."""
        t0 = time.perf_counter()
        res = await self._run("query", sql, slot=slot, timeout=timeout)
        ms = round((time.perf_counter() - t0) * 1000, 2)
        summary = getattr(res, "summary", {}) or {}
        rr = int(summary["read_rows"]) if "read_rows" in summary else None
        return QueryOut(list(res.result_rows), list(res.column_names), ms, rr, sql)

    async def warm(self, slot: str = "hold") -> None:
        """Open the slot's connection ahead of the first hot-path lookup."""
        try:
            await self._run("query", "SELECT 1", slot=slot, timeout=5.0)
        except CHUnavailable as exc:
            logger.info(f"chread: warm-up of slot {slot} skipped ({exc})")

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


# ---------------------------------------------------------------------------
# Signature queries (SQL text in receipts is exactly what ran: literals escaped by sql_str)
# ---------------------------------------------------------------------------

HOLD_HISTORY_TIMEOUT_S = 0.8


async def hold_history(ch: CHReader, agent_id: str, host: str, before_ms: int) -> tuple[bool, dict[str, Any]]:
    """Has ``agent_id`` ever http_post'ed to ``host`` with result 'ok' before ``before_ms``?"""
    from checkpoint.policy import sql_str

    sql = (
        "SELECT count() FROM (SELECT 1 FROM " + ch.table + " "
        f"WHERE agent_id = {sql_str(agent_id)} AND action = 'http_post' AND result = 'ok' "
        f"AND ts < fromUnixTimestamp64Milli(toInt64({int(before_ms)})) "
        f"AND lower(domain(target)) = {sql_str(host.lower())} LIMIT 1) "
        "SETTINGS log_comment = 'tripwire:hold_history', max_execution_time = 1"
    )
    out = await ch.query(sql, slot="hold", timeout=HOLD_HISTORY_TIMEOUT_S)
    seen = bool(out.rows and int(out.rows[0][0]) > 0)
    return seen, out.receipt(kind="hold_history")


async def agent_external_hosts(ch: CHReader, agent_id: str) -> tuple[list[str], dict[str, Any]]:
    """Distinct external http_post hosts this agent reached with result 'ok' (all history)."""
    from checkpoint.policy import sql_str

    sql = (
        "SELECT DISTINCT lower(domain(target)) AS host FROM " + ch.table + " "
        f"WHERE agent_id = {sql_str(agent_id)} AND action = 'http_post' AND is_external = 1 "
        "AND result = 'ok' AND host != '' ORDER BY host LIMIT 200 "
        "SETTINGS log_comment = 'tripwire:guardrail_hosts'"
    )
    out = await ch.query(sql, slot="heavy", timeout=15.0)
    return [str(r[0]) for r in out.rows], out.receipt(kind="guardrail_hosts")

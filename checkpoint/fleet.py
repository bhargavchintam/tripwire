"""Fleet analytics over tripwire.events: backtest, risk heatmap, top-risk agents (master §13, D3).

Each function issues ONE ClickHouse query (tagged with log_comment) and returns the
measured wall time + the server-reported rows read. SQL in receipts is exactly the
text that ran (literals escaped with sql_str).
"""

from __future__ import annotations

from typing import Any

from checkpoint.chread import CHReader
from checkpoint.policy import SECRET_PATH_SQL_RE, VERIFY_PREFIX, sql_arr, sql_str
from tripwire.contracts import GUILD_AGENT, LIVE_AGENTS, Policy

HOUR_MS = 3_600_000
HEATMAP_MAX_AGENTS = 42

RISK_FORMULA = (
    "raw = denied*5 + honeytoken_hit*20 + (http_post AND is_external)*1 "
    "+ (read_file of a secret-looking path: .env|secret|credential|.pem|id_rsa)*2 "
    "+ (action in assume_role, disable_logging, list_permissions)*3; "
    "score = round(100*raw/max_raw) over the window (0 when max_raw = 0)"
)


def _raw_expr() -> str:
    return (
        "5 * (result = 'denied') + 20 * honeytoken_hit + (action = 'http_post' AND is_external = 1) "
        f"+ 2 * (action = 'read_file' AND match(target, {sql_str(SECRET_PATH_SQL_RE)})) "
        "+ 3 * (action IN ('assume_role', 'disable_logging', 'list_permissions'))"
    )


def _not_verify() -> str:
    # guardrail sandbox replays (verify:*) and Guild's one-off integration self-test are not fleet agents
    return f"NOT startsWith(agent_id, {sql_str(VERIFY_PREFIX)}) AND agent_id != {sql_str('guild:integration-test')}"


def _score(raw: float, max_raw: float) -> int:
    return int(round(100 * raw / max_raw)) if max_raw > 0 else 0


# ---------------------------------------------------------------------------
# B. backtest
# ---------------------------------------------------------------------------


def backtest_sql(policy: Policy, table: str = "events") -> str:
    deny = sorted({h.strip().lower() for h in policy.denylist if h and h.strip()})
    al_agents = sorted(policy.allowlists)
    pairs = sorted(
        f"{a}|{h.strip().lower()}" for a, hosts in policy.allowlists.items() for h in hosts if h and h.strip()
    )
    return (
        f"SELECT count() AS would_block FROM {table} "
        "WHERE action = 'http_post' AND is_external = 1 AND ("
        f"has(CAST({sql_arr(deny)} AS Array(String)), lower(domain(target))) "
        f"OR (has(CAST({sql_arr(al_agents)} AS Array(String)), agent_id) "
        f"AND NOT has(CAST({sql_arr(pairs)} AS Array(String)), concat(agent_id, '|', lower(domain(target)))))) "
        # The query condition cache would skip granules a previous run already ruled out; turn it
        # off so events_scanned is a full evaluation over the table every time.
        "SETTINGS log_comment = 'tripwire:backtest', use_query_condition_cache = 0"
    )


async def backtest_history(ch: CHReader, policy: Policy) -> tuple[int, int | None, float, str]:
    """(would_block, rows_read, query_ms, sql) over ALL of tripwire.events."""
    out = await ch.query(backtest_sql(policy, ch.table), slot="heavy", timeout=60.0)
    would = int(out.rows[0][0]) if out.rows else 0
    return would, out.rows_read, out.ms, out.sql


# ---------------------------------------------------------------------------
# E. heatmap / F. top
# ---------------------------------------------------------------------------


def _agent_order(totals: dict[str, float], limit: int = HEATMAP_MAX_AGENTS) -> list[str]:
    live = [a for a in LIVE_AGENTS]
    guild = sorted(a for a in totals if a.startswith("guild:") and a not in live)
    if GUILD_AGENT in totals and GUILD_AGENT not in guild:
        guild.insert(0, GUILD_AGENT)
    first = live + guild
    rest = sorted((a for a in totals if a not in first), key=lambda a: (-totals[a], a))
    return (first + rest)[:limit]


async def heatmap(ch: CHReader, hours: int, now_ms: int) -> dict[str, Any]:
    hours = max(1, min(int(hours), 24 * 14))
    start = (now_ms // HOUR_MS) * HOUR_MS - (hours - 1) * HOUR_MS
    hour_list = [start + i * HOUR_MS for i in range(hours)]
    sql = (
        "SELECT agent_id, toInt64(toUnixTimestamp(toStartOfHour(ts))) * 1000 AS hour_ms, "
        f"count() AS events, sum({_raw_expr()}) AS raw FROM {ch.table} "
        f"WHERE ts >= fromUnixTimestamp64Milli(toInt64({start})) AND {_not_verify()} "
        "GROUP BY agent_id, hour_ms "
        "SETTINGS log_comment = 'tripwire:heatmap', use_query_condition_cache = 0"
    )
    out = await ch.query(sql, slot="heavy", timeout=30.0)
    totals: dict[str, float] = {}
    raw_cells: list[tuple[str, int, float, int]] = []
    for agent, hour_ms, events, raw in out.rows:
        agent = str(agent)
        idx = (int(hour_ms) - start) // HOUR_MS
        if not 0 <= idx < hours:
            continue
        raw_cells.append((agent, int(idx), float(raw), int(events)))
        totals[agent] = totals.get(agent, 0.0) + float(raw)
    max_raw = max((c[2] for c in raw_cells), default=0.0)
    agents = _agent_order(totals)
    pos = {a: i for i, a in enumerate(agents)}
    cells = [[pos[a], h, _score(r, max_raw), n] for a, h, r, n in raw_cells if a in pos]
    cells.sort(key=lambda c: (c[0], c[1]))
    return {
        "agents": agents,
        "hours": hour_list,
        "cells": cells,
        "query_ms": out.ms,
        "rows_read": out.rows_read,
        "formula": RISK_FORMULA,
        "sql": out.sql,
    }


async def top(ch: CHReader, minutes: int, limit: int, now_ms: int) -> list[dict[str, Any]]:
    minutes = max(1, min(int(minutes), 7 * 24 * 60))
    limit = max(1, min(int(limit), 100))
    since = now_ms - minutes * 60_000
    sql = (
        "SELECT agent_id, count() AS events, countIf(result = 'denied') AS denied, "
        "countIf(action = 'http_post' AND is_external = 1) AS external_posts, "
        f"sum({_raw_expr()}) AS raw FROM {ch.table} "
        f"WHERE ts >= fromUnixTimestamp64Milli(toInt64({since})) AND {_not_verify()} "
        "GROUP BY agent_id "
        "SETTINGS log_comment = 'tripwire:fleet_top'"
    )
    out = await ch.query(sql, slot="heavy", timeout=30.0)
    rows = [(str(a), int(n), int(d), int(x), float(r)) for a, n, d, x, r in out.rows]
    max_raw = max((r[4] for r in rows), default=0.0)
    rows.sort(key=lambda r: (-r[4], r[0]))
    return [
        {"agent_id": a, "score": _score(r, max_raw), "events": n, "denied": d, "external_posts": x}
        for a, n, d, x, r in rows[:limit]
    ]

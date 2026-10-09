"""Seed + bulk-load synthetic history into tripwire.events (master plan §5). Owner: Bindu.

    uv run python -m tripwire.loader seed [--replace]        # ~540 rows for deploy-bot / support-bot
    uv run python -m tripwire.loader load --rows 30000000 --chunk 5000000
    uv run python -m tripwire.loader count

Every row written here is synthetic=1. All timings printed and saved to var/load_summary.json are
measured (wall clock around each INSERT, plus the server's own elapsed time when it reports one);
anything not measured is null. Exit code 2 = ClickHouse unreachable, 3 = schema not applied.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from tripwire import ch
from tripwire.config import get_settings
from tripwire.contracts import LIVE_AGENTS

ROOT = Path(__file__).resolve().parent.parent
SEED_SQL = ROOT / "data" / "seed_live_agents.sql"
BACKGROUND_SQL = ROOT / "data" / "background_data.sql"
SUMMARY_PATH = ROOT / "var" / "load_summary.json"

BACKGROUND_AGENT_PREFIX = "agent-"
EXIT_UNREACHABLE = 2
EXIT_NO_SCHEMA = 3

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Seed / bulk-load synthetic history into tripwire.events (synthetic=1).")


# ---------------------------------------------------------------------------
# SQL helpers (also used by tests/integration to load into a scratch table)
# ---------------------------------------------------------------------------


def split_sql(text: str) -> list[str]:
    """Drop full-line `--` comments and split on ';' (the data/*.sql files never put ';' in strings)."""
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def _retarget(stmt: str, table: str) -> str:
    if table == "events":
        return stmt
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", table):
        raise ValueError(f"bad table name: {table!r}")
    return re.sub(r"\bINSERT INTO events\b", f"INSERT INTO {table}", stmt, count=1)


def seed_statements(table: str = "events") -> list[str]:
    return [_retarget(s, table) for s in split_sql(SEED_SQL.read_text())]


def background_statement(table: str = "events") -> str:
    stmts = split_sql(BACKGROUND_SQL.read_text())
    if len(stmts) != 1:
        raise ValueError(f"{BACKGROUND_SQL.name} must contain exactly one statement, found {len(stmts)}")
    return _retarget(stmts[0], table)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def ping_url() -> str:
    s = get_settings()
    return f"{'https' if s.clickhouse_secure else 'http'}://{s.clickhouse_host}:{s.clickhouse_port}/ping"


def clickhouse_reachable(timeout: float = 5.0) -> bool:
    """Authenticated reachability check against the configured host/port.

    Not an anonymous GET /ping: ClickHouse Cloud's proxy resets unauthenticated /ping
    requests (seen 11:20), so that check falsely reported Cloud as down. A real client
    round-trip (`SELECT 1`, no default database) works for local Docker and Cloud alike.
    """
    try:
        import clickhouse_connect

        from tripwire.ch import _kwargs

        kw = _kwargs(database="")
        kw["connect_timeout"] = timeout
        return clickhouse_connect.get_client(**kw).command("SELECT 1") == 1
    except Exception:
        return False


def connect():
    """client() or exit 2 (unreachable) / exit 3 (schema missing) with a readable message."""
    if not clickhouse_reachable():
        typer.echo(
            f"ClickHouse is not reachable at {ping_url()} — start it with `make up` "
            "(docker compose) and retry. Nothing was written.",
            err=True,
        )
        raise typer.Exit(EXIT_UNREACHABLE)
    try:
        c = ch.client()
        exists = int(c.command("EXISTS TABLE events"))
    except Exception as exc:
        typer.echo(f"ClickHouse answered /ping but the client failed: {exc.__class__.__name__}: {str(exc)[:200]}",
                   err=True)
        raise typer.Exit(EXIT_UNREACHABLE) from exc
    if not exists:
        typer.echo("Table tripwire.events does not exist — run `make db` "
                   "(uv run python -m tripwire.ch init) first.", err=True)
        raise typer.Exit(EXIT_NO_SCHEMA)
    return c


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _written(summary: Any) -> int | None:
    """Rows the server reports as written (X-ClickHouse-Summary), or None if it reported nothing."""
    data = getattr(summary, "summary", None)
    if isinstance(data, dict) and "written_rows" in data:
        return int(data["written_rows"])
    return None


def _server_elapsed_s(summary: Any) -> float | None:
    data = getattr(summary, "summary", None)
    if isinstance(data, dict) and str(data.get("elapsed_ns", "")).isdigit():
        return int(data["elapsed_ns"]) / 1e9
    return None


def _rate(rows: int | None, seconds: float | None) -> float | None:
    if rows is None or not seconds:
        return None
    return round(rows / seconds, 1)


# ---------------------------------------------------------------------------
# Operations (pure functions over a client so tests can point them at a scratch table)
# ---------------------------------------------------------------------------


def live_seed_rows(c, table: str = "events") -> int:
    return int(c.command(
        f"SELECT count() FROM {table} WHERE synthetic = 1 AND agent_id IN {{agents:Array(String)}}",
        parameters={"agents": list(LIVE_AGENTS)},
    ))


def run_seed(c, table: str = "events", replace: bool = False) -> dict[str, Any]:
    """Insert the live-agent history. Idempotent: skips if seed rows exist unless replace=True."""
    existing = live_seed_rows(c, table)
    deleted = 0
    if existing and not replace:
        return {"skipped": True, "existing_rows": existing, "rows_written": 0, "seconds": None}
    if existing and replace:
        c.command(
            f"DELETE FROM {table} WHERE synthetic = 1 AND agent_id IN {{agents:Array(String)}}",
            parameters={"agents": list(LIVE_AGENTS)},
        )
        deleted = existing
    total = 0
    t0 = time.perf_counter()
    for stmt in seed_statements(table):
        total += _written(c.command(stmt)) or 0
    seconds = time.perf_counter() - t0
    return {"skipped": False, "deleted_rows": deleted, "rows_written": total, "seconds": round(seconds, 4)}


def run_load(
    c,
    rows: int,
    chunk: int,
    salt: int | None = None,
    table: str = "events",
    echo=print,
) -> dict[str, Any]:
    """Insert `rows` background rows in chunks of `chunk`, timing each INSERT. Returns the summary."""
    if rows <= 0 or chunk <= 0:
        raise ValueError("rows and chunk must be positive")
    salt = secrets.randbits(32) if salt is None else salt
    sql = background_statement(table)
    summary: dict[str, Any] = {
        "kind": "background_load",
        "table": table if "." in table else f"{get_settings().clickhouse_database}.{table}",
        "synthetic": True,
        "clickhouse_version": str(c.command("SELECT version()")),
        "started_at_utc": _now_iso(),
        "finished_at_utc": None,
        "rows_requested": rows,
        "chunk_size": chunk,
        "salt": salt,
        "chunks": [],
        "rows_written_total": None,
        "wall_seconds_total": None,
        "rows_per_s": None,
        "events_total_after": None,
        "background_rows_after": None,
        "error": None,
    }
    done, index, written_total, wall_total = 0, 0, 0, 0.0
    all_reported = True
    try:
        while done < rows:
            n = min(chunk, rows - done)
            t0 = time.perf_counter()
            result = c.command(sql, parameters={"rows": n, "salt": salt, "chunk": index})
            wall = time.perf_counter() - t0
            written = _written(result)
            entry = {
                "index": index,
                "rows_requested": n,
                "rows_written": written,
                "wall_seconds": round(wall, 4),
                "server_seconds": _server_elapsed_s(result),
                "rows_per_s": _rate(written, wall),
            }
            summary["chunks"].append(entry)
            echo(
                f"chunk {index}: rows_written={written if written is not None else '—'} "
                f"wall={wall:.3f}s server={entry['server_seconds'] if entry['server_seconds'] is not None else '—'}s "
                f"rows/s={entry['rows_per_s'] if entry['rows_per_s'] is not None else '—'}"
            )
            if written is None:
                all_reported = False
            else:
                written_total += written
            wall_total += wall
            done += n
            index += 1
    except Exception as exc:
        summary["error"] = f"{exc.__class__.__name__}: {str(exc)[:300]}"
    summary["finished_at_utc"] = _now_iso()
    summary["rows_written_total"] = written_total if all_reported else None
    summary["wall_seconds_total"] = round(wall_total, 4)
    summary["rows_per_s"] = _rate(summary["rows_written_total"], wall_total)
    try:
        summary["events_total_after"] = int(c.command(f"SELECT count() FROM {table}"))
        summary["background_rows_after"] = int(c.command(
            f"SELECT count() FROM {table} WHERE startsWith(agent_id, {{p:String}})",
            parameters={"p": BACKGROUND_AGENT_PREFIX},
        ))
    except Exception:
        pass  # leave as None: not measured
    return summary


def write_summary(summary: dict[str, Any], path: Path = SUMMARY_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n")
    return path


def table_stats(c, table: str = "events") -> dict[str, Any]:
    t0 = time.perf_counter()
    row = c.query(
        f"""
        SELECT
            count() AS total,
            countIf(synthetic = 1) AS synthetic_rows,
            countIf(synthetic = 0) AS live_rows,
            countIf(startsWith(agent_id, {{p:String}})) AS background_rows,
            countIf(synthetic = 1 AND agent_id IN {{agents:Array(String)}}) AS live_agent_seed_rows,
            uniqExact(agent_id) AS agents,
            toUnixTimestamp64Milli(min(ts)) AS min_ts_ms,
            toUnixTimestamp64Milli(max(ts)) AS max_ts_ms,
            countIf(synthetic = 1 AND is_external != toUInt8(action IN ('http_get', 'http_post')
                AND extract(target, '^[A-Za-z][A-Za-z0-9+.-]*://([^/:?#]+)')
                    NOT IN ('api.internal.example', 'status.internal.example', 'localhost'))) AS is_external_mismatch
        FROM {table}
        """,
        parameters={"p": BACKGROUND_AGENT_PREFIX, "agents": list(LIVE_AGENTS)},
    ).first_item
    query_ms = (time.perf_counter() - t0) * 1000
    stats = dict(row)
    if not stats["total"]:
        stats["min_ts_ms"] = stats["max_ts_ms"] = None
    stats["count_query_ms"] = round(query_ms, 2)
    if "." not in table:
        parts = c.query(
            "SELECT count(), uniqExact(partition) FROM system.parts "
            "WHERE database = {db:String} AND table = {t:String} AND active",
            parameters={"db": get_settings().clickhouse_database, "t": table},
        ).result_rows[0]
        stats["active_parts"], stats["partitions"] = int(parts[0]), int(parts[1])
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@app.command()
def seed(
    replace: Annotated[bool, typer.Option(help="delete existing live-agent seed rows first")] = False,
) -> None:
    """Load ~540 historical rows of normal behaviour for deploy-bot and support-bot."""
    c = connect()
    res = run_seed(c, replace=replace)
    if res["skipped"]:
        typer.echo(f"seed: {res['existing_rows']} live-agent seed rows already present — skipped "
                   "(use --replace to reload).")
        return
    if res.get("deleted_rows"):
        typer.echo(f"seed: deleted {res['deleted_rows']} previous seed rows")
    typer.echo(f"seed: rows_written={res['rows_written']} seconds={res['seconds']:.3f}")


@app.command()
def load(
    rows: Annotated[int, typer.Option(help="total background rows to insert")] = 1_000_000,
    chunk: Annotated[int, typer.Option(help="rows per INSERT")] = 5_000_000,
    salt: Annotated[int | None, typer.Option(help="fixed salt for reproducible rows (default: random)")] = None,
) -> None:
    """Load synthetic background rows (agent-00..agent-39) in chunks; writes var/load_summary.json."""
    if rows <= 0 or chunk <= 0:
        typer.echo("--rows and --chunk must be positive", err=True)
        raise typer.Exit(1)
    c = connect()
    typer.echo(f"load: {rows:,} rows in chunks of {chunk:,} -> tripwire.events (synthetic=1)")
    summary = run_load(c, rows=rows, chunk=chunk, salt=salt, echo=typer.echo)
    path = write_summary(summary)
    total, wall = summary["rows_written_total"], summary["wall_seconds_total"]
    typer.echo(
        f"load: rows_written={total if total is not None else '—'} wall={wall:.3f}s "
        f"rows/s={summary['rows_per_s'] if summary['rows_per_s'] is not None else '—'} "
        f"table_total={summary['events_total_after'] if summary['events_total_after'] is not None else '—'}"
    )
    typer.echo(f"load: summary -> {path.relative_to(ROOT)}")
    if summary["error"]:
        typer.echo(f"load: FAILED after {len(summary['chunks'])} chunk(s): {summary['error']}", err=True)
        raise typer.Exit(1)


@app.command()
def count() -> None:
    """Row counts for tripwire.events (total, synthetic, background, live seed) with query time."""
    c = connect()
    s = table_stats(c)
    now_ms = int(time.time() * 1000)
    for key in ("total", "synthetic_rows", "live_rows", "background_rows", "live_agent_seed_rows", "agents",
                "partitions", "active_parts", "is_external_mismatch", "count_query_ms"):
        if key in s:
            typer.echo(f"{key:>22}: {s[key]:,}" if isinstance(s[key], int) else f"{key:>22}: {s[key]}")
    if s["min_ts_ms"] is not None:
        typer.echo(f"{'oldest_age_h':>22}: {(now_ms - s['min_ts_ms']) / 3_600_000:.2f}")
        typer.echo(f"{'newest_age_min':>22}: {(now_ms - s['max_ts_ms']) / 60_000:.2f}")


if __name__ == "__main__":
    app()

"""Outbreak tracing (master §1 "Traces", S3): patient zero, exposed agents, attacker destinations.

Given an incident (the GET /incidents/{id} JSON), trace() answers three questions with ClickHouse
(detection/sql/outbreak_*.sql; every query is timed and the total is Outbreak.query_ms):

  source_id             the untrusted input behind the attack: the most common non-empty tainted_by on the
                        attacker's rows at or after the first incident step (outbreak_source.sql); when no
                        row is tainted, the attacker's latest read_file of an untrusted input inside the
                        window (policy.untrusted_sources prefixes, outbreak_untrusted_read.sql); "" if none.
  exposed_agents        every OTHER agent that read the source (target = source) or acted on it
                        (tainted_by = source) inside the window, synthetic rows included; sorted,
                        attacker excluded (outbreak_exposed.sql).
  blocked_destinations  distinct hosts of the attacker's external http_post / http_get at or after the first
                        incident step (outbreak_destinations.sql), parsed with host_of() (the same rule as
                        checkpoint.policy.host_of) minus policy.internal_hosts; sorted.

run_outbreak() is what the detector hook and the CLI call: GET /incidents/{id} -> GET /policy -> trace()
-> POST /incidents/{id}/outbreak. The checkpoint then puts the exposed agents on heightened watch, pushes
the hosts to the fleet denylist and emits SSE 'outbreak'. It logs and returns None on any failure
(unknown incident, ClickHouse or checkpoint error, timeout); it never raises.

``ch`` is anything with ``await ch.query(sql, parameters) -> list[dict]`` (detection.loop.ClickHouseAdapter
in production, a scripted fake in tests). Read-only: these queries only SELECT from events.

Run from the repo root:
    uv run python -m detection.outbreak <incident_id>                    # trace + POST the outbreak
    uv run python -m detection.outbreak <incident_id> --no-write         # trace only, print outbreak + receipts
    uv run python -m detection.outbreak <incident_id> --checkpoint-url http://localhost:8000 --window-h 72
Exit 0 when the trace ran (and was posted unless --no-write), 1 otherwise.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
import typer
from loguru import logger

from detection.metrics import Timer
from detection.sql_loader import load_sql
from tripwire.config import get_settings
from tripwire.contracts import Outbreak, Policy

WINDOW_H = 72
HOOK_TIMEOUT_S = 10.0  # overall budget of run_outbreak (the detector hook uses the same number)
QUERY_TIMEOUT_S = 5.0  # per ClickHouse query when run_outbreak builds its own adapter
HTTP_TIMEOUT_S = 5.0
SQL_SOURCE = "outbreak_source"
SQL_UNTRUSTED_READ = "outbreak_untrusted_read"
SQL_EXPOSED = "outbreak_exposed"
SQL_DESTINATIONS = "outbreak_destinations"


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def host_of(target: str) -> str:
    """Lower-case host of a URL or bare host[:port]/path; "" when unparseable.

    Copied verbatim from checkpoint.policy.host_of (Bindu's lane) so the hosts pushed to the denylist are
    exactly what the checkpoint will compare against (tests/unit/test_outbreak.py checks they agree)."""
    t = (target or "").strip()
    if not t:
        return ""
    try:
        parts = urlsplit(t if "://" in t else "//" + t)
        return (parts.hostname or "").lower()
    except ValueError:
        return ""


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_dict(incident: Any) -> dict[str, Any]:
    if isinstance(incident, dict):
        return incident
    dump = getattr(incident, "model_dump", None)
    if callable(dump):
        return dump()
    raise TypeError(f"incident must be a dict or a contracts.Incident, got {type(incident).__name__}")


def incident_window(incident: Any) -> tuple[str, int]:
    """(agent_id, since_ms): the attacker and the first incident step (epoch ms).

    since_ms = the earliest step ts; without steps, last_step_ts_ms, then opened_ms. Raises ValueError
    when the incident names no agent."""
    inc = _as_dict(incident)
    agent = str(inc.get("agent_id") or "").strip()
    if not agent:
        raise ValueError("incident has no agent_id")
    steps = [s for s in (inc.get("steps") or []) if isinstance(s, dict)]
    ts = [_int(s.get("ts_ms")) for s in steps if _int(s.get("ts_ms")) > 0]
    since = min(ts) if ts else (_int(inc.get("last_step_ts_ms")) or _int(inc.get("opened_ms")))
    return agent, since


def _first_str(rows: list[dict[str, Any]], key: str) -> str:
    for r in rows:
        v = str(r.get(key) or "").strip()
        if v:
            return v
    return ""


async def trace_with_receipts(
    incident: Any, *, ch: Any, window_h: int = WINDOW_H, policy: Policy | None = None
) -> tuple[Outbreak, list[dict[str, Any]]]:
    """trace() plus one receipt per query: {"sql": <detection/sql name>, "ms": wall ms, "rows": returned rows}."""
    policy = policy or Policy()
    agent, since_ms = incident_window(incident)
    window_h = max(1, int(window_h))
    receipts: list[dict[str, Any]] = []

    async def q(name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with Timer() as t:
            rows = await ch.query(load_sql(name), params)
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        receipts.append({"sql": name, "ms": t.ms, "rows": len(rows)})
        return rows

    source = _first_str(await q(SQL_SOURCE, {"agent": agent, "since_ms": since_ms}), "source_id")
    if not source:
        prefixes = [p for p in policy.untrusted_sources if p] or list(Policy().untrusted_sources)
        rows = await q(SQL_UNTRUSTED_READ, {"agent": agent, "window_h": window_h, "prefixes": prefixes})
        source = _first_str(rows, "source_id")

    exposed: list[str] = []
    if source:  # nothing can match an empty source: skip the fleet scan
        rows = await q(SQL_EXPOSED, {"agent": agent, "source": source, "window_h": window_h})
        exposed = sorted({str(r.get("agent_id")) for r in rows if r.get("agent_id") and str(r.get("agent_id")) != agent})

    rows = await q(SQL_DESTINATIONS, {"agent": agent, "since_ms": since_ms})
    internal = {h.strip().lower() for h in policy.internal_hosts if h and h.strip()}
    hosts = sorted({h for h in (host_of(str(r.get("target") or "")) for r in rows) if h and h not in internal})

    total_ms = round(sum(float(r["ms"]) for r in receipts), 3)
    ob = Outbreak(source_id=source, exposed_agents=exposed, blocked_destinations=hosts, query_ms=total_ms)
    logger.debug(f"outbreak: {agent} since={since_ms} -> {ob.model_dump()} receipts={receipts}")
    return ob, receipts


async def trace(incident: Any, *, ch: Any, window_h: int = WINDOW_H, policy: Policy | None = None) -> Outbreak:
    """Pure analysis: the Outbreak for an incident dict (see module docstring). query_ms = measured total."""
    ob, _ = await trace_with_receipts(incident, ch=ch, window_h=window_h, policy=policy)
    return ob


# ---------------------------------------------------------------------------
# checkpoint round trip
# ---------------------------------------------------------------------------


def _headers(token: str | None) -> dict[str, str]:
    tok = token if token is not None else get_settings().tripwire_token
    return {"X-Tripwire-Token": tok} if tok else {}


async def fetch_incident(client: httpx.AsyncClient, incident_id: str, token: str | None = None) -> dict[str, Any] | None:
    """GET /incidents/{id} as a dict; None (logged) when the checkpoint answers anything but 200."""
    r = await client.get(f"/incidents/{incident_id}", headers=_headers(token))
    if r.status_code != 200:
        logger.warning(f"outbreak: GET /incidents/{incident_id} -> HTTP {r.status_code} {r.text[:120]}")
        return None
    data = r.json()
    return data if isinstance(data, dict) else None


async def fetch_policy(client: httpx.AsyncClient, token: str | None = None) -> Policy:
    """GET /policy; the contract default Policy() when it fails (logged)."""
    try:
        r = await client.get("/policy", headers=_headers(token))
        if r.status_code == 200:
            return Policy.model_validate(r.json())
        logger.warning(f"outbreak: GET /policy -> HTTP {r.status_code}; using the default policy")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"outbreak: GET /policy failed ({exc!r}); using the default policy")
    return Policy()


async def _run(
    incident_id: str,
    *,
    client: httpx.AsyncClient,
    ch: Any,
    token: str | None,
    window_h: int,
    write: bool,
) -> tuple[Outbreak, list[dict[str, Any]]] | None:
    incident = await fetch_incident(client, incident_id, token)
    if incident is None:
        return None
    policy = await fetch_policy(client, token)
    own = ch is None
    if own:
        from detection.loop import ClickHouseAdapter  # lazy: detection.loop imports this module lazily too

        ch = ClickHouseAdapter(timeout_s=QUERY_TIMEOUT_S)
    try:
        ob, receipts = await trace_with_receipts(incident, ch=ch, window_h=window_h, policy=policy)
    finally:
        if own:
            await ch.close()
    if write:
        resp = await client.post(f"/incidents/{incident_id}/outbreak", json=ob.model_dump(), headers=_headers(token))
        if resp.status_code >= 400:
            logger.warning(f"outbreak: POST /incidents/{incident_id}/outbreak -> HTTP {resp.status_code} {resp.text[:160]}")
            return None
        applied = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        logger.info(
            f"outbreak: {incident_id} ({incident.get('agent_id')}) source={ob.source_id!r} "
            f"exposed={ob.exposed_agents} heightened={applied.get('heightened')} "
            f"destinations={ob.blocked_destinations} denylist_added={applied.get('denylist_added')} "
            f"query_ms={ob.query_ms}"
        )
    return ob, receipts


async def run_outbreak(
    incident_id: str,
    *,
    client: httpx.AsyncClient,
    ch: Any = None,
    token: str | None = None,
    window_h: int = WINDOW_H,
    timeout_s: float = HOOK_TIMEOUT_S,
) -> Outbreak | None:
    """GET /incidents/{id} -> trace() -> POST /incidents/{id}/outbreak; the Outbreak, or None on any failure.

    ``client`` talks to the checkpoint (base_url set; an in-process ASGITransport client works). ``token``
    defaults to settings.tripwire_token and is sent on every request. ``ch`` defaults to a fresh
    detection.loop.ClickHouseAdapter that is closed afterwards. Bounded by timeout_s overall; never raises."""
    try:
        out = await asyncio.wait_for(
            _run(incident_id, client=client, ch=ch, token=token, window_h=window_h, write=True), timeout_s
        )
    except asyncio.TimeoutError:
        logger.warning(f"outbreak: {incident_id} timed out after {timeout_s:g} s")
        return None
    except Exception as exc:  # noqa: BLE001 - the hook must never raise into the detector loop
        logger.warning(f"outbreak: {incident_id} failed: {type(exc).__name__}: {str(exc)[:160]}")
        return None
    return out[0] if out else None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

app = typer.Typer(add_completion=False, help="Trace an incident's outbreak and post it to the checkpoint.")


@app.command()
def main(
    incident_id: str = typer.Argument(..., help="incident id from GET /incidents (inc-...)"),
    checkpoint_url: Optional[str] = typer.Option(None, "--checkpoint-url", help="default: CHECKPOINT_URL from .env"),
    window_h: int = typer.Option(WINDOW_H, "--window-h", help="exposure window in hours (> 0)"),
    write: bool = typer.Option(True, "--write/--no-write", help="--no-write: trace only, do not POST the outbreak"),
    timeout_s: float = typer.Option(30.0, "--timeout-s", help="overall budget in seconds"),
) -> None:
    """Exit 0 when the trace ran (and was posted unless --no-write), 1 otherwise."""
    if window_h <= 0 or timeout_s <= 0:
        typer.echo("bad arguments: --window-h and --timeout-s must be > 0", err=True)
        raise typer.Exit(2)
    s = get_settings()
    url = (checkpoint_url or s.checkpoint_url).rstrip("/")

    async def go() -> tuple[Outbreak, list[dict[str, Any]]] | None:
        async with httpx.AsyncClient(base_url=url, timeout=HTTP_TIMEOUT_S) as client:
            try:
                return await asyncio.wait_for(
                    _run(incident_id, client=client, ch=None, token=s.tripwire_token, window_h=window_h, write=write),
                    timeout_s,
                )
            except asyncio.TimeoutError:
                logger.error(f"outbreak: timed out after {timeout_s:g} s")
            except Exception as exc:  # noqa: BLE001 - CLI: report and exit 1
                logger.error(f"outbreak: {type(exc).__name__}: {str(exc)[:200]} (checkpoint {url})")
            return None

    out = asyncio.run(go())
    if out is None:
        raise typer.Exit(1)
    ob, receipts = out
    typer.echo(json.dumps({"incident_id": incident_id, "written": write, **ob.model_dump(), "receipts": receipts}, indent=2))
    raise typer.Exit(0)


if __name__ == "__main__":
    app()

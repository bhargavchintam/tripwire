"""Replay a recorded fixtures/*.json Scenario against the checkpoint (master §7b).

Local mode drives the steps itself (honouring offset_ms) and, for an
"denied_after_block" step, polls GET /status until the agent is blocked (up to
--block-wait seconds) before sending the call. Per-step ok is computed exactly like
checkpoint/service.py _run_replay. --via-api instead asks the checkpoint to run the
scenario (POST /demo/replay) and polls GET /demo/replay/{run_id}.

    uv run python -m agents.replay fixtures/secret_theft.json
    uv run python -m agents.replay fixtures/secret_theft.json --agent-id deploy-bot --block-wait 10
    uv run python -m agents.replay fixtures/normal_ops.json --via-api --checkpoint-url http://localhost:8000
    uv run python -m agents.replay fixtures/secret_theft.json --json

Exit code: 0 pass, 1 fail, 2 checkpoint down or it rejected the request (401/422/5xx).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from loguru import logger

from agents.checkpoint_client import CheckpointClient, CheckpointDown, CheckpointError
from tripwire.config import get_settings
from tripwire.contracts import Scenario, ToolCall

POLL_INTERVAL_S = 0.05
API_POLL_INTERVAL_S = 0.25
API_TIMEOUT_S = 60.0


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _ok_for(expect: str, result: str) -> bool:
    """Same rule as checkpoint/service.py _run_replay."""
    if expect == "any":
        return True
    if expect == "ok":
        return result == "ok"
    return result == "denied"  # "denied" or "denied_after_block"


async def run_scenario(
    scenario: Scenario,
    client: CheckpointClient,
    agent_override: Optional[str] = None,
    block_wait_s: float = 10.0,
) -> dict[str, Any]:
    """Drive a scenario locally. Returns {passed, outcomes, started_ms, finished_ms}.

    Mirrors the checkpoint's own replay: offset_ms is relative to start, a
    denied_after_block step waits for the agent to be blocked (poll /status) before
    the call, and ok is computed with _ok_for. Raises CheckpointDown if the checkpoint
    cannot be reached mid-run.
    """
    session_id = f"replay:local-{uuid.uuid4().hex[:8]}"
    t0 = time.monotonic()
    started_ms = _now_ms()
    outcomes: list[dict[str, Any]] = []
    for i, step in enumerate(scenario.steps):
        delay = t0 + step.offset_ms / 1000.0 - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        agent = agent_override or step.agent_id
        waited_ms: Optional[int] = None
        if step.expect == "denied_after_block":
            w0 = time.monotonic()
            while time.monotonic() - w0 < block_wait_s:
                st = await client.status()
                if agent in st.blocked:
                    break
                await asyncio.sleep(POLL_INTERVAL_S)
            waited_ms = round((time.monotonic() - w0) * 1000)
        res = await client.tool(
            ToolCall(
                agent_id=agent,
                action=step.action,
                target=step.target,
                payload=step.payload,
                tainted_by=step.tainted_by,
                bytes=step.bytes,
                session_id=session_id,
            )
        )
        outcomes.append(
            {
                "i": i,
                "agent_id": agent,
                "action": step.action,
                "target": step.target,
                "expect": step.expect,
                "result": res.result,
                "reason": res.reason,
                "ts_ms": res.ts_ms,
                "incident_id": res.incident_id,
                "waited_for_block_ms": waited_ms,
                "ok": _ok_for(step.expect, res.result),
            }
        )
    return {
        "passed": all(o["ok"] for o in outcomes),
        "outcomes": outcomes,
        "started_ms": started_ms,
        "finished_ms": _now_ms(),
    }


async def _run_via_api(
    scenario_name: str, client: CheckpointClient, agent_override: Optional[str]
) -> dict[str, Any]:
    """Ask the checkpoint to run the scenario and poll until it is not "running".

    An HTTP error (404 unknown fixture, 422 bad name, 401 bad token) or a poll timeout is
    reported as a failed run with an "error" string, never as a traceback. CheckpointDown
    (server gone) propagates so the CLI can exit 2."""
    try:
        started = await client.start_replay(scenario_name, agent_override)
    except CheckpointError as exc:
        return {"passed": False, "outcomes": [], "error": str(exc)}
    run_id = started.get("run_id") if isinstance(started, dict) else None
    if not run_id:
        return {"passed": False, "outcomes": [], "error": f"no run_id in reply: {str(started)[:200]}"}
    deadline = time.monotonic() + API_TIMEOUT_S
    run: dict[str, Any] = {}
    while True:
        try:
            run = await client.replay_status(run_id)
        except CheckpointError as exc:
            return {"passed": False, "outcomes": [], "run_id": run_id, "error": str(exc)}
        if run.get("status") != "running":
            break
        if time.monotonic() >= deadline:
            run["error"] = f"timed out after {API_TIMEOUT_S:.0f}s; checkpoint still reports status=running"
            break
        await asyncio.sleep(API_POLL_INTERVAL_S)
    if not isinstance(run.get("outcomes"), list):
        run["outcomes"] = []
    if run.get("passed") is None:
        run["passed"] = False
    return run


def _truncate(text: str, width: int = 48) -> str:
    text = text or ""
    return text if len(text) <= width else text[: width - 3] + "..."


def _format_table(outcomes: list[dict[str, Any]]) -> str:
    header = ["i", "agent", "action", "target", "expect", "result", "reason", "waited_ms", "ok"]
    rows = [header]
    for o in outcomes:
        waited = o.get("waited_for_block_ms")
        rows.append(
            [
                str(o.get("i", "")),
                str(o.get("agent_id", "")),
                str(o.get("action", "")),
                _truncate(str(o.get("target", ""))),
                str(o.get("expect", "")),
                str(o.get("result", "")),
                str(o.get("reason", "")),
                "" if waited is None else str(waited),
                "OK" if o.get("ok") else "XX",
            ]
        )
    widths = [max(len(r[c]) for r in rows) for c in range(len(header))]
    lines = []
    for ri, r in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[c]) for c, cell in enumerate(r)))
        if ri == 0:
            lines.append("  ".join("-" * widths[c] for c in range(len(header))))
    return "\n".join(lines)


app = typer.Typer(add_completion=False, help="Replay a fixtures/*.json scenario against the checkpoint.")


@app.command()
def main(
    scenario_path: Annotated[Path, typer.Argument(help="path to a fixtures/*.json Scenario")],
    agent_id: Annotated[str, typer.Option("--agent-id", help="override every step's agent")] = "",
    checkpoint_url: Annotated[str, typer.Option("--checkpoint-url", help="checkpoint base URL")] = "",
    via_api: Annotated[bool, typer.Option("--via-api", help="run through POST /demo/replay instead of locally")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="print the result as JSON")] = False,
    block_wait: Annotated[float, typer.Option("--block-wait", help="seconds to wait for a block")] = 10.0,
) -> None:
    try:
        scenario = Scenario.model_validate_json(Path(scenario_path).read_text())
    except (OSError, ValueError) as exc:
        logger.error(f"cannot load scenario {scenario_path}: {exc}")
        raise typer.Exit(2) from exc

    url = checkpoint_url or get_settings().checkpoint_url
    override = agent_id or None

    async def _go() -> dict[str, Any]:
        client = CheckpointClient(base_url=url)
        try:
            if via_api:
                return await _run_via_api(scenario.name, client, override)
            return await run_scenario(scenario, client, override, block_wait_s=block_wait)
        finally:
            await client.aclose()

    try:
        result = asyncio.run(_go())
    except CheckpointDown as exc:
        logger.error(f"checkpoint unreachable at {url}: {exc}")
        raise typer.Exit(2) from exc
    except CheckpointError as exc:
        hint = " (check TRIPWIRE_TOKEN / PUBLIC)" if exc.status == 401 else ""
        logger.error(f"checkpoint rejected the request: {exc}{hint}")
        raise typer.Exit(2) from exc

    outcomes = result.get("outcomes", [])
    passed = bool(result.get("passed"))
    if as_json:
        typer.echo(json.dumps(result, indent=2, default=str))
    else:
        typer.echo(_format_table(outcomes))
        if result.get("error"):
            typer.echo(f"error: {result['error']}")
        typer.echo(f"\n{'PASS' if passed else 'FAIL'} — {scenario.name} ({len(outcomes)} steps)")
    raise typer.Exit(0 if passed else 1)


if __name__ == "__main__":
    app()

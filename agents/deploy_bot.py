"""deploy-bot: a benign deploy agent that drives the checkpoint over HTTP.

One normal session: read /app/.env, read /app/config.yml, `npm test`, `make deploy`,
then post the deployment + status to internal services. Everything is record-only
(see agents/fake_tools.py); no real file, shell or network side effects.

    uv run python -m agents.deploy_bot --once
    uv run python -m agents.deploy_bot --loop --interval 5
    uv run python -m agents.deploy_bot --once --checkpoint-url http://localhost:8000

Exit code (--once): 0 all ok, 2 checkpoint down or rejected the request, 3 a step was denied.
--loop never exits on its own: denials and an unreachable checkpoint are logged and the next
session is retried after --interval (see agents/botbase.py). Ctrl-C exits 0.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Annotated

import typer

from agents.botbase import SessionFn, drive
from agents.checkpoint_client import CheckpointClient
from agents.fake_tools import FakeTools
from tripwire.config import get_settings

app = typer.Typer(add_completion=False, help="deploy-bot: benign deploy agent (record-only).")


def _session_factory(agent_id: str) -> SessionFn:
    async def _run(client: CheckpointClient) -> FakeTools:
        sid = f"deploy-{secrets.token_hex(4)}"
        tools = FakeTools(agent_id, client, session_id=sid, raise_on_deny=True, verbose=True)
        await tools.read_file("/app/.env")
        await tools.read_file("/app/config.yml")
        await tools.run_command("npm test")
        await tools.run_command("make deploy")
        await tools.http_post("https://api.internal.example/v1/deployments", body='{"service":"app","version":"2.14.0"}')
        await tools.http_post("https://status.internal.example/v1/status", body='{"status":"deployed"}')
        return tools

    return _run


@app.command()
def main(
    once: Annotated[bool, typer.Option("--once", help="run one session (default)")] = False,
    loop: Annotated[bool, typer.Option("--loop", help="repeat sessions until Ctrl-C")] = False,
    interval: Annotated[float, typer.Option("--interval", help="seconds between loop sessions")] = 5.0,
    checkpoint_url: Annotated[str, typer.Option("--checkpoint-url", help="checkpoint base URL")] = "",
    agent_id: Annotated[str, typer.Option("--agent-id", help="agent id to run as")] = "deploy-bot",
) -> None:
    url = checkpoint_url or get_settings().checkpoint_url
    session_fn = _session_factory(agent_id)
    try:
        code = asyncio.run(drive(url, once, loop, interval, session_fn))
    except KeyboardInterrupt:
        code = 0
    raise typer.Exit(code)


if __name__ == "__main__":
    app()

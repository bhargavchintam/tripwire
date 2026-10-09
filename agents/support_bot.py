"""support-bot: a benign support agent that reads a (possibly poisoned) ticket.

One normal session: read a random ticket (1000-1999), read /var/log/app.log, fetch a
KB article, then post a status update. Every step after the ticket read carries
tainted_by="ticket:<id>", so outbreak tracing can follow the untrusted input.
Everything is record-only (agents/fake_tools.py); no real IO.

    uv run python -m agents.support_bot --once
    uv run python -m agents.support_bot --loop --interval 5
    uv run python -m agents.support_bot --once --checkpoint-url http://localhost:8000

Exit code (--once): 0 all ok, 2 checkpoint down or rejected the request, 3 a step was denied.
--loop never exits on its own: denials and an unreachable checkpoint are logged and the next
session is retried after --interval (see agents/botbase.py). Ctrl-C exits 0.
"""

from __future__ import annotations

import asyncio
import random
import secrets
from typing import Annotated

import typer

from agents.botbase import SessionFn, drive
from agents.checkpoint_client import CheckpointClient
from agents.fake_tools import FakeTools
from tripwire.config import get_settings

app = typer.Typer(add_completion=False, help="support-bot: benign support agent (record-only).")

_KB_TOPICS = ("billing-faq", "password-reset", "api-limits", "sso-setup", "data-export")


def _session_factory(agent_id: str) -> SessionFn:
    async def _run(client: CheckpointClient) -> FakeTools:
        ticket = f"ticket:{random.randint(1000, 1999)}"
        sid = f"support-{secrets.token_hex(4)}"
        tools = FakeTools(agent_id, client, session_id=sid, raise_on_deny=True, verbose=True)
        await tools.read_file(ticket)
        await tools.read_file("/var/log/app.log", tainted_by=ticket)
        await tools.http_get(f"https://docs.example.com/kb/{random.choice(_KB_TOPICS)}", tainted_by=ticket)
        await tools.http_post(
            "https://status.internal.example/v1/updates",
            body='{"ticket":"' + ticket.split(":", 1)[-1] + '","state":"answered"}',
            tainted_by=ticket,
        )
        return tools

    return _run


@app.command()
def main(
    once: Annotated[bool, typer.Option("--once", help="run one session (default)")] = False,
    loop: Annotated[bool, typer.Option("--loop", help="repeat sessions until Ctrl-C")] = False,
    interval: Annotated[float, typer.Option("--interval", help="seconds between loop sessions")] = 5.0,
    checkpoint_url: Annotated[str, typer.Option("--checkpoint-url", help="checkpoint base URL")] = "",
    agent_id: Annotated[str, typer.Option("--agent-id", help="agent id to run as")] = "support-bot",
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

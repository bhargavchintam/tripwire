"""A minimal stand-in checkpoint, so the agents can be developed without Bindu's server.

POST /tool   -> records the call in app.state.calls and always answers result="ok"
GET  /status -> an empty StatusResponse
GET  /health -> {"ok": true, "stub": true}

It does NOT detect, block, hold or persist anything; it is just a sink that speaks the
/tool contract. Swap CHECKPOINT_URL back to the real checkpoint for the real pipeline.

    uv run python -m agents.stub_checkpoint --port 18000
"""

from __future__ import annotations

import time
from typing import Annotated, Any

import typer
import uvicorn
from fastapi import FastAPI

from tripwire.contracts import StatusResponse, ToolCall, ToolResult


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def create_stub_app() -> FastAPI:
    app = FastAPI(title="Tripwire stub checkpoint", version="0.1.0")
    app.state.calls = []

    @app.post("/tool", response_model=ToolResult)
    async def tool(call: ToolCall) -> ToolResult:
        app.state.calls.append(call.model_dump())
        return ToolResult(agent_id=call.agent_id, result="ok", reason="", ts_ms=_now_ms())

    @app.get("/status", response_model=StatusResponse)
    async def status() -> StatusResponse:
        return StatusResponse(active=[], blocked=[], open_incidents=[], watermarks={})

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "stub": True, "calls": len(app.state.calls)}

    return app


app = create_stub_app()

cli = typer.Typer(add_completion=False, help="Serve the stub checkpoint for offline agent dev.")


@cli.command()
def main(
    port: Annotated[int, typer.Option("--port", help="port to bind (use 18xxx locally)")] = 18000,
    host: Annotated[str, typer.Option("--host", help="host to bind")] = "127.0.0.1",
) -> None:
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    cli()

"""OpenAI-compatible fake LLM (owner: Sripadha). Tests bind it in-process; manual runs serve a port.

    from tests.fakes.fake_llm import make_app
    app = make_app([{"json": {"verdict": "malicious", "confidence": 0.9, "reason": "x"}}])
    client = AsyncOpenAI(api_key="x", base_url="http://fake/v1",
                         http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                       base_url="http://fake"))

Endpoints: GET /v1/models and POST /v1/chat/completions. The script is consumed in order, one
item per chat call; when it is exhausted the last item repeats:
    {"json": {...}}                   content = json.dumps(obj)
    {"text": "..."}                   raw content (fenced JSON, prose, garbage)
    {"status": 500}                   HTTP error with that status (OpenAI error body)
    {"delay_s": 2.0, "json": {...}}   sleep first, then answer
Responses have the OpenAI shape (id, object, created, model, choices[], usage{}); usage tokens
are len(text) // 4 (at least 1). app.state.calls records every chat request body, in order;
app.state.model_calls counts /v1/models requests.

Manual run (fixed verdict on a throwaway port):
    uv run python -m tests.fakes.fake_llm --port 18099 --verdict malicious
    AKASHML_BASE_URL=http://localhost:18099/v1 AKASHML_API_KEY=fake AKASHML_MODEL_SMALL=fake-small \\
      uv run python -m ai.quick_check fixtures/secret_theft.json
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import typer
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

DEFAULT_MODELS = ["fake-small", "fake-large-70b"]


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def make_app(script: list[dict[str, Any]], models: list[str] | None = None) -> FastAPI:
    """FastAPI app serving the scripted answers (see module docstring)."""
    if not script:
        raise ValueError("script needs at least one item")
    app = FastAPI(title="fake LLM")
    app.state.script = [dict(item) for item in script]
    app.state.calls = []
    app.state.model_calls = 0
    app.state.models = list(models or DEFAULT_MODELS)

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        app.state.model_calls += 1
        return {
            "object": "list",
            "data": [{"id": m, "object": "model", "owned_by": "fake"} for m in app.state.models],
        }

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Any:
        body = await request.json()
        app.state.calls.append(body)
        item = app.state.script[min(len(app.state.calls) - 1, len(app.state.script) - 1)]
        if item.get("delay_s"):
            await asyncio.sleep(float(item["delay_s"]))
        if item.get("status"):
            status = int(item["status"])
            return JSONResponse(
                status_code=status,
                content={"error": {"message": f"fake error {status}", "type": "server_error", "code": status}},
            )
        content = json.dumps(item["json"]) if "json" in item else str(item.get("text", ""))
        prompt_tokens = _tokens(json.dumps(body.get("messages", [])))
        completion_tokens = _tokens(content)
        return {
            "id": f"chatcmpl-fake-{len(app.state.calls)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", "fake"),
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    return app


def main(
    port: int = 18099,
    host: str = "127.0.0.1",
    verdict: str = "malicious",
    confidence: float = 0.9,
    reason: str = "fake LLM fixed verdict (not a real model)",
    delay_s: float = 0.0,
    model: str = "fake-small",
) -> None:
    """Serve one fixed verdict so classify() / the detector can be pointed at this process."""
    import uvicorn

    item: dict[str, Any] = {"json": {"verdict": verdict, "confidence": confidence, "reason": reason}}
    if delay_s > 0:
        item["delay_s"] = delay_s
    app = make_app([item], models=[model, "fake-large-70b"])
    typer.echo(f"fake LLM on http://{host}:{port}/v1  verdict={verdict} confidence={confidence} model={model}")
    typer.echo(f"  AKASHML_BASE_URL=http://{host}:{port}/v1 AKASHML_API_KEY=fake AKASHML_MODEL_SMALL={model}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    typer.run(main)

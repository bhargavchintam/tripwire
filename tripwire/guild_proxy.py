"""Guild proxy (master §2, §13; D4): the only public surface a Guild-hosted agent can reach.

    GUILD_PROXY_TOKEN=... uv run uvicorn tripwire.guild_proxy:app --host 127.0.0.1 --port 8010

Guild agents have no internet egress (docs.guild.ai/guide/sdk-introduction#network-isolation).
They call our custom Guild integration (guild/openapi.yaml), whose base URL is a public tunnel
to this app. Guild injects the integration's API-key credential server-side as a header
(CLI default template ``X-API-Key: {token}``; guild/RUNBOOK.md creates it with
``--header-template "X-Tripwire-Token: {token}"``), so the agent never sees the token.

Exposes exactly ``POST /tool`` and ``GET /health`` (no /docs, no /openapi.json, nothing else):

* auth: ``X-Tripwire-Token`` (or ``X-API-Key``) must equal the proxy token: env
  ``GUILD_PROXY_TOKEN`` when set, else ``settings.tripwire_token``. Constant-time compare.
  An empty or default ("change-me") token refuses every call (503): this app is internet-facing.
* ``agent_id`` is forced into the ``guild:`` namespace (prefixed if missing, ``guild:deploy-bot``
  if absent), so a Guild agent can never act as a local agent such as ``deploy-bot``.
* forwards the ToolCall to ``{checkpoint_url}/tool`` with ``X-Tripwire-Token`` (10 s timeout)
  and returns the checkpoint's ToolResult verbatim; any upstream failure becomes a 502.
"""

from __future__ import annotations

import hmac
import json
import os
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import ValidationError

from tripwire.config import Settings, get_settings
from tripwire.contracts import GUILD_AGENT, ToolCall, ToolResult

PREFIX = "guild:"
# Header names Guild may send the integration credential in (ours first, Guild's default second).
AUTH_HEADERS = ("x-tripwire-token", "x-api-key")
UNSAFE_TOKENS = ("", "change-me")
UPSTREAM_TIMEOUT_S = 10.0
HEALTH_TIMEOUT_S = 2.0
MAX_BODY_BYTES = 64 * 1024


def guild_agent_id(raw: Any) -> str:
    """Force an agent id into the guild: namespace (missing/blank -> guild:deploy-bot)."""
    aid = raw.strip() if isinstance(raw, str) else ""
    if not aid:
        return GUILD_AGENT
    if not aid.startswith(PREFIX):
        aid = PREFIX + aid
    return aid if len(aid) > len(PREFIX) else GUILD_AGENT


def _err(status: int, detail: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, **extra})


def create_app(
    settings: Settings | None = None,
    *,
    inbound_token: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the proxy. ``inbound_token`` overrides settings.tripwire_token for inbound auth;
    ``transport`` lets tests swap in an httpx.MockTransport for the checkpoint."""
    settings = settings or get_settings()
    token = (inbound_token or settings.tripwire_token or "").strip()
    configured = token not in UNSAFE_TOKENS
    token_b = token.encode("utf-8")
    base = settings.checkpoint_url.rstrip("/")
    upstream_headers = {"X-Tripwire-Token": settings.tripwire_token}
    if not configured:
        logger.warning("guild proxy: no safe token (set GUILD_PROXY_TOKEN); every /tool call gets 503")

    app = FastAPI(title="Tripwire Guild proxy", docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(request: Request) -> bool:
        for name in AUTH_HEADERS:
            got = request.headers.get(name)
            if got and hmac.compare_digest(got.encode("utf-8"), token_b):
                return True
        return False

    @app.post("/tool")
    async def tool(request: Request) -> JSONResponse:
        if not configured:
            return _err(503, "guild proxy token not configured (set GUILD_PROXY_TOKEN)")
        if not authorized(request):
            return _err(401, "missing or invalid X-Tripwire-Token")
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return _err(413, f"body over {MAX_BODY_BYTES} bytes")
        try:
            data = json.loads(raw or b"null")
        except ValueError:
            return _err(422, "body must be a JSON object (ToolCall)")
        if not isinstance(data, dict):
            return _err(422, "body must be a JSON object (ToolCall)")
        data["agent_id"] = guild_agent_id(data.get("agent_id"))
        try:
            call = ToolCall.model_validate(data)
        except ValidationError as exc:
            errors = json.loads(exc.json(include_url=False, include_input=False))
            return _err(422, "invalid ToolCall", errors=errors)

        try:
            async with httpx.AsyncClient(transport=transport, timeout=UPSTREAM_TIMEOUT_S) as c:
                r = await c.post(base + "/tool", json=call.model_dump(), headers=upstream_headers)
        except httpx.HTTPError as exc:
            logger.warning(f"guild proxy: checkpoint unreachable ({type(exc).__name__})")
            return _err(502, f"checkpoint unreachable: {type(exc).__name__}")
        if r.status_code != 200:
            logger.warning(f"guild proxy: checkpoint answered {r.status_code}")
            return _err(502, "checkpoint error", upstream_status=r.status_code)
        try:
            result = ToolResult.model_validate(r.json())
        except (ValueError, ValidationError):
            return _err(502, "checkpoint returned an invalid ToolResult")
        # Never log the payload (it may carry decoy secrets); action/target/result only.
        logger.info(
            f"guild proxy: {call.agent_id} {call.action} {call.target!r} -> {result.result} {result.reason}"
        )
        return JSONResponse(status_code=200, content=result.model_dump())

    @app.get("/health")
    async def health() -> dict[str, Any]:
        up = False
        try:
            async with httpx.AsyncClient(transport=transport, timeout=HEALTH_TIMEOUT_S) as c:
                up = (await c.get(base + "/health")).is_success
        except httpx.HTTPError:
            up = False
        return {"ok": True, "checkpoint": up, "token_configured": configured}

    return app


app = create_app(inbound_token=os.environ.get("GUILD_PROXY_TOKEN") or None)

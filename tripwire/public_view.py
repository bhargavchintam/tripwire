"""Read-only public view of the live console: what a judge's link points at.

    uv run uvicorn tripwire.public_view:app --host 127.0.0.1 --port 8020
    cloudflared tunnel --url http://127.0.0.1:8020          # a second quick tunnel; Guild's stays as is

The checkpoint itself runs with PUBLIC=0 on the demo laptop, so its mutating endpoints (replay,
reset, hold, prove, approve, Guild run, policy copilot) are open locally. This proxy is the only
thing the public tunnel reaches, and it forwards reads only:

* ``GET``/``HEAD`` pass through to the checkpoint (console UI, /status, /incidents, /evidence,
  /fleet/*). Everything else is a 403, so a visitor watches the live console but cannot replay,
  reset, approve, or spend model / Guild credits.
* ``/stream``: Cloudflare quick tunnels do not deliver Server-Sent Events incrementally (the
  response is held until it ends), so the public view answers each /stream request with one
  complete response: ``retry: 2000`` plus the checkpoint's current ``snapshot`` event (full state:
  status, incidents, recent events, alerts, hold, policy, eval), then closes. The browser's
  EventSource reconnects by itself, so the console refreshes about every 2 s. One upstream snapshot
  is shared by all viewers for ``SNAPSHOT_TTL_S``.
* ``/guild/*`` is refused even for GET (each read-back spawns the Guild CLI), as are /docs,
  /redoc and /openapi.json.
* Request headers are not forwarded except Accept/Cache-Control/Last-Event-ID/Range, so no
  ``X-Tripwire-Token`` (or anything else) can be smuggled through.
"""

from __future__ import annotations

import asyncio
import os
import time

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

UPSTREAM = os.environ.get("TRIPWIRE_CHECKPOINT_URL", "http://127.0.0.1:8000").rstrip("/")
SNAPSHOT_TTL_S = 1.0
RETRY_MS = 2000
BLOCKED_PREFIXES = ("/guild/", "/docs", "/redoc", "/openapi.json")
FORWARD_HEADERS = ("accept", "cache-control", "last-event-id", "range")
DROP_RESPONSE_HEADERS = {"content-length", "transfer-encoding", "connection"}  # raw bytes keep their encoding
READ_ONLY = {"detail": "read-only public view: replays, resets and approvals run on the demo laptop"}

client = httpx.AsyncClient(base_url=UPSTREAM, timeout=httpx.Timeout(15.0))
_snap: dict[str, object] = {"at": 0.0, "body": b""}
_snap_lock = asyncio.Lock()


async def snapshot_event() -> bytes:
    """The first event of the checkpoint's /stream (always the full-state snapshot), cached briefly."""
    async with _snap_lock:
        if time.monotonic() - float(_snap["at"]) < SNAPSHOT_TTL_S and _snap["body"]:
            return bytes(_snap["body"])  # type: ignore[arg-type]
        buf = b""
        async with client.stream("GET", "/stream", headers={"accept": "text/event-stream"}) as r:
            r.raise_for_status()
            async for chunk in r.aiter_raw():
                buf += chunk
                norm = buf.replace(b"\r\n", b"\n")
                for block in norm.split(b"\n\n")[:-1]:
                    if b"event: snapshot" in block:
                        _snap.update(at=time.monotonic(), body=block + b"\n\n")
                        return block + b"\n\n"
                if len(buf) > 8_000_000:
                    break
        raise httpx.HTTPError("no snapshot from the checkpoint")


async def proxy(request: Request) -> Response:
    path = request.url.path
    if request.method not in ("GET", "HEAD"):
        return JSONResponse(READ_ONLY, status_code=403)
    if path.startswith(BLOCKED_PREFIXES):
        return JSONResponse({"detail": "not available in the public view"}, status_code=403)
    if path == "/stream":
        try:
            body = await snapshot_event()
        except httpx.HTTPError:
            return JSONResponse({"detail": "the live console is offline"}, status_code=502)
        return Response(
            f"retry: {RETRY_MS}\n\n".encode() + body,
            media_type="text/event-stream",
            headers={"cache-control": "no-store"},
        )
    headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_HEADERS}
    upstream = client.build_request(request.method, path, params=request.query_params, headers=headers)
    try:
        r = await client.send(upstream, stream=True)
    except httpx.HTTPError:
        return JSONResponse({"detail": "the live console is offline"}, status_code=502)
    out_headers = {k: v for k, v in r.headers.items() if k.lower() not in DROP_RESPONSE_HEADERS}
    return StreamingResponse(
        r.aiter_raw(), status_code=r.status_code, headers=out_headers, background=BackgroundTask(r.aclose)
    )


app = Starlette(
    routes=[Route("/{path:path}", proxy, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])]
)

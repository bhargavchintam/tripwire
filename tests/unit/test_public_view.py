"""tripwire/public_view.py: reads pass through, every write is refused, /stream is one full snapshot."""

from __future__ import annotations

import json

import httpx
import pytest

from tripwire import public_view

SNAPSHOT = b'id: 7\r\nevent: snapshot\r\ndata: {"seq":7,"type":"snapshot","data":{"status":{}}}\r\n\r\n'


class Upstream:
    """Stands in for the checkpoint; records every request the proxy forwards."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path == "/stream":
            body = b": ping\r\n\r\n" + SNAPSHOT + b"id: 8\r\nevent: tool_event\r\ndata: {}\r\n\r\n"
            return streamed(body, "text/event-stream")
        body = json.dumps({"path": request.url.path, "query": str(request.url.query, "ascii")}).encode()
        return streamed(body, "application/json")


def streamed(body: bytes, content_type: str) -> httpx.Response:
    """A response whose body is streamed like the real checkpoint's (bytes content would be pre-read)."""
    return httpx.Response(200, headers={"content-type": content_type}, stream=httpx.ByteStream(body))


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> Upstream:
    up = Upstream()
    monkeypatch.setattr(
        public_view, "client", httpx.AsyncClient(transport=httpx.MockTransport(up), base_url="http://cp.test")
    )
    monkeypatch.setattr(public_view, "_snap", {"at": 0.0, "body": b""})
    return up


@pytest.fixture
async def view() -> httpx.AsyncClient:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=public_view.app), base_url="http://view.test"
    ) as c:
        yield c


async def test_reads_pass_through_with_query(upstream: Upstream, view: httpx.AsyncClient) -> None:
    r = await view.get("/fleet/heatmap", params={"hours": "72"})
    assert r.status_code == 200
    assert r.json() == {"path": "/fleet/heatmap", "query": "hours=72"}


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "path", ["/demo/reset", "/demo/replay", "/config/hold", "/guild/run", "/policy/copilot", "/tool"]
)
async def test_writes_are_refused_and_never_forwarded(
    upstream: Upstream, view: httpx.AsyncClient, method: str, path: str
) -> None:
    r = await view.request(method, path, json={"enabled": False})
    assert r.status_code == 403
    assert upstream.calls == []


@pytest.mark.parametrize("path", ["/guild/session/abcdef12/decision", "/docs", "/redoc", "/openapi.json"])
async def test_blocked_reads(upstream: Upstream, view: httpx.AsyncClient, path: str) -> None:
    assert (await view.get(path)).status_code == 403
    assert upstream.calls == []


async def test_token_header_is_not_forwarded(upstream: Upstream, view: httpx.AsyncClient) -> None:
    await view.get(
        "/status", headers={"X-Tripwire-Token": "guess", "Cookie": "a=b", "Accept": "application/json"}
    )
    sent = upstream.calls[-1].headers
    assert "x-tripwire-token" not in sent and "cookie" not in sent
    assert sent["accept"] == "application/json"


async def test_stream_is_one_complete_snapshot_with_retry(
    upstream: Upstream, view: httpx.AsyncClient
) -> None:
    r = await view.get("/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    body = r.content.replace(b"\r\n", b"\n")
    assert body.startswith(b"retry: 2000\n\n")
    assert b"event: snapshot" in body and b"tool_event" not in body  # only the full-state event, then close
    await view.get("/stream")  # served from the shared cache within SNAPSHOT_TTL_S
    assert sum(c.url.path == "/stream" for c in upstream.calls) == 1

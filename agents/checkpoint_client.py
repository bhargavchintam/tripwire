"""Async HTTP client for the Tripwire checkpoint (master §4, §7a/§7b).

This is the ONLY way the agents, replay and bots talk to the checkpoint. It always
sends the X-Tripwire-Token header (harmless when PUBLIC=0), has a timeout on every
request, and turns failures into two typed errors so callers can exit cleanly:

    CheckpointDown   -- connection refused / timeout (the server is not there)
    CheckpointError  -- the server answered with an HTTP error (401 bad token, 404,
                        422 bad body, 5xx); never a pydantic traceback
    Conflict409      -- POST /block answered 409 (stale alert / duplicate verdict)

    async with CheckpointClient() as cp:
        res = await cp.tool(ToolCall(agent_id="deploy-bot", action="read_file", target="/app/.env"))

Tests build one over an in-process app:

    cp = CheckpointClient.from_transport(httpx.ASGITransport(app=app), "http://test", "change-me")
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

import httpx

from tripwire.config import get_settings
from tripwire.contracts import AlertPayload, Heartbeat, StatusResponse, ToolCall, ToolResult


class CheckpointDown(RuntimeError):
    """The checkpoint could not be reached (connection refused / timeout)."""


class CheckpointError(RuntimeError):
    """The checkpoint answered with an HTTP error status (401/404/422/5xx)."""

    def __init__(self, method: str, path: str, status: int, detail: str) -> None:
        super().__init__(f"{method} {path} -> HTTP {status}: {detail}")
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail


class Conflict409(RuntimeError):
    """POST /block returned 409 (stale alert or a verdict already recorded)."""

    def __init__(self, detail: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.payload = payload or {}


class CheckpointClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 5.0,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        if base_url is None or token is None:
            s = get_settings()
            base_url = base_url or s.checkpoint_url
            token = token if token is not None else s.tripwire_token
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-Tripwire-Token": token},
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_transport(
        cls,
        transport: httpx.AsyncBaseTransport,
        base_url: str = "http://test",
        token: str = "change-me",
    ) -> CheckpointClient:
        """Build a client over an in-process ASGI transport (for tests)."""
        return cls(base_url=base_url, token=token, transport=transport)

    # ---- lifecycle --------------------------------------------------------------
    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> CheckpointClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ---- transport --------------------------------------------------------------
    async def _request(self, method: str, path: str, *, allow: Iterable[int] = (), **kw: Any) -> httpx.Response:
        """One request with the timeout; CheckpointDown on transport failure, CheckpointError on
        any HTTP status >= 400 that is not in `allow`. Never logs the body (it may be a payload)."""
        try:
            r = await self._client.request(method, path, **kw)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise CheckpointDown(f"{method} {path}: {type(exc).__name__}: {exc}") from exc
        if r.status_code >= 400 and r.status_code not in set(allow):
            raise CheckpointError(method, path, r.status_code, _detail(r))
        return r

    # ---- endpoints --------------------------------------------------------------
    async def tool(self, call: ToolCall) -> ToolResult:
        r = await self._request("POST", "/tool", json=call.model_dump())
        return ToolResult.model_validate(r.json())

    async def status(self) -> StatusResponse:
        r = await self._request("GET", "/status")
        return StatusResponse.model_validate(r.json())

    async def health(self) -> dict[str, Any]:
        r = await self._request("GET", "/health")
        return r.json()

    async def restore(self, agent_id: str) -> dict[str, Any]:
        r = await self._request("POST", f"/restore/{agent_id}")
        return r.json()

    async def block(self, agent_id: str, payload: AlertPayload) -> dict[str, Any]:
        body = payload.model_dump()
        body["agent_id"] = agent_id  # path is authoritative; stamp the body too
        r = await self._request("POST", f"/block/{agent_id}", json=body, allow=(409,))
        if r.status_code == 409:
            data = _safe_json(r)
            raise Conflict409(str(data.get("detail", "conflict")), data)
        return r.json()

    async def alerts(self, payload: AlertPayload, agent_id: Optional[str] = None) -> dict[str, Any]:
        """POST /alerts. agent_id is REQUIRED by the checkpoint (body field or ?agent_id=);
        pass it here or set it on the payload. 409 (stale/duplicate) raises Conflict409."""
        agent = agent_id or payload.agent_id
        if not agent:
            raise ValueError("POST /alerts needs agent_id (AlertPayload.agent_id or the agent_id argument)")
        body = payload.model_dump()
        body["agent_id"] = agent
        r = await self._request("POST", "/alerts", json=body, allow=(409,))
        if r.status_code == 409:
            data = _safe_json(r)
            raise Conflict409(str(data.get("detail", "conflict")), data)
        return r.json()

    async def heartbeat(self, hb: Heartbeat) -> dict[str, Any]:
        r = await self._request("POST", "/heartbeat", json=hb.model_dump())
        return r.json()

    async def start_replay(self, scenario: str, agent_id: Optional[str] = None) -> dict[str, Any]:
        body: dict[str, Any] = {"scenario": scenario}
        if agent_id:
            body["agent_id"] = agent_id
        r = await self._request("POST", "/demo/replay", json=body)
        return r.json()

    async def replay_status(self, run_id: str) -> dict[str, Any]:
        r = await self._request("GET", f"/demo/replay/{run_id}")
        return r.json()


def _safe_json(r: httpx.Response) -> dict[str, Any]:
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _detail(r: httpx.Response) -> str:
    """Short error detail from a FastAPI error body ({"detail": ...}); never the whole body."""
    data = _safe_json(r)
    detail = data.get("detail") if data else None
    text = str(detail) if detail is not None else (r.reason_phrase or "")
    return text[:200]

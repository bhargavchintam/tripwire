"""Tripwire checkpoint HTTP API (master §4 + §7a).

    uv run uvicorn checkpoint.app:app --port 8000

create_app(writer=None) builds an isolated app (tests pass InMemoryWriter and a
temp state path); the module-level ``app`` is the production instance.
"""

from __future__ import annotations

import asyncio
import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel
from sse_starlette import EventSourceResponse, ServerSentEvent

from checkpoint import hold
from checkpoint.auth import require_token
from checkpoint.bus import Bus
from checkpoint.chread import CHReader, CHUnavailable
from checkpoint.service import FIXTURES_DIR, Checkpoint, Conflict, NotFound
from checkpoint.state import DEFAULT_STATE_PATH, State
from checkpoint.writer import ClickHouseWriter, Writer
from tripwire.config import Settings, get_settings
from tripwire.contracts import (
    AlertPayload,
    EvidenceBundle,
    Heartbeat,
    Incident,
    Outbreak,
    Policy,
    ReportPayload,
    StatusResponse,
    ToolCall,
    ToolResult,
)

ROOT = Path(__file__).resolve().parent.parent
WEB_DIST = ROOT / "web" / "dist"
SSE_PING_S = 15


class HoldConfig(BaseModel):
    enabled: bool


class ReplayRequest(BaseModel):
    scenario: str = "secret_theft"
    agent_id: Optional[str] = None


class AlertIn(AlertPayload):
    """AlertPayload has no agent_id; POST /alerts accepts it in the body or as ?agent_id=."""

    agent_id: str = ""


def create_app(
    writer: Writer | None = None,
    *,
    settings: Settings | None = None,
    state_path: Path | str | None = DEFAULT_STATE_PATH,
    ch_enabled: bool = True,
    events_table: str = "events",
    fixtures_dir: Path = FIXTURES_DIR,
    honeytokens: list[str] | None = None,
    web_dist: Path | None = WEB_DIST,
) -> FastAPI:
    settings = settings or get_settings()
    writer = writer if writer is not None else ClickHouseWriter(table=events_table)
    state = State(state_path)
    svc = Checkpoint(
        writer=writer,
        state=state,
        bus=Bus(),
        ch=CHReader(enabled=ch_enabled, table=events_table),
        honeytokens=settings.honeytokens if honeytokens is None else honeytokens,
        fixtures_dir=fixtures_dir,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state.load()
        if ch_enabled:
            try:
                await asyncio.wait_for(svc.reconcile_chain(), 6.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"startup: chain reconcile skipped ({exc!r})")
        hold.get_classify()
        await writer.start()
        save_task = asyncio.create_task(state.save_loop(), name="tripwire-state-save")
        logger.info(
            f"checkpoint up: writer={writer.stats().get('kind')} classify={hold.classify_source} "
            f"public={settings.public} honeytokens={len(svc.honeytokens)}"
        )
        try:
            yield
        finally:
            save_task.cancel()
            await svc.shutdown()
            await writer.stop()
            try:
                state.save()
            except OSError as exc:
                logger.error(f"shutdown: state save failed: {exc!r}")

    app = FastAPI(title="Tripwire checkpoint", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.svc = svc
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": exc.detail, **exc.extra})

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    auth = [Depends(require_token)]

    # ---------------------------------------------------------------- core
    @app.post("/tool", response_model=ToolResult, dependencies=auth)
    async def tool(call: ToolCall) -> ToolResult:
        return await svc.handle_tool(call)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        w = writer.stats()
        return {
            "ok": True,
            "clickhouse": await svc.ch.ping(),
            "writer_queue": w.get("queue", 0),
            "writer_dropped": w.get("dropped", 0),
            "writer_last_error": w.get("last_error"),
            "writer": w.get("kind"),
            "classify": hold.classify_source,
            "state_error": state.load_error,
        }

    @app.get("/status", response_model=StatusResponse)
    async def status() -> StatusResponse:
        return svc.status()

    @app.post("/block/{agent_id}", dependencies=auth)
    async def block(agent_id: str, p: AlertPayload) -> dict[str, Any]:
        # The path is authoritative; stamp it so stored alerts never carry "" (AlertPayload.agent_id).
        return await svc.block(agent_id, p.model_copy(update={"agent_id": agent_id}))

    @app.post("/alerts", dependencies=auth)
    async def post_alert(p: AlertIn, agent_id: Optional[str] = Query(default=None)) -> dict[str, Any]:
        agent = agent_id or p.agent_id
        if not agent:
            raise HTTPException(status_code=422, detail="agent_id required (body field or ?agent_id=)")
        payload = AlertPayload.model_validate({**p.model_dump(), "agent_id": agent})
        return await svc.record_alert(agent, payload)

    @app.get("/alerts")
    async def get_alerts() -> list[dict[str, Any]]:
        return list(state.recent_alerts)

    @app.post("/restore/{agent_id}", dependencies=auth)
    async def restore(agent_id: str) -> dict[str, Any]:
        return await svc.restore(agent_id)

    @app.get("/stream")
    async def stream() -> EventSourceResponse:
        async def gen() -> AsyncIterator[ServerSentEvent]:
            q = svc.bus.subscribe()
            try:
                snap = svc.bus.make("snapshot", svc.snapshot())
                yield ServerSentEvent(data=snap.model_dump_json(), event="snapshot", id=str(snap.seq))
                while True:
                    ev = await q.get()
                    yield ServerSentEvent(data=ev.model_dump_json(), event=ev.type, id=str(ev.seq))
            finally:
                svc.bus.unsubscribe(q)

        return EventSourceResponse(gen(), ping=SSE_PING_S)

    @app.post("/heartbeat", dependencies=auth)
    async def heartbeat(hb: Heartbeat) -> dict[str, Any]:
        return svc.heartbeat(hb)

    @app.post("/config/hold", dependencies=auth)
    async def config_hold(cfg: HoldConfig) -> dict[str, Any]:
        return svc.set_hold(cfg.enabled)

    @app.get("/policy", response_model=Policy)
    async def get_policy() -> Policy:
        return state.policy

    @app.put("/policy", response_model=Policy, dependencies=auth)
    async def put_policy(p: Policy) -> Policy:
        return svc.put_policy(p)

    # ---------------------------------------------------------------- incidents
    @app.get("/incidents", response_model=list[Incident])
    async def list_incidents() -> list[Incident]:
        return svc.incidents()

    @app.get("/incidents/{incident_id}", response_model=Incident)
    async def get_incident(incident_id: str) -> Incident:
        return svc.get_incident(incident_id)

    @app.put("/incidents/{incident_id}/report", response_model=Incident, dependencies=auth)
    async def put_report(incident_id: str, rp: ReportPayload) -> Incident:
        return svc.put_report(incident_id, rp)

    @app.post("/incidents/{incident_id}/outbreak", dependencies=auth)
    async def post_outbreak(incident_id: str, o: Outbreak) -> dict[str, Any]:
        return svc.outbreak(incident_id, o)

    # ---------------------------------------------------------------- demo
    @app.post("/demo/replay", dependencies=auth)
    async def demo_replay(req: ReplayRequest) -> dict[str, Any]:
        try:
            return await svc.start_replay(req.scenario, req.agent_id or None)
        except ValueError as exc:  # bad name or malformed fixture
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/demo/replay/{run_id}")
    async def demo_replay_status(run_id: str) -> dict[str, Any]:
        return svc.get_replay(run_id)

    @app.post("/demo/reset", dependencies=auth)
    async def demo_reset() -> dict[str, Any]:
        return await svc.reset()

    # ---------------------------------------------------------------- evidence / audit
    @app.get("/evidence", response_model=EvidenceBundle)
    async def evidence() -> EvidenceBundle:
        return await svc.evidence()

    @app.get("/audit/verify/{agent_id}")
    async def audit_verify(agent_id: str) -> dict[str, Any]:
        try:
            return await svc.audit_verify(agent_id)
        except CHUnavailable as exc:
            raise HTTPException(status_code=503, detail=f"clickhouse unavailable: {exc}") from exc

    # ---------------------------------------------------------------- phase-2 stubs
    def _phase2() -> JSONResponse:
        return JSONResponse(status_code=501, content={"detail": "phase 2"})

    @app.post("/policy/backtest", dependencies=auth)
    async def policy_backtest() -> JSONResponse:
        return _phase2()

    @app.post("/policy/copilot", dependencies=auth)
    async def policy_copilot() -> JSONResponse:
        # Master §7a: 503 until Sripadha's ai/copilot.py exists; then 501 until it is wired here.
        try:
            present = importlib.util.find_spec("ai.copilot") is not None
        except (ImportError, ValueError):
            present = False
        if not present:
            return JSONResponse(status_code=503, content={"detail": "ai/copilot.py not available yet"})
        return _phase2()

    @app.post("/guardrail/{incident_id}/prove", dependencies=auth)
    async def guardrail_prove(incident_id: str) -> JSONResponse:
        return _phase2()

    @app.post("/guardrail/{incident_id}/approve", dependencies=auth)
    async def guardrail_approve(incident_id: str) -> JSONResponse:
        return _phase2()

    @app.post("/guild/run", dependencies=auth)
    async def guild_run() -> JSONResponse:
        return _phase2()

    # ---------------------------------------------------------------- UI (mounted LAST)
    if web_dist is not None and Path(web_dist).is_dir():
        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")

    return app


app = create_app()

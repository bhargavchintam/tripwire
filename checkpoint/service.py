"""Checkpoint core logic, independent of HTTP (routes in app.py; replay calls it directly)."""

from __future__ import annotations

import asyncio
import math
import re
import statistics
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from checkpoint import hold, honeytoken
from checkpoint.audit import chain_hash, verify_chain
from checkpoint.bus import Bus
from checkpoint.chread import CHReader, hold_history
from checkpoint.policy import HTTP_ACTIONS, host_of
from checkpoint.policy import is_external as _is_external
from checkpoint.state import INCIDENT_STEPS_MAX, State, now_ms
from checkpoint.writer import Writer
from tripwire.contracts import (
    RULE_HONEYTOKEN,
    RULE_TAGS,
    AlertPayload,
    ClassifyFn,
    EvidenceBundle,
    Heartbeat,
    Incident,
    IncidentStep,
    Outbreak,
    Policy,
    Reason,
    ReportPayload,
    Result,
    Scenario,
    StatusResponse,
    ToolCall,
    ToolResult,
    Verdict,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
__all__ = ["Checkpoint", "Conflict", "NotFound", "host_of", "HTTP_ACTIONS", "FIXTURES_DIR"]
_DEFAULT = object()
REPLAY_BLOCK_WAIT_S = 10.0
REPLAYS_MAX = 50
_SCENARIO_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


class Conflict(Exception):
    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


class NotFound(Exception):
    pass


def _median(xs: Any) -> float | None:
    xs = list(xs)
    return float(statistics.median(xs)) if xs else None


def _pct(xs: Any, p: float) -> float | None:
    xs = sorted(xs)
    if not xs:
        return None
    k = max(0, math.ceil(p * len(xs)) - 1)  # nearest-rank
    return float(xs[k])


def _num(v: Any, typ: type) -> Any:
    if v is None:
        return None
    try:
        return typ(v)
    except (TypeError, ValueError):
        return None


class Checkpoint:
    def __init__(
        self,
        writer: Writer,
        state: State,
        bus: Bus,
        ch: CHReader,
        honeytokens: list[str],
        fixtures_dir: Path = FIXTURES_DIR,
        history_lookup: Any = _DEFAULT,
        classify: ClassifyFn | None = None,
    ) -> None:
        self.writer = writer
        self.state = state
        self.bus = bus
        self.ch = ch
        self.honeytokens = [t for t in honeytokens if t]
        self.fixtures_dir = fixtures_dir
        self.replays: dict[str, dict[str, Any]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        # Hold-mode seams (tests inject fakes; None classify = hold.get_classify() at call time,
        # so ai.quick_check is picked up automatically when it lands).
        if history_lookup is _DEFAULT:
            history_lookup = self._ch_history if ch.enabled else None
        self.history_lookup: hold.HistoryLookup | None = history_lookup
        self.classify_override: ClassifyFn | None = classify
        self.hold_cache = hold.HistoryCache()

    async def _ch_history(self, agent_id: str, host: str, before_ms: int) -> tuple[bool, dict[str, Any] | None]:
        return await hold_history(self.ch, agent_id, host, before_ms)

    def classify_fn(self) -> ClassifyFn:
        return self.classify_override or hold.get_classify()

    # ------------------------------------------------------------------ helpers
    def emit(self, type_: Any, data: dict[str, Any]) -> None:
        self.bus.publish(type_, data)

    def agent_state(self, agent_id: str) -> dict[str, Any]:
        st = self.state
        open_inc = st.open_incidents(agent_id)
        return {
            "agent_id": agent_id,
            "mode": st.modes.get(agent_id, "normal"),
            "blocked": agent_id in st.blocked,
            "incident_id": open_inc[-1].id if open_inc else None,
            "watermark": st.watermarks.get(agent_id, 0),
        }

    @staticmethod
    def is_external(action: str, target: str, policy: Policy) -> int:
        return _is_external(action, target, policy)

    def honeytoken_hit(self, call: ToolCall) -> bool:
        if honeytoken.scan(call.payload, self.honeytokens):
            return True
        # Exfil through a URL (query string) is the same leak; scan http targets too.
        return call.action in HTTP_ACTIONS and honeytoken.scan(call.target, self.honeytokens)

    def _steps_from_ring(self, agent_id: str, now: int) -> list[IncidentStep]:
        return [
            IncidentStep(
                ts_ms=s["ts_ms"],
                action=s["action"],
                target=s["target"],
                result=s["result"],
                reason=s.get("reason", ""),
            )
            for s in self.state.ring_window(agent_id, now)
        ]

    def _open_or_update_incident(
        self, agent_id: str, rule: str, verdict: Verdict | None, last_step_ts: int, now: int
    ) -> tuple[Incident, bool]:
        st = self.state
        steps = self._steps_from_ring(agent_id, now)
        inc = st.find_open_incident(agent_id, rule)
        if inc is None:
            inc = Incident(
                id=f"inc-{uuid.uuid4().hex[:10]}",
                agent_id=agent_id,
                rule=rule,
                opened_ms=now,
                last_step_ts_ms=last_step_ts,
                steps=steps,
                verdict=verdict,
                tags=list(RULE_TAGS.get(rule, [])),
            )
            st.add_incident(inc)
            return inc, True
        seen = {s.ts_ms for s in inc.steps}
        inc.steps = sorted(inc.steps + [s for s in steps if s.ts_ms not in seen], key=lambda s: s.ts_ms)[
            -INCIDENT_STEPS_MAX:
        ]
        inc.last_step_ts_ms = max(inc.last_step_ts_ms, last_step_ts)
        if verdict is not None:
            inc.verdict = verdict
        if not inc.tags:
            inc.tags = list(RULE_TAGS.get(rule, []))
        return inc, False

    def _quarantine(self, agent_id: str) -> None:
        self.state.blocked.add(agent_id)
        self.state.modes[agent_id] = "quarantined"

    # ------------------------------------------------------------------ POST /tool
    async def handle_tool(self, call: ToolCall) -> ToolResult:
        st = self.state
        agent = call.agent_id
        async with st.lock_for(agent):
            prev_mode = st.modes.get(agent)
            ts = st.next_ts(agent)
            policy = st.policy
            is_ext = self.is_external(call.action, call.target, policy)
            honey = self.honeytoken_hit(call)
            result: Result = "ok"
            reason: Reason = ""
            decision: hold.HoldDecision | None = None

            if agent in st.blocked:
                result, reason = "denied", "blocked"
            elif honey:
                result, reason = "denied", "honeytoken"
            else:
                # 1-2: policy (fixed-deny actions, denylisted destinations) always applies.
                decision = hold.policy_check(call, policy, is_ext)
                # 3: hold mode for external posts outside the agent's allowlist.
                if decision is None and st.hold_on(agent) and hold.hold_applies(call, policy, is_ext):
                    decision = await hold.decide(
                        call,
                        st.ring_window(agent, ts),
                        ts=ts,
                        mode=st.modes.get(agent, "normal"),
                        is_ext=is_ext,
                        lookup=self.history_lookup,
                        classify=self.classify_fn(),
                        cache=self.hold_cache,
                    )
                    if decision.timed and decision.ms is not None:
                        st.hold_samples.append(decision.ms)
                    if decision.receipt and not decision.receipt.get("cached"):
                        st.hold_receipts.append({**decision.receipt, "agent_id": agent, "ts_ms": ts})
                if decision is not None and not decision.allow:
                    result, reason = "denied", (decision.reason or "hold_rule")

            prev_hash = st.last_hash.get(agent, "")
            h = chain_hash(prev_hash, ts, agent, call.action, call.target, result, reason)
            st.last_hash[agent] = h
            nbytes = call.bytes or len(call.payload.encode("utf-8"))
            row: dict[str, Any] = {
                "ts_ms": ts,
                "agent_id": agent,
                "action": call.action,
                "target": call.target,
                "bytes": nbytes,
                "is_external": is_ext,
                "result": result,
                "reason": reason,
                "honeytoken_hit": 1 if honey else 0,
                "tainted_by": call.tainted_by,
                "code_ref": call.code_ref,
                "session_id": call.session_id,
                "synthetic": 0,
                "prev_hash": prev_hash,
                "hash": h,
            }
            self.writer.enqueue(row)  # never the payload
            st.push_ring(
                agent,
                {
                    "ts_ms": ts,
                    "action": call.action,
                    "target": call.target,
                    "result": result,
                    "reason": reason,
                    "is_external": is_ext,
                    "bytes": nbytes,
                    "tainted_by": call.tainted_by,
                    "honeytoken_hit": 1 if honey else 0,
                },
            )

            incident: Incident | None = None
            incident_changed = False
            now = now_ms()
            if reason == "honeytoken":
                verdict = Verdict(
                    verdict="malicious",
                    confidence=1.0,
                    reason="decoy credential (honeytoken) found in an outbound tool call; no model call",
                    decision_source="honeytoken",
                )
                incident, _ = self._open_or_update_incident(agent, RULE_HONEYTOKEN, verdict, ts, now)
                incident_changed = True
            elif decision is not None and not decision.allow and decision.quarantine:
                verdict = decision.verdict or Verdict(
                    verdict="malicious",
                    confidence=1.0,
                    reason=decision.note or "denied by hold policy",
                    decision_source="policy",
                )
                incident, _ = self._open_or_update_incident(agent, decision.rule, verdict, ts, now)
                incident_changed = True
            elif reason == "blocked":
                open_inc = st.open_incidents(agent)
                if open_inc:
                    incident = open_inc[-1]
                    incident.steps = (
                        incident.steps
                        + [IncidentStep(ts_ms=ts, action=call.action, target=call.target, result=result, reason=reason)]
                    )[-INCIDENT_STEPS_MAX:]

            if incident_changed and incident is not None:
                self._quarantine(agent)
                if incident.contained_ms is None:
                    incident.contained_ms = now_ms()
                    sample = float(max(0, incident.contained_ms - ts))
                    # H: honeytoken trips are reported separately from detector/hold containment.
                    (st.honey_ttc_samples if reason == "honeytoken" else st.ttc_samples).append(sample)

            hold_alert: dict[str, Any] | None = None
            if decision is not None and decision.alert and decision.verdict is not None:
                hold_alert = self._hold_alert(agent, decision, ts, result)

            event = {k: v for k, v in row.items() if k not in ("prev_hash", "hash")}
            event["incident_id"] = incident.id if incident else None
            st.recent_events.append(event)
            st.mark_dirty()
            self.emit("tool_event", event)
            if incident_changed and incident is not None:
                self.emit("incident", incident.model_dump())
                v = incident.verdict
                if decision is not None and v is not None and v.decision_source == "quorum":
                    self.emit("quorum", self._quorum_data(agent, incident.id, incident.rule, v))
            if hold_alert is not None:
                self.emit("alert", hold_alert)
            if st.modes.get(agent) != prev_mode:
                self.emit("agent_state", self.agent_state(agent))

        return ToolResult(
            agent_id=agent,
            result=result,
            reason=reason,
            incident_id=incident.id if incident else None,
            ts_ms=ts,
        )

    def _hold_alert(self, agent: str, d: hold.HoldDecision, ts: int, result: str) -> dict[str, Any]:
        """Non-blocking alert for a hold/policy decision (flagged -> benign, held for review, ...)."""
        v = d.verdict
        assert v is not None
        reason = v.reason if d.note in ("", "flagged -> benign") else f"{d.note}: {v.reason}"
        p = AlertPayload(
            agent_id=agent,
            rule=d.rule,
            verdict=v.verdict,
            confidence=v.confidence,
            reason=reason,
            decision_source=v.decision_source,
            detected_at_ms=now_ms(),
            last_step_ts_ms=ts,
            model_ids=list(v.model_ids),
        )
        key = f"{agent}|{d.rule}|{ts}"
        self.state.add_verdict_key(key)
        rec = self._alert_record(agent, p, key, blocking=False, incident_id=None)
        rec.update({"source": "hold", "result": result, "hold_decision_ms": d.ms, "why": list(d.why)})
        return rec

    @staticmethod
    def _quorum_data(agent: str, incident_id: str, rule: str, v: Verdict) -> dict[str, Any]:
        return {
            "agent_id": agent,
            "incident_id": incident_id,
            "rule": rule,
            "verdict": v.verdict,
            "confidence": v.confidence,
            "reason": v.reason,
            "model_ids": list(v.model_ids),
        }

    # ------------------------------------------------------------------ alerts / block
    def _check_and_record_key(self, agent_id: str, p: AlertPayload) -> str:
        st = self.state
        key = f"{agent_id}|{p.rule}|{p.last_step_ts_ms}"
        wm = st.watermarks.get(agent_id, 0)
        if p.last_step_ts_ms <= wm:
            raise Conflict(
                f"stale alert: last_step_ts_ms {p.last_step_ts_ms} <= watermark {wm}",
                reason="stale",
                key=key,
                watermark=wm,
            )
        if key in st.verdict_keys:
            raise Conflict("duplicate alert: key already has a verdict", reason="duplicate", key=key, watermark=wm)
        st.add_verdict_key(key)
        return key

    def _alert_record(
        self, agent_id: str, p: AlertPayload, key: str, blocking: bool, incident_id: str | None
    ) -> dict[str, Any]:
        rec = {
            "agent_id": agent_id,
            **p.model_dump(),
            "key": key,
            "blocking": blocking,
            "incident_id": incident_id,
            "received_ms": now_ms(),
        }
        self.state.recent_alerts.appendleft(rec)
        return rec

    async def block(self, agent_id: str, p: AlertPayload) -> dict[str, Any]:
        st = self.state
        async with st.lock_for(agent_id):
            key = self._check_and_record_key(agent_id, p)
            now = now_ms()
            verdict = Verdict(
                verdict=p.verdict,
                confidence=min(1.0, max(0.0, float(p.confidence))),
                reason=p.reason,
                decision_source=p.decision_source,
                model_ids=list(p.model_ids),
                latency_ms=float(p.latency_ms),
                tokens_in=int(p.tokens_in),
                tokens_out=int(p.tokens_out),
            )
            inc, _ = self._open_or_update_incident(agent_id, p.rule, verdict, p.last_step_ts_ms, now)
            self._quarantine(agent_id)
            if inc.contained_ms is None:
                inc.contained_ms = now
            ttd = p.detected_at_ms - p.last_step_ts_ms
            ttc = now - p.detected_at_ms
            if ttd >= 0:
                st.ttd_samples.append(float(ttd))
            if ttc >= 0:
                st.ttc_samples.append(float(ttc))
            alert = self._alert_record(agent_id, p, key, blocking=True, incident_id=inc.id)
            st.mark_dirty()
            self.emit("alert", alert)
            self.emit("incident", inc.model_dump())
            self.emit("agent_state", self.agent_state(agent_id))
            if p.decision_source == "quorum":
                self.emit("quorum", self._quorum_data(agent_id, inc.id, p.rule, verdict))
        return {"status": "blocked", "incident_id": inc.id}

    async def record_alert(self, agent_id: str, p: AlertPayload) -> dict[str, Any]:
        st = self.state
        async with st.lock_for(agent_id):
            key = self._check_and_record_key(agent_id, p)
            alert = self._alert_record(agent_id, p, key, blocking=False, incident_id=None)
            st.mark_dirty()
            self.emit("alert", alert)
        return {"status": "recorded", "key": key}

    # ------------------------------------------------------------------ restore / reset
    def _restore_locked(self, agent_id: str, now: int) -> tuple[int, list[Incident]]:
        st = self.state
        st.blocked.discard(agent_id)
        st.modes[agent_id] = "normal"
        closed = []
        for inc in st.open_incidents(agent_id):
            inc.closed_ms = now
            closed.append(inc)
        wm = max(st.last_ts.get(agent_id, 0), now, st.watermarks.get(agent_id, 0))
        st.watermarks[agent_id] = wm
        st.last_ts[agent_id] = wm  # next event ts is strictly after the watermark
        st.rings.pop(agent_id, None)
        return wm, closed

    async def restore(self, agent_id: str) -> dict[str, Any]:
        st = self.state
        async with st.lock_for(agent_id):
            wm, closed = self._restore_locked(agent_id, now_ms())
            st.mark_dirty()
            for inc in closed:
                self.emit("incident", inc.model_dump())
            self.emit("agent_state", self.agent_state(agent_id))
        return {
            "status": "restored",
            "agent_id": agent_id,
            "watermark": wm,
            "closed_incidents": [i.id for i in closed],
        }

    async def reset(self, full: bool = False) -> dict[str, Any]:
        """Demo reset. full=True also restores the default Policy and clears guardrail proofs,
        so every rehearsal / recording take starts clean (an approved guardrail otherwise keeps
        the attacker host denylisted and the next replay is stopped by policy, not detection)."""
        st = self.state
        if full:
            from tripwire.contracts import Policy

            st.policy = Policy(version=st.policy.version + 1)
            st.proofs.clear()
            self.emit("metrics", {"source": "checkpoint", "kind": "policy", "policy_version": st.policy.version})
        agents = sorted(set(st.last_ts) | set(st.modes) | st.blocked | set(st.watermarks))
        for a in agents:
            async with st.lock_for(a):
                self._restore_locked(a, now_ms())
        now = now_ms()
        for inc in st.incidents.values():
            if inc.closed_ms is None:
                inc.closed_ms = now
        st.recent_alerts.clear()
        st.rings.clear()
        st.mark_dirty()
        for a in agents:
            self.emit("agent_state", self.agent_state(a))
        # Connected UIs replace their local lists from a fresh snapshot (same shape as on connect).
        self.emit("snapshot", self.snapshot())
        return {"status": "reset", "agents": agents, "full": full, "policy_version": st.policy.version}

    # ------------------------------------------------------------------ read models
    def status(self) -> StatusResponse:
        st = self.state
        return StatusResponse(
            active=sorted(st.last_ts),
            blocked=sorted(st.blocked),
            open_incidents=st.open_incidents(),
            watermarks=dict(st.watermarks),
            modes=dict(st.modes),
            verdict_keys=list(st.verdict_keys),
            hold_enabled=st.hold_enabled,
            policy_version=st.policy.version,
        )

    def incidents(self) -> list[Incident]:
        return sorted(self.state.incidents.values(), key=lambda i: (i.opened_ms, i.id), reverse=True)

    def get_incident(self, incident_id: str) -> Incident:
        inc = self.state.incidents.get(incident_id)
        if inc is None:
            raise NotFound(f"incident {incident_id} not found")
        return inc

    def snapshot(self) -> dict[str, Any]:
        st = self.state
        return {
            "status": self.status().model_dump(),
            "incidents": [i.model_dump() for i in self.incidents()[:100]],
            "recent_events": list(st.recent_events),  # oldest first
            "alerts": list(st.recent_alerts),  # newest first
            "hold_enabled": st.hold_enabled,
            "policy": st.policy.model_dump(),
        }

    # ------------------------------------------------------------------ misc mutations
    def heartbeat(self, hb: Heartbeat) -> dict[str, Any]:
        st = self.state
        rec = {**hb.model_dump(), "received_ms": now_ms()}
        st.heartbeats[hb.source] = rec
        samples = st.timing(hb.source)
        for v in hb.query_timings_ms.values():
            if isinstance(v, (int, float)) and math.isfinite(v) and v >= 0:
                samples.append(float(v))
        st.mark_dirty()
        self.emit("metrics", rec)
        return {"status": "ok"}

    def set_hold(self, enabled: bool) -> dict[str, Any]:
        self.state.hold_enabled = bool(enabled)
        self.state.mark_dirty()
        self.emit("metrics", {"source": "checkpoint", "kind": "hold", "hold_enabled": self.state.hold_enabled})
        return {"hold_enabled": self.state.hold_enabled}

    def put_policy(self, p: Policy) -> Policy:
        new = p.model_copy(update={"version": self.state.policy.version + 1})
        self.state.policy = new
        self.state.mark_dirty()
        self.emit("metrics", {"source": "checkpoint", "kind": "policy", "policy_version": new.version})
        return new

    def put_report(self, incident_id: str, rp: ReportPayload) -> Incident:
        inc = self.get_incident(incident_id)
        inc.report_md = rp.report_md
        inc.receipts = list(rp.receipts)
        self.state.mark_dirty()
        self.emit(
            "report_ready",
            {"incident_id": inc.id, "agent_id": inc.agent_id, "model_ids": list(rp.model_ids)},
        )
        return inc

    def outbreak(self, incident_id: str, o: Outbreak) -> dict[str, Any]:
        st = self.state
        inc = self.get_incident(incident_id)
        inc.outbreak = o
        heightened: list[str] = []
        for a in o.exposed_agents:
            if a in st.blocked or st.modes.get(a) == "quarantined":
                continue
            if st.modes.get(a) != "heightened":
                st.modes[a] = "heightened"
                heightened.append(a)
        added = [d for d in dict.fromkeys(o.blocked_destinations) if d and d not in st.policy.denylist]
        if added:
            st.policy = st.policy.model_copy(
                update={"denylist": st.policy.denylist + added, "version": st.policy.version + 1}
            )
        st.mark_dirty()
        self.emit(
            "outbreak",
            {
                "incident_id": inc.id,
                **o.model_dump(),
                "heightened": heightened,
                "denylist_added": added,
                "policy_version": st.policy.version,
            },
        )
        for a in heightened:
            self.emit("agent_state", self.agent_state(a))
        self.emit("incident", inc.model_dump())
        return {
            "status": "applied",
            "incident_id": inc.id,
            "heightened": heightened,
            "denylist_added": added,
            "policy_version": st.policy.version,
        }

    # ------------------------------------------------------------------ evidence / audit
    async def evidence(self) -> EvidenceBundle:
        st = self.state
        count, receipt = await self.ch.count_events()
        det = list(st.timing_samples.get("detector", []))
        ev = st.heartbeats.get("eval") or {}
        m = ev.get("metrics") or {}
        receipts: list[dict[str, Any]] = [receipt] if receipt else []
        if st.honey_ttc_samples:
            receipts.append(
                {
                    "kind": "honeytoken_contain",
                    "ms": _median(st.honey_ttc_samples),
                    "n": len(st.honey_ttc_samples),
                    "note": "honeytoken trips (no detector/model in the loop); excluded from time_to_contain_ms",
                }
            )
        if st.hold_receipts:
            receipts.append(dict(st.hold_receipts[-1]))
        return EvidenceBundle(
            events_stored=count,
            query_p50_ms=_pct(det, 0.50),
            query_p95_ms=_pct(det, 0.95),
            time_to_detect_ms=_median(st.ttd_samples),
            time_to_contain_ms=_median(st.ttc_samples),
            hold_decision_ms=_median(st.hold_samples),
            precision=_num(m.get("precision"), float),
            recall=_num(m.get("recall"), float),
            n_cases=_num(m.get("n_cases"), int),
            cost_akashml=_num(m.get("cost_akashml"), float),
            cost_openai=_num(m.get("cost_openai"), float),
            priced_on=_num(m.get("priced_on"), str),
            receipts=receipts,
        )

    async def audit_verify(self, agent_id: str) -> dict[str, Any]:
        rows = await self.ch.agent_rows(agent_id)  # CHUnavailable -> 503 in app
        return {"agent_id": agent_id, **verify_chain(rows)}

    async def reconcile_chain(self) -> None:
        """Adopt ClickHouse's chain heads so a stale/deleted state file never breaks the chain."""
        heads = await self.ch.chain_heads()
        st = self.state
        for agent, (max_ts, head_hash) in heads.items():
            if st.last_hash.get(agent) != head_hash:
                logger.info(f"reconcile: {agent} chain head taken from ClickHouse (ts={max_ts})")
            st.last_hash[agent] = head_hash
            st.last_ts[agent] = max(st.last_ts.get(agent, 0), max_ts)
            st.modes.setdefault(agent, "normal")
        if heads:
            st.mark_dirty()

    # ------------------------------------------------------------------ replay
    def load_scenario(self, name: str) -> Scenario:
        if not _SCENARIO_RE.match(name or ""):
            raise ValueError("scenario must match [A-Za-z0-9_-]{1,64}")
        path = self.fixtures_dir / f"{name}.json"
        if not path.is_file():
            raise NotFound(
                f"fixtures/{name}.json not found — scenario fixtures are Sripadha's (fixtures/), not landed yet"
            )
        return Scenario.model_validate_json(path.read_text())

    async def start_replay(self, scenario: str, agent_id: str | None = None) -> dict[str, Any]:
        sc = self.load_scenario(scenario)
        run_id = uuid.uuid4().hex[:12]
        run: dict[str, Any] = {
            "run_id": run_id,
            "scenario": sc.name,
            "agent_override": agent_id,
            "status": "running",
            "steps": len(sc.steps),
            "outcomes": [],
            "started_ms": now_ms(),
            "finished_ms": None,
            "passed": None,
            "error": None,
        }
        self.replays[run_id] = run
        while len(self.replays) > REPLAYS_MAX:
            self.replays.pop(next(iter(self.replays)))
        task = asyncio.create_task(self._run_replay(run, sc, agent_id), name=f"replay-{run_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {"run_id": run_id, "steps": len(sc.steps)}

    async def _run_replay(self, run: dict[str, Any], sc: Scenario, override: str | None) -> None:
        t0 = time.monotonic()
        try:
            for i, step in enumerate(sc.steps):
                delay = t0 + step.offset_ms / 1000.0 - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                agent = override or step.agent_id
                waited_ms: int | None = None
                if step.expect == "denied_after_block":
                    w0 = time.monotonic()
                    while agent not in self.state.blocked and time.monotonic() - w0 < REPLAY_BLOCK_WAIT_S:
                        await asyncio.sleep(0.05)
                    waited_ms = round((time.monotonic() - w0) * 1000)
                res = await self.handle_tool(
                    ToolCall(
                        agent_id=agent,
                        action=step.action,
                        target=step.target,
                        payload=step.payload,
                        tainted_by=step.tainted_by,
                        bytes=step.bytes,
                        session_id=f"replay:{run['run_id']}",
                    )
                )
                if step.expect == "any":
                    ok = True
                elif step.expect == "ok":
                    ok = res.result == "ok"
                else:
                    ok = res.result == "denied"
                run["outcomes"].append(
                    {
                        "i": i,
                        "agent_id": agent,
                        "action": step.action,
                        "target": step.target,
                        "expect": step.expect,
                        "result": res.result,
                        "reason": res.reason,
                        "ts_ms": res.ts_ms,
                        "incident_id": res.incident_id,
                        "waited_for_block_ms": waited_ms,
                        "ok": ok,
                    }
                )
            run["status"] = "done"
            run["passed"] = all(o["ok"] for o in run["outcomes"])
        except asyncio.CancelledError:
            run["status"] = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001
            run["status"] = "error"
            run["error"] = f"{type(exc).__name__}: {exc}"
            logger.exception("replay failed")
        finally:
            run["finished_ms"] = now_ms()

    def get_replay(self, run_id: str) -> dict[str, Any]:
        run = self.replays.get(run_id)
        if run is None:
            raise NotFound(f"replay run {run_id} not found")
        return run

    async def shutdown(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        for t in list(self._tasks):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

"""Proven cure (master §1 "Cures", S4): policy backtest + guardrail prove / approve.

prove   = build a candidate policy from the incident, then run four gates:
          replay_refused  — the incident's steps replayed as ``verify:<agent>`` in an isolated
                            sandbox checkpoint (fresh state, no ClickHouse writes, no SSE) with
                            the candidate applied and hold forced on: the external send is denied.
          normal_ops_ok   — fixtures/normal_ops.json replayed the same way: every step ok
                            (skipped, passed=None, when the fixture is absent).
          backtest        — one ClickHouse query over all of tripwire.events + the eval fixtures.
          policy_lint     — the candidate validates and no host is both allowed and denied.
approve = only after a passing proof: apply the proven delta (version+1), restore the agent,
          close the incident, and take the outbreak's heightened agents back to normal.
The live state is never touched by a proof: the real agent is never quarantined by it and
verify agents never exist outside the sandbox.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field

from checkpoint.bus import Bus
from checkpoint.chread import CHUnavailable, agent_external_hosts
from checkpoint.fleet import backtest_history
from checkpoint.policy import (
    HTTP_ACTIONS,
    VERIFY_PREFIX,
    host_of,
    is_external,
    lint,
    scenario_denied,
    scenario_files,
)
from checkpoint.state import State, now_ms
from checkpoint.writer import InMemoryWriter
from tripwire.contracts import BacktestResult, Policy, Scenario, ToolCall

if TYPE_CHECKING:
    from checkpoint.service import Checkpoint


class Gate(BaseModel):
    name: str
    passed: Optional[bool]  # None = skipped (does not count against all_passed)
    detail: str = ""
    ms: float = 0.0


class GuardrailProof(BaseModel):
    incident_id: str
    candidate: Policy
    added_allowlist: list[str] = Field(default_factory=list)
    added_denylist: list[str] = Field(default_factory=list)
    gates: list[Gate] = Field(default_factory=list)
    backtest: Optional[BacktestResult] = None
    all_passed: bool = False
    proved_at_ms: int = 0


def _norm(xs: list[str]) -> list[str]:
    return [x.strip().lower() for x in xs if x and x.strip()]


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)


# ---------------------------------------------------------------------------
# B. backtest
# ---------------------------------------------------------------------------


async def backtest(svc: Checkpoint, policy: Policy, emit_extra: dict[str, Any] | None = None) -> BacktestResult:
    """Raises CHUnavailable when ClickHouse is down (the route answers 503)."""
    would, rows_read, ms, sql = await backtest_history(svc.ch, policy)
    attacks = scenario_files(svc.fixtures_dir, "attack")
    benign = scenario_files(svc.fixtures_dir, "benign")
    res = BacktestResult(
        events_scanned=int(rows_read) if rows_read is not None else 0,
        query_ms=ms,
        would_block=would,
        would_block_attack_cases=sum(1 for sc in attacks if scenario_denied(policy, sc)),
        would_block_normal_cases=sum(1 for sc in benign if scenario_denied(policy, sc)),
        sql=sql,
    )
    data = {
        **res.model_dump(),
        "n_attack_cases": len(attacks),
        "n_normal_cases": len(benign),
        "rows_read_reported": rows_read is not None,
        **(emit_extra or {}),
    }
    svc.emit("backtest", data)
    return res


# ---------------------------------------------------------------------------
# C. prove
# ---------------------------------------------------------------------------


def _sandbox(svc: Checkpoint, policy: Policy, agent: str) -> Checkpoint:
    from checkpoint.service import Checkpoint

    vagent = VERIFY_PREFIX + agent
    st = State(None)
    allow = dict(policy.allowlists)
    if agent in allow:
        allow[vagent] = list(allow[agent])
    st.policy = policy.model_copy(update={"allowlists": allow})
    st.hold_forced.add(vagent)
    sb = Checkpoint(
        writer=InMemoryWriter(),  # verify replays never reach ClickHouse
        state=st,
        bus=Bus(),  # nor the live SSE stream
        ch=svc.ch,
        honeytokens=svc.honeytokens,
        fixtures_dir=svc.fixtures_dir,
        history_lookup=svc.history_lookup,
        classify=svc.classify_override,
    )
    sb.hold_cache = svc.hold_cache
    return sb


async def _replay(sb: Checkpoint, vagent: str, steps: list[dict[str, Any]], session: str) -> list[dict[str, Any]]:
    out = []
    for s in steps:
        res = await sb.handle_tool(
            ToolCall(
                agent_id=vagent,
                action=s["action"],
                target=s["target"],
                payload=s.get("payload", ""),
                tainted_by=s.get("tainted_by", ""),
                bytes=s.get("bytes", 0),
                session_id=session,
            )
        )
        out.append({**{k: s[k] for k in ("action", "target")}, "result": res.result, "reason": res.reason})
    return out


def _candidate(cur: Policy, agent: str, dests: list[str], hist: list[str]) -> tuple[Policy, list[str], list[str]]:
    deny_now = set(_norm(cur.denylist))
    added_deny = [d for d in dict.fromkeys(dests) if d not in deny_now]
    new_deny = list(cur.denylist) + added_deny
    exclude = set(dests) | set(_norm(new_deny))
    cur_allow = cur.allowlists.get(agent)
    hist_ok = [h for h in dict.fromkeys(_norm(hist)) if h not in exclude]
    added_allow = [h for h in hist_ok if h not in set(_norm(cur_allow or []))]
    allowlists = {a: list(h) for a, h in cur.allowlists.items()}
    # Only pin an allowlist when there is evidence (history) or one already exists: an empty
    # entry would make the backtest count every external post of this agent as blocked.
    if cur_allow is not None or hist_ok:
        allowlists[agent] = sorted({h for h in _norm(cur_allow or []) if h not in exclude} | set(hist_ok))
    cand = cur.model_copy(update={"denylist": new_deny, "allowlists": allowlists})
    return cand, added_allow, added_deny


async def prove(svc: Checkpoint, incident_id: str) -> dict[str, Any]:
    from checkpoint.service import Conflict

    inc = svc.get_incident(incident_id)  # NotFound -> 404
    st = svc.state
    # Re-proving an approved incident would replace the stored proof (dropping approved_ms)
    # and allow a second approve that bumps the policy again. Refuse it.
    existing = st.proofs.get(incident_id)
    if existing and existing.get("approved_ms"):
        raise Conflict("guardrail already approved for this incident", reason="already_approved", incident_id=incident_id)
    agent = inc.agent_id
    vagent = VERIFY_PREFIX + agent
    cur = st.policy

    sends = [s for s in inc.steps if s.action in HTTP_ACTIONS and is_external(s.action, s.target, cur)]
    dests = [h for h in dict.fromkeys(host_of(s.target) for s in sends) if h]
    if inc.outbreak:
        dests += [d.strip().lower() for d in inc.outbreak.blocked_destinations if d and d.strip().lower() not in dests]

    hist: list[str] = []
    hist_note = ""
    try:
        hist, rc = await agent_external_hosts(svc.ch, agent)
        hist_note = f"{len(hist)} historical external host(s) in {rc['ms']} ms (rows_read={rc['rows_read']})"
    except CHUnavailable as exc:
        hist_note = f"history unavailable ({exc}); no allowlist hosts added"
    candidate, added_allow, added_deny = _candidate(cur, agent, dests, hist)

    gates: list[Gate] = []

    # 1. replay_refused
    t0 = time.perf_counter()
    if not sends:
        gates.append(Gate(name="replay_refused", passed=False, detail="incident has no external send step to replay", ms=_ms(t0)))
    else:
        sb = _sandbox(svc, candidate, agent)
        outs = await _replay(
            sb, vagent, [{"action": s.action, "target": s.target} for s in inc.steps], f"verify:{inc.id}"
        )
        sent = [o for o in outs if o["action"] in HTTP_ACTIONS and is_external(o["action"], o["target"], candidate)]
        ok = bool(sent) and all(o["result"] == "denied" for o in sent)
        reasons = ",".join(sorted({o["reason"] for o in sent})) or "-"
        gates.append(
            Gate(
                name="replay_refused",
                passed=ok,
                detail=(
                    f"{len(outs)} step(s) replayed as {vagent}; {sum(o['result'] == 'denied' for o in sent)}/"
                    f"{len(sent)} external send(s) denied (reason {reasons})"
                ),
                ms=_ms(t0),
            )
        )

    # 2. normal_ops_ok
    t0 = time.perf_counter()
    path = svc.fixtures_dir / "normal_ops.json"
    if not path.is_file():
        gates.append(Gate(name="normal_ops_ok", passed=None, detail="fixtures/normal_ops.json not present", ms=_ms(t0)))
    else:
        try:
            sc = Scenario.model_validate_json(path.read_text())
            sb = _sandbox(svc, candidate, agent)
            outs = await _replay(sb, vagent, [s.model_dump() for s in sc.steps], f"verify-normal:{inc.id}")
            bad = [o for o in outs if o["result"] != "ok"]
            gates.append(
                Gate(
                    name="normal_ops_ok",
                    passed=not bad,
                    detail=(
                        f"{len(outs) - len(bad)}/{len(outs)} normal step(s) ok as {vagent}"
                        + (f"; first refused: {bad[0]['action']} {bad[0]['target']} ({bad[0]['reason']})" if bad else "")
                    ),
                    ms=_ms(t0),
                )
            )
        except Exception as exc:  # noqa: BLE001 — malformed fixture
            gates.append(Gate(name="normal_ops_ok", passed=False, detail=f"normal_ops.json unreadable: {exc}", ms=_ms(t0)))

    # 3. backtest
    t0 = time.perf_counter()
    bt: BacktestResult | None = None
    try:
        bt = await backtest(svc, candidate, {"source": "guardrail", "incident_id": inc.id})
        n_att = len(scenario_files(svc.fixtures_dir, "attack"))
        n_ben = len(scenario_files(svc.fixtures_dir, "benign"))
        summary = f"{bt.would_block} historical external post(s) would be denied over {bt.events_scanned} events in {bt.query_ms} ms"
        if n_att or n_ben:
            gates.append(
                Gate(
                    name="backtest",
                    passed=bt.would_block_normal_cases == 0,
                    detail=(
                        f"{summary}; blocks {bt.would_block_attack_cases}/{n_att} attack and "
                        f"{bt.would_block_normal_cases}/{n_ben} normal case(s)"
                    ),
                    ms=_ms(t0),
                )
            )
        else:
            gates.append(
                Gate(name="backtest", passed=True, detail=f"{summary}; no eval fixtures present (case counts skipped)", ms=_ms(t0))
            )
    except CHUnavailable as exc:
        gates.append(Gate(name="backtest", passed=False, detail=f"ClickHouse unavailable: {exc}", ms=_ms(t0)))

    # 4. policy_lint
    t0 = time.perf_counter()
    problems = lint(candidate)
    gates.append(
        Gate(
            name="policy_lint",
            passed=not problems,
            detail="; ".join(problems) if problems else f"candidate valid; no host both allowed and denied ({hist_note})",
            ms=_ms(t0),
        )
    )

    proof = GuardrailProof(
        incident_id=inc.id,
        candidate=candidate,
        added_allowlist=added_allow,
        added_denylist=added_deny,
        gates=gates,
        backtest=bt,
        all_passed=all(g.passed for g in gates if g.passed is not None),
        proved_at_ms=now_ms(),
    ).model_dump()
    st.add_proof(inc.id, proof)
    st.mark_dirty()
    svc.emit("guardrail", {"phase": "proved", **proof})
    return proof


# ---------------------------------------------------------------------------
# D. approve
# ---------------------------------------------------------------------------


async def approve(svc: Checkpoint, incident_id: str) -> dict[str, Any]:
    from checkpoint.service import Conflict

    inc = svc.get_incident(incident_id)  # NotFound -> 404
    st = svc.state
    proof = st.proofs.get(incident_id)
    if not proof or not proof.get("all_passed"):
        raise Conflict(
            "no passing proof for this incident: run POST /guardrail/{id}/prove first",
            reason="unproven",
            incident_id=incident_id,
        )
    if proof.get("approved_ms"):
        raise Conflict("guardrail already approved for this incident", reason="already_approved", incident_id=incident_id)

    agent = inc.agent_id
    cur = st.policy
    # Apply the proven delta on top of the current policy (keeps concurrent outbreak pushes).
    deny_now = set(_norm(cur.denylist))
    deny = list(cur.denylist) + [d for d in proof.get("added_denylist", []) if d.lower() not in deny_now]
    deny_set = set(_norm(deny))
    allowlists = {a: list(h) for a, h in cur.allowlists.items()}
    cand_allow = (proof.get("candidate") or {}).get("allowlists", {})
    if agent in cand_allow or proof.get("added_allowlist"):
        merged = set(_norm(allowlists.get(agent, []))) | set(_norm(proof.get("added_allowlist", [])))
        allowlists[agent] = sorted(h for h in merged if h not in deny_set)
    new = cur.model_copy(update={"denylist": deny, "allowlists": allowlists, "version": cur.version + 1})
    st.policy = new

    now = now_ms()
    restored: list[str] = []
    async with st.lock_for(agent):
        _, closed = svc._restore_locked(agent, now)
    restored.append(agent)
    if inc.closed_ms is None:
        inc.closed_ms = now
        closed.append(inc)
    if inc.outbreak:
        # The approved guardrail now protects the whole fleet, so exposed agents come back too:
        # quarantined ones (e.g. stopped by the IOC push) are restored, heightened ones go normal.
        for a in inc.outbreak.exposed_agents:
            if a == agent:
                continue
            if a in st.blocked or st.modes.get(a) == "quarantined":
                async with st.lock_for(a):
                    _, also_closed = svc._restore_locked(a, now)
                closed.extend(also_closed)
                restored.append(a)
            elif st.modes.get(a) == "heightened":
                st.modes[a] = "normal"
                restored.append(a)
    proof["approved_ms"] = now
    proof["approved_policy_version"] = new.version
    st.mark_dirty()

    svc.emit("metrics", {"source": "checkpoint", "kind": "policy", "policy_version": new.version})
    for c in {c.id: c for c in closed}.values():
        svc.emit("incident", c.model_dump())
    svc.emit(
        "guardrail",
        {"phase": "approved", "incident_id": inc.id, "policy_version": new.version, "restored": restored},
    )
    for a in restored:
        svc.emit("agent_state", svc.agent_state(a))
    return {"incident_id": inc.id, "policy_version": new.version, "restored": restored}

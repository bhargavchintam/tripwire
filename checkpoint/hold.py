"""Hold mode (master §1 "Prevents", §6) — the decision made inside POST /tool.

Two layers, evaluated by checkpoint/service.py under the per-agent lock, after the
blocked + honeytoken checks:

1. ``policy_check()`` — ALWAYS applies (hold on or off), no model:
   - action in ``policy.fixed_deny_actions`` -> denied ``hold_policy``, non-blocking
     alert (role_grab / log_tamper), decision_source ``policy``, no quarantine.
   - http_post/http_get to a denylisted host -> denied ``hold_policy``, quarantine +
     incident (rule ``hold``), decision_source ``policy`` (the fleet IOC push).
2. ``decide()`` — only when hold is on for the agent and the call is an external
   http_post to a host outside the agent's allowlist. Suspicious = funnel prefix
   (secret read -> encode command within 60 s) OR heightened mode OR a novel
   destination (ClickHouse history lookup, 800 ms budget, 30 s cache). Suspicious
   calls go to ``classify()`` with a 3 s timeout.

The classify() seam: ``get_classify()`` returns ``ai.quick_check.classify`` when
HOLD_CHECK=real (and it imports), otherwise ``stub_classify``. The stub is a rule,
not a model, and says so via ``decision_source="rule_only"``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from checkpoint.policy import ENCODE_CMD_RE, SECRET_PATH_RE, host_of, real_agent
from tripwire.config import get_settings
from tripwire.contracts import (
    RULE_HOLD,
    RULE_LOG_TAMPER,
    RULE_ROLE_GRAB,
    ClassifyFn,
    Policy,
    QuickCheckInput,
    Reason,
    ToolCall,
    Verdict,
)

STUB_WINDOW_MS = 60_000
PREFIX_WINDOW_MS = 60_000
CLASSIFY_TIMEOUT_S = 3.0
HISTORY_TIMEOUT_S = 0.8
HISTORY_CACHE_S = 30.0
NOVEL_LOOKBACK_GAP_MS = 10 * 60_000  # "ever posted here before now - 10 min?"
MALICIOUS_MIN_CONFIDENCE = 0.8

FIXED_DENY_RULES = {"assume_role": RULE_ROLE_GRAB, "disable_logging": RULE_LOG_TAMPER}

# (agent_id, host, before_ms) -> (seen_before, receipt {sql, ms, rows_read})
HistoryLookup = Callable[[str, str, int], Awaitable[tuple[bool, dict[str, Any] | None]]]


@dataclass
class HoldDecision:
    allow: bool = True
    reason: Reason = ""
    verdict: Verdict | None = None
    rule: str = RULE_HOLD
    quarantine: bool = True  # on deny: also block the agent and open an incident
    note: str = ""
    alert: bool = False  # record a non-blocking alert (flagged -> benign, held for review, ...)
    timed: bool = False  # a hold-mode decision (step 3) ran: record hold_decision_ms
    ms: float | None = None
    receipt: dict[str, Any] | None = None  # history lookup receipt
    why: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ts(e: dict[str, Any]) -> int:
    try:
        return int(e.get("ts_ms", 0))
    except (TypeError, ValueError):
        return 0


def prefix_match(events: list[dict[str, Any]], now: int | None = None, window_ms: int = PREFIX_WINDOW_MS) -> bool:
    """A read_file of a secret-looking path followed by an encode run_command; with ``now``
    both must fall within ``window_ms`` before now, otherwise within window_ms of each other."""
    evs = sorted(events, key=_ts)
    reads: list[int] = []
    for e in evs:
        t = _ts(e)
        if now is not None and not (now - window_ms <= t <= now):
            continue
        act, tgt = e.get("action"), str(e.get("target", ""))
        if act == "read_file" and SECRET_PATH_RE.search(tgt):
            reads.append(t)
        elif act == "run_command" and ENCODE_CMD_RE.search(tgt):
            if any(0 <= t - r <= window_ms for r in reads):
                return True
    return False


def reason_for(v: Verdict) -> Reason:
    return "hold_rule" if v.decision_source == "rule_only" else "hold_model"


def policy_check(call: ToolCall, policy: Policy, is_ext: int) -> HoldDecision | None:
    """Steps 1-2 (always on). None = policy has nothing to say about this call."""
    if call.action in policy.fixed_deny_actions:
        rule = FIXED_DENY_RULES.get(call.action, RULE_HOLD)
        return HoldDecision(
            allow=False,
            reason="hold_policy",
            rule=rule,
            quarantine=False,
            alert=True,
            verdict=Verdict(
                verdict="malicious",
                confidence=1.0,
                reason=f"policy: '{call.action}' is a fixed-deny action (no model call)",
                decision_source="policy",
            ),
            note="fixed deny action",
        )
    if call.action in ("http_post", "http_get") and is_ext:
        host = host_of(call.target)
        deny = {h.strip().lower() for h in policy.denylist}
        if host and host in deny:
            return HoldDecision(
                allow=False,
                reason="hold_policy",
                rule=RULE_HOLD,
                quarantine=True,
                verdict=Verdict(
                    verdict="malicious",
                    confidence=1.0,
                    reason=f"policy: destination {host} is on the fleet denylist (known-bad, no model call)",
                    decision_source="policy",
                ),
                note="denylisted destination",
            )
    return None


def hold_applies(call: ToolCall, policy: Policy, is_ext: int) -> bool:
    """Step 3 eligibility (caller also checks that hold is on for the agent)."""
    if call.action != "http_post" or not is_ext:
        return False
    host = host_of(call.target)
    allowed = {h.strip().lower() for h in policy.allowlists.get(call.agent_id, [])}
    return host not in allowed


class HistoryCache:
    """(agent, host) -> (expires_monotonic, seen, receipt). Only successful lookups are cached."""

    def __init__(self, ttl_s: float = HISTORY_CACHE_S) -> None:
        self.ttl_s = ttl_s
        self._d: dict[tuple[str, str], tuple[float, bool, dict[str, Any] | None]] = {}

    def get(self, agent: str, host: str) -> tuple[bool, dict[str, Any] | None] | None:
        hit = self._d.get((agent, host))
        if hit and hit[0] > time.monotonic():
            return hit[1], hit[2]
        return None

    def put(self, agent: str, host: str, seen: bool, receipt: dict[str, Any] | None) -> None:
        if len(self._d) > 5000:
            self._d.clear()
        self._d[(agent, host)] = (time.monotonic() + self.ttl_s, seen, receipt)


async def _novelty(
    agent: str, host: str, now: int, lookup: HistoryLookup | None, cache: HistoryCache
) -> tuple[bool, dict[str, Any] | None, str]:
    """(novel, receipt, note). Timeout/error -> not novel, with a note saying so."""
    if lookup is None:
        return False, None, "history lookup unavailable (treated as not novel)"
    key_agent = real_agent(agent)
    cached = cache.get(key_agent, host)
    if cached is not None:
        seen, receipt = cached
        return (not seen), ({**receipt, "cached": True} if receipt else None), "history lookup (cached)"
    try:
        seen, receipt = await asyncio.wait_for(
            lookup(key_agent, host, now - NOVEL_LOOKBACK_GAP_MS), HISTORY_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        return False, None, f"history lookup timed out (> {int(HISTORY_TIMEOUT_S * 1000)} ms); treated as not novel"
    except Exception as exc:  # noqa: BLE001
        return False, None, f"history lookup failed ({type(exc).__name__}); treated as not novel"
    cache.put(key_agent, host, bool(seen), receipt)
    return (not seen), receipt, "history lookup"


async def decide(
    call: ToolCall,
    ring: list[dict[str, Any]],
    *,
    ts: int,
    mode: str,
    is_ext: int,
    lookup: HistoryLookup | None,
    classify: ClassifyFn,
    cache: HistoryCache,
) -> HoldDecision:
    """Step 3: hold-mode decision for an external http_post outside the agent's allowlist."""
    t0 = time.perf_counter()
    host = host_of(call.target)
    prefix = prefix_match(ring, now=ts)
    heightened = mode == "heightened"
    novel, receipt, lookup_note = await _novelty(call.agent_id, host, ts, lookup, cache)

    # `why` is shown to humans (incident / receipt). `signals` is the model's context line: fixed
    # checkpoint text only. The host is chosen by the (possibly hijacked) agent, so it reaches the
    # model only inside the fenced, JSON-escaped events (semgrep/FINDINGS.md #1, OWASP LLM01).
    why: list[str] = []
    signals: list[str] = []
    if prefix:
        why.append("funnel prefix matched: secret read -> encode command within 60 s")
        signals.append(why[-1])
    if heightened:
        why.append("agent is on heightened watch (outbreak exposure)")
        signals.append(why[-1])
    if novel:
        why.append(f"novel destination: no earlier ok http_post to {host} by this agent")
        signals.append("novel destination: no earlier ok http_post to this host by this agent (see the pending event)")

    def done(d: HoldDecision) -> HoldDecision:
        d.timed = True
        d.ms = round((time.perf_counter() - t0) * 1000, 3)
        d.receipt = receipt
        d.why = why
        if lookup_note not in ("history lookup", "history lookup (cached)"):
            d.note = (d.note + "; " if d.note else "") + lookup_note
        return d

    if not why:
        return done(HoldDecision(allow=True, note="hold: not suspicious"))

    pending = {
        "ts_ms": ts,
        "action": call.action,
        "target": call.target,
        "is_external": is_ext,
        "bytes": call.bytes or len(call.payload.encode("utf-8")),
        "tainted_by": call.tainted_by,
        "result": "pending",
    }
    inp = QuickCheckInput(
        agent_id=call.agent_id,
        rule=RULE_HOLD,
        events=[dict(e) for e in ring] + [pending],
        context="; ".join(signals),
    )
    try:
        verdict = await asyncio.wait_for(classify(inp), CLASSIFY_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — timeout or classify error
        what = (
            f"model timed out (> {CLASSIFY_TIMEOUT_S:g} s)"
            if isinstance(exc, asyncio.TimeoutError)
            else f"model error ({type(exc).__name__})"
        )
        if prefix or heightened:
            basis = "funnel prefix matched" if prefix else "agent on heightened watch"
            return done(
                HoldDecision(
                    allow=False,
                    reason="hold_rule",
                    quarantine=prefix,  # only quarantine on the funnel evidence
                    alert=not prefix,
                    verdict=Verdict(
                        verdict="malicious" if prefix else "uncertain",
                        confidence=1.0 if prefix else 0.5,
                        reason=f"{what}; denied by rule: {basis}",
                        decision_source="rule_only",
                    ),
                    note=what,
                )
            )
        return done(
            HoldDecision(
                allow=True,
                alert=True,
                verdict=Verdict(
                    verdict="uncertain",
                    confidence=0.0,
                    reason=f"{what}; allowed (only signal was a novel destination); flagged for review",
                    decision_source="rule_only",
                ),
                note=what,
            )
        )

    if verdict.verdict == "malicious" and verdict.confidence >= MALICIOUS_MIN_CONFIDENCE:
        return done(HoldDecision(allow=False, reason=reason_for(verdict), verdict=verdict, quarantine=True))
    if verdict.verdict == "benign":
        return done(HoldDecision(allow=True, alert=True, verdict=verdict, note="flagged -> benign"))
    # uncertain, or malicious below the confidence bar: hold for a human, no quarantine.
    return done(
        HoldDecision(
            allow=False,
            reason=reason_for(verdict),
            verdict=verdict,
            quarantine=False,
            alert=True,
            note="held for human review",
        )
    )


# ---------------------------------------------------------------------------
# classify() loader
# ---------------------------------------------------------------------------


async def stub_classify(inp: QuickCheckInput) -> Verdict:
    """Rule stub (master §6): malicious iff the funnel prefix matched (a secret-looking
    read followed by an encode command within 60 s); otherwise benign."""
    if prefix_match(inp.events, window_ms=STUB_WINDOW_MS):
        return Verdict(
            verdict="malicious",
            confidence=1.0,
            reason="rule stub: secret-looking read followed by an encode command within 60 s",
            decision_source="rule_only",
        )
    return Verdict(
        verdict="benign",
        confidence=0.5,
        reason="rule stub: no secret read -> encode command sequence within 60 s (not a model decision)",
        decision_source="rule_only",
    )


_classify: ClassifyFn | None = None
classify_source: str = "unloaded"
_last_real_attempt: float = 0.0
REAL_RETRY_S = 30.0


def get_classify(force_reload: bool = False, want_real: bool | None = None) -> ClassifyFn:
    """The classify() to use now. With HOLD_CHECK=real and ai.quick_check not importable yet,
    the stub is used and the import is retried every 30 s, so Sripadha's module is picked
    up automatically when it lands (no restart).

    want_real: the calling app's own setting (Checkpoint passes settings.hold_check). None falls
    back to the global .env. An app configured for the stub always gets the stub and never
    touches the process-wide real-classify cache (12:25 fix: tests built with hold_check="stub"
    were calling AkashML because this read the global .env)."""
    global _classify, classify_source, _last_real_attempt
    explicit = want_real is not None
    if want_real is None:
        want_real = get_settings().hold_check == "real"
    if not want_real:
        if explicit:  # an app configured for the stub: never touch the shared real cache
            return stub_classify
        _classify, classify_source = stub_classify, "stub"  # global setting says stub
        return _classify
    fallback = classify_source.startswith("stub (real import failed")
    retry = want_real and fallback and time.monotonic() - _last_real_attempt >= REAL_RETRY_S
    if _classify is not None and not force_reload and not retry:
        return _classify
    if want_real:
        _last_real_attempt = time.monotonic()
        try:
            import importlib

            importlib.invalidate_caches()
            from ai.quick_check import classify  # type: ignore[import-not-found]

            if fallback:
                logger.info("hold: ai.quick_check.classify landed; switching from the stub")
            _classify, classify_source = classify, "real"
            return _classify
        except Exception as exc:  # noqa: BLE001
            if not fallback or force_reload:
                logger.warning(f"hold: HOLD_CHECK=real but ai.quick_check import failed ({exc!r}); using stub")
            _classify, classify_source = stub_classify, f"stub (real import failed: {type(exc).__name__})"
            return _classify
    _classify, classify_source = stub_classify, "stub"
    return _classify

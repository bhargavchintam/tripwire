"""Hold mode (master §1 "Prevents", §6).

PHASE 1: ``decide()`` is a placeholder that always allows. It is only called by
POST /tool when ``state.hold_enabled`` is true and the action is in
``policy.high_risk_actions``; the plumbing (deny -> block -> incident, hold timing
samples) is already wired in checkpoint/service.py, so phase 2 only has to fill in
``decide()``.

The classify() seam: ``get_classify()`` returns ``ai.quick_check.classify`` when
HOLD_CHECK=real (and it imports), otherwise ``stub_classify``. The stub is a rule,
not a model, and says so via ``decision_source="rule_only"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from tripwire.config import get_settings
from tripwire.contracts import (
    RULE_HOLD,
    ClassifyFn,
    Policy,
    QuickCheckInput,
    Reason,
    ToolCall,
    Verdict,
)

if TYPE_CHECKING:
    from checkpoint.state import State

STUB_WINDOW_MS = 60_000


@dataclass
class HoldDecision:
    allow: bool = True
    reason: Reason = ""
    verdict: Verdict | None = None
    rule: str = RULE_HOLD
    quarantine: bool = True  # on deny: also block the agent and open an incident
    note: str = ""


async def decide(call: ToolCall, ring: list[dict[str, Any]], state: State, policy: Policy) -> HoldDecision:
    """Synchronous hold-mode decision for a high-risk call. Filled in phase 2.

    Phase-2 plan (BINDU §Block hold): ring-buffer prefix check + allowlist/denylist +
    ClickHouse history lookup; if suspicious call ``get_classify()`` with a 3 s timeout;
    malicious -> deny (reason hold_model/hold_rule) + block + incident; benign -> allow
    and record a flagged->benign alert; timeout -> deny only if the prefix matched
    (decision_source rule_only).

    PHASE-1 PLACEHOLDER: always allow, no verdict (so no hold timing is recorded).
    """
    return HoldDecision(allow=True, note="phase-1 placeholder: hold decision not implemented yet")


# ---------------------------------------------------------------------------
# classify() loader
# ---------------------------------------------------------------------------


def _ts(e: dict[str, Any]) -> int:
    try:
        return int(e.get("ts_ms", 0))
    except (TypeError, ValueError):
        return 0


async def stub_classify(inp: QuickCheckInput) -> Verdict:
    """Rule stub: malicious iff a read of a path containing '.env' is followed by a
    run_command containing 'base64' within 60 s; otherwise benign."""
    events = sorted(inp.events, key=_ts)
    env_reads = [
        _ts(e) for e in events if e.get("action") == "read_file" and ".env" in str(e.get("target", ""))
    ]
    for e in events:
        if e.get("action") == "run_command" and "base64" in str(e.get("target", "")):
            t = _ts(e)
            if any(0 <= t - r <= STUB_WINDOW_MS for r in env_reads):
                return Verdict(
                    verdict="malicious",
                    confidence=1.0,
                    reason="rule stub: read of a .env file followed by a base64 command within 60 s",
                    decision_source="rule_only",
                )
    return Verdict(
        verdict="benign",
        confidence=0.5,
        reason="rule stub: no .env read -> base64 command sequence within 60 s (not a model decision)",
        decision_source="rule_only",
    )


_classify: ClassifyFn | None = None
classify_source: str = "unloaded"


def get_classify(force_reload: bool = False) -> ClassifyFn:
    global _classify, classify_source
    if _classify is not None and not force_reload:
        return _classify
    if get_settings().hold_check == "real":
        try:
            from ai.quick_check import classify  # type: ignore[import-not-found]

            _classify, classify_source = classify, "real"
            return _classify
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"hold: HOLD_CHECK=real but ai.quick_check import failed ({exc!r}); using stub")
            _classify, classify_source = stub_classify, f"stub (real import failed: {type(exc).__name__})"
            return _classify
    _classify, classify_source = stub_classify, "stub"
    return _classify

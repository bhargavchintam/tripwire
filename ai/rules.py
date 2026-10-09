"""Deterministic verdicts for the classify() seam; decision_source is always 'rule_only'.

rule_verdict() answers when no AkashML key is configured, when the model fails or times
out (ai.quick_check), and when detection.loop cannot import the model path. It is pure and
synchronous: no I/O, no settings, no logging. Every reason string starts with "rule: ".

Rules (master §6, frozen for today):
  secret_theft     malicious 0.9 iff read_file(target contains ".env") -> run_command(target
                   contains "base64") within 60 s -> a later http_post with is_external=1, all
                   inside 60 s of the read; else benign 0.5.
  hold             malicious 0.9 iff the read -> base64 pair above (the call being held is not
                   in the ring yet, so no http_post is required); else benign 0.5.
  baseline_novelty uncertain 0.5 (novelty needs a model or fleet history, not a rule).
  role_grab        malicious 0.85 iff an assume_role event is present (any result: a denied
                   attempt is still the signal); else benign 0.5.
  log_tamper       malicious 0.85 iff a disable_logging event is present (any result); else benign 0.5.
  secret_exfil_direct (OPT-IN detector rule; contracts.py has no constant for it, rules are plain
                   strings) malicious 0.9 iff a read_file of a secret-looking path (is_secret_path)
                   is followed within 60 s by an http_post with is_external=1, with or without an
                   encode step in between; else benign 0.5. Same predicate as
                   detection/sql/secret_exfil_direct.sql.
  unknown rule     uncertain 0.5.

Event dicts are the checkpoint ring rows / detector rows: ts_ms, action, target, result,
reason, is_external, bytes, tainted_by. Extra keys are ignored, missing keys default.
Events are sorted by ts_ms (stable, so ties keep arrival order) before matching.
"""

from __future__ import annotations

from typing import Any

from tripwire.contracts import (
    RULE_BASELINE,
    RULE_HOLD,
    RULE_LOG_TAMPER,
    RULE_ROLE_GRAB,
    RULE_SECRET_THEFT,
    QuickCheckInput,
    Verdict,
    VerdictLabel,
)

# Opt-in rule (not in detection.loop.DEFAULT_RULES). tripwire/contracts.py (frozen) has no constant for it;
# AlertPayload.rule is a plain string and the checkpoint looks its tags up with RULE_TAGS.get(rule, []).
RULE_SECRET_EXFIL_DIRECT = "secret_exfil_direct"
# Secret-looking read_file targets, case-insensitive (mirrors the ILIKE list in secret_exfil_direct.sql).
SECRET_SUBSTRINGS: tuple[str, ...] = (".env", "secret", "credential", "id_rsa")
SECRET_SUFFIXES: tuple[str, ...] = (".pem",)

WINDOW_MS = 60_000
CHAIN_CONFIDENCE = 0.9
SINGLE_EVENT_CONFIDENCE = 0.85
NO_MATCH_CONFIDENCE = 0.5


def _ts(e: dict[str, Any]) -> int:
    try:
        return int(e.get("ts_ms", 0))
    except (TypeError, ValueError):
        return 0


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _short(v: Any, n: int = 60) -> str:
    s = " ".join(str(v).split())
    return s if len(s) <= n else s[: n - 3] + "..."


def _rule(verdict: VerdictLabel, confidence: float, reason: str) -> Verdict:
    return Verdict(verdict=verdict, confidence=confidence, reason="rule: " + reason, decision_source="rule_only")


def find_chain(events: list[dict[str, Any]], need_external_post: bool) -> dict[str, Any] | None:
    """First read_file(.env) -> run_command(base64) [-> external http_post] chain inside WINDOW_MS.

    Returns {"read", "encode", "post"} (post is None when not required) or None.
    """
    evs = sorted(events, key=_ts)
    for i, read in enumerate(evs):
        if read.get("action") != "read_file" or ".env" not in str(read.get("target", "")):
            continue
        t_read = _ts(read)
        for j in range(i + 1, len(evs)):
            enc = evs[j]
            if _ts(enc) - t_read > WINDOW_MS:
                break
            if enc.get("action") != "run_command" or "base64" not in str(enc.get("target", "")):
                continue
            if not need_external_post:
                return {"read": read, "encode": enc, "post": None}
            for k in range(j + 1, len(evs)):
                post = evs[k]
                if _ts(post) - t_read > WINDOW_MS:
                    break
                if post.get("action") == "http_post" and _int(post.get("is_external")) == 1:
                    return {"read": read, "encode": enc, "post": post}
    return None


def is_secret_path(target: Any) -> bool:
    """True for a secret-looking read_file target: contains .env / secret / credential / id_rsa, or ends with
    .pem (case-insensitive)."""
    t = str(target or "").lower()
    return any(x in t for x in SECRET_SUBSTRINGS) or t.endswith(SECRET_SUFFIXES)


def find_direct_exfil(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """First read_file(secret-looking path) -> http_post(is_external=1) pair inside WINDOW_MS, no encode step
    required. Returns {"read", "post"} or None."""
    evs = sorted(events, key=_ts)
    for i, read in enumerate(evs):
        if read.get("action") != "read_file" or not is_secret_path(read.get("target")):
            continue
        t_read = _ts(read)
        for j in range(i + 1, len(evs)):
            post = evs[j]
            if _ts(post) - t_read > WINDOW_MS:
                break
            if post.get("action") == "http_post" and _int(post.get("is_external")) == 1:
                return {"read": read, "post": post}
    return None


def _first(events: list[dict[str, Any]], action: str) -> dict[str, Any] | None:
    for e in sorted(events, key=_ts):
        if e.get("action") == action:
            return e
    return None


def rule_verdict(inp: QuickCheckInput) -> Verdict:
    """Deterministic verdict for inp.rule over inp.events (see module docstring)."""
    events = inp.events or []
    rule = inp.rule

    if rule == RULE_SECRET_THEFT:
        chain = find_chain(events, need_external_post=True)
        if chain is not None:
            t0 = _ts(chain["read"])
            return _rule(
                "malicious",
                CHAIN_CONFIDENCE,
                f"read_file {_short(chain['read'].get('target'))} -> run_command with base64 "
                f"{(_ts(chain['encode']) - t0) / 1000:.1f}s later -> external http_post to "
                f"{_short(chain['post'].get('target'))} {(_ts(chain['post']) - t0) / 1000:.1f}s after the read",
            )
        if find_chain(events, need_external_post=False) is not None:
            return _rule(
                "benign", NO_MATCH_CONFIDENCE, ".env read -> base64 seen but no external http_post within 60 s"
            )
        return _rule("benign", NO_MATCH_CONFIDENCE, "no .env read -> base64 -> external http_post chain within 60 s")

    if rule == RULE_HOLD:
        chain = find_chain(events, need_external_post=False)
        if chain is not None:
            dt = (_ts(chain["encode"]) - _ts(chain["read"])) / 1000
            return _rule(
                "malicious",
                CHAIN_CONFIDENCE,
                f"read_file {_short(chain['read'].get('target'))} -> run_command with base64 {dt:.1f}s later; "
                "a secret was read and encoded within 60 s before this high-risk call",
            )
        return _rule("benign", NO_MATCH_CONFIDENCE, "no .env read -> base64 command within 60 s before this call")

    if rule == RULE_BASELINE:
        return _rule("uncertain", NO_MATCH_CONFIDENCE, "baseline novelty needs a model or fleet history, not a rule")

    if rule == RULE_ROLE_GRAB:
        e = _first(events, "assume_role")
        if e is not None:
            return _rule("malicious", SINGLE_EVENT_CONFIDENCE, f"assume_role present (target {_short(e.get('target'))})")
        return _rule("benign", NO_MATCH_CONFIDENCE, "no assume_role event")

    if rule == RULE_LOG_TAMPER:
        e = _first(events, "disable_logging")
        if e is not None:
            return _rule(
                "malicious", SINGLE_EVENT_CONFIDENCE, f"disable_logging present (target {_short(e.get('target'))})"
            )
        return _rule("benign", NO_MATCH_CONFIDENCE, "no disable_logging event")

    if rule == RULE_SECRET_EXFIL_DIRECT:
        pair = find_direct_exfil(events)
        if pair is not None:
            dt = (_ts(pair["post"]) - _ts(pair["read"])) / 1000
            return _rule(
                "malicious",
                CHAIN_CONFIDENCE,
                f"read_file {_short(pair['read'].get('target'))} -> external http_post to "
                f"{_short(pair['post'].get('target'))} {dt:.1f}s later (secret read sent out; no encode step needed)",
            )
        return _rule("benign", NO_MATCH_CONFIDENCE, "no secret read -> external http_post within 60 s")

    return _rule("uncertain", NO_MATCH_CONFIDENCE, f"no deterministic rule for '{_short(rule, 40)}'")

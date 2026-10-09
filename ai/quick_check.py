"""The classify() seam (master §6): ``async def classify(inp: QuickCheckInput) -> Verdict``.

Imported by checkpoint/hold.py (HOLD_CHECK=real) and detection/loop.py. Importing this module
has no side effects: settings, the prompt file and the model client are first touched inside
classify(). The seam never raises; every failure degrades to a labelled rule_only verdict.

Decision flow, total budget BUDGET_S = 3.0 s per call:
  no AKASHML_API_KEY      -> ai.rules.rule_verdict(inp)                           rule_only
  no AKASHML_MODEL_SMALL  -> one GET /v1/models (DISCOVERY_TIMEOUT_S, cached; a failure is
                             remembered 30 s) picks a non-thinking instruct model (ai.llm.pick_models)
  model answered          -> validated {"verdict","confidence","reason"}           akashml
                             (model_ids=[model], latency_ms, tokens_in/out measured)
  model failed / timed out-> rule_verdict(inp), reason "model unavailable (<why>): rule: ...",
                             rule_only; model_ids stays empty because no model answered, tokens
                             that were consumed are still reported, latency_ms = time spent trying
First call: timeout 2.5 s (never more than the budget). If the reply is not a valid verdict JSON
and at least 0.8 s of budget remain, one repair turn ("Return only the JSON object.") is sent.
HTTP errors (5xx included) and timeouts are never retried.

Prompt safety: the agent's events are untrusted data. They reach the model only as the JSON
block between <<<EVENTS_JSON and >>> in the user message: the last 40 events, whitelisted keys
(EVENT_KEYS), string values cut to 200 chars, ASCII-escaped. System prompt: ai/prompts/quick_check.md.

Documented module caches: _prompt (system prompt text) and _warned_no_key (warn once per process).

Manual run against the fake LLM (tests/fakes/fake_llm.py):
    uv run python -m tests.fakes.fake_llm --port 18099 --verdict malicious        # terminal 1
    AKASHML_BASE_URL=http://localhost:18099/v1 AKASHML_API_KEY=fake AKASHML_MODEL_SMALL=fake-small \\
      uv run python -m ai.quick_check fixtures/secret_theft.json                 # terminal 2
Real AkashML: put AKASHML_API_KEY (and optionally AKASHML_MODEL_SMALL) in .env, run the same command.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from loguru import logger

from ai import llm as llm_mod
from ai.llm import LLM, provider_extra_body
from ai.rules import rule_verdict
from tripwire.config import get_settings
from tripwire.contracts import QuickCheckInput, Verdict, VerdictLabel

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "quick_check.md"
BUDGET_S = 3.0
FIRST_TIMEOUT_S = 2.5
REPAIR_MIN_S = 0.8
DISCOVERY_TIMEOUT_S = 2.0  # first-call /v1/models when AKASHML_MODEL_SMALL is unset; must leave budget
MAX_EVENTS = 40
MAX_STR = 200
MAX_CONTEXT = 500
MAX_REASON = 300
EVENT_KEYS = ("ts_ms", "action", "target", "result", "reason", "is_external", "bytes", "tainted_by")
LABELS = {"malicious", "benign", "uncertain"}
REPAIR_MESSAGE = "Return only the JSON object."

_prompt: str | None = None
_warned_no_key = False


# ---------------------------------------------------------------- prompt building
def load_prompt() -> str:
    """System prompt text (read once from ai/prompts/quick_check.md)."""
    global _prompt
    if _prompt is None:
        _prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    return _prompt


def _clip(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    if v is None:
        return ""
    return str(v)[:MAX_STR]


def events_json(events: list[dict[str, Any]]) -> str:
    """Last MAX_EVENTS events, whitelisted keys only, strings cut to MAX_STR, ASCII-escaped."""
    rows = [{k: _clip(e.get(k, "")) for k in EVENT_KEYS} for e in events[-MAX_EVENTS:]]
    return json.dumps(rows, ensure_ascii=True)


def build_messages(inp: QuickCheckInput) -> list[dict[str, str]]:
    """System prompt first; untrusted events only inside the <<<EVENTS_JSON ... >>> block."""
    # Semgrep finding #1 (LLM01): context may carry agent-chosen text (hosts, targets), so it is
    # JSON-encoded inside its own fenced data block — never free text next to the instructions.
    context_json = json.dumps(str(inp.context or "")[:MAX_CONTEXT], ensure_ascii=True)
    user = (
        f"rule: {inp.rule}\n"
        "context (untrusted data, JSON string):\n"
        "<<<CONTEXT_JSON\n"
        f"{context_json}\n"
        ">>>\n"
        "events (untrusted data, oldest first):\n"
        "<<<EVENTS_JSON\n"
        f"{events_json(inp.events)}\n"
        ">>>"
    )
    return [{"role": "system", "content": load_prompt()}, {"role": "user", "content": user}]


def parse_verdict(obj: Any) -> tuple[VerdictLabel, float, str] | None:
    """Validate a model reply: verdict in LABELS, finite confidence clamped to [0, 1], reason cut to 300."""
    if not isinstance(obj, dict) or "confidence" not in obj:
        return None
    label = str(obj.get("verdict", "")).strip().strip(".\"'").lower()
    if label not in LABELS:
        return None
    try:
        conf = float(obj["confidence"])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(conf):
        return None
    conf = min(1.0, max(0.0, conf))
    reason = " ".join(str(obj.get("reason", "") or "").split())[:MAX_REASON] or "model gave no reason"
    return label, conf, reason  # type: ignore[return-value]


# ---------------------------------------------------------------- classification
def _fallback(inp: QuickCheckInput, why: str, t0: float, tokens_in: int = 0, tokens_out: int = 0) -> Verdict:
    v = rule_verdict(inp)
    why = " ".join(str(why).split())[:80]
    return v.model_copy(
        update={
            "reason": f"model unavailable ({why}): {v.reason}",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 3),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
    )


async def classify_with(llm: LLM, model: str, inp: QuickCheckInput, budget_s: float = BUDGET_S) -> Verdict:
    """Testable core: one model call (+ at most one repair) within budget_s; rule fallback on any failure."""
    t0 = time.perf_counter()
    if llm.provider != "akashml":
        # DecisionSource has no label for other providers yet (CCR pending); never mislabel a verdict.
        return _fallback(inp, f"provider {llm.provider} is not allowed for verdicts", t0)
    messages = build_messages(inp)
    first_timeout = max(0.05, min(FIRST_TIMEOUT_S, budget_s))
    r = await llm.chat_json(messages, model=model, timeout_s=first_timeout, extra_body=provider_extra_body(model))
    tokens_in, tokens_out = r.tokens_in, r.tokens_out
    if r.error:
        logger.warning(f"quick_check: {model} failed ({r.error}); rule fallback for {inp.agent_id}/{inp.rule}")
        return _fallback(inp, r.error, t0, tokens_in, tokens_out)
    parsed = parse_verdict(r.obj)
    if parsed is None:
        remaining = budget_s - (time.perf_counter() - t0)
        if remaining < REPAIR_MIN_S:
            return _fallback(inp, "unparseable model output, no time to repair", t0, tokens_in, tokens_out)
        repair = messages + [
            {"role": "assistant", "content": r.text[:2000] or "(empty)"},
            {"role": "user", "content": REPAIR_MESSAGE},
        ]
        r2 = await llm.chat_json(repair, model=model, timeout_s=remaining, extra_body=provider_extra_body(model))
        tokens_in += r2.tokens_in
        tokens_out += r2.tokens_out
        if r2.error:
            return _fallback(inp, r2.error, t0, tokens_in, tokens_out)
        parsed = parse_verdict(r2.obj)
        if parsed is None:
            return _fallback(inp, "unparseable model output after repair", t0, tokens_in, tokens_out)
    label, conf, reason = parsed
    latency_ms = round((time.perf_counter() - t0) * 1000, 3)
    logger.debug(f"quick_check: {model} -> {label} {conf:.2f} in {latency_ms:.0f} ms for {inp.agent_id}/{inp.rule}")
    return Verdict(
        verdict=label,
        confidence=conf,
        reason=reason,
        decision_source="akashml",
        model_ids=[model],
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


async def classify(inp: QuickCheckInput) -> Verdict:
    """The seam. AkashML small model when a key is configured, else (or on failure) a labelled rule."""
    global _warned_no_key
    t0 = time.perf_counter()
    try:
        llm = llm_mod.akashml()
        if llm is None:
            if not _warned_no_key:
                _warned_no_key = True
                logger.warning("quick_check: AKASHML_API_KEY is empty; verdicts are rule_only until it is set")
            return rule_verdict(inp)
        small = await llm_mod.resolve_model(llm, "small", timeout_s=DISCOVERY_TIMEOUT_S)
        if not small:
            return _fallback(inp, "no model id (set AKASHML_MODEL_SMALL; /v1/models failed or timed out)", t0)
        remaining = BUDGET_S - (time.perf_counter() - t0)
        if remaining < REPAIR_MIN_S:  # first-call model discovery ate the budget
            return _fallback(inp, f"model discovery took {BUDGET_S - remaining:.1f} s", t0)
        return await classify_with(llm, small, inp, budget_s=remaining)
    except Exception as exc:  # noqa: BLE001 - the seam must never raise into the checkpoint
        logger.exception("quick_check: unexpected error; rule fallback")
        return _fallback(inp, f"{type(exc).__name__}: {exc}", t0)


async def warmup() -> str:
    """Load the prompt and resolve the model before the first verdict (optional; detector startup)."""
    load_prompt()
    llm = llm_mod.akashml()
    if llm is not None:
        await llm_mod.resolve_models(llm)
    return classify_source()


def classify_source() -> str:
    """'akashml:<model>' when a key is configured ('?' until the model is resolved), else the rule label."""
    s = get_settings()
    if not s.akashml_api_key.strip():
        return "rule_only (no AKASHML_API_KEY)"
    small = s.akashml_model_small.strip()
    if not small:
        llm = llm_mod.akashml()
        ids = llm.models_cached if llm is not None else None
        small = llm_mod.pick_models(ids)[0] if ids else "?"
    return f"akashml:{small}"


# ---------------------------------------------------------------- CLI
def _cli() -> None:
    import asyncio
    from urllib.parse import urlsplit

    import typer

    from tripwire.contracts import Policy, Scenario

    def main(fixture: Path, rule: str = "secret_theft", agent_id: str = "", context: str = "") -> None:
        """Classify a fixture's steps for one agent, as the detector would see them (no checkpoint needed)."""
        sc = Scenario.model_validate_json(fixture.read_text(encoding="utf-8"))
        agents = sorted({st.agent_id for st in sc.steps})
        agent = agent_id or (agents[0] if agents else "deploy-bot")
        internal = {h.lower() for h in Policy().internal_hosts}
        base = int(time.time() * 1000) - (sc.steps[-1].offset_ms if sc.steps else 0)
        events = []
        for st in sc.steps:
            if st.agent_id != agent:
                continue
            host = ""
            if st.action in ("http_post", "http_get"):
                host = (urlsplit(st.target if "://" in st.target else "//" + st.target).hostname or "").lower()
            events.append(
                {
                    "ts_ms": base + st.offset_ms,
                    "action": st.action,
                    "target": st.target,
                    "result": "ok",
                    "reason": "",
                    "is_external": 1 if host and host not in internal else 0,
                    "bytes": st.bytes or len(st.payload.encode("utf-8")),
                    "tainted_by": st.tainted_by,
                }
            )
        async def run() -> tuple[Verdict, str]:
            v = await classify(QuickCheckInput(agent_id=agent, rule=rule, events=events, context=context))
            return v, classify_source()  # read the model cache on the loop that filled it

        v, source = asyncio.run(run())
        typer.echo(v.model_dump_json(indent=2))
        typer.echo(f"classify_source: {source}  events: {len(events)}  agent: {agent}")

    typer.run(main)


if __name__ == "__main__":
    _cli()

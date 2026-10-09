"""Two-model quorum (master plan D1): two AkashML model families must agree before an auto-quarantine.

    from ai.quorum import classify_quorum
    verdict = await classify_quorum(inp)                 # same shape as ai.quick_check.classify (ClassifyFn)

classify_quorum(inp, *, budget_s=3.4) asks the SMALL and the LARGE AkashML model (settings
AKASHML_MODEL_SMALL/LARGE, else ai.llm.resolve_models discovery) the quick-check question
concurrently, each bounded by budget_s, and combines the answers truthfully:
  both answered, same label     -> that verdict, confidence = min of the two, decision_source "quorum",
                                   model_ids = [small, large], latency_ms = max, tokens summed
  both answered, different      -> "uncertain" 0.5, decision_source "quorum" (human review), the reason
                                   names both verdicts
  exactly one answered          -> that model's verdict, decision_source "akashml", model_ids = [it]
                                   (the reason notes which model failed and why)
  none answered (no key, no ids, both failed/timed out)
                                -> ai.rules.rule_verdict(inp), decision_source "rule_only",
                                   reason prefixed "model unavailable: "
A model "answered" only when it returned a valid {"verdict","confidence","reason"} JSON (one repair
turn is tried when time remains, as in ai.quick_check). When the two configured ids are the same
model, only one call is made and the result is labelled "akashml" — a quorum needs two families.
The prompt and the untrusted-events handling are ai.quick_check's (build_messages / parse_verdict).
Never raises: any unexpected error degrades to the labelled rule verdict.

CLI (classifies a fixture's events for one agent, prints both model answers and the combined verdict):
    uv run python -m ai.quorum fixtures/secret_theft.json
    uv run python -m ai.quorum fixtures/normal_ops.json --agent-id support-bot --rule secret_theft --budget-s 5
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from loguru import logger

from ai import llm as llm_mod
from ai import quick_check as qc
from ai.llm import LLM
from ai.rules import rule_verdict
from tripwire.contracts import QuickCheckInput, Verdict

BUDGET_S = 3.4
DISCOVERY_TIMEOUT_S = 2.0
REPAIR_MIN_S = 0.8
MAX_TOKENS = 600  # gpt-oss-style models count their reasoning here; 400 is the measured floor
MAX_REASON = 400
DISAGREE_CONFIDENCE = 0.5


@dataclass
class ModelAnswer:
    """One model's attempt: verdict is None when it did not produce a valid verdict JSON."""

    model: str
    verdict: Verdict | None = None
    error: str | None = None
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def answered(self) -> bool:
        return self.verdict is not None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.model_dump() if self.verdict is not None else None
        return d


@dataclass
class QuorumResult:
    verdict: Verdict
    answers: list[ModelAnswer]
    elapsed_ms: float


def _cut(text: str, n: int = MAX_REASON) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 3] + "..."


async def ask_model(llm: LLM, model: str, inp: QuickCheckInput, timeout_s: float) -> ModelAnswer:
    """One quick-check call (+ one repair turn when time remains). Never raises."""
    t0 = time.perf_counter()
    ans = ModelAnswer(model=model)
    try:
        messages = qc.build_messages(inp)
        r = await llm.chat_json(messages, model=model, timeout_s=max(0.05, timeout_s), max_tokens=MAX_TOKENS)
        ans.tokens_in, ans.tokens_out = r.tokens_in, r.tokens_out
        parsed = None if r.error else qc.parse_verdict(r.obj)
        if r.error:
            ans.error = r.error
        elif parsed is None:
            remaining = timeout_s - (time.perf_counter() - t0)
            if remaining < REPAIR_MIN_S:
                ans.error = "unparseable model output, no time to repair"
            else:
                repair = messages + [
                    {"role": "assistant", "content": r.text[:2000] or "(empty)"},
                    {"role": "user", "content": qc.REPAIR_MESSAGE},
                ]
                r2 = await llm.chat_json(repair, model=model, timeout_s=remaining, max_tokens=MAX_TOKENS)
                ans.tokens_in += r2.tokens_in
                ans.tokens_out += r2.tokens_out
                parsed = None if r2.error else qc.parse_verdict(r2.obj)
                if r2.error:
                    ans.error = r2.error
                elif parsed is None:
                    ans.error = "unparseable model output after repair"
        ans.latency_ms = round((time.perf_counter() - t0) * 1000, 3)
        if parsed is not None:
            label, conf, reason = parsed
            ans.verdict = Verdict(
                verdict=label,
                confidence=conf,
                reason=reason,
                decision_source="akashml",
                model_ids=[model],
                latency_ms=ans.latency_ms,
                tokens_in=ans.tokens_in,
                tokens_out=ans.tokens_out,
            )
    except Exception as exc:  # noqa: BLE001 - one model crashing must not sink the quorum
        ans.latency_ms = round((time.perf_counter() - t0) * 1000, 3)
        ans.error = f"{type(exc).__name__}: {str(exc)[:80]}"
    return ans


def combine(inp: QuickCheckInput, answers: list[ModelAnswer], elapsed_ms: float) -> Verdict:
    """The quorum decision over the per-model answers (pure; see the module docstring)."""
    got = [a for a in answers if a.answered]
    failed = [a for a in answers if not a.answered]
    tokens_in = sum(a.tokens_in for a in answers)
    tokens_out = sum(a.tokens_out for a in answers)
    latency = max([a.latency_ms for a in answers] + [0.0])

    if len(got) >= 2:
        a, b = got[0], got[1]
        va, vb = a.verdict, b.verdict
        assert va is not None and vb is not None
        if va.verdict == vb.verdict:
            return Verdict(
                verdict=va.verdict,
                confidence=min(va.confidence, vb.confidence),
                reason=_cut(f"quorum: both models say {va.verdict}. {a.model}: {va.reason} | {b.model}: {vb.reason}"),
                decision_source="quorum",
                model_ids=[a.model, b.model],
                latency_ms=latency,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        return Verdict(
            verdict="uncertain",
            confidence=DISAGREE_CONFIDENCE,
            reason=_cut(
                f"quorum: models disagree, human review needed. {a.model} says {va.verdict} "
                f"({va.confidence:.2f}): {va.reason} | {b.model} says {vb.verdict} ({vb.confidence:.2f}): {vb.reason}"
            ),
            decision_source="quorum",
            model_ids=[a.model, b.model],
            latency_ms=latency,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    if len(got) == 1:
        a = got[0]
        v = a.verdict
        assert v is not None
        note = "; ".join(f"{f.model} failed ({f.error})" for f in failed)
        reason = f"{v.reason} (quorum incomplete: {note})" if note else v.reason
        return v.model_copy(
            update={
                "reason": _cut(reason),
                "decision_source": "akashml",
                "model_ids": [a.model],
                "latency_ms": latency,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            }
        )

    why = "; ".join(f"{f.model or '?'} {f.error}" for f in failed) or "no model id configured or discoverable"
    rule = rule_verdict(inp)
    return rule.model_copy(
        update={
            "reason": _cut(f"model unavailable: {why}: {rule.reason}"),
            "decision_source": "rule_only",
            "model_ids": [],
            "latency_ms": round(elapsed_ms, 3),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
    )


async def quorum_with(
    llm_small: LLM | None,
    small: str,
    llm_large: LLM | None,
    large: str,
    inp: QuickCheckInput,
    budget_s: float = BUDGET_S,
) -> QuorumResult:
    """Testable core: run the two models concurrently within budget_s and combine. Never raises.

    llm_small / llm_large are usually the same AkashML client; tests pass one per fake app. A model
    with no client or an empty id is skipped (counted as "did not answer"). Identical ids -> one call.
    """
    t0 = time.perf_counter()
    jobs: list[tuple[LLM, str]] = []
    if llm_small is not None and small:
        jobs.append((llm_small, small))
    if llm_large is not None and large and large != small:
        jobs.append((llm_large, large))
    answers: list[ModelAnswer] = []
    if jobs:
        try:
            answers = list(
                await asyncio.wait_for(
                    asyncio.gather(*(ask_model(llm, m, inp, budget_s) for llm, m in jobs)),
                    timeout=budget_s + 1.0,  # defensive outer bound; chat_json already honours budget_s
                )
            )
        except asyncio.TimeoutError:
            answers = [ModelAnswer(model=m, error=f"timeout after {budget_s:g} s") for _, m in jobs]
    if not jobs:
        answers = [ModelAnswer(model="", error="no model id")]
    elapsed_ms = (time.perf_counter() - t0) * 1000
    verdict = combine(inp, answers, elapsed_ms)
    logger.debug(
        f"quorum: {inp.agent_id}/{inp.rule} -> {verdict.verdict} {verdict.confidence:.2f} "
        f"({verdict.decision_source}, models={verdict.model_ids}) in {elapsed_ms:.0f} ms"
    )
    return QuorumResult(verdict=verdict, answers=answers, elapsed_ms=round(elapsed_ms, 3))


async def run_quorum(inp: QuickCheckInput, *, budget_s: float = BUDGET_S) -> QuorumResult:
    """Resolve the AkashML client + the two model ids from settings, then quorum_with(). Never raises."""
    t0 = time.perf_counter()
    try:
        llm = llm_mod.akashml()
        if llm is None:
            answers = [ModelAnswer(model="", error="AKASHML_API_KEY is empty")]
            return QuorumResult(combine(inp, answers, 0.0), answers, 0.0)
        small, large = await llm_mod.resolve_models(llm, timeout_s=min(DISCOVERY_TIMEOUT_S, budget_s))
        remaining = budget_s - (time.perf_counter() - t0)
        if remaining < REPAIR_MIN_S:
            answers = [ModelAnswer(model=small or large, error=f"model discovery took {budget_s - remaining:.1f} s")]
            return QuorumResult(combine(inp, answers, (time.perf_counter() - t0) * 1000), answers, 0.0)
        return await quorum_with(llm, small, llm, large, inp, budget_s=remaining)
    except Exception as exc:  # noqa: BLE001 - the seam never raises into the detector / hold path
        logger.exception("quorum: unexpected error; rule fallback")
        answers = [ModelAnswer(model="", error=f"{type(exc).__name__}: {str(exc)[:80]}")]
        return QuorumResult(combine(inp, answers, (time.perf_counter() - t0) * 1000), answers, 0.0)


async def classify_quorum(inp: QuickCheckInput, *, budget_s: float = BUDGET_S) -> Verdict:
    """ClassifyFn-compatible entry point (detection.loop --quorum): the combined Verdict only."""
    return (await run_quorum(inp, budget_s=budget_s)).verdict


# ---------------------------------------------------------------- CLI
def events_from_scenario(path: Any, agent_id: str = "") -> tuple[str, list[dict[str, Any]]]:
    """(agent, events) as the detector would see a fixture's steps for one agent (no checkpoint needed)."""
    from tripwire.contracts import Policy, Scenario

    sc = Scenario.model_validate_json(Path(path).read_text(encoding="utf-8"))
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
    return agent, events


def _cli() -> None:
    import typer

    def main(
        fixture: Path,
        rule: str = "secret_theft",
        agent_id: str = "",
        context: str = "",
        budget_s: float = BUDGET_S,
    ) -> None:
        """Run the two-model quorum over a fixture's events; prints each model's answer and the decision."""
        agent, events = events_from_scenario(fixture, agent_id)
        inp = QuickCheckInput(agent_id=agent, rule=rule, events=events, context=context)
        res = asyncio.run(run_quorum(inp, budget_s=budget_s))
        for a in res.answers:
            v = a.verdict
            if v is not None:
                typer.echo(
                    f"{a.model}: {v.verdict} {v.confidence:.2f} in {a.latency_ms:.0f} ms "
                    f"(tokens {a.tokens_in}/{a.tokens_out}) - {v.reason}"
                )
            else:
                typer.echo(f"{a.model or '?'}: no answer ({a.error}) after {a.latency_ms:.0f} ms")
        typer.echo(json.dumps(res.verdict.model_dump(), indent=2))
        typer.echo(f"agent: {agent}  events: {len(events)}  elapsed_ms: {res.elapsed_ms}")

    typer.run(main)


if __name__ == "__main__":
    _cli()

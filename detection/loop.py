"""Tripwire detector: ClickHouse funnel -> classify() -> checkpoint /block or /alerts -> /heartbeat.

Every ~1 s: GET /status (blocked agents, verdict keys, per-agent watermarks) -> run detection/sql/funnel.sql
(and the optional rules) -> for each new hit fetch the agent's recent rows -> classify (AkashML-capable
ai.quick_check.classify, or the deterministic rule labelled rule_only) -> POST /block/{agent} when the
verdict is malicious with confidence >= threshold, otherwise POST /alerts -> POST /heartbeat with the
measured query timings. The detector holds no shared state: it only reads ClickHouse and calls the
checkpoint over HTTP (master plan §2, §4, §5).

Run from the repo root:
    uv run python -m detection.loop                                  # forever, every 1 s
    uv run python -m detection.loop --once                           # one iteration, JSON summary; exit 1 on error
    uv run python -m detection.loop --rules secret_theft,baseline_novelty,role_grab,log_tamper
    uv run python -m detection.loop --interval 1.0 --threshold 0.8 --window-s 300 --checkpoint-url http://localhost:8000
    uv run python -m detection.loop --no-outbreak --no-investigator     # containment only, no post-block hooks
    uv run python -m detection.loop --quorum                            # two-model quorum (ai.quorum) when importable

Post-containment hooks (master §1 "Traces" + "Explains"): after a successful POST /block the detector schedules
a background task that runs detection.outbreak.run_outbreak (10 s budget: patient zero, exposed agents ->
heightened, attacker hosts -> fleet denylist) and then ai.investigator.investigate (30 s budget: the markdown
report with SQL receipts). Incidents the checkpoint contained on its own (hold mode, honeytoken, policy) get the
same hooks from the open-incident sweep. Hooks never block or crash the loop; they are counted in the summary
and heartbeat (outbreaks, reports, hook_errors) and awaited on shutdown. The in-process run_once(client) and a
bare Detector(ch, cp, classify) keep the hooks OFF (the CLI turns them on; pass outbreak=True / investigator=True).

Honesty: decision_source is whatever classify() returned ('akashml' only when a model answered, 'quorum' only
when ai.quorum had two models agree); every fallback decision made here is labelled rule_only and its reason
starts with "model unavailable: ". Every AlertPayload (block and alert) carries the verdict's measured
latency_ms / tokens_in / tokens_out, which the checkpoint copies into the incident Verdict.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx
import typer
from loguru import logger

from detection.metrics import RollingSamples, Timer, make_heartbeat
from detection.sql_loader import load_sql, watermark_params
from tripwire.config import get_settings
from tripwire.contracts import (
    RULE_BASELINE,
    RULE_HOLD,
    RULE_LOG_TAMPER,
    RULE_ROLE_GRAB,
    RULE_SECRET_THEFT,
    AlertPayload,
    ClassifyFn,
    Heartbeat,
    QuickCheckInput,
    Verdict,
)

SQL_FOR_RULE: dict[str, str] = {
    RULE_SECRET_THEFT: "funnel",
    RULE_BASELINE: "baseline",
    RULE_ROLE_GRAB: "role_grab",
    RULE_LOG_TAMPER: "log_tamper",
}
DEFAULT_RULES: tuple[str, ...] = (RULE_SECRET_THEFT,)
CLASSIFY_TIMEOUT_S = 3.5
RULE_WINDOW_MS = 60_000
SEEN_MAX = 5000
PENDING_MAX = 1000
RETRY_AFTER_S = 5.0  # re-send a decided verdict this long after a failed checkpoint POST (no re-classify)
ERROR_LOG_EVERY_S = 30.0
# Post-containment hooks (background tasks; they never block the loop): outbreak trace, then the investigator.
OUTBREAK_TIMEOUT_S = 10.0
INVESTIGATE_TIMEOUT_S = 30.0
HOOK_DRAIN_S = 5.0  # shutdown: how long run_forever / the CLI wait for hooks still running
RUN_ONCE_HOOK_DRAIN_S = 45.0  # module-level run_once(hooks=True) waits for the hooks it scheduled
SWEEP_MAX_AGE_MS = 15 * 60_000  # checkpoint-contained incidents older than this are left alone
HOOKED_MAX = 2000

RuleFn = Callable[[QuickCheckInput], Verdict]


def now_ms() -> int:
    return time.time_ns() // 1_000_000


# ---------------------------------------------------------------------------
# Deterministic fallback (same contract as ai.rules.rule_verdict; used only when that import fails)
# ---------------------------------------------------------------------------


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _ts(e: dict[str, Any]) -> int:
    return _int(e.get("ts_ms"))


def _secret_theft_chain(events: list[dict[str, Any]], need_post: bool) -> bool:
    env_reads = [_ts(e) for e in events if e.get("action") == "read_file" and ".env" in str(e.get("target", ""))]
    cmds = [_ts(e) for e in events if e.get("action") == "run_command" and "base64" in str(e.get("target", ""))]
    posts = [_ts(e) for e in events if e.get("action") == "http_post" and _int(e.get("is_external")) == 1]
    for t1 in env_reads:
        for t2 in cmds:
            if not 0 <= t2 - t1 <= RULE_WINDOW_MS:
                continue
            if not need_post or any(t2 <= t3 <= t1 + RULE_WINDOW_MS for t3 in posts):
                return True
    return False


def local_rule_verdict(inp: QuickCheckInput) -> Verdict:
    """Pure, deterministic verdict (decision_source rule_only). Mirrors the frozen ai.rules.rule_verdict:
    secret_theft/hold: malicious 0.9 iff read_file(.env) -> run_command(base64) within 60 s
    (secret_theft also needs an external http_post after it); baseline_novelty -> uncertain 0.5;
    role_grab -> malicious 0.85 iff an assume_role is present; log_tamper -> same for disable_logging."""
    events = sorted((e for e in inp.events if isinstance(e, dict)), key=_ts)
    rule = inp.rule
    if rule in (RULE_SECRET_THEFT, RULE_HOLD):
        need_post = rule == RULE_SECRET_THEFT
        if _secret_theft_chain(events, need_post=need_post):
            what = "read_file of a .env target followed within 60 s by a base64 run_command"
            if need_post:
                what += " and an external http_post"
            return Verdict(verdict="malicious", confidence=0.9, reason=f"rule: {what}", decision_source="rule_only")
        return Verdict(
            verdict="benign",
            confidence=0.5,
            reason="rule: no .env read -> base64 command sequence within 60 s",
            decision_source="rule_only",
        )
    if rule == RULE_BASELINE:
        return Verdict(
            verdict="uncertain",
            confidence=0.5,
            reason="rule: first-seen action/target for this agent; no deterministic verdict",
            decision_source="rule_only",
        )
    if rule in (RULE_ROLE_GRAB, RULE_LOG_TAMPER):
        action = "assume_role" if rule == RULE_ROLE_GRAB else "disable_logging"
        if any(e.get("action") == action for e in events):
            return Verdict(
                verdict="malicious", confidence=0.85, reason=f"rule: {action} present", decision_source="rule_only"
            )
        return Verdict(verdict="benign", confidence=0.5, reason=f"rule: no {action}", decision_source="rule_only")
    return Verdict(verdict="benign", confidence=0.5, reason=f"rule: unknown rule {rule!r}", decision_source="rule_only")


def resolve_rule_fallback() -> tuple[RuleFn, str]:
    """ai.rules.rule_verdict when importable, else the local copy above. Never calls a model."""
    try:
        from ai.rules import rule_verdict  # type: ignore[import-not-found]

        return rule_verdict, "ai.rules.rule_verdict"
    except Exception as exc:  # noqa: BLE001  (lane being built concurrently; any import error -> local copy)
        logger.debug(f"detector: ai.rules not importable ({exc!r}); using the local rule copy")
        return local_rule_verdict, "detection.loop.local_rule_verdict"


def resolve_classify(quorum: bool = False) -> tuple[ClassifyFn, str]:
    """ai.quick_check.classify when importable (AkashML-capable), else the rule fallback wrapped async.
    With quorum=True, ai.quorum.classify_quorum (two AkashML model families must agree) is preferred and the
    single classify is the fallback when it is not importable. Importing never calls a model; the returned
    label is logged at startup."""
    if quorum:
        try:
            from ai.quorum import classify_quorum  # type: ignore[import-not-found]

            return classify_quorum, "ai.quorum.classify_quorum"
        except Exception as exc:  # noqa: BLE001  (lane being built concurrently; any import error -> single model)
            logger.warning(f"detector: --quorum requested but ai.quorum is not importable ({exc!r}); single classify")
    try:
        from ai.quick_check import classify  # type: ignore[import-not-found]

        return classify, "ai.quick_check.classify"
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"detector: ai.quick_check not importable ({exc!r}); classify = rule fallback")
    rule_fn, label = resolve_rule_fallback()

    async def _rule_classify(inp: QuickCheckInput) -> Verdict:
        return rule_fn(inp)

    return _rule_classify, f"{label} (rule_only)"


# ---------------------------------------------------------------------------
# Collaborators: ClickHouse adapter + checkpoint HTTP client
# ---------------------------------------------------------------------------


class ClickHouseAdapter:
    """`await ch.query(sql, parameters) -> list[dict]` over tripwire.ch.async_client().

    Connects lazily; a failed call drops the client so the next call reconnects. `last_summary`
    keeps the server summary of the last query (read_rows etc.) for receipts."""

    def __init__(self, timeout_s: float = 10.0) -> None:
        self.timeout_s = timeout_s
        self._client: Any = None
        self.last_summary: dict[str, Any] = {}

    async def query(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        try:
            if self._client is None:
                from tripwire.ch import async_client

                self._client = await asyncio.wait_for(async_client(), self.timeout_s)
            res = await asyncio.wait_for(self._client.query(sql, parameters=parameters or {}), self.timeout_s)
        except Exception:
            self._client = None
            raise
        self.last_summary = dict(getattr(res, "summary", None) or {})
        cols = list(res.column_names)
        return [dict(zip(cols, row, strict=False)) for row in res.result_rows]

    async def close(self) -> None:
        c, self._client = self._client, None
        if c is not None:
            try:
                await c.close()
            except Exception:  # noqa: BLE001
                pass


@dataclass
class CheckpointView:
    """The parts of GET /status the detector uses."""

    blocked: set[str] = field(default_factory=set)
    verdict_keys: set[str] = field(default_factory=set)
    watermarks: dict[str, int] = field(default_factory=dict)
    open_incidents: list[dict[str, Any]] = field(default_factory=list)  # contracts.Incident dicts


class CheckpointHTTP:
    """Thin httpx client for the checkpoint. Always sends X-Tripwire-Token (harmless when PUBLIC=0)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_s: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Pass `client` to reuse an existing httpx.AsyncClient (e.g. an in-process ASGITransport client
        from tests/e2e); it is then not closed by close(). Otherwise a client is built from base_url."""
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._headers = {"X-Tripwire-Token": token}
        self._owned = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers,
            timeout=timeout_s,
            transport=transport,
        )

    @classmethod
    def from_client(cls, client: httpx.AsyncClient, token: str = "") -> "CheckpointHTTP":
        return cls(str(client.base_url) or "http://checkpoint", token, client=client)

    @property
    def http(self) -> httpx.AsyncClient:
        """The underlying httpx client (the hooks hand it to detection.outbreak / ai.investigator)."""
        return self._client

    async def status(self) -> CheckpointView:
        resp = await self._client.get("/status", headers=self._headers)
        resp.raise_for_status()
        data = resp.json()
        return CheckpointView(
            blocked=set(data.get("blocked") or []),
            verdict_keys=set(data.get("verdict_keys") or []),
            watermarks={str(k): _int(v) for k, v in (data.get("watermarks") or {}).items()},
            open_incidents=[i for i in (data.get("open_incidents") or []) if isinstance(i, dict)],
        )

    async def block(self, agent_id: str, payload: AlertPayload) -> httpx.Response:
        return await self._client.post(f"/block/{agent_id}", json=payload.model_dump(), headers=self._headers)

    async def alert(self, payload: AlertPayload) -> httpx.Response:
        return await self._client.post("/alerts", json=payload.model_dump(), headers=self._headers)

    async def heartbeat(self, hb: Heartbeat) -> httpx.Response:
        return await self._client.post("/heartbeat", json=hb.model_dump(), headers=self._headers)

    async def close(self) -> None:
        if self._owned:
            await self._client.aclose()


class _Throttle:
    """Log one kind of failure at WARNING at most once per `every_s`; the repeats go to DEBUG."""

    def __init__(self, every_s: float = ERROR_LOG_EVERY_S) -> None:
        self.every_s = every_s
        self._last: dict[str, float] = {}

    def warn(self, kind: str, msg: str) -> None:
        now = time.monotonic()
        last = self._last.get(kind)
        if last is None or now - last >= self.every_s:
            self._last[kind] = now
            logger.warning(msg)
        else:
            logger.debug(msg)

    def clear(self, kind: str) -> None:
        if self._last.pop(kind, None) is not None:
            logger.info(f"detector: {kind} is back")


# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------


class Detector:
    """One detection pass per run_once(); run_forever() schedules it. All collaborators are injected."""

    def __init__(
        self,
        ch: Any,
        checkpoint: CheckpointHTTP,
        classify: ClassifyFn | None = None,
        *,
        fallback: RuleFn | None = None,
        threshold: float = 0.8,
        window_s: int = 300,
        rules: tuple[str, ...] | list[str] = DEFAULT_RULES,
        classify_timeout_s: float = CLASSIFY_TIMEOUT_S,
        seen_max: int = SEEN_MAX,
        retry_after_s: float = RETRY_AFTER_S,
        outbreak: bool = False,
        investigator: bool = False,
        quorum: bool = False,
    ) -> None:
        """outbreak / investigator: run the post-containment hooks (background tasks) after every block the
        detector lands and for incidents the checkpoint contained itself. They default to OFF so an in-process
        Detector built by a test (Bindu's e2e builds Detector(ch, cp, classify) directly and never drains the
        hooks) cannot mutate the shared fleet policy between runs; `python -m detection.loop` turns both on
        unless --no-outbreak / --no-investigator is given. quorum: use ai.quorum.classify_quorum when no
        classify is injected (falls back to the single classify when ai.quorum is not importable)."""
        unknown = [r for r in rules if r not in SQL_FOR_RULE]
        if unknown:
            raise ValueError(f"unknown rules {unknown}; known: {sorted(SQL_FOR_RULE)}")
        self.ch = ch
        self.checkpoint = checkpoint
        self.quorum = bool(quorum)
        if classify is None:
            classify, self.classify_source = resolve_classify(quorum=self.quorum)
        else:
            self.classify_source = getattr(classify, "__qualname__", None) or type(classify).__name__
        self.classify: ClassifyFn = classify
        self.outbreak_enabled = bool(outbreak)
        self.investigator_enabled = bool(investigator)
        # Hooks: tracked background tasks (awaited by drain_hooks), cumulative counters, incident ids already
        # hooked in this process (LRU) so the open-incident sweep runs each incident's hooks once.
        self._hook_tasks: set[asyncio.Task[Any]] = set()
        self.hook_stats: dict[str, int] = {"scheduled": 0, "outbreaks": 0, "reports": 0, "hook_errors": 0}
        self._hooked: OrderedDict[str, None] = OrderedDict()
        if fallback is None:
            fallback, self.fallback_source = resolve_rule_fallback()
        else:
            self.fallback_source = getattr(fallback, "__qualname__", None) or type(fallback).__name__
        self.fallback: RuleFn = fallback
        self.threshold = float(threshold)
        self.window_s = int(window_s)
        self.rules: tuple[str, ...] = tuple(rules)
        self.classify_timeout_s = float(classify_timeout_s)
        self.retry_after_s = float(retry_after_s)
        # Documented caches: keys already acted on (LRU set), decided verdicts whose checkpoint POST failed
        # (key -> (payload, retry-at monotonic s); re-sent as-is, never re-classified) and rolling timings.
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.seen_max = seen_max
        self._pending: OrderedDict[str, tuple[AlertPayload, float]] = OrderedDict()
        self.iteration = 0
        self.funnel_samples = RollingSamples(600)
        self.classify_samples = RollingSamples(200)
        self._throttle = _Throttle()

    # ---- helpers -----------------------------------------------------------------
    def _remember(self, key: str) -> None:
        self._pending.pop(key, None)
        self.seen[key] = None
        self.seen.move_to_end(key)
        while len(self.seen) > self.seen_max:
            self.seen.popitem(last=False)

    def _defer(self, key: str, payload: AlertPayload) -> None:
        """Keep a decided verdict for re-sending after retry_after_s (the checkpoint did not accept it)."""
        self._pending[key] = (payload, time.monotonic() + self.retry_after_s)
        self._pending.move_to_end(key)
        while len(self._pending) > PENDING_MAX:
            self._pending.popitem(last=False)

    async def _classify(self, inp: QuickCheckInput) -> tuple[Verdict, float, bool]:
        """(verdict, latency_ms, fell_back). Any exception or timeout -> rule_only fallback."""
        t0 = time.perf_counter()
        try:
            raw = await asyncio.wait_for(self.classify(inp), self.classify_timeout_s)
            verdict = raw if isinstance(raw, Verdict) else Verdict.model_validate(raw)
            return verdict, round((time.perf_counter() - t0) * 1000, 3), False
        except Exception as exc:  # noqa: BLE001  (TimeoutError included)
            latency = round((time.perf_counter() - t0) * 1000, 3)
            why = "timeout" if isinstance(exc, asyncio.TimeoutError) else f"{type(exc).__name__}: {str(exc)[:120]}"
            logger.warning(f"detector: classify failed for {inp.agent_id}/{inp.rule} ({why}); rule_only fallback")
            try:
                rule_v = self.fallback(inp)
            except Exception as exc2:  # noqa: BLE001  (a broken fallback must not stop containment)
                logger.error(f"detector: fallback {self.fallback_source} raised {exc2!r}; using the local rule")
                rule_v = local_rule_verdict(inp)
            verdict = rule_v.model_copy(
                update={
                    "decision_source": "rule_only",
                    "reason": f"model unavailable: {rule_v.reason}",
                    "model_ids": [],
                    "latency_ms": latency,
                }
            )
            return verdict, latency, True

    @staticmethod
    def _hits_for(rule: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Funnel rows are one per agent already; the other rules collapse to one hit per agent
        (the newest row) so a burst of novel rows costs one classify call, not one per row."""
        if rule == RULE_SECRET_THEFT:
            return [r for r in rows if r.get("agent_id")]
        per_agent: dict[str, dict[str, Any]] = {}
        for r in rows:
            agent = r.get("agent_id")
            if not agent:
                continue
            cur = per_agent.get(agent)
            if cur is None:
                per_agent[agent] = {**r, "last_step_ts_ms": _int(r.get("ts_ms")), "n_rows": 1, "targets": [r.get("target")]}
            else:
                cur["last_step_ts_ms"] = max(cur["last_step_ts_ms"], _int(r.get("ts_ms")))
                cur["n_rows"] += 1
                cur["targets"].append(r.get("target"))
        return list(per_agent.values())

    @staticmethod
    def _context(rule: str, hit: dict[str, Any]) -> str:
        if rule == RULE_SECRET_THEFT:
            return (
                "Tripwire detector; funnel matched: read_file(.env) -> run_command(base64) -> external http_post "
                f"within 60 s (first_step_ts_ms={hit.get('first_step_ts_ms')}, "
                f"last_step_ts_ms={hit.get('last_step_ts_ms')}, n_events={hit.get('n_events')})"
            )
        targets = ", ".join(str(t) for t in (hit.get("targets") or [hit.get("target")])[:5])
        return f"Tripwire detector; rule {rule} matched {hit.get('n_rows', 1)} row(s) in the window: {targets}"

    async def _handle_hit(
        self,
        rule: str,
        hit: dict[str, Any],
        view: CheckpointView,
        detected_at_ms: int,
        summary: dict[str, Any],
    ) -> None:
        agent = str(hit["agent_id"])
        last_step = _int(hit.get("last_step_ts_ms"))
        wm = view.watermarks.get(agent, 0)
        if agent in view.blocked:
            summary["skipped"] += 1
            logger.debug(f"detector: {agent} already blocked; skip {rule}@{last_step}")
            return
        key = f"{agent}|{rule}|{last_step}"
        if key in view.verdict_keys or key in self.seen or last_step <= wm:
            self._pending.pop(key, None)  # the checkpoint has it (or it is history): nothing left to send
            summary["skipped"] += 1
            logger.debug(f"detector: {key} already decided (or <= watermark {wm}); skip")
            return

        pending = self._pending.get(key)
        latency_ms: float | None = None
        inp: QuickCheckInput | None = None
        if pending is not None:
            # Decided earlier; the checkpoint did not accept the POST. Re-send the same verdict after the
            # backoff -- no second recent_events query and no second classify call for the same hit.
            payload, retry_at = pending
            if time.monotonic() < retry_at:
                summary["skipped"] += 1
                return
            summary["retries"] += 1
        else:
            with Timer() as t_ev:
                events = await self.ch.query(
                    load_sql("recent_events"), {"agent": agent, "wm": wm, "window_s": self.window_s}
                )
            summary["timings_ms"]["recent_events"] = t_ev.ms
            inp = QuickCheckInput(agent_id=agent, rule=rule, events=events, context=self._context(rule, hit))
            verdict, latency_ms, fell_back = await self._classify(inp)
            self.classify_samples.add(latency_ms)
            summary["classify_latency_ms"] = latency_ms
            summary["classify_source"] = verdict.decision_source
            summary["fallbacks"] += int(fell_back)
            # Contract (AlertPayload, 11:55 CCR): carry the Verdict's measured cost into every /block and /alerts
            # so the incident Verdict never shows 0 beside a real model id. A verdict without its own latency
            # (rule_only, or a classify that did not time itself) gets the wall time measured here.
            payload = AlertPayload(
                agent_id=agent,
                rule=rule,
                verdict=verdict.verdict,
                confidence=float(verdict.confidence),
                reason=verdict.reason,
                decision_source=verdict.decision_source,
                detected_at_ms=detected_at_ms,
                last_step_ts_ms=last_step,
                model_ids=list(verdict.model_ids),
                latency_ms=float(verdict.latency_ms) if verdict.latency_ms > 0 else float(latency_ms),
                tokens_in=max(0, _int(verdict.tokens_in)),
                tokens_out=max(0, _int(verdict.tokens_out)),
            )

        blocking = payload.verdict == "malicious" and payload.confidence >= self.threshold
        what = "block" if blocking else "alert"
        try:
            resp = await (self.checkpoint.block(agent, payload) if blocking else self.checkpoint.alert(payload))
        except Exception as exc:  # noqa: BLE001  (checkpoint down / timeout)
            summary["http_errors"] += 1
            self._defer(key, payload)
            self._throttle.warn(
                "checkpoint_post",
                f"detector: {what} {key} failed: {exc!r}; re-sending in {self.retry_after_s:.0f}s",
            )
            return
        if resp.status_code == 409:
            logger.info(f"detector: checkpoint 409 for {key} ({resp.text[:160]}); remembered")
            summary["conflicts"] += 1
        elif resp.is_error:
            # 401 (token mismatch under PUBLIC=1), 5xx, 422: the verdict stands; keep it and re-send later.
            summary["http_errors"] += 1
            self._defer(key, payload)
            self._throttle.warn(
                "checkpoint_post",
                f"detector: {what} {key} -> HTTP {resp.status_code} {resp.text[:160]}; "
                f"re-sending in {self.retry_after_s:.0f}s",
            )
            return
        else:
            summary["blocks" if blocking else "alerts"] += 1
            self._throttle.clear("checkpoint_post")
            logger.info(
                f"detector: {what.upper()} {agent} rule={rule} verdict={payload.verdict} "
                f"conf={payload.confidence:.2f} source={payload.decision_source} "
                f"ttd_ms={detected_at_ms - last_step} classify_ms={latency_ms}"
                f"{' (re-sent)' if pending is not None else ''}"
            )
            if blocking and self.hooks_enabled:
                incident_id = self._incident_id_of(resp)
                if incident_id and self._schedule_hooks(
                    agent, incident_id, inp, do_outbreak=self.outbreak_enabled, do_report=self.investigator_enabled
                ):
                    summary["hooks_scheduled"] += 1
                elif not incident_id:
                    logger.warning(f"detector: /block reply for {agent} carried no incident_id; hooks skipped")
        self._remember(key)

    # ---- post-containment hooks ---------------------------------------------------
    @property
    def hooks_enabled(self) -> bool:
        return self.outbreak_enabled or self.investigator_enabled

    @staticmethod
    def _incident_id_of(resp: httpx.Response) -> str:
        try:
            data = resp.json()
        except ValueError:
            return ""
        return str(data.get("incident_id") or "") if isinstance(data, dict) else ""

    def _mark_hooked(self, incident_id: str) -> None:
        self._hooked[incident_id] = None
        self._hooked.move_to_end(incident_id)
        while len(self._hooked) > HOOKED_MAX:
            self._hooked.popitem(last=False)

    def _schedule_hooks(
        self,
        agent: str,
        incident_id: str,
        inp: QuickCheckInput | None,
        *,
        do_outbreak: bool,
        do_report: bool,
    ) -> bool:
        """Start _after_block as a tracked background task. False when there is nothing to run."""
        if not incident_id or not (do_outbreak or do_report):
            return False
        self._mark_hooked(incident_id)
        task = asyncio.create_task(
            self._after_block(agent, incident_id, inp, do_outbreak=do_outbreak, do_report=do_report),
            name=f"tripwire-hooks-{incident_id}",
        )
        self._hook_tasks.add(task)
        task.add_done_callback(self._hook_tasks.discard)
        self.hook_stats["scheduled"] += 1
        return True

    async def _after_block(
        self,
        agent: str,
        incident_id: str,
        inp: QuickCheckInput | None,
        *,
        do_outbreak: bool = True,
        do_report: bool = True,
    ) -> None:
        """The hooks, in order: (a) detection.outbreak.run_outbreak, 10 s; (b) ai.investigator.investigate, 30 s.
        Both lazily imported, guarded and counted; a failure is logged (hook_errors) and never raised.
        `inp` is the QuickCheckInput the verdict was made from (None for swept incidents); logged only."""
        cp = self.checkpoint
        n_events = len(inp.events) if inp is not None else None
        logger.debug(f"detector: hooks for {incident_id} ({agent}, {n_events} events) outbreak={do_outbreak} report={do_report}")
        if do_outbreak:
            ob = None
            try:
                from detection.outbreak import run_outbreak

                ob = await asyncio.wait_for(
                    run_outbreak(incident_id, client=cp.http, ch=self.ch, token=cp.token), OUTBREAK_TIMEOUT_S
                )
            except Exception as exc:  # noqa: BLE001  (TimeoutError included)
                logger.warning(f"detector: outbreak hook for {incident_id} failed: {type(exc).__name__}: {str(exc)[:160]}")
            if ob is None:
                self.hook_stats["hook_errors"] += 1
            else:
                self.hook_stats["outbreaks"] += 1
                logger.info(
                    f"detector: OUTBREAK {incident_id} ({agent}) source={ob.source_id!r} exposed={ob.exposed_agents} "
                    f"denylist+={ob.blocked_destinations} query_ms={ob.query_ms}"
                )
        if do_report:
            try:
                from ai.investigator import investigate  # type: ignore[import-not-found]

                report = await asyncio.wait_for(
                    investigate(incident_id, client=cp.http, token=cp.token), INVESTIGATE_TIMEOUT_S
                )
            except Exception as exc:  # noqa: BLE001  (ImportError while the lane is being built, timeout, ...)
                self.hook_stats["hook_errors"] += 1
                logger.warning(f"detector: investigator hook for {incident_id} failed: {type(exc).__name__}: {str(exc)[:160]}")
            else:
                self.hook_stats["reports"] += 1
                logger.info(
                    f"detector: REPORT {incident_id} ({agent}) models={list(getattr(report, 'model_ids', []) or [])} "
                    f"receipts={len(getattr(report, 'receipts', []) or [])}"
                )

    def _sweep_incidents(self, view: CheckpointView, summary: dict[str, Any]) -> None:
        """Incidents the checkpoint contained on its own (hold mode, honeytoken, policy denylist) never pass
        through POST /block here, so they would get no trace and no report. Schedule the same hooks for open,
        contained, recent incidents that still lack them -- each incident once per detector process."""
        now = now_ms()
        for inc in view.open_incidents:
            inc_id, agent = str(inc.get("id") or ""), str(inc.get("agent_id") or "")
            if not inc_id or not agent or inc_id in self._hooked or inc.get("contained_ms") is None:
                continue
            if _int(inc.get("opened_ms")) < now - SWEEP_MAX_AGE_MS:
                self._mark_hooked(inc_id)  # history (e.g. restored checkpoint state): leave it alone
                continue
            need_outbreak = self.outbreak_enabled and inc.get("outbreak") is None
            need_report = self.investigator_enabled and not inc.get("report_md")
            if not (need_outbreak or need_report):
                self._mark_hooked(inc_id)
                continue
            if self._schedule_hooks(agent, inc_id, None, do_outbreak=need_outbreak, do_report=need_report):
                summary["hooks_scheduled"] += 1
                logger.info(
                    f"detector: incident {inc_id} ({agent}, rule {inc.get('rule')}) was contained by the checkpoint; "
                    f"running outbreak={need_outbreak} report={need_report}"
                )

    async def drain_hooks(self, timeout: float = HOOK_DRAIN_S) -> int:
        """Await the running hook tasks for up to `timeout` s; stragglers are cancelled. Returns how many were."""
        tasks = [t for t in self._hook_tasks if not t.done()]
        if not tasks:
            return 0
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            logger.warning(f"detector: {len(pending)} hook task(s) still running after {timeout:g} s; cancelled")
        return len(pending)

    # ---- one pass -----------------------------------------------------------------
    async def run_once(self) -> dict[str, Any]:
        """One detection pass. Never raises; errors are reported in the returned summary."""
        self.iteration += 1
        summary: dict[str, Any] = {
            "iteration": self.iteration,
            "ts_ms": now_ms(),
            "rules": list(self.rules),
            "hits": 0,
            "blocks": 0,
            "alerts": 0,
            "skipped": 0,
            "conflicts": 0,
            "http_errors": 0,
            "retries": 0,
            "fallbacks": 0,
            "hooks_scheduled": 0,
            "timings_ms": {},
            "classify_latency_ms": None,
            "classify_source": None,
            "error": None,
        }
        try:
            with Timer() as t_status:
                view = await self.checkpoint.status()
            summary["status_ms"] = t_status.ms
            self._throttle.clear("checkpoint")
        except Exception as exc:  # noqa: BLE001
            summary["error"] = f"checkpoint /status: {type(exc).__name__}: {str(exc)[:160]}"
            self._throttle.warn("checkpoint", f"detector: {summary['error']}")
            return summary
        summary["blocked_agents"] = sorted(view.blocked)

        params = {**watermark_params(view.watermarks), "window_s": self.window_s}
        for rule in self.rules:
            name = SQL_FOR_RULE[rule]
            try:
                with Timer() as t_q:
                    rows = await self.ch.query(load_sql(name), params)
                detected_at_ms = now_ms()
                summary["timings_ms"][name] = t_q.ms
                if name == "funnel":
                    self.funnel_samples.add(t_q.ms)
                    read_rows = (getattr(self.ch, "last_summary", None) or {}).get("read_rows")
                    if read_rows is not None:
                        summary["funnel_rows_read"] = _int(read_rows)
                self._throttle.clear("clickhouse")
            except Exception as exc:  # noqa: BLE001
                summary["error"] = f"clickhouse {name}: {type(exc).__name__}: {str(exc)[:160]}"
                self._throttle.warn("clickhouse", f"detector: {summary['error']}")
                continue
            hits = self._hits_for(rule, rows)
            summary["hits"] += len(hits)
            for hit in hits:
                try:
                    await self._handle_hit(rule, hit, view, detected_at_ms, summary)
                except Exception as exc:  # noqa: BLE001
                    summary["error"] = f"hit {rule}/{hit.get('agent_id')}: {type(exc).__name__}: {str(exc)[:160]}"
                    self._throttle.warn("hit", f"detector: {summary['error']}")

        if self.hooks_enabled:
            try:
                self._sweep_incidents(view, summary)
            except Exception as exc:  # noqa: BLE001  (a malformed incident dict must not stop the pass)
                self._throttle.warn("sweep", f"detector: incident sweep failed: {exc!r}")
        summary.update(
            {
                "outbreaks": self.hook_stats["outbreaks"],
                "reports": self.hook_stats["reports"],
                "hook_errors": self.hook_stats["hook_errors"],
                "hooks_running": len([t for t in self._hook_tasks if not t.done()]),
            }
        )
        await self._heartbeat(summary)
        return summary

    async def _heartbeat(self, summary: dict[str, Any]) -> None:
        metrics: dict[str, Any] = {
            "iteration": summary["iteration"],
            "hits": summary["hits"],
            "blocks": summary["blocks"],
            "alerts": summary["alerts"],
            "skipped": summary["skipped"],
            "conflicts": summary["conflicts"],
            "http_errors": summary["http_errors"],
            "retries": summary["retries"],
            "pending": len(self._pending),
            "fallbacks": summary["fallbacks"],
            "hooks_scheduled": summary["hooks_scheduled"],
            "outbreaks": summary.get("outbreaks", self.hook_stats["outbreaks"]),
            "reports": summary.get("reports", self.hook_stats["reports"]),
            "hook_errors": summary.get("hook_errors", self.hook_stats["hook_errors"]),
            "hooks_running": summary.get("hooks_running", 0),
            "hooks": {"outbreak": self.outbreak_enabled, "investigator": self.investigator_enabled, "quorum": self.quorum},
            "classify_latency_ms": summary["classify_latency_ms"],
            "classify_source": summary["classify_source"],
            "classify_impl": self.classify_source,
            "rules": list(self.rules),
            "window_s": self.window_s,
            "threshold": self.threshold,
            "status_ms": summary.get("status_ms"),
            "funnel_p50_ms": self.funnel_samples.p50,
            "funnel_p95_ms": self.funnel_samples.p95,
            "funnel_rows_read": summary.get("funnel_rows_read"),
            "classify_p50_ms": self.classify_samples.p50,
            "error": summary["error"],
        }
        hb = make_heartbeat("detector", summary["timings_ms"], **metrics)
        try:
            resp = await self.checkpoint.heartbeat(hb)
            if resp.is_error:
                self._throttle.warn("heartbeat", f"detector: /heartbeat -> HTTP {resp.status_code} {resp.text[:120]}")
            else:
                summary["heartbeat"] = "ok"
        except Exception as exc:  # noqa: BLE001
            self._throttle.warn("heartbeat", f"detector: /heartbeat failed: {exc!r}")

    # ---- forever -----------------------------------------------------------------
    async def run_forever(self, interval: float = 1.0) -> None:
        """run_once() on a monotonic schedule; logs and survives every error (never exits)."""
        logger.info(
            f"detector: running every {interval:.2f}s rules={list(self.rules)} threshold={self.threshold} "
            f"window_s={self.window_s} classify={self.classify_source} fallback={self.fallback_source} "
            f"hooks: outbreak={self.outbreak_enabled} investigator={self.investigator_enabled}"
        )
        next_tick = time.monotonic()
        while True:
            try:
                summary = await self.run_once()
                if summary["hits"] or summary["error"]:
                    logger.debug(f"detector: {json.dumps(summary, default=str)}")
            except Exception:  # noqa: BLE001
                logger.exception("detector: iteration crashed; continuing")
            next_tick += interval
            delay = next_tick - time.monotonic()
            if delay <= 0:  # fell behind (slow classify / query): resync instead of bursting
                next_tick = time.monotonic()
                delay = 0.0
            await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

app = typer.Typer(add_completion=False, help="Tripwire detector loop (see module docstring).")


async def run_once(
    client: httpx.AsyncClient,
    *,
    ch: Any | None = None,
    classify: ClassifyFn | None = None,
    rules: tuple[str, ...] | list[str] = DEFAULT_RULES,
    threshold: float = 0.8,
    window_s: int = 300,
    token: str | None = None,
    hooks: bool = False,
    quorum: bool = False,
) -> dict[str, Any]:
    """One in-process detection pass that talks to the checkpoint ONLY through `client`.

    This is the entry point tests/e2e/test_acceptance.py (Bindu) calls with an httpx.ASGITransport
    client, so the detector never posts to the live :8000. ClickHouse is read through tripwire.ch
    (whatever .env / the Makefile's LOCAL_CH points at) unless a `ch` adapter is injected. Returns the
    same summary dict as Detector.run_once(); it never raises for service errors.

    The post-containment hooks (outbreak trace + investigator report) are OFF by default so the acceptance
    suite sees exactly one detection pass; hooks=True runs them and waits for them (<= 45 s) before returning
    (summary["hooks_pending"] = how many were still running and got cancelled).
    """
    adapter = ch if ch is not None else ClickHouseAdapter()
    cp = CheckpointHTTP.from_client(client, token if token is not None else get_settings().tripwire_token)
    detector = Detector(
        adapter,
        cp,
        classify,
        threshold=threshold,
        window_s=window_s,
        rules=rules,
        outbreak=hooks,
        investigator=hooks,
        quorum=quorum,
    )
    try:
        summary = await detector.run_once()
        if hooks:
            summary["hooks_pending"] = await detector.drain_hooks(RUN_ONCE_HOOK_DRAIN_S)
        return summary
    finally:
        if ch is None:
            await adapter.close()


def _log_startup(detector: Detector, checkpoint_url: str) -> None:
    s = get_settings()
    akash = "present" if s.akashml_api_key else "ABSENT"
    model_capable = detector.classify_source.startswith("ai.quick_check")
    model_capable = model_capable or detector.classify_source.startswith("ai.quorum")
    logger.info(
        f"detector: classify={detector.classify_source} "
        f"({'akashml-capable' if model_capable else 'rule_only'}), AKASHML_API_KEY {akash}, "
        f"fallback={detector.fallback_source}, checkpoint={checkpoint_url}, "
        f"hooks: outbreak={detector.outbreak_enabled} investigator={detector.investigator_enabled}"
    )
    if model_capable and not s.akashml_api_key:
        logger.info("detector: no AkashML key -> verdicts will be labelled rule_only until one is set")


async def _amain(
    interval: float,
    once: bool,
    rules: tuple[str, ...],
    threshold: float,
    window_s: int,
    checkpoint_url: str,
    outbreak: bool = True,
    investigator: bool = True,
    quorum: bool = False,
) -> int:
    """0 on success; with --once, 1 when the pass reported an error (ClickHouse or checkpoint unreachable)."""
    s = get_settings()
    ch = ClickHouseAdapter()
    cp = CheckpointHTTP(checkpoint_url, s.tripwire_token)
    detector = Detector(
        ch, cp, threshold=threshold, window_s=window_s, rules=rules, outbreak=outbreak, investigator=investigator, quorum=quorum
    )
    _log_startup(detector, checkpoint_url)
    try:
        if once:
            summary = await detector.run_once()
            if summary["hooks_scheduled"]:
                summary["hooks_pending"] = await detector.drain_hooks(RUN_ONCE_HOOK_DRAIN_S)
            typer.echo(json.dumps(summary, default=str, indent=2))
            return 1 if summary.get("error") else 0
        await detector.run_forever(interval)
        return 0
    finally:
        try:
            await detector.drain_hooks(HOOK_DRAIN_S)
        finally:
            await cp.close()
            await ch.close()


@app.command()
def main(
    interval: float = typer.Option(1.0, "--interval", help="seconds between iterations (> 0)"),
    once: bool = typer.Option(
        False, "--once", help="run one iteration, print the JSON summary; exit 0, or 1 when it reports an error"
    ),
    rules: str = typer.Option(
        ",".join(DEFAULT_RULES), "--rules", help="comma-separated: secret_theft,baseline_novelty,role_grab,log_tamper"
    ),
    threshold: float = typer.Option(0.8, "--threshold", help="block when malicious and confidence >= threshold (0..1)"),
    window_s: int = typer.Option(300, "--window-s", help="lookback window in seconds (> 0)"),
    checkpoint_url: Optional[str] = typer.Option(None, "--checkpoint-url", help="default: CHECKPOINT_URL from .env"),
    outbreak: bool = typer.Option(
        True, "--outbreak/--no-outbreak", help="after a block: trace the outbreak (detection.outbreak) and post it"
    ),
    investigator: bool = typer.Option(
        True, "--investigator/--no-investigator", help="after a block: write the incident report (ai.investigator)"
    ),
    quorum: bool = typer.Option(
        False, "--quorum", help="classify with the two-model quorum (ai.quorum); single model when not importable"
    ),
) -> None:
    """Run the Tripwire detector against ClickHouse and the checkpoint. Exit 2 on bad arguments."""
    rule_list = tuple(r.strip() for r in rules.split(",") if r.strip())
    unknown = [r for r in rule_list if r not in SQL_FOR_RULE]
    if unknown or not rule_list:
        typer.echo(f"unknown rules {unknown or rules!r}; known: {sorted(SQL_FOR_RULE)}", err=True)
        raise typer.Exit(2)
    if not interval > 0 or not 0.0 <= threshold <= 1.0 or window_s <= 0:
        typer.echo(
            f"bad arguments: --interval {interval} (> 0), --threshold {threshold} (0..1), --window-s {window_s} (> 0)",
            err=True,
        )
        raise typer.Exit(2)
    url = checkpoint_url or get_settings().checkpoint_url
    try:
        code = asyncio.run(
            _amain(
                interval,
                once,
                rule_list,
                threshold,
                window_s,
                url,
                outbreak=outbreak,
                investigator=investigator,
                quorum=quorum,
            )
        )
    except KeyboardInterrupt:
        logger.info("detector: stopped")
        code = 0
    raise typer.Exit(code)


if __name__ == "__main__":
    app()

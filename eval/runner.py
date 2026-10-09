"""Evaluation runner: replay labelled cases through the checkpoint, measure precision / recall / time to
detect and the model cost per 1,000 events, then push the numbers to the checkpoint (POST /heartbeat,
source "eval") so the Evidence tab and /evidence show them (master §10 D6, §14 act 5).

    uv run python -m eval.runner                                    # fixtures/eval/{attack,benign}/*.json
    uv run python -m eval.runner --cases fixtures/eval --checkpoint-url http://localhost:8000
    uv run python -m eval.runner --max-parallel 4 --wait-s 8 --out reports/
    uv run python -m eval.runner --no-heartbeat --limit 3           # smoke run: nothing pushed to /evidence
    uv run python -m eval.runner --no-detect                        # rely on the detector already running (make dev)
    uv run python -m eval.runner --restore                          # POST /restore for every eval agent afterwards

Cases: fixtures/eval/attack/*.json and fixtures/eval/benign/*.json (contracts.Scenario; the directory is the
label). New files are picked up on the next run, no code change needed. When NO labelled case exists the three
top-level fixtures are used instead (secret_theft -> attack, honeytoken_exfil -> attack, normal_ops -> benign)
and the report says so: n_cases is then 3, never more.

What one case does (agent id ``eval-<label>-<name>-<run6>``, so cases never contaminate each other or the demo agents):
  1. replay the Scenario through POST /tool (agents.replay.run_scenario, step expectations ignored);
  2. if the checkpoint did not contain the agent synchronously (honeytoken, hold mode, policy denylist): wait
     until the case's rows are visible in ClickHouse (count per agent, synthetic = 0, <= wait_s), run
     detection.loop.run_once in-process (up to 2 passes, one at a time) and poll GET /status (<= wait_s);
  3. read GET /incidents + GET /alerts for the agent: mechanism, decision_source, verdict, detect / contain ms
     and every quick check performed (latency, tokens) for the cost column.
Contained = the agent is quarantined at the end of the case. attack+contained = TP, attack+free = FN,
benign+contained = FP, benign+free = TN. precision = TP/(TP+FP), recall = TP/(TP+FN), None when the
denominator is 0. detect_ms = the checkpoint's own timestamps (alert.detected_at_ms - last_step_ts_ms for the
detector; incident.contained_ms - the denied step's ts for synchronous containment).

Cost per 1,000 events = (quick checks performed / events replayed) x 1000 x mean cost per quick check, where one
quick check costs tokens_in x input price + tokens_out x output price with the tokens measured in this run
(the alerts' / incident verdicts' tokens_in / tokens_out). When none of them carried token counts, the quick
check is re-run on up to MEASURE_MAX flagged windows with ai.quick_check.classify to measure them and the
report says so. Prices: eval.pricing (AkashML live from GET /v1/models for the model that answered; OpenAI
gpt-4o-mini from its pricing page, else the dated constant marked "constant (fetch failed)"). The OpenAI
column prices the same measured tokens; no OpenAI call is ever made.

Never calls POST /demo/reset. Eval agents stay quarantined afterwards (Reset in the UI, or --restore).
Reports: reports/eval_<utc>.json + .md (gitignored). Exit 0 when the run completed, 2 when it could not start.
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit

import httpx
import typer
from loguru import logger

from agents.checkpoint_client import CheckpointClient, CheckpointDown, CheckpointError
from agents.replay import run_scenario
from eval.pricing import Prices, cost_per_1000_events, fetch_prices, mean_cost_per_call
from tripwire.config import get_settings
from tripwire.contracts import RULE_SECRET_THEFT, Heartbeat, Policy, QuickCheckInput, Scenario

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
DEFAULT_CASES_DIR = FIXTURES_DIR / "eval"
DEFAULT_OUT_DIR = ROOT / "reports"
LABELS: tuple[str, ...] = ("attack", "benign")
FALLBACK_FIXTURES: tuple[tuple[str, str], ...] = (
    ("secret_theft", "attack"),
    ("honeytoken_exfil", "attack"),
    ("normal_ops", "benign"),
)
MODEL_SOURCES: tuple[str, ...] = ("akashml", "quorum", "openai")
QUICK_CHECK_SOURCES: tuple[str, ...] = MODEL_SOURCES + ("rule_only",)  # verdicts that came out of classify()
SYNC_REASONS: tuple[str, ...] = ("honeytoken", "hold_rule", "hold_model", "hold_policy")
POLL_S = 0.25
DETECT_PASSES = 2
DETECT_RETRY_S = 0.5
DETECT_TIMEOUT_S = 30.0
VISIBLE_TIMEOUT_S = 5.0
MEASURE_MAX = 5
MEASURE_TIMEOUT_S = 6.0
HTTP_TIMEOUT_S = 10.0
_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")

VisibleFn = Callable[[str], Awaitable[Optional[int]]]
DetectFn = Callable[[httpx.AsyncClient], Awaitable[dict[str, Any]]]


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ratio(num: int, den: int) -> Optional[float]:
    return round(num / den, 4) if den > 0 else None


def _short(exc: BaseException, n: int = 160) -> str:
    msg = " ".join(str(exc).split())[:n]
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------


@dataclass
class Case:
    name: str  # file stem
    label: str  # "attack" | "benign"
    path: str
    scenario: Scenario


@dataclass
class CaseSet:
    cases: list[Case]
    note: Optional[str] = None  # set when the fallback fixtures were used
    skipped: list[str] = field(default_factory=list)  # unreadable files


def safe_name(name: str) -> str:
    return (_NAME_RE.sub("_", name).strip("_") or "case")[:48]


def agent_id_for(label: str, name: str, run_id: str) -> str:
    return f"eval-{label}-{safe_name(name)}-{run_id}"


def _load_scenario(path: Path) -> Scenario:
    return Scenario.model_validate_json(path.read_text(encoding="utf-8"))


def load_cases(cases_dir: Path | str = DEFAULT_CASES_DIR, fixtures_dir: Path | str = FIXTURES_DIR) -> CaseSet:
    """<cases_dir>/attack/*.json + <cases_dir>/benign/*.json; the three top-level fixtures when none exist."""
    cases_dir, fixtures_dir = Path(cases_dir), Path(fixtures_dir)
    cases: list[Case] = []
    skipped: list[str] = []
    for label in LABELS:
        d = cases_dir / label
        for p in sorted(d.glob("*.json")) if d.is_dir() else []:
            try:
                cases.append(Case(p.stem, label, str(p), _load_scenario(p)))
            except (OSError, ValueError) as exc:
                skipped.append(f"{p}: {_short(exc, 120)}")
    if cases:
        return CaseSet(cases, None, skipped)
    for stem, label in FALLBACK_FIXTURES:
        p = fixtures_dir / f"{stem}.json"
        if not p.is_file():
            skipped.append(f"{p}: missing")
            continue
        try:
            cases.append(Case(stem, label, str(p), _load_scenario(p)))
        except (OSError, ValueError) as exc:
            skipped.append(f"{p}: {_short(exc, 120)}")
    note = (
        f"no labelled cases under {cases_dir}/{{attack,benign}}; fell back to the {len(cases)} top-level "
        f"fixtures ({', '.join(f'{c.name} -> {c.label}' for c in cases)}). n_cases = {len(cases)}."
    )
    return CaseSet(cases, note, skipped)


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


@dataclass
class QuickCheck:
    """One classify() decision observed for the case (from /alerts or the incident verdict)."""

    origin: str  # "alert:detector" | "alert:hold" | "incident"
    rule: str
    decision_source: str
    verdict: str
    confidence: float
    reason: str
    model_ids: list[str]
    latency_ms: float
    tokens_in: int
    tokens_out: int
    blocking: bool


@dataclass
class CaseResult:
    name: str
    label: str
    agent_id: str
    contained: bool = False
    outcome: str = ""  # TP | FP | FN | TN
    mechanism: str = "none"  # detector | honeytoken | hold | policy | unknown | none
    decision_source: Optional[str] = None
    verdict: Optional[str] = None
    detect_ms: Optional[float] = None
    contain_ms: Optional[float] = None
    events: int = 0
    denied_steps: int = 0
    replay_passed: Optional[bool] = None
    visible_ms: Optional[float] = None
    detect_passes: int = 0
    detect_summaries: list[dict[str, Any]] = field(default_factory=list)
    rule: Optional[str] = None
    incident_id: Optional[str] = None
    quick_checks: list[QuickCheck] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    wall_ms: float = 0.0


@dataclass
class EvalResult:
    run_id: str
    started_utc: str
    finished_utc: str = ""
    checkpoint_url: str = ""
    hold_enabled: Optional[bool] = None
    detector: str = ""
    fallback_note: Optional[str] = None
    skipped_files: list[str] = field(default_factory=list)
    cases: list[CaseResult] = field(default_factory=list)
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    precision: Optional[float] = None
    recall: Optional[float] = None
    median_detect_ms: Optional[float] = None  # detector path only (alert.detected_at_ms - last step)
    median_sync_contain_ms: Optional[float] = None  # honeytoken / hold / policy denials (no detector in the loop)
    mechanisms: dict[str, int] = field(default_factory=dict)
    prevented_not_quarantined: int = 0  # cases with a denied step but no quarantine
    n_events: int = 0
    n_quick_checks: int = 0
    n_model_calls: int = 0
    tokens_measured: str = ""
    measured_calls: list[list[int]] = field(default_factory=list)  # (tokens_in, tokens_out) behind the mean cost
    akashml_model: Optional[str] = None
    openai_model: str = ""
    mean_cost_akashml_per_call: Optional[float] = None
    mean_cost_openai_per_call: Optional[float] = None
    cost_akashml: Optional[float] = None
    cost_openai: Optional[float] = None
    priced_on: str = ""
    prices: dict[str, Any] = field(default_factory=dict)
    cost_note: str = ""
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def n_cases(self) -> int:
        return len(self.cases)

    def note(self) -> str:
        return "; ".join(x for x in (self.fallback_note, self.cost_note) if x)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["n_cases"] = self.n_cases
        d["note"] = self.note()
        return d


def outcome_of(label: str, contained: bool) -> str:
    if label == "attack":
        return "TP" if contained else "FN"
    return "FP" if contained else "TN"


def tally(results: list[CaseResult]) -> tuple[int, int, int, int]:
    c = Counter(r.outcome for r in results)
    return c["TP"], c["FP"], c["FN"], c["TN"]


def case_row(c: CaseResult) -> dict[str, Any]:
    return {
        "name": c.name,
        "label": c.label,
        "agent_id": c.agent_id,
        "contained": c.contained,
        "outcome": c.outcome,
        "mechanism": c.mechanism,
        "decision_source": c.decision_source,
        "verdict": c.verdict,
        "detect_ms": c.detect_ms,
        "contain_ms": c.contain_ms,
        "events": c.events,
        "denied_steps": c.denied_steps,
        "rule": c.rule,
        "incident_id": c.incident_id,
        "error": c.error,
    }


# ---------------------------------------------------------------------------
# default collaborators: ClickHouse visibility + the in-process detector
# ---------------------------------------------------------------------------


class CHVisibility:
    """count of a case's live rows: SELECT count() FROM events WHERE agent_id = ... AND synthetic = 0,
    through tripwire.ch (thread-backed client, one per runner; calls are serialized by the client's lock).
    None when ClickHouse cannot be queried (logged once per failure streak)."""

    SQL = "SELECT count() FROM events WHERE agent_id = {a:String} AND synthetic = 0"

    def __init__(self, timeout_s: float = VISIBLE_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        self._client: Any = None
        self._warned = False
        self.errors = 0

    async def count(self, agent: str) -> Optional[int]:
        try:
            if self._client is None:
                from tripwire.ch import async_client

                self._client = await asyncio.wait_for(async_client(), self.timeout_s)
            res = await asyncio.wait_for(self._client.query(self.SQL, parameters={"a": agent}), self.timeout_s)
            self._warned = False
            return int(res.result_rows[0][0])
        except Exception as exc:  # noqa: BLE001  (ClickHouse down / timeout: the run goes on without visibility)
            self._client = None
            self.errors += 1
            if not self._warned:
                self._warned = True
                logger.warning(f"eval: ClickHouse visibility check failed ({_short(exc)}); not waiting for rows")
            return None

    async def close(self) -> None:
        c, self._client = self._client, None
        if c is not None:
            try:
                await c.close()
            except Exception:  # noqa: BLE001
                pass


def make_detector(token: str) -> DetectFn:
    """detection.loop.run_once(client) through the eval's own httpx client (never CHECKPOINT_URL directly),
    hooks off, bounded by DETECT_TIMEOUT_S. Imported lazily so the eval still runs when the detector lane
    is mid-edit (the pass then reports an error instead of raising)."""

    async def detect(http: httpx.AsyncClient) -> dict[str, Any]:
        from detection.loop import run_once

        return await asyncio.wait_for(run_once(http, token=token), DETECT_TIMEOUT_S)

    return detect


def _is_external(action: str, target: str) -> int:
    if action not in ("http_post", "http_get"):
        return 0
    try:
        host = (urlsplit(target if "://" in target else "//" + target).hostname or "").lower()
    except ValueError:
        host = ""
    internal = {h.lower() for h in Policy().internal_hosts}
    return 0 if host and host in internal else 1


def events_for_window(scenario: Scenario, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The agent's replayed rows in detector/ring shape (for re-measuring a quick check)."""
    out: list[dict[str, Any]] = []
    for st in steps:
        i = int(st.get("i", -1))
        src = scenario.steps[i] if 0 <= i < len(scenario.steps) else None
        out.append(
            {
                "ts_ms": int(st.get("ts_ms") or 0),
                "action": st.get("action", ""),
                "target": st.get("target", ""),
                "result": st.get("result", ""),
                "reason": st.get("reason", ""),
                "is_external": _is_external(str(st.get("action", "")), str(st.get("target", ""))),
                "bytes": (src.bytes or len(src.payload.encode("utf-8"))) if src is not None else 0,
                "tainted_by": src.tainted_by if src is not None else "",
            }
        )
    return out


async def measure_quick_checks(windows: list[QuickCheckInput], max_n: int = MEASURE_MAX) -> list[tuple[int, int]]:
    """Re-run the quick check (ai.quick_check.classify, the real seam) on up to max_n windows and return the
    (tokens_in, tokens_out) of the calls a model answered. Used only when the run's alerts carried no tokens.
    [] without an AkashML key or when every call fell back to the rule."""
    if not windows or not get_settings().akashml_api_key.strip():
        return []
    try:
        from ai.quick_check import classify
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"eval: ai.quick_check not importable ({_short(exc)}); tokens not re-measured")
        return []
    sem = asyncio.Semaphore(2)

    async def one(inp: QuickCheckInput) -> Any:
        async with sem:
            try:
                return await asyncio.wait_for(classify(inp), MEASURE_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"eval: re-measured quick check failed for {inp.agent_id} ({_short(exc)})")
                return None

    verdicts = await asyncio.gather(*(one(w) for w in windows[:max_n]))
    out = [
        (int(v.tokens_in), int(v.tokens_out))
        for v in verdicts
        if v is not None and v.decision_source in MODEL_SOURCES and (v.tokens_in or v.tokens_out)
    ]
    logger.info(f"eval: re-measured tokens on {len(out)} of {min(len(windows), max_n)} flagged window(s)")
    return out


# ---------------------------------------------------------------------------
# evidence -> per-case analysis
# ---------------------------------------------------------------------------


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def quick_checks_of(alerts: list[dict[str, Any]], incident: Optional[dict[str, Any]]) -> list[QuickCheck]:
    """Every classify() decision recorded for the agent, oldest first. Alerts are newest-first on /alerts.
    honeytoken / policy verdicts are not quick checks (no model, no classify call) and are left out.
    The incident verdict is added unless it is the same decision as one of the alerts (POST /block records both)."""
    out: list[QuickCheck] = []
    for a in reversed(alerts):
        src = str(a.get("decision_source") or "")
        if src not in QUICK_CHECK_SOURCES:
            continue
        out.append(
            QuickCheck(
                origin="alert:hold" if a.get("source") == "hold" else "alert:detector",
                rule=str(a.get("rule") or ""),
                decision_source=src,
                verdict=str(a.get("verdict") or ""),
                confidence=_f(a.get("confidence")),
                reason=str(a.get("reason") or "")[:160],
                model_ids=[str(m) for m in (a.get("model_ids") or [])],
                latency_ms=_f(a.get("latency_ms")),
                tokens_in=_i(a.get("tokens_in")),
                tokens_out=_i(a.get("tokens_out")),
                blocking=bool(a.get("blocking")),
            )
        )
    v = (incident or {}).get("verdict") or {}
    src = str(v.get("decision_source") or "")
    if src in QUICK_CHECK_SOURCES:
        key = (str(v.get("reason") or "")[:160], _f(v.get("latency_ms")), _i(v.get("tokens_in")), _i(v.get("tokens_out")))
        if not any((q.reason, q.latency_ms, q.tokens_in, q.tokens_out) == key for q in out):
            out.append(
                QuickCheck(
                    origin="incident",
                    rule=str((incident or {}).get("rule") or ""),
                    decision_source=src,
                    verdict=str(v.get("verdict") or ""),
                    confidence=_f(v.get("confidence")),
                    reason=key[0],
                    model_ids=[str(m) for m in (v.get("model_ids") or [])],
                    latency_ms=key[1],
                    tokens_in=key[2],
                    tokens_out=key[3],
                    blocking=True,
                )
            )
    return out


def mechanism_of(incident: Optional[dict[str, Any]]) -> str:
    """How the checkpoint contained the agent, from its own incident record."""
    if not incident:
        return "unknown"
    rule = str(incident.get("rule") or "")
    if rule == "honeytoken":
        return "honeytoken"
    if rule == "hold":
        src = str(((incident.get("verdict") or {}).get("decision_source")) or "")
        return "policy" if src == "policy" else "hold"
    return "detector"


def analyse_case(
    r: CaseResult, outcomes: list[dict[str, Any]], alerts: list[dict[str, Any]], incidents: list[dict[str, Any]]
) -> None:
    """Fill mechanism / decision_source / verdict / detect_ms / contain_ms / quick_checks from the checkpoint's
    records for this agent (alerts newest first; incidents as GET /incidents returns them)."""
    mine = [i for i in incidents if i.get("agent_id") == r.agent_id]
    open_ = [i for i in mine if i.get("closed_ms") is None]
    pool = open_ or mine
    incident = max(pool, key=lambda i: (_i(i.get("opened_ms")), str(i.get("id")))) if pool else None
    r.quick_checks = quick_checks_of(alerts, incident)
    r.denied_steps = sum(1 for o in outcomes if o.get("result") == "denied")
    if incident is not None:
        r.incident_id = str(incident.get("id") or "") or None
        r.rule = str(incident.get("rule") or "") or None
    if not r.contained:
        r.mechanism = "none"
        last = next((q for q in reversed(r.quick_checks)), None)  # e.g. "flagged -> benign", held for review
        if last is not None:
            r.decision_source, r.verdict = last.decision_source, last.verdict
        return
    r.mechanism = mechanism_of(incident)
    v = (incident or {}).get("verdict") or {}
    r.decision_source = str(v.get("decision_source") or "") or None
    r.verdict = str(v.get("verdict") or "") or None
    contained_ms = _i((incident or {}).get("contained_ms"), -1)
    if r.mechanism == "detector":
        blocking = [a for a in alerts if a.get("blocking") and a.get("source") != "hold"]
        if blocking:
            a = blocking[0]  # newest
            last_step = _i(a.get("last_step_ts_ms"))
            r.detect_ms = float(_i(a.get("detected_at_ms")) - last_step)
            if contained_ms >= 0 and last_step:
                r.contain_ms = float(contained_ms - last_step)
            if r.decision_source is None:
                r.decision_source, r.verdict = str(a.get("decision_source") or ""), str(a.get("verdict") or "")
        return
    denied = next((o for o in outcomes if o.get("result") == "denied" and o.get("reason") in SYNC_REASONS), None)
    if denied is not None and contained_ms >= 0:
        r.detect_ms = float(max(0, contained_ms - _i(denied.get("ts_ms"))))
        r.contain_ms = r.detect_ms


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


class _Runner:
    def __init__(
        self,
        cp: CheckpointClient,
        http: httpx.AsyncClient,
        visible: VisibleFn | None,
        detect: DetectFn | None,
        wait_s: float,
        detect_passes: int,
        run_id: str,
    ) -> None:
        self.cp = cp
        self.http = http
        self.visible = visible
        self.detect = detect
        self.wait_s = max(0.0, float(wait_s))
        self.detect_passes = max(0, int(detect_passes))
        self.run_id = run_id
        self.detect_lock = asyncio.Lock()

    async def is_blocked(self, agent: str) -> bool:
        st = await self.cp.status()
        return agent in st.blocked or st.modes.get(agent) == "quarantined"

    async def poll_blocked(self, agent: str, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while True:
            if await self.is_blocked(agent):
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(POLL_S)

    async def wait_visible(self, agent: str, n_rows: int) -> Optional[float]:
        """ms until the agent's n_rows live rows are countable; None when unknown or not within wait_s."""
        if self.visible is None or n_rows <= 0:
            return None
        t0 = time.monotonic()
        deadline = t0 + self.wait_s
        while True:
            n = await self.visible(agent)
            if n is None:
                return None
            if n >= n_rows:
                return round((time.monotonic() - t0) * 1000, 1)
            if time.monotonic() >= deadline:
                logger.warning(f"eval: {agent}: {n}/{n_rows} rows visible in ClickHouse after {self.wait_s:g}s")
                return None
            await asyncio.sleep(POLL_S)

    async def run_detect(self, r: CaseResult) -> dict[str, Any]:
        assert self.detect is not None
        r.detect_passes += 1
        try:
            summary = await self.detect(self.http)
        except Exception as exc:  # noqa: BLE001  (ImportError / timeout / ClickHouse: the case goes on)
            summary = {"error": f"detect: {_short(exc)}"}
        keep = {k: summary.get(k) for k in ("hits", "blocks", "alerts", "error", "classify_source", "classify_latency_ms")}
        keep["funnel_ms"] = (summary.get("timings_ms") or {}).get("funnel") if isinstance(summary, dict) else None
        r.detect_summaries.append(keep)
        if keep.get("error"):
            logger.warning(f"eval: detection pass for {r.agent_id} reported: {keep['error']}")
        return keep

    async def contain_async(self, r: CaseResult) -> bool:
        """Visibility wait -> detection passes (serialized) -> status poll. True when the agent got blocked."""
        r.visible_ms = await self.wait_visible(r.agent_id, r.events)
        ch_error = False
        if self.detect is not None:
            for i in range(self.detect_passes):
                if await self.is_blocked(r.agent_id):
                    return True
                async with self.detect_lock:
                    if await self.is_blocked(r.agent_id):  # another case's pass already blocked it
                        return True
                    summary = await self.run_detect(r)
                ch_error = bool(summary.get("error")) and "clickhouse" in str(summary.get("error")).lower()
                if await self.is_blocked(r.agent_id):
                    return True
                if ch_error:
                    break  # nothing will block this agent without ClickHouse
                if i + 1 < self.detect_passes:
                    await asyncio.sleep(DETECT_RETRY_S)
        if ch_error and r.visible_ms is None:
            return False
        return await self.poll_blocked(r.agent_id, self.wait_s)

    async def run_case(self, case: Case) -> CaseResult:
        agent = agent_id_for(case.label, case.name, self.run_id)
        r = CaseResult(name=case.name, label=case.label, agent_id=agent)
        t0 = time.monotonic()
        try:
            rep = await run_scenario(case.scenario, self.cp, agent_override=agent, block_wait_s=0.0)
        except CheckpointDown as exc:
            r.error = f"checkpoint unreachable during replay: {exc}"
            rep = {"outcomes": [], "passed": None}
        except CheckpointError as exc:
            r.error = f"checkpoint rejected the replay: {exc}"
            rep = {"outcomes": [], "passed": None}
        outcomes: list[dict[str, Any]] = list(rep.get("outcomes") or [])
        r.events = len(outcomes)
        r.replay_passed = rep.get("passed")
        r.steps = [
            {k: o.get(k) for k in ("i", "action", "target", "result", "reason", "ts_ms", "incident_id")} for o in outcomes
        ]
        try:
            if outcomes:
                r.contained = await self.is_blocked(agent)
                if not r.contained:
                    r.contained = await self.contain_async(r)
                alerts = [a for a in await self._get("/alerts") if a.get("agent_id") == agent]
                st = await self.cp.status()  # the open incidents ride along; GET /incidents only when needed
                incidents = [i.model_dump() for i in st.open_incidents if i.agent_id == agent]
                if r.contained and not incidents:
                    incidents = await self._get("/incidents")
                analyse_case(r, outcomes, alerts, incidents)
        except (CheckpointDown, CheckpointError, httpx.HTTPError) as exc:
            r.error = (r.error + "; " if r.error else "") + f"checkpoint error after replay: {_short(exc)}"
        r.outcome = outcome_of(case.label, r.contained)
        r.wall_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            f"eval: {case.name} [{case.label}] -> {r.outcome} contained={r.contained} mechanism={r.mechanism} "
            f"source={r.decision_source} verdict={r.verdict} detect_ms={r.detect_ms} events={r.events} "
            f"denied={r.denied_steps} quick_checks={len(r.quick_checks)}"
            f"{' error=' + r.error if r.error else ''}"
        )
        return r

    async def _get(self, path: str) -> list[dict[str, Any]]:
        resp = await self.http.get(path)
        resp.raise_for_status()
        data = resp.json()
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def finalize_costs(result: EvalResult, prices: Prices, measured: list[tuple[int, int]] | None = None) -> None:
    """The cost column: (quick checks / events) x 1000 x mean cost per quick check, both providers."""
    checks = [q for c in result.cases for q in c.quick_checks]
    result.n_quick_checks = len(checks)
    result.n_model_calls = sum(1 for q in checks if q.decision_source in MODEL_SOURCES)
    with_tokens = [q for q in checks if q.tokens_in or q.tokens_out]
    calls: list[tuple[int, int]]
    if measured:
        calls = list(measured)
        result.tokens_measured = (
            f"re-measured: none of the run's {len(checks)} quick check(s) carried token counts (no model answered "
            f"in time, or the checkpoint's record has none), so the quick check was re-run on {len(measured)} flagged "
            "window(s) with ai.quick_check.classify; the cost shown is what these checks cost when the model answers"
        )
    elif with_tokens:
        # A model-answered check whose record carries no tokens (the checkpoint's hold alerts drop them) is priced
        # at the mean of the measured model calls, never at 0; rule_only checks (no model answer) cost 0 tokens.
        model_measured = [(q.tokens_in, q.tokens_out) for q in with_tokens if q.decision_source in MODEL_SOURCES]
        fill = model_measured or [(q.tokens_in, q.tokens_out) for q in with_tokens]
        mean_in = round(sum(i for i, _ in fill) / len(fill))
        mean_out = round(sum(o for _, o in fill) / len(fill))
        imputed = 0
        calls = []
        for q in checks:
            if q.tokens_in or q.tokens_out:
                calls.append((q.tokens_in, q.tokens_out))
            elif q.decision_source in MODEL_SOURCES:
                imputed += 1
                calls.append((mean_in, mean_out))
            else:
                calls.append((0, 0))
        result.tokens_measured = (
            f"from this run: {len(with_tokens)} of {len(checks)} quick checks carried measured token counts"
            + (
                f"; {imputed} model-answered check(s) arrived without token counts and were priced at the mean of the "
                f"measured model calls ({mean_in} in / {mean_out} out)"
                if imputed
                else ""
            )
        )
    else:
        calls = []
        result.tokens_measured = "no token counts were measured in this run"
    result.measured_calls = [[int(i), int(o)] for i, o in calls]
    result.akashml_model = prices.akashml.model if prices.akashml else None
    result.openai_model = prices.openai.model
    result.priced_on = prices.label()
    result.prices = prices.to_dict()
    n_calls, n_events = len(checks), result.n_events
    if n_calls == 0:
        result.cost_note = "no quick check was performed in this run (nothing was flagged), so no model cost was measured"
        return
    if not calls:
        result.cost_note = (
            f"{n_calls} quick check(s) ran without a model answer (rule_only) and no token count could be "
            "measured; the cost is not reported"
        )
        return
    ak = mean_cost_per_call(calls, prices.akashml) if prices.akashml else None
    oa = mean_cost_per_call(calls, prices.openai)
    result.mean_cost_akashml_per_call = ak
    result.mean_cost_openai_per_call = oa
    result.cost_akashml = cost_per_1000_events(n_calls, n_events, ak)
    result.cost_openai = cost_per_1000_events(n_calls, n_events, oa)
    quorum = any(len(q.model_ids) > 1 for q in checks)
    result.cost_note = (
        f"cost per 1,000 events = ({n_calls} quick checks / {n_events} events replayed) x 1000 x mean cost per "
        f"quick check; tokens {result.tokens_measured}"
        + (f"; AkashML price not fetched for {result.akashml_model or 'the small model'}" if ak is None else "")
        + ("; quorum calls sum the tokens of both models and are priced at the small model's rate" if quorum else "")
    )


def _observed_model(cases: list[CaseResult]) -> Optional[str]:
    ids = [q.model_ids[0] for c in cases for q in c.quick_checks if q.decision_source in MODEL_SOURCES and q.model_ids]
    return Counter(ids).most_common(1)[0][0] if ids else None


async def run_eval(
    cases: list[Case],
    *,
    cp: CheckpointClient,
    http: httpx.AsyncClient,
    visible: VisibleFn | None = None,
    detect: DetectFn | None = None,
    max_parallel: int = 4,
    wait_s: float = 8.0,
    detect_passes: int = DETECT_PASSES,
    prices: Prices | None = None,
    measure_tokens: bool = True,
    run_id: str | None = None,
    note: str | None = None,
    skipped: list[str] | None = None,
    detector_label: str = "",
) -> EvalResult:
    """The core: every case through `cp` (replay) and `http` (status/alerts/incidents + the detector), at most
    max_parallel at a time, then the tallies and the cost column. `visible(agent) -> row count | None` and
    `detect(http) -> summary` are injectable (tests); None = no visibility wait / no in-process detection.
    `prices` None = fetched live for the model that answered. Raises CheckpointDown/CheckpointError only when
    the first GET /status fails (the checkpoint is not there)."""
    run_id = run_id or uuid.uuid4().hex[:6]
    t0 = time.monotonic()
    result = EvalResult(
        run_id=run_id,
        started_utc=utc_now(),
        checkpoint_url=str(cp.base_url),
        fallback_note=note,
        skipped_files=list(skipped or []),
        detector=detector_label
        or ("detection.loop.run_once (in-process)" if detect is not None else "none (external detector, if any)"),
    )
    result.hold_enabled = bool((await cp.status()).hold_enabled)
    runner = _Runner(cp, http, visible, detect, wait_s, detect_passes, run_id)
    sem = asyncio.Semaphore(max(1, int(max_parallel)))

    async def one(case: Case) -> CaseResult:
        async with sem:
            return await runner.run_case(case)

    result.cases = list(await asyncio.gather(*(one(c) for c in cases)))
    result.errors = [f"{c.name}: {c.error}" for c in result.cases if c.error]
    result.n_events = sum(c.events for c in result.cases)
    result.tp, result.fp, result.fn, result.tn = tally(result.cases)
    result.precision = ratio(result.tp, result.tp + result.fp)
    result.recall = ratio(result.tp, result.tp + result.fn)
    # Two different clocks, never averaged together: the detector's query->verdict time vs. a synchronous
    # denial at the send (honeytoken / hold / policy), which has no detector or model in the loop.
    detect = [c.detect_ms for c in result.cases if c.contained and c.mechanism == "detector" and c.detect_ms is not None]
    sync = [c.detect_ms for c in result.cases if c.contained and c.mechanism != "detector" and c.detect_ms is not None]
    result.median_detect_ms = round(float(statistics.median(detect)), 1) if detect else None
    result.median_sync_contain_ms = round(float(statistics.median(sync)), 1) if sync else None
    result.mechanisms = dict(Counter(c.mechanism for c in result.cases if c.contained))
    result.prevented_not_quarantined = sum(1 for c in result.cases if not c.contained and c.denied_steps)

    checks = [q for c in result.cases for q in c.quick_checks]
    measured: list[tuple[int, int]] | None = None
    if measure_tokens and checks and not any(q.tokens_in or q.tokens_out for q in checks):
        by_name = {c.name: c for c in cases}
        windows = [
            QuickCheckInput(
                agent_id=r.agent_id,
                rule=r.quick_checks[0].rule or RULE_SECRET_THEFT,
                events=events_for_window(by_name[r.name].scenario, r.steps),
                context="Tripwire eval: re-measuring the quick check's token usage for the cost column",
            )
            for r in result.cases
            if r.quick_checks and r.name in by_name and r.steps
        ]
        measured = await measure_quick_checks(windows, MEASURE_MAX)
    if prices is None:
        prices = await fetch_prices(akashml_model=_observed_model(result.cases))
    finalize_costs(result, prices, measured)
    result.finished_utc = utc_now()
    result.duration_s = round(time.monotonic() - t0, 3)
    return result


# ---------------------------------------------------------------------------
# outputs: heartbeat, reports, console
# ---------------------------------------------------------------------------


def heartbeat_payload(result: EvalResult) -> Heartbeat:
    """POST /heartbeat body (source "eval"); /evidence reads precision, recall, n_cases, cost_akashml,
    cost_openai and priced_on from the metrics of the last eval heartbeat."""
    metrics: dict[str, Any] = {
        "precision": result.precision,
        "recall": result.recall,
        "n_cases": result.n_cases,
        "tp": result.tp,
        "fp": result.fp,
        "fn": result.fn,
        "tn": result.tn,
        "cost_akashml": result.cost_akashml,
        "cost_openai": result.cost_openai,
        "priced_on": result.priced_on,
        "cases": [case_row(c) for c in result.cases],
        "akashml_model": result.akashml_model,
        "openai_model": result.openai_model,
        "note": result.note(),
        "n_events": result.n_events,
        "n_quick_checks": result.n_quick_checks,
        "n_model_calls": result.n_model_calls,
        "tokens_measured": result.tokens_measured,
        "mean_cost_akashml_per_call": result.mean_cost_akashml_per_call,
        "mean_cost_openai_per_call": result.mean_cost_openai_per_call,
        "median_detect_ms": result.median_detect_ms,
        "median_sync_contain_ms": result.median_sync_contain_ms,
        "mechanisms": result.mechanisms,
        "prevented_not_quarantined": result.prevented_not_quarantined,
        "hold_enabled": result.hold_enabled,
        "detector": result.detector,
        "prices": result.prices,
        "run_id": result.run_id,
        "started_utc": result.started_utc,
        "finished_utc": result.finished_utc,
        "duration_s": result.duration_s,
        "errors": result.errors,
    }
    return Heartbeat(source="eval", query_timings_ms={}, metrics=metrics)


def _fmt(v: Any, digits: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def _usd(v: Optional[float]) -> str:
    return "—" if v is None else f"${v:.6f}"


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return "\n".join(lines)


def render_markdown(result: EvalResult) -> str:
    n_attack = sum(1 for c in result.cases if c.label == "attack")
    n_benign = result.n_cases - n_attack
    mech = ", ".join(f"{k} {v}" for k, v in sorted(result.mechanisms.items())) or "—"
    ak, oa = result.prices.get("akashml") or {}, result.prices.get("openai") or {}
    summary = [
        ["cases", f"{result.n_cases} (attack {n_attack}, benign {n_benign})"],
        ["TP / FP / FN / TN", f"{result.tp} / {result.fp} / {result.fn} / {result.tn}"],
        ["precision", _fmt(result.precision)],
        ["recall", _fmt(result.recall)],
        ["median detect ms (detector path: alert detected_at - last step)", _fmt(result.median_detect_ms, 1)],
        ["median synchronous containment ms (honeytoken / hold / policy, no detector)", _fmt(result.median_sync_contain_ms, 1)],
        ["containment mechanisms", mech],
        ["denied but not quarantined", str(result.prevented_not_quarantined)],
        ["events replayed", str(result.n_events)],
        ["quick checks performed", f"{result.n_quick_checks} ({result.n_model_calls} answered by a model)"],
        ["hold mode during the run", _fmt(result.hold_enabled)],
        ["detector", result.detector],
        ["duration", f"{result.duration_s:.1f} s"],
    ]
    cost_rows = [
        [
            "AkashML",
            result.akashml_model or "—",
            _fmt(ak.get("input_per_token", 0.0) * 1e6 if ak else None, 4),
            _fmt(ak.get("output_per_token", 0.0) * 1e6 if ak else None, 4),
            _usd(result.mean_cost_akashml_per_call),
            _usd(result.cost_akashml),
            ak.get("source", "not fetched") if ak else "not fetched",
        ],
        [
            "OpenAI (comparison only, no call made)",
            result.openai_model or "—",
            _fmt(oa.get("input_per_token", 0.0) * 1e6 if oa else None, 4),
            _fmt(oa.get("output_per_token", 0.0) * 1e6 if oa else None, 4),
            _usd(result.mean_cost_openai_per_call),
            _usd(result.cost_openai),
            oa.get("source", "—") if oa else "—",
        ],
    ]
    case_rows = [
        [
            c.name,
            c.label,
            c.outcome,
            _fmt(c.contained),
            c.mechanism,
            c.decision_source or "—",
            c.verdict or "—",
            _fmt(c.detect_ms, 0),
            str(c.events),
            str(c.denied_steps),
            str(len(c.quick_checks)),
            c.error or "",
        ]
        for c in result.cases
    ]
    parts = [
        f"# Tripwire eval — {result.finished_utc or result.started_utc}",
        "",
        f"run `{result.run_id}` · checkpoint `{result.checkpoint_url}` · started {result.started_utc} · "
        f"hold mode {'ON' if result.hold_enabled else 'OFF'} · every number below was measured in this run.",
        "",
    ]
    if result.fallback_note:
        parts += [f"> **Note:** {result.fallback_note}", ""]
    if result.skipped_files:
        parts += ["> Skipped (unreadable) case files: " + "; ".join(result.skipped_files), ""]
    parts += ["## Summary", "", _md_table(["metric", "value"], summary), ""]
    parts += [
        "## Cost per 1,000 events",
        "",
        "cost per 1,000 events = (quick checks performed / events replayed) × 1000 × mean cost per quick check, "
        "where one quick check costs tokens_in × input price + tokens_out × output price.",
        "",
        f"- quick checks: {result.n_quick_checks} · events replayed: {result.n_events} · tokens: {result.tokens_measured}",
        f"- priced on: {result.priced_on or '—'}",
        f"- {result.cost_note}" if result.cost_note else "",
        "",
        _md_table(
            ["provider", "model", "input $/1M", "output $/1M", "mean cost / quick check", "cost / 1,000 events", "price source"],
            cost_rows,
        ),
        "",
        "## Cases",
        "",
        _md_table(
            ["case", "label", "outcome", "contained", "mechanism", "decision_source", "verdict", "detect ms", "events", "denied", "quick checks", "error"],
            case_rows,
        ),
        "",
    ]
    if result.errors:
        parts += ["## Errors", ""] + [f"- {e}" for e in result.errors] + [""]
    return "\n".join(parts)


def write_reports(result: EvalResult, out_dir: Path | str = DEFAULT_OUT_DIR) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = (result.finished_utc or utc_now()).replace("-", "").replace(":", "")
    json_path, md_path = out / f"eval_{stamp}.json", out / f"eval_{stamp}.md"
    json_path.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    return json_path, md_path


def summary_table(result: EvalResult) -> str:
    header = ["case", "label", "outcome", "mechanism", "source", "verdict", "detect_ms", "events", "denied", "error"]
    rows = [header]
    for c in result.cases:
        rows.append(
            [
                c.name[:40],
                c.label,
                c.outcome,
                c.mechanism,
                c.decision_source or "-",
                c.verdict or "-",
                _fmt(c.detect_ms, 0),
                str(c.events),
                str(c.denied_steps),
                (c.error or "")[:60],
            ]
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    lines = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)) for r in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    lines += [
        "",
        f"n_cases={result.n_cases} TP={result.tp} FP={result.fp} FN={result.fn} TN={result.tn} "
        f"precision={_fmt(result.precision)} recall={_fmt(result.recall)} "
        f"median_detect_ms(detector)={_fmt(result.median_detect_ms, 1)} "
        f"median_sync_contain_ms={_fmt(result.median_sync_contain_ms, 1)} mechanisms={result.mechanisms}",
        f"cost per 1,000 events: AkashML {_usd(result.cost_akashml)} ({result.akashml_model or '-'}) · "
        f"OpenAI {_usd(result.cost_openai)} ({result.openai_model}) · priced on {result.priced_on or '-'}",
        f"quick checks {result.n_quick_checks} ({result.n_model_calls} by a model) over {result.n_events} events; "
        f"tokens {result.tokens_measured}",
    ]
    if result.note():
        lines.append(f"note: {result.note()}")
    if result.errors:
        lines.append(f"errors ({len(result.errors)}): " + " | ".join(result.errors)[:600])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

app = typer.Typer(add_completion=False, help="Replay labelled cases; measure precision/recall/detect time/cost; push to /heartbeat.")


async def _amain(
    cs: CaseSet,
    url: str,
    token: str,
    *,
    max_parallel: int,
    wait_s: float,
    heartbeat: bool,
    detect: bool,
    restore: bool,
) -> tuple[EvalResult, str]:
    cp = CheckpointClient(base_url=url, token=token, timeout=HTTP_TIMEOUT_S)
    http = httpx.AsyncClient(base_url=url, headers={"X-Tripwire-Token": token}, timeout=HTTP_TIMEOUT_S)
    vis = CHVisibility()
    try:
        await cp.health()  # CheckpointDown / CheckpointError -> exit 2 in main()
        result = await run_eval(
            cs.cases,
            cp=cp,
            http=http,
            visible=vis.count,
            detect=make_detector(token) if detect else None,
            max_parallel=max_parallel,
            wait_s=wait_s,
            note=cs.note,
            skipped=cs.skipped,
        )
        if vis.errors and vis.errors >= len(cs.cases):
            result.errors.append("ClickHouse visibility check never succeeded (see the log); detector path unverified")
        hb_status = "skipped (--no-heartbeat)"
        if result.n_events == 0 and result.errors:
            heartbeat = False  # nothing was measured (every replay failed): never push empty numbers
            hb_status = "skipped (nothing was measured)"
        if heartbeat:
            try:
                await cp.heartbeat(heartbeat_payload(result))
                hb_status = f"posted to {url}/heartbeat (source=eval) -> /evidence"
            except (CheckpointDown, CheckpointError) as exc:
                hb_status = f"FAILED: {exc}"
                result.errors.append(f"heartbeat: {exc}")
        if restore:
            n = 0
            for c in result.cases:
                try:
                    await cp.restore(c.agent_id)
                    n += 1
                except (CheckpointDown, CheckpointError) as exc:
                    result.errors.append(f"restore {c.agent_id}: {exc}")
            logger.info(f"eval: restored {n}/{len(result.cases)} eval agents")
        return result, hb_status
    finally:
        await vis.close()
        await http.aclose()
        await cp.aclose()


@app.command()
def main(
    cases: Annotated[Path, typer.Option("--cases", help="dir with attack/*.json and benign/*.json")] = DEFAULT_CASES_DIR,
    checkpoint_url: Annotated[str, typer.Option("--checkpoint-url", help="default: CHECKPOINT_URL from .env")] = "",
    max_parallel: Annotated[int, typer.Option("--max-parallel", min=1, help="cases replayed at the same time")] = 4,
    wait_s: Annotated[float, typer.Option("--wait-s", min=0.0, help="max wait for ClickHouse rows / the block")] = 8.0,
    heartbeat: Annotated[bool, typer.Option("--heartbeat/--no-heartbeat", help="POST /heartbeat (source eval)")] = True,
    out: Annotated[Path, typer.Option("--out", help="reports directory")] = DEFAULT_OUT_DIR,
    detect: Annotated[bool, typer.Option("--detect/--no-detect", help="run detection.loop.run_once in-process")] = True,
    limit: Annotated[int, typer.Option("--limit", min=0, help="run only the first N cases (0 = all)")] = 0,
    restore: Annotated[bool, typer.Option("--restore", help="POST /restore for every eval agent afterwards")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="print the full result as JSON instead of the table")] = False,
) -> None:
    """Run the evaluation against the checkpoint in .env (never POST /demo/reset). Exit 0 when it ran, 2 otherwise."""
    s = get_settings()
    url = (checkpoint_url or s.checkpoint_url).rstrip("/")
    cs = load_cases(cases)
    for msg in cs.skipped:
        logger.warning(f"eval: skipped {msg}")
    if cs.note:
        logger.warning(f"eval: {cs.note}")
    if not cs.cases:
        typer.echo(f"no cases: nothing under {cases}/{{attack,benign}} and the fallback fixtures are missing", err=True)
        raise typer.Exit(2)
    if limit:
        cs.cases = cs.cases[:limit]
    logger.info(f"eval: {len(cs.cases)} case(s) against {url} (max_parallel={max_parallel}, wait_s={wait_s:g}, detect={detect})")
    try:
        result, hb_status = asyncio.run(
            _amain(cs, url, s.tripwire_token, max_parallel=max_parallel, wait_s=wait_s, heartbeat=heartbeat, detect=detect, restore=restore)
        )
    except CheckpointDown as exc:
        typer.echo(f"checkpoint unreachable at {url}: {exc}", err=True)
        raise typer.Exit(2) from exc
    except CheckpointError as exc:
        hint = " (check TRIPWIRE_TOKEN / PUBLIC)" if exc.status == 401 else ""
        typer.echo(f"checkpoint rejected the request: {exc}{hint}", err=True)
        raise typer.Exit(2) from exc
    json_path, md_path = write_reports(result, out)
    typer.echo(json.dumps(result.to_dict(), indent=2, default=str) if as_json else summary_table(result))
    typer.echo(f"reports: {json_path} and {md_path}")
    typer.echo(f"heartbeat: {hb_status}")
    if result.n_events == 0 and result.errors:
        typer.echo("nothing was measured: every replay failed (see errors above; 401 = TRIPWIRE_TOKEN / PUBLIC)", err=True)
        raise typer.Exit(2)
    if not restore:
        typer.echo("eval agents stay quarantined in the checkpoint (Reset in the UI, or re-run with --restore)")
    raise typer.Exit(0)


if __name__ == "__main__":
    app()

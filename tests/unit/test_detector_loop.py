"""detection.loop.Detector with a scripted FakeCH and an httpx.MockTransport fake checkpoint.

No ClickHouse, no sockets, no model calls. Run: uv run pytest tests/unit/test_detector_loop.py -q
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import time
import types
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
SQL_NAMES = ("funnel", "recent_events", "baseline", "role_grab", "log_tamper", "secret_exfil_direct")


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
        open_incidents: list[dict[str, Any]] | None = None,
        incident_id: str | None = "inc-test",
    ) -> None:
        self.blocked = list(blocked or [])
        self.verdict_keys = list(verdict_keys or [])
        self.watermarks = dict(watermarks or {})
        self.block_status = block_status
        self.alert_status = alert_status
        self.down = down
        self.open_incidents = list(open_incidents or [])
        self.incident_id = incident_id  # None: the /block reply carries no incident_id
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
                    "open_incidents": self.open_incidents,
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
            reply = {"status": "blocked"}
            if self.incident_id:
                reply["incident_id"] = self.incident_id
            return httpx.Response(self.block_status, json=reply)
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
    """A Detector with the post-block hooks OFF unless the test opts in (outbreak=True / investigator=True)."""
    kw.setdefault("outbreak", False)
    kw.setdefault("investigator", False)
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


# ---------------------------------------------------------------------------
# Module-level run_once(client): the in-process entry point tests/e2e/test_acceptance.py calls
# ---------------------------------------------------------------------------


async def test_module_run_once_uses_given_client_and_blocks() -> None:
    """run_once(client=...) must do one pass through the injected HTTP client only (never CHECKPOINT_URL),
    post /block for a malicious funnel hit, send the token header, and not close the caller's client."""
    from detection import loop as loop_mod

    fcp = FakeCheckpoint()
    ch = FakeCH({"funnel": [FUNNEL_HIT], "recent_events": ATTACK_EVENTS})

    async def malicious(inp: QuickCheckInput) -> Verdict:
        return Verdict(verdict="malicious", confidence=0.97, reason="test", decision_source="akashml", model_ids=["m"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(fcp.handler), base_url="http://checkpoint.test") as c:
        summary = await loop_mod.run_once(c, ch=ch, classify=malicious, token="tok-123")
        assert summary["error"] is None and summary["blocks"] == 1, summary
        posts = [r for r in fcp.requests if r[0] == "POST"]
        assert any(r[1].startswith("/block/") for r in posts), fcp.requests
        assert all(r[3].get("x-tripwire-token") == "tok-123" for r in posts), [r[3] for r in posts]
        # the caller's client is still usable (not closed by the detector)
        assert (await c.get("/status")).status_code == 200
    # signature contract Bindu's test relies on: a parameter named `client`, nothing else required
    import inspect

    params = inspect.signature(loop_mod.run_once).parameters
    assert "client" in params
    assert [p.name for p in params.values() if p.default is inspect.Parameter.empty] == ["client"]


# ---------------------------------------------------------------------------
# AlertPayload cost fields (11:55 CCR): latency_ms / tokens_in / tokens_out come from the Verdict
# ---------------------------------------------------------------------------


async def test_block_and_alert_payloads_carry_the_verdict_cost_fields():
    cp = FakeCheckpoint()
    summary = await make(attack_ch(), cp, scripted(MALICIOUS)).run_once()
    (block,) = cp.posts("/block/")
    assert (block["latency_ms"], block["tokens_in"], block["tokens_out"]) == (120.0, 300, 20)
    assert summary["classify_latency_ms"] is not None
    priced = BENIGN.model_copy(update={"latency_ms": 80.5, "tokens_in": 210, "tokens_out": 12})
    cp2 = FakeCheckpoint()
    await make(attack_ch(), cp2, scripted(priced)).run_once()
    (alert,) = cp2.posts("/alerts")
    assert (alert["latency_ms"], alert["tokens_in"], alert["tokens_out"]) == (80.5, 210, 12)
    # the whole payload still validates as the frozen contract model
    from tripwire.contracts import AlertPayload

    assert AlertPayload.model_validate(block).tokens_in == 300 and AlertPayload.model_validate(alert).latency_ms == 80.5


async def test_payload_latency_is_the_measured_wall_time_when_the_verdict_has_none():
    cp = FakeCheckpoint()
    summary = await make(attack_ch(), cp, scripted(BENIGN)).run_once()  # BENIGN carries latency_ms 0
    (alert,) = cp.posts("/alerts")
    assert alert["latency_ms"] == summary["classify_latency_ms"] >= 0 and alert["tokens_in"] == alert["tokens_out"] == 0
    cp2 = FakeCheckpoint()
    summary2 = await make(attack_ch(), cp2, scripted(RuntimeError("model API 500"))).run_once()
    (block,) = cp2.posts("/block/")  # rule_only fallback: measured latency of the failed call, no tokens
    assert block["decision_source"] == "rule_only" and block["latency_ms"] == summary2["classify_latency_ms"]
    assert block["tokens_in"] == 0 and block["tokens_out"] == 0


# ---------------------------------------------------------------------------
# Post-containment hooks: outbreak trace + investigator report after a successful /block
# ---------------------------------------------------------------------------


def install_fake_hooks(monkeypatch, *, outbreak: Any = "ok", investigate: Any = "ok") -> dict[str, list[Any]]:
    """Replace the lazily imported hook targets (detection.outbreak.run_outbreak, ai.investigator.investigate)
    with recording fakes. A value that is an Exception is raised; a coroutine function is awaited for its value."""
    from tripwire.contracts import Outbreak, ReportPayload

    calls: dict[str, list[Any]] = {"outbreak": [], "investigate": [], "order": []}

    async def fake_run_outbreak(incident_id: str, *, client: Any, ch: Any = None, token: Any = None, **kw: Any):
        calls["outbreak"].append((incident_id, client, ch, token))
        calls["order"].append("outbreak")
        if isinstance(outbreak, Exception):
            raise outbreak
        if callable(outbreak):
            return await outbreak()
        if outbreak is None:
            return None
        return Outbreak(source_id="ticket:4821", exposed_agents=[OTHER], blocked_destinations=["drop.example.net"], query_ms=3.5)

    async def fake_investigate(incident_id: str, *, client: Any = None, token: Any = None, **kw: Any):
        calls["investigate"].append((incident_id, client, token))
        calls["order"].append("investigate")
        if isinstance(investigate, Exception):
            raise investigate
        if callable(investigate):
            return await investigate()
        return ReportPayload(report_md="# report", receipts=[{"sql": "x", "ms": 1.0, "rows_read": 1}], model_ids=["m"])

    import detection.outbreak as ob_mod

    monkeypatch.setattr(ob_mod, "run_outbreak", fake_run_outbreak)
    fake_mod = types.ModuleType("ai.investigator")
    fake_mod.investigate = fake_investigate  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ai.investigator", fake_mod)
    return calls


async def test_hooks_are_scheduled_after_a_200_from_block_and_awaited_by_drain(monkeypatch):
    calls = install_fake_hooks(monkeypatch)
    ch, cp = attack_ch(), FakeCheckpoint()
    d = make(ch, cp, scripted(MALICIOUS), outbreak=True, investigator=True)
    summary = await d.run_once()
    assert summary["blocks"] == 1 and summary["hooks_scheduled"] == 1 and d.hook_stats["scheduled"] == 1
    assert await d.drain_hooks(5.0) == 0  # nothing cancelled
    assert calls["order"] == ["outbreak", "investigate"]  # outbreak first, then the report
    assert calls["outbreak"] == [("inc-test", d.checkpoint.http, ch, "test-token")]
    assert calls["investigate"] == [("inc-test", d.checkpoint.http, "test-token")]
    assert d.hook_stats == {"scheduled": 1, "outbreaks": 1, "reports": 1, "hook_errors": 0}
    # counters reach the summary and the heartbeat of the next pass
    second = await d.run_once()
    assert (second["outbreaks"], second["reports"], second["hook_errors"], second["hooks_running"]) == (1, 1, 0, 0)
    hb = cp.posts("/heartbeat")[-1]["metrics"]
    assert (hb["outbreaks"], hb["reports"], hb["hook_errors"], hb["hooks_scheduled"]) == (1, 1, 0, 0)
    assert hb["hooks"] == {"outbreak": True, "investigator": True, "quorum": False}


async def test_hooks_not_scheduled_on_409_alerts_or_a_reply_without_incident_id(monkeypatch):
    calls = install_fake_hooks(monkeypatch)
    d = make(attack_ch(), FakeCheckpoint(block_status=409), scripted(MALICIOUS), outbreak=True, investigator=True)
    summary = await d.run_once()
    assert summary["conflicts"] == 1 and summary["hooks_scheduled"] == 0 and not d._hook_tasks
    # a non-blocking verdict never triggers the hooks
    d2 = make(attack_ch(), FakeCheckpoint(), scripted(BENIGN), outbreak=True, investigator=True)
    assert (await d2.run_once())["hooks_scheduled"] == 0
    # a 200 whose body carries no incident_id: nothing to trace, logged and skipped
    d3 = make(attack_ch(), FakeCheckpoint(incident_id=None), scripted(MALICIOUS), outbreak=True, investigator=True)
    s3 = await d3.run_once()
    assert s3["blocks"] == 1 and s3["hooks_scheduled"] == 0
    await asyncio.sleep(0)
    assert calls["outbreak"] == [] and calls["investigate"] == []


async def test_flags_select_which_hooks_run(monkeypatch):
    calls = install_fake_hooks(monkeypatch)
    d = make(attack_ch(), FakeCheckpoint(), scripted(MALICIOUS), outbreak=True, investigator=False)
    await d.run_once()
    await d.drain_hooks(5.0)
    assert len(calls["outbreak"]) == 1 and calls["investigate"] == []
    d2 = make(attack_ch(), FakeCheckpoint(), scripted(MALICIOUS), outbreak=False, investigator=True)
    await d2.run_once()
    await d2.drain_hooks(5.0)
    assert len(calls["outbreak"]) == 1 and len(calls["investigate"]) == 1
    d3 = make(attack_ch(), FakeCheckpoint(), scripted(MALICIOUS))  # both off: nothing scheduled, not even tasks
    s3 = await d3.run_once()
    assert s3["blocks"] == 1 and s3["hooks_scheduled"] == 0 and not d3.hooks_enabled and not d3._hook_tasks


async def test_hook_failures_are_counted_never_raised(monkeypatch):
    calls = install_fake_hooks(monkeypatch, outbreak=RuntimeError("CH down"), investigate=TimeoutError("model slow"))
    cp = FakeCheckpoint()
    d = make(attack_ch(), cp, scripted(MALICIOUS), outbreak=True, investigator=True)
    summary = await d.run_once()
    assert summary["blocks"] == 1 and summary["error"] is None
    assert await d.drain_hooks(5.0) == 0
    assert len(calls["outbreak"]) == 1 and len(calls["investigate"]) == 1  # the report still ran after the failed trace
    assert d.hook_stats == {"scheduled": 1, "outbreaks": 0, "reports": 0, "hook_errors": 2}
    second = await d.run_once()
    assert second["hook_errors"] == 2 and cp.posts("/heartbeat")[-1]["metrics"]["hook_errors"] == 2
    # run_outbreak answering None (it never raises) is a failed trace too
    calls2 = install_fake_hooks(monkeypatch, outbreak=None)
    d2 = make(attack_ch(), FakeCheckpoint(), scripted(MALICIOUS), outbreak=True, investigator=True)
    await d2.run_once()
    await d2.drain_hooks(5.0)
    assert d2.hook_stats == {"scheduled": 1, "outbreaks": 0, "reports": 1, "hook_errors": 1} and len(calls2["investigate"]) == 1


async def test_hook_timeouts_are_bounded_and_drain_cancels_stragglers(monkeypatch):
    async def slow():
        await asyncio.sleep(5)

    install_fake_hooks(monkeypatch, outbreak=slow, investigate="ok")
    monkeypatch.setattr(L, "OUTBREAK_TIMEOUT_S", 0.05)
    d = make(attack_ch(), FakeCheckpoint(), scripted(MALICIOUS), outbreak=True, investigator=True)
    await d.run_once()
    t0 = time.perf_counter()
    assert await d.drain_hooks(5.0) == 0
    assert time.perf_counter() - t0 < 1.0  # the 5 s trace was cut at 50 ms
    assert d.hook_stats == {"scheduled": 1, "outbreaks": 0, "reports": 1, "hook_errors": 1}
    # a hook still running at shutdown is cancelled by drain_hooks and reported
    install_fake_hooks(monkeypatch, outbreak="ok", investigate=slow)
    d2 = make(attack_ch(), FakeCheckpoint(), scripted(MALICIOUS), outbreak=True, investigator=True)
    await d2.run_once()
    (task,) = list(d2._hook_tasks)
    assert await d2.drain_hooks(0.05) == 1
    assert task.cancelled() or task.done()
    assert not [t for t in d2._hook_tasks if not t.done()]


async def test_sweep_runs_hooks_once_for_incidents_the_checkpoint_contained_itself(monkeypatch):
    calls = install_fake_hooks(monkeypatch)
    now = L.now_ms()
    hold_inc = {"id": "inc-hold", "agent_id": OTHER, "rule": "hold", "opened_ms": now - 1000, "contained_ms": now - 900,
                "outbreak": None, "report_md": None, "steps": []}
    uncontained = {"id": "inc-open", "agent_id": OTHER, "rule": "hold", "opened_ms": now, "contained_ms": None}
    old = {"id": "inc-old", "agent_id": AGENT, "rule": "honeytoken", "opened_ms": now - 60 * 60_000, "contained_ms": now - 60 * 60_000}
    done = {"id": "inc-done", "agent_id": AGENT, "rule": "hold", "opened_ms": now, "contained_ms": now,
            "outbreak": {"source_id": "t", "exposed_agents": [], "blocked_destinations": [], "query_ms": 1.0}, "report_md": "# r"}
    cp = FakeCheckpoint(open_incidents=[hold_inc, uncontained, old, done])
    d = make(FakeCH({"funnel": []}), cp, scripted(MALICIOUS), outbreak=True, investigator=True)
    first = await d.run_once()
    assert first["hooks_scheduled"] == 1
    await d.drain_hooks(5.0)
    assert [c[0] for c in calls["outbreak"]] == ["inc-hold"] and [c[0] for c in calls["investigate"]] == ["inc-hold"]
    second = await d.run_once()  # the same open incident is not hooked twice by this process
    assert second["hooks_scheduled"] == 0 and len(calls["outbreak"]) == 1
    # only the missing half is run: an incident with its outbreak but no report gets just the investigator
    half = {"id": "inc-half", "agent_id": AGENT, "rule": "hold", "opened_ms": now, "contained_ms": now,
            "outbreak": {"source_id": "t", "exposed_agents": [], "blocked_destinations": [], "query_ms": 1.0}, "report_md": None}
    cp.open_incidents.append(half)
    third = await d.run_once()
    await d.drain_hooks(5.0)
    assert third["hooks_scheduled"] == 1 and len(calls["outbreak"]) == 1 and [c[0] for c in calls["investigate"]][-1] == "inc-half"
    # with the hooks off the sweep never runs
    d_off = make(FakeCH({"funnel": []}), FakeCheckpoint(open_incidents=[hold_inc]), scripted(MALICIOUS))
    assert (await d_off.run_once())["hooks_scheduled"] == 0


async def test_module_run_once_keeps_hooks_off_unless_asked(monkeypatch):
    calls = install_fake_hooks(monkeypatch)
    fcp = FakeCheckpoint()

    async def malicious(inp: QuickCheckInput) -> Verdict:
        return MALICIOUS

    async with httpx.AsyncClient(transport=httpx.MockTransport(fcp.handler), base_url="http://checkpoint.test") as c:
        summary = await L.run_once(c, ch=attack_ch(), classify=malicious, token="tok")
        assert summary["blocks"] == 1 and summary["hooks_scheduled"] == 0 and "hooks_pending" not in summary
        await asyncio.sleep(0.01)
        assert calls["outbreak"] == [] and calls["investigate"] == []
        fcp2 = FakeCheckpoint()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fcp2.handler), base_url="http://checkpoint.test") as c2:
        summary2 = await L.run_once(c2, ch=attack_ch(), classify=malicious, token="tok", hooks=True)
        assert summary2["blocks"] == 1 and summary2["hooks_scheduled"] == 1 and summary2["hooks_pending"] == 0
        assert [x[0] for x in calls["outbreak"]] == ["inc-test"] and calls["outbreak"][0][1] is c2
        assert [x[0] for x in calls["investigate"]] == ["inc-test"]
        assert (await c2.get("/status")).status_code == 200  # the caller's client is still open


# ---------------------------------------------------------------------------
# --quorum wiring: ai.quorum.classify_quorum when importable, single classify otherwise
# ---------------------------------------------------------------------------


def test_quorum_flag_falls_back_to_single_classify_when_ai_quorum_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "ai.quorum", None)  # `from ai.quorum import ...` -> ImportError
    classify, label = L.resolve_classify(quorum=True)
    assert callable(classify) and not label.startswith("ai.quorum")
    d = Detector(FakeCH(), FakeCheckpoint().client(), None, quorum=True, outbreak=False, investigator=False)
    assert d.quorum is True and d.classify is not None and not d.classify_source.startswith("ai.quorum")
    assert d.classify_source == label


def test_quorum_flag_picks_ai_quorum_when_importable(monkeypatch):
    async def classify_quorum(inp: QuickCheckInput) -> Verdict:
        return MALICIOUS.model_copy(update={"decision_source": "quorum", "model_ids": ["a", "b"]})

    fake = types.ModuleType("ai.quorum")
    fake.classify_quorum = classify_quorum  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ai.quorum", fake)
    classify, label = L.resolve_classify(quorum=True)
    assert classify is classify_quorum and label == "ai.quorum.classify_quorum"
    d = Detector(FakeCH(), FakeCheckpoint().client(), None, quorum=True, outbreak=False, investigator=False)
    assert d.classify is classify_quorum and d.classify_source == "ai.quorum.classify_quorum"
    # without the flag the quorum is never picked, even when importable
    single, single_label = L.resolve_classify(quorum=False)
    assert single is not classify_quorum and not single_label.startswith("ai.quorum")
    assert Detector(FakeCH(), FakeCheckpoint().client(), None, outbreak=False, investigator=False).quorum is False


async def test_quorum_verdict_is_sent_as_quorum_with_both_model_ids(monkeypatch):
    quorum_v = MALICIOUS.model_copy(update={"decision_source": "quorum", "model_ids": ["akash/small", "akash/large"], "latency_ms": 2100.0})
    cp = FakeCheckpoint()
    d = make(attack_ch(), cp, scripted(quorum_v), quorum=True)
    summary = await d.run_once()
    assert summary["blocks"] == 1 and summary["classify_source"] == "quorum"
    (block,) = cp.posts("/block/")
    assert block["decision_source"] == "quorum" and block["model_ids"] == ["akash/small", "akash/large"]
    assert block["latency_ms"] == 2100.0
    assert cp.posts("/heartbeat")[-1]["metrics"]["hooks"]["quorum"] is True


def test_cli_accepts_the_hook_and_quorum_flags():
    runner = CliRunner()
    help_out = runner.invoke(L.app, ["--help"]).output
    for flag in ("--no-outbreak", "--no-investigator", "--quorum"):
        assert flag in help_out, flag
    res = runner.invoke(
        L.app, ["--once", "--no-outbreak", "--no-investigator", "--quorum", "--checkpoint-url", "http://127.0.0.1:18999"]
    )
    assert res.exit_code == 1, res.output  # flags accepted; the throwaway port is unreachable -> error reported
    out = res.stdout
    summary = json.loads(out[out.index("{") :])
    assert summary["error"].startswith("checkpoint /status") and summary["hooks_scheduled"] == 0


# ---------------------------------------------------------------------------
# opt-in rules: secret_exfil_direct wiring, role_grab / log_tamper keyed past the policy alert
# ---------------------------------------------------------------------------

DIRECT_EVENTS = [
    _ev(T0, "read_file", "ticket:5251"),
    _ev(T0 + 700, "read_file", "/app/secrets.yaml"),
    _ev(T0 + 1500, "http_post", "https://transfer.example.net/u", is_external=1),
]
DIRECT_HIT = {"agent_id": AGENT, "last_step_ts_ms": T0 + 1500, "first_step_ts_ms": T0 + 700, "n_events": 3}


def test_secret_exfil_direct_is_opt_in_and_registered():
    from ai.rules import RULE_SECRET_EXFIL_DIRECT

    assert L.DEFAULT_RULES == ("secret_theft",)  # default behaviour unchanged
    assert L.RULE_SECRET_EXFIL_DIRECT == RULE_SECRET_EXFIL_DIRECT == "secret_exfil_direct"
    assert L.SQL_FOR_RULE["secret_exfil_direct"] == "secret_exfil_direct"
    sql = load_sql("secret_exfil_direct")
    assert "windowFunnel(60000)(" in sql and ") = 2" in sql and "is_external = 1" in sql
    assert "transform(agent_id, {ids:Array(String)}, {wms:Array(Int64)}, toInt64(0))" in sql
    assert "synthetic = 0" in sql and "{window_s:UInt32}" in sql and "run_command" not in sql.split("SELECT", 1)[1]
    for name in ("role_grab", "log_tamper"):
        body = load_sql(name).split("SELECT", 1)[1]
        assert "last_step_ts_ms" in body and "result" not in body  # any result counts (denied attempts too)


async def test_secret_exfil_direct_hit_goes_through_classify_and_blocks_with_a_plain_rule_string():
    ch = FakeCH({"secret_exfil_direct": [DIRECT_HIT], "recent_events": DIRECT_EVENTS})
    cp, classify = FakeCheckpoint(), scripted(MALICIOUS)
    summary = await make(ch, cp, classify, rules=("secret_theft", "secret_exfil_direct")).run_once()
    assert summary["hits"] == 1 and summary["blocks"] == 1 and ch.names()[:2] == ["funnel", "secret_exfil_direct"]
    (inp,) = classify.calls
    assert inp.rule == "secret_exfil_direct" and [e["action"] for e in inp.events] == ["read_file", "read_file", "http_post"]
    assert "secret_exfil_direct" in inp.context and "secrets.yaml" not in inp.context and "example.net" not in inp.context
    (block,) = cp.posts("/block/")
    assert block["rule"] == "secret_exfil_direct" and block["last_step_ts_ms"] == T0 + 1500
    assert block["decision_source"] == "akashml" and block["model_ids"] == ["akash/test-small"]


async def test_secret_exfil_direct_benign_verdict_only_alerts():
    ch = FakeCH({"secret_exfil_direct": [DIRECT_HIT], "recent_events": DIRECT_EVENTS})
    cp = FakeCheckpoint()
    summary = await make(ch, cp, scripted(BENIGN), rules=("secret_exfil_direct",)).run_once()
    assert summary["blocks"] == 0 and summary["alerts"] == 1 and cp.posts("/block/") == []
    assert cp.posts("/alerts")[0]["rule"] == "secret_exfil_direct"


async def test_agent_blocked_by_one_rule_is_skipped_by_later_rules_in_the_same_pass():
    ch = FakeCH({"funnel": [FUNNEL_HIT], "secret_exfil_direct": [{**FUNNEL_HIT}], "recent_events": ATTACK_EVENTS})
    cp, classify = FakeCheckpoint(), scripted(MALICIOUS)
    summary = await make(ch, cp, classify, rules=("secret_theft", "secret_exfil_direct")).run_once()
    assert summary["hits"] == 2 and summary["blocks"] == 1 and summary["skipped"] == 1
    assert len(classify.calls) == 1 and [b["rule"] for b in cp.posts("/block/")] == ["secret_theft"]


async def test_role_grab_hit_is_keyed_by_the_newest_row_not_the_policy_alert():
    denied_at, newest = T0 + 500, T0 + 1300
    policy_key = f"{OTHER}|role_grab|{denied_at}"  # what the checkpoint recorded for its hold_policy deny
    events = [
        _ev(T0, "read_file", "ticket:5123"),
        _ev(denied_at, "assume_role", "role/cluster-admin", result="denied"),
        _ev(newest, "read_file", "/etc/app/.env"),
    ]
    row = {"agent_id": OTHER, "ts_ms": denied_at, "target": "role/cluster-admin", "last_step_ts_ms": newest}
    ch = FakeCH({"role_grab": [row], "recent_events": events})
    cp = FakeCheckpoint(verdict_keys=[policy_key])
    summary = await make(ch, cp, rule_classify, rules=("role_grab",)).run_once()
    assert summary["blocks"] == 1
    (block,) = cp.posts("/block/")
    assert block["rule"] == "role_grab" and block["last_step_ts_ms"] == newest and block["verdict"] == "malicious"

    # the denied call is the agent's newest row: same key as the policy alert -> skipped, no classify
    lone = {**row, "last_step_ts_ms": denied_at}
    ch2, cp2, classify2 = FakeCH({"role_grab": [lone]}), FakeCheckpoint(verdict_keys=[policy_key]), scripted(MALICIOUS)
    summary2 = await make(ch2, cp2, classify2, rules=("role_grab",)).run_once()
    assert summary2["skipped"] == 1 and classify2.calls == [] and cp2.posts("/block/") == []


def test_local_rule_secret_exfil_direct():
    hit = local_rule_verdict(_qc("secret_exfil_direct", DIRECT_EVENTS))
    assert (hit.verdict, hit.confidence, hit.decision_source) == ("malicious", 0.9, "rule_only")
    internal = DIRECT_EVENTS[:2] + [_ev(T0 + 1500, "http_post", "https://api.internal.example/v1/x")]
    assert local_rule_verdict(_qc("secret_exfil_direct", internal)).verdict == "benign"
    late = DIRECT_EVENTS[:2] + [_ev(T0 + 700 + 60_001, "http_post", "https://x.example.net/u", is_external=1)]
    assert local_rule_verdict(_qc("secret_exfil_direct", late)).verdict == "benign"
    pem = [_ev(T0, "read_file", "/HOME/app/Deploy.PEM"), _ev(T0 + 10, "http_post", "https://x.example.net", is_external=1)]
    assert local_rule_verdict(_qc("secret_exfil_direct", pem)).verdict == "malicious"


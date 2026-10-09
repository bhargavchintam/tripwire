"""eval.runner + eval.pricing against Bindu's real checkpoint app in-process (InMemoryWriter, ch_enabled=False).

No ClickHouse, no sockets, no model calls: the detector is an injected fake that blocks the agents whose id
contains a pattern, ClickHouse visibility is an injected fake and the prices are fixed. The checkpoint itself
is real, so synchronous containment (honeytoken) and the /alerts, /incidents, /heartbeat and /evidence paths
are exercised end to end.

Run: uv run pytest tests/unit/test_eval_runner.py -q
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from agents.checkpoint_client import CheckpointClient
from checkpoint.app import create_app
from checkpoint.writer import InMemoryWriter
from eval import pricing
from eval import runner as R
from tests.unit.conftest import make_settings
from tripwire.contracts import AlertPayload, EvidenceBundle, Heartbeat, Scenario

TOKEN = "change-me"
AK = pricing.PricePoint("akashml", "test/small", 2e-7, 5.2e-7, source="test (fixed)", fetched=True)
OA = pricing.PricePoint("openai", "gpt-4o-mini", 1.5e-7, 6e-7, source="test (fixed)", fetched=False)
PRICES = pricing.Prices(akashml=AK, openai=OA, priced_on="2026-10-09")
TOKENS = (300, 20)  # tokens_in, tokens_out stamped on every fake-detector verdict
ATTACK_NAMES = ("secret_theft_a", "secret_theft_b")
BENIGN_NAMES = ("normal_ops_a", "normal_ops_b")


# ---------------------------------------------------------------------------
# fixtures + fakes
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    return create_app(
        writer=InMemoryWriter(),
        settings=make_settings(),
        state_path=tmp_path / "state.json",
        ch_enabled=False,
        fixtures_dir=R.FIXTURES_DIR,
        web_dist=None,
    )


@pytest.fixture
async def clients(app):
    transport = httpx.ASGITransport(app=app)
    cp = CheckpointClient.from_transport(transport, "http://test", TOKEN)
    http = httpx.AsyncClient(transport=transport, base_url="http://test", headers={"X-Tripwire-Token": TOKEN})
    try:
        yield cp, http
    finally:
        await http.aclose()
        await cp.aclose()


def write_case(dst: Path, name: str, source: str, scale: float = 0.02) -> Path:
    """A copy of fixtures/<source>.json named <name> with the offsets compressed (fast tests, same step order)."""
    sc = Scenario.model_validate_json((R.FIXTURES_DIR / f"{source}.json").read_text())
    sc.name = name
    for st in sc.steps:
        st.offset_ms = int(st.offset_ms * scale)
    dst.mkdir(parents=True, exist_ok=True)
    p = dst / f"{name}.json"
    p.write_text(sc.model_dump_json(indent=1))
    return p


@pytest.fixture
def cases_dir(tmp_path) -> Path:
    d = tmp_path / "cases"
    for name in ATTACK_NAMES:
        write_case(d / "attack", name, "secret_theft")
    for name in BENIGN_NAMES:
        write_case(d / "benign", name, "normal_ops")
    return d


async def always_visible(agent: str) -> int:
    return 1_000_000


def blocking_detector(pattern: str):
    """Fake detector: POST /block for every active, unblocked agent whose id contains `pattern`, with a
    model-labelled verdict detected 500 ms after its last step, so the runner's bookkeeping is exercised."""
    calls: list[int] = []

    async def detect(http: httpx.AsyncClient) -> dict[str, Any]:
        st = (await http.get("/status")).json()
        n = 0
        for agent in st["active"]:
            if pattern in agent and agent not in st["blocked"]:
                now = R.now_ms()
                p = AlertPayload(
                    agent_id=agent,
                    rule="secret_theft",
                    verdict="malicious",
                    confidence=0.95,
                    reason="fake detector",
                    decision_source="akashml",
                    detected_at_ms=now,
                    last_step_ts_ms=now - 500,
                    model_ids=["test/small"],
                    latency_ms=120.0,
                    tokens_in=TOKENS[0],
                    tokens_out=TOKENS[1],
                )
                r = await http.post(f"/block/{agent}", json=p.model_dump())
                assert r.status_code == 200, r.text
                n += 1
        calls.append(n)
        return {"hits": n, "blocks": n, "alerts": 0, "error": None, "classify_source": "akashml", "timings_ms": {"funnel": 1.0}}

    detect.calls = calls  # type: ignore[attr-defined]
    return detect


async def run(cs: R.CaseSet, clients, pattern: str = "attack", **kw) -> R.EvalResult:
    cp, http = clients
    return await R.run_eval(
        cs.cases,
        cp=cp,
        http=http,
        visible=always_visible,
        detect=blocking_detector(pattern),
        prices=PRICES,
        wait_s=kw.pop("wait_s", 0.3),
        note=cs.note,
        skipped=cs.skipped,
        run_id="t3st01",
        **kw,
    )


# ---------------------------------------------------------------------------
# the core coroutine
# ---------------------------------------------------------------------------


async def test_confusion_matrix_and_case_rows_with_injected_detector(cases_dir, clients):
    cs = R.load_cases(cases_dir)
    assert [(c.name, c.label) for c in cs.cases] == [(n, "attack") for n in ATTACK_NAMES] + [(n, "benign") for n in BENIGN_NAMES]
    assert cs.note is None and cs.skipped == []

    res = await run(cs, clients)
    assert (res.tp, res.fp, res.fn, res.tn) == (2, 0, 0, 2)
    assert res.precision == 1.0 and res.recall == 1.0 and res.n_cases == 4
    assert res.n_events == 5 + 5 + 10 + 10 and res.errors == []
    by = {c.name: c for c in res.cases}
    for name in ATTACK_NAMES:
        c = by[name]
        assert c.outcome == "TP" and c.contained and c.mechanism == "detector" and c.rule == "secret_theft", c
        assert c.decision_source == "akashml" and c.verdict == "malicious" and c.detect_ms == 500.0, c
        assert c.agent_id == f"eval-attack-{name}-t3st01" and c.incident_id and c.events == 5
        # cases run concurrently: a pass for one case may block the other while its last step is in flight,
        # so a denied step is possible, but only ever as "blocked" after containment (never pre-block).
        assert c.denied_steps <= 1 and all(s["reason"] == "blocked" for s in c.steps if s["result"] == "denied"), c.steps
        assert [(q.origin, q.tokens_in, q.tokens_out, q.blocking) for q in c.quick_checks] == [("alert:detector", 300, 20, True)]
        assert c.contain_ms is not None and c.contain_ms >= c.detect_ms
    for name in BENIGN_NAMES:
        c = by[name]
        assert c.outcome == "TN" and not c.contained and c.mechanism == "none" and c.decision_source is None, c
        assert c.events == 10 and c.quick_checks == [] and c.denied_steps == 0 and c.error is None
        assert c.detect_passes == 2  # two in-process passes, then the status poll
    assert res.median_detect_ms == 500.0 and res.mechanisms == {"detector": 2}
    assert res.hold_enabled is False and res.detector.startswith("detection.loop.run_once")

    # cost: 2 quick checks over 30 events, mean cost per call from the measured tokens, both providers
    ak_call = TOKENS[0] * 2e-7 + TOKENS[1] * 5.2e-7
    oa_call = TOKENS[0] * 1.5e-7 + TOKENS[1] * 6e-7
    assert res.n_quick_checks == 2 and res.n_model_calls == 2 and res.measured_calls == [[300, 20], [300, 20]]
    assert res.mean_cost_akashml_per_call == pytest.approx(ak_call)
    assert res.mean_cost_openai_per_call == pytest.approx(oa_call)
    assert res.cost_akashml == pytest.approx(2 / 30 * 1000 * ak_call)
    assert res.cost_openai == pytest.approx(2 / 30 * 1000 * oa_call)
    assert res.akashml_model == "test/small" and res.openai_model == "gpt-4o-mini"
    assert res.priced_on.startswith("2026-10-09") and "from this run" in res.tokens_measured
    assert "2 quick checks / 30 events" in res.cost_note

    # the checkpoint really quarantined the attack agents and left the benign ones alone
    st = await clients[0].status()
    assert {by[n].agent_id for n in ATTACK_NAMES} <= set(st.blocked)
    assert not ({by[n].agent_id for n in BENIGN_NAMES} & set(st.blocked))


async def test_false_positives_lower_precision(cases_dir, clients):
    res = await run(R.load_cases(cases_dir), clients, pattern="eval-")  # blocks every eval agent
    assert (res.tp, res.fp, res.fn, res.tn) == (2, 2, 0, 0)
    assert res.precision == 0.5 and res.recall == 1.0
    assert {c.outcome for c in res.cases if c.label == "benign"} == {"FP"}
    assert res.mechanisms == {"detector": 4} and res.n_quick_checks == 4


async def test_misses_lower_recall_and_undefined_precision(cases_dir, clients):
    res = await run(R.load_cases(cases_dir), clients, pattern="no-such-agent")  # blocks nothing
    assert (res.tp, res.fp, res.fn, res.tn) == (0, 0, 2, 2)
    assert res.precision is None and res.recall == 0.0
    assert res.mechanisms == {} and res.median_detect_ms is None
    assert res.n_quick_checks == 0 and res.cost_akashml is None and res.cost_openai is None
    assert "no quick check" in res.cost_note
    assert all(c.detect_passes == 2 for c in res.cases)


async def test_fallback_fixtures_and_synchronous_honeytoken_containment(tmp_path, clients):
    empty = tmp_path / "nocases"
    (empty / "attack").mkdir(parents=True)
    (empty / "benign").mkdir()
    cs = R.load_cases(empty)
    assert [(c.name, c.label) for c in cs.cases] == [
        ("secret_theft", "attack"),
        ("honeytoken_exfil", "attack"),
        ("normal_ops", "benign"),
    ]
    assert cs.note and "fell back" in cs.note and "n_cases = 3" in cs.note and cs.skipped == []

    # the detector blocks nothing: only the checkpoint's own (synchronous) containment can count
    res = await run(cs, clients, pattern="no-such-agent")
    assert res.fallback_note == cs.note and res.n_cases == 3
    by = {c.name: c for c in res.cases}
    h = by["honeytoken_exfil"]
    assert h.outcome == "TP" and h.contained and h.mechanism == "honeytoken", h
    assert h.decision_source == "honeytoken" and h.verdict == "malicious" and h.rule == "honeytoken"
    assert h.denied_steps == 2  # the send (honeytoken) and the step after it (blocked)
    assert h.detect_ms is not None and 0 <= h.detect_ms < 5000 and h.contain_ms == h.detect_ms
    assert h.quick_checks == [] and h.detect_passes == 0  # no model call; no detection pass was needed
    assert by["secret_theft"].outcome == "FN" and by["secret_theft"].detect_passes == 2
    assert by["normal_ops"].outcome == "TN" and by["normal_ops"].events == 10
    assert (res.tp, res.fp, res.fn, res.tn) == (1, 0, 1, 1) and res.precision == 1.0 and res.recall == 0.5
    assert res.mechanisms == {"honeytoken": 1} and res.n_events == 5 + 5 + 10
    # the honeytoken's synchronous denial is never averaged into the detector's time to detect
    assert res.median_detect_ms is None and res.median_sync_contain_ms == h.detect_ms
    assert res.n_quick_checks == 0 and res.cost_akashml is None and res.cost_openai is None
    assert "fell back" in res.note() and "no quick check" in res.note()


async def test_heartbeat_matches_contract_and_feeds_evidence(cases_dir, clients):
    cp, http = clients
    res = await run(R.load_cases(cases_dir), clients)
    hb = R.heartbeat_payload(res)
    assert hb.source == "eval" and hb.query_timings_ms == {}
    body = hb.model_dump()
    json.dumps(body)  # serialisable as sent
    Heartbeat.model_validate(body)
    m = body["metrics"]
    assert m["n_cases"] == 4 and m["precision"] == 1.0 and m["recall"] == 1.0
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 0, 0, 2)
    assert len(m["cases"]) == 4 and {c["outcome"] for c in m["cases"]} == {"TP", "TN"}
    assert m["akashml_model"] == "test/small" and m["openai_model"] == "gpt-4o-mini" and isinstance(m["note"], str)
    assert m["cost_akashml"] == pytest.approx(res.cost_akashml) and m["priced_on"] == res.priced_on

    await cp.heartbeat(hb)
    ev = EvidenceBundle.model_validate((await http.get("/evidence")).json())
    assert ev.precision == 1.0 and ev.recall == 1.0 and ev.n_cases == 4
    assert ev.cost_akashml == pytest.approx(res.cost_akashml) and ev.cost_openai == pytest.approx(res.cost_openai)
    assert ev.priced_on == res.priced_on


async def test_reports_written_and_markdown_has_the_tables(cases_dir, clients, tmp_path):
    res = await run(R.load_cases(cases_dir), clients)
    out = tmp_path / "reports"
    jp, mp = R.write_reports(res, out)
    assert jp.exists() and mp.exists() and jp.name.startswith("eval_") and jp.suffix == ".json" and mp.suffix == ".md"
    data = json.loads(jp.read_text())
    assert data["n_cases"] == 4 and data["precision"] == 1.0 and len(data["cases"]) == 4 and data["run_id"] == "t3st01"
    md = mp.read_text()
    for needle in (
        "## Summary",
        "## Cost per 1,000 events",
        "## Cases",
        "secret_theft_a",
        "normal_ops_b",
        "precision",
        "test/small",
        "gpt-4o-mini",
        "test (fixed)",
        "2 quick checks / 30 events",
    ):
        assert needle in md, needle
    table = R.summary_table(res)
    assert "precision=1.000" in table and "secret_theft_b" in table and "TN" in table


def _qc(source: str, tokens_in: int, tokens_out: int) -> R.QuickCheck:
    return R.QuickCheck("alert:hold", "hold", source, "benign", 0.9, "r", ["m"] if source != "rule_only" else [],
                        100.0, tokens_in, tokens_out, False)


def test_finalize_costs_imputes_model_checks_without_tokens_and_zeroes_rule_only():
    """A hold-mode model alert arrives without token counts (the checkpoint drops them): it is priced at the mean
    of the measured model calls, never at 0; a rule_only check costs 0 tokens; the report says what happened."""
    res = R.EvalResult(run_id="x", started_utc="t")
    res.n_events = 100
    res.cases = [R.CaseResult("a", "benign", "eval-a")]
    res.cases[0].quick_checks = [_qc("akashml", 800, 50), _qc("akashml", 0, 0), _qc("rule_only", 0, 0)]
    R.finalize_costs(res, PRICES)
    assert res.measured_calls == [[800, 50], [800, 50], [0, 0]]
    assert "1 model-answered check(s) arrived without token counts" in res.tokens_measured
    per_call = 800 * 2e-7 + 50 * 5.2e-7
    assert res.mean_cost_akashml_per_call == pytest.approx(2 * per_call / 3)
    assert res.cost_akashml == pytest.approx(3 / 100 * 1000 * 2 * per_call / 3)  # == 2 priced calls per 100 events
    # re-measured path: the run's checks carried no tokens at all -> the measured windows price every check
    res2 = R.EvalResult(run_id="y", started_utc="t")
    res2.n_events = 50
    res2.cases = [R.CaseResult("b", "attack", "eval-b")]
    res2.cases[0].quick_checks = [_qc("rule_only", 0, 0)]
    R.finalize_costs(res2, PRICES, measured=[(820, 54)])
    assert res2.measured_calls == [[820, 54]] and res2.tokens_measured.startswith("re-measured")
    assert "what these checks cost when the model answers" in res2.tokens_measured
    assert res2.cost_akashml == pytest.approx(1 / 50 * 1000 * (820 * 2e-7 + 54 * 5.2e-7))


# ---------------------------------------------------------------------------
# case loading
# ---------------------------------------------------------------------------


def test_load_cases_picks_up_new_files_without_code_changes(tmp_path):
    d = tmp_path / "cases"
    assert R.load_cases(d).note  # nothing there yet -> the fallback fixtures, and it says so
    write_case(d / "attack", "one", "secret_theft")
    cs = R.load_cases(d)
    assert [c.name for c in cs.cases] == ["one"] and cs.note is None
    write_case(d / "benign", "two", "normal_ops")
    (d / "benign" / "broken.json").write_text("{not json")
    cs = R.load_cases(d)
    assert [(c.name, c.label) for c in cs.cases] == [("one", "attack"), ("two", "benign")]
    assert len(cs.skipped) == 1 and "broken.json" in cs.skipped[0]
    assert R.agent_id_for("attack", "weird name/ü", "abc123") == "eval-attack-weird_name-abc123"
    assert R.outcome_of("attack", True) == "TP" and R.outcome_of("attack", False) == "FN"
    assert R.outcome_of("benign", True) == "FP" and R.outcome_of("benign", False) == "TN"


# ---------------------------------------------------------------------------
# pricing math
# ---------------------------------------------------------------------------


def test_pricing_math_with_fixed_tokens_and_prices():
    assert pricing.cost_usd(812, 31, AK) == pytest.approx(812 * 2e-7 + 31 * 5.2e-7)
    assert pricing.cost_usd(-5, 10, AK) == pytest.approx(10 * 5.2e-7)  # negative counts clamp to 0
    assert pricing.mean_cost_per_call([], AK) is None
    two = (100 * 2e-7 + 10 * 5.2e-7 + 300 * 2e-7 + 30 * 5.2e-7) / 2
    assert pricing.mean_cost_per_call([(100, 10), (300, 30)], AK) == pytest.approx(two)
    assert pricing.cost_per_1000_events(2, 30, 7.04e-5) == pytest.approx(2 / 30 * 1000 * 7.04e-5)
    assert pricing.cost_per_1000_events(0, 30, 1.0) is None
    assert pricing.cost_per_1000_events(2, 0, 1.0) is None
    assert pricing.cost_per_1000_events(2, 30, None) is None
    assert AK.per_1m == pytest.approx((0.2, 0.52))


def test_pricing_parsers_and_dated_constant():
    payload = {
        "data": [
            {"id": "x/large", "pricing": {"input": "0.0000005", "output": "0.000001"}},
            {"id": "test/small", "pricing": {"input": "0.0000002", "output": "0.00000052"}},
        ]
    }
    pp = pricing.parse_akashml_models(payload, "test/small", source="s")
    assert pp is not None and pp.model == "test/small" and pp.fetched
    assert (pp.input_per_token, pp.output_per_token) == (2e-7, 5.2e-7)
    assert pricing.parse_akashml_models(payload, "missing/model", source="s") is None
    assert pricing.parse_akashml_models({"data": [{"id": "test/small"}]}, "test/small", source="s") is None
    assert pricing.parse_akashml_models({"nope": 1}, "test/small", source="s") is None

    html = (
        'x [0,"gpt-4o-mini-tts"],[0,0.6],[0,null],[0,12] y '
        "[0,&quot;gpt-4o-mini&quot;],[0,0.15],[0,0.075],[0,0.6] z"
    )
    assert pricing.parse_openai_pricing_html(html) == (0.15, 0.6)  # suffixed ids never match
    assert pricing.parse_openai_pricing_html("nothing here") is None

    c = pricing.constant_openai_price(why="403")
    assert c.fetched is False and c.source.startswith("constant (fetch failed)") and "403" in c.source
    assert c.per_1m == pytest.approx(pricing.OPENAI_CONSTANT_PER_1M)
    prices = pricing.Prices(akashml=None, openai=c, priced_on="2026-10-09")
    assert "not fetched" in prices.label() and "constant" in prices.label() and prices.to_dict()["akashml"] is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_exits_2_when_checkpoint_unreachable(tmp_path):
    out = tmp_path / "reports"
    res = CliRunner().invoke(
        R.app,
        ["--checkpoint-url", "http://127.0.0.1:18999", "--no-heartbeat", "--out", str(out), "--cases", str(tmp_path / "none")],
    )
    assert res.exit_code == 2, res.output
    assert not out.exists()  # nothing ran, nothing written

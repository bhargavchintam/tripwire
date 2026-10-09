"""eval/model_compare.py: window builder + rule heuristic, scoring, agreement/quorum math, and an end-to-end
run against two in-process fake LLMs (tests/fakes/fake_llm.py via httpx.ASGITransport; no network)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from ai.llm import LLM, MAX_TOKENS
from eval import model_compare as mc
from eval.runner import load_cases
from tests.fakes.fake_llm import make_app

CASES = Path(__file__).resolve().parents[2] / "fixtures" / "eval"


def fake(script):
    app = make_app(script)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fake")
    return LLM(
        "akashml",
        "x",
        "http://fake/v1",
        client=AsyncOpenAI(api_key="x", base_url="http://fake/v1", http_client=http),
    ), app


def by_name() -> dict[str, mc.Window]:
    return {w.name: w for w in mc.load_windows(CASES)}


def call(case, label, verdict, status="ok", ms=100.0, tin=100, tout=10):
    return mc.Call(
        case=case,
        label=label,
        rule="r",
        model="m",
        status=status,
        verdict=verdict,
        confidence=0.9 if status == "ok" else None,
        reason="",
        latency_ms=ms,
        tokens_in=tin,
        tokens_out=tout,
    )


# ---------------------------------------------------------------- windows + rule heuristic
def test_window_builder_and_rule_heuristic_on_three_cases():
    ws = by_name()
    theft = ws["a1-secret-encode-send-01"]
    assert theft.label == "attack" and theft.rule == "secret_theft"
    evs = theft.inp.events
    assert [e["action"] for e in evs] == ["read_file", "read_file", "run_command", "http_post"]
    assert [e["ts_ms"] for e in evs] == sorted(e["ts_ms"] for e in evs)  # oldest first
    assert evs[0]["ts_ms"] == mc.BASE_TS_MS and evs[-1]["is_external"] == 1 and evs[1]["is_external"] == 0
    assert evs[1]["tainted_by"] == "ticket:5101" and evs[1]["bytes"] == 300 and evs[0]["result"] == "ok"
    assert theft.inp.context == mc.CONTEXT

    grab = ws["a4-privilege-grab-01"]
    assert grab.rule == "role_grab"
    denied = [e for e in grab.inp.events if e["action"] == "assume_role"]
    assert denied and all(e["result"] == "denied" and e["reason"] == "hold_policy" for e in denied)

    benign = ws["b1-startup-config-01"]
    assert benign.label == "benign" and benign.rule == "baseline_novelty"


def test_rule_heuristic_branches():
    def e(action, target, ext=0):
        return {"action": action, "target": target, "is_external": ext}

    assert mc.rule_for([e("disable_logging", "audit")]) == "log_tamper"
    assert (
        mc.rule_for([e("read_file", "/app/.env"), e("http_post", "https://x.example", 1)])
        == "secret_exfil_direct"
    )
    assert (
        mc.rule_for(
            [
                e("read_file", "/app/.env"),
                e("run_command", "xxd -p /app/.env"),
                e("http_post", "https://x.example", 1),
            ]
        )
        == "secret_theft"
    )
    assert mc.rule_for([e("read_file", "/app/.env"), e("http_post", "http://api.internal.example", 0)]) == (
        "baseline_novelty"
    )


def test_all_sixty_cases_load_with_a_rule():
    cases = load_cases(CASES).cases
    ws = mc.load_windows(CASES)
    assert len(ws) == len(cases) == 60
    assert {w.rule for w in ws} <= {
        "secret_theft",
        "secret_exfil_direct",
        "role_grab",
        "log_tamper",
        "baseline_novelty",
    }
    assert [w.label for w in mc.mixed(ws)[:4]] == ["attack", "benign", "attack", "benign"]


# ---------------------------------------------------------------- scoring
def test_scoring_counts_uncertain_and_errors_as_fn_on_attacks():
    calls = [
        call("a1", "attack", "malicious"),
        call("a2", "attack", "uncertain"),
        call("a3", "attack", "none", status="timeout", ms=10000.0, tin=0, tout=0),
        call("a4", "attack", "benign", ms=3000.0),
        call("b1", "benign", "malicious", ms=2600.0),
        call("b2", "benign", "benign"),
        call("b3", "benign", "none", status="unparseable", tout=400),
        call("b4", "benign", "uncertain"),
    ]
    price = mc.PricePoint("akashml", "m", 1e-6, 2e-6, source="test", fetched=True)
    s = mc.summarize(calls, price, qc_per_event=26 / 232)
    assert (s["tp"], s["fp"], s["fn"], s["tn"]) == (1, 1, 3, 3)
    assert s["precision"] == pytest.approx(0.5) and s["recall"] == pytest.approx(0.25)
    assert s["accuracy"] == pytest.approx(4 / 8)
    assert (s["uncertain"], s["uncertain_on_attack"], s["timeouts"], s["errors"]) == (2, 1, 1, 1)
    assert s["within_hold"] == pytest.approx(
        4 / 8
    )  # 100 ms x4 valid; 2600/3000 miss; timeout/unparseable never
    assert s["within_detector"] == pytest.approx(6 / 8)
    assert s["median_ms"] == pytest.approx(100.0) and s["p95_ms"] == pytest.approx(3000.0)
    # tokens/cost over the 7 calls that reported usage (the timeout had none)
    per_call = (6 * (100 * 1e-6 + 10 * 2e-6) + (100 * 1e-6 + 400 * 2e-6)) / 7
    assert s["usd_per_call"] == pytest.approx(per_call)
    assert s["usd_per_1k_events"] == pytest.approx(per_call * 1000 * 26 / 232)


def test_percentile_nearest_rank():
    assert mc.percentile([], 95) is None
    assert mc.percentile([5.0], 95) == 5.0
    assert mc.percentile([float(i) for i in range(1, 21)], 95) == 19.0


# ---------------------------------------------------------------- agreement / quorum / majority
def test_pair_agreement_and_quorum():
    a = [
        call("a1", "attack", "malicious"),
        call("a2", "attack", "malicious"),
        call("b1", "benign", "malicious"),
        call("b2", "benign", "benign"),
    ]
    b = [
        call("a1", "attack", "malicious"),
        call("a2", "attack", "benign"),
        call("b1", "benign", "malicious"),
        call("b2", "benign", "uncertain"),
    ]
    p = mc.pair_stats(a, b)
    assert p["cases"] == 4 and p["agreement"] == pytest.approx(0.5)  # a1, b1 agree
    assert p["quorum_recall"] == pytest.approx(0.5) and p["quorum_fp"] == pytest.approx(0.5)
    assert p["either"]["recall"] == pytest.approx(1.0) and p["either"]["fp_rate"] == pytest.approx(0.5)


def test_majority_of_three():
    calls = {
        "x": [call("a1", "attack", "malicious"), call("b1", "benign", "malicious")],
        "y": [call("a1", "attack", "malicious"), call("b1", "benign", "benign")],
        "z": [call("a1", "attack", "benign"), call("b1", "benign", "benign")],
    }
    m = mc.majority_stats(calls, ["x", "y", "z"])
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 0, 0, 1)
    assert mc.majority_stats(calls, ["x", "y"]) is None
    summaries = {
        "x": {"usable": True, "median_ms": 300.0, "fail_rate": 0.0},
        "y": {"usable": True, "median_ms": 100.0, "fail_rate": 0.0},
        "z": {"usable": True, "median_ms": 200.0, "fail_rate": 0.5},
        "w": {"usable": True, "median_ms": 400.0, "fail_rate": 0.0},
        "u": {"usable": False},
    }
    models, why = mc.majority_models(summaries)
    assert models == ["y", "x", "w"] and "reliable" in why


# ---------------------------------------------------------------- end to end (two fake LLMs, no network)
async def test_end_to_end_two_fakes_plus_an_unusable_one(tmp_path):
    ws = mc.mixed(mc.load_windows(CASES))[:10]  # 5 attack, 5 benign
    always_mal, app_a = fake([{"json": {"verdict": "malicious", "confidence": 0.9, "reason": "chain"}}])
    always_ben, app_b = fake(
        [{"text": '```json\n{"verdict": "benign", "confidence": 0.7, "reason": "ok"}\n```'}]
    )
    empty, _ = fake([{"text": ""}])  # a thinking model that spent max_tokens: empty content
    specs = [mc.spec_for("fake-mal"), mc.spec_for("fake-ben"), mc.spec_for("Qwen/Qwen3-fake")]
    clients = {"fake-mal": always_mal, "fake-ben": always_ben, "Qwen/Qwen3-fake": empty}
    price = mc.PricePoint("akashml", "fake-mal", 1e-7, 4e-7, source="test", fetched=True)
    result = await mc.run_compare(
        ws,
        specs,
        clients,
        concurrency=3,
        timeout_s=5.0,
        prices={"fake-mal": price, "fake-ben": None},
        price_note="test prices",
        qc=(26, 232, "test"),
    )
    m = result["models"]
    assert m["fake-mal"]["usable"] and (m["fake-mal"]["tp"], m["fake-mal"]["fp"]) == (5, 5)
    assert m["fake-mal"]["usd_per_call"] is not None and m["fake-ben"]["usd_per_call"] is None
    assert (m["fake-ben"]["tn"], m["fake-ben"]["fn"]) == (5, 5)
    assert not m["Qwen/Qwen3-fake"]["usable"] and "empty content" in m["Qwen/Qwen3-fake"]["unusable_reason"]
    assert m["Qwen/Qwen3-fake"]["switches"].endswith('{"chat_template_kwargs": {"enable_thinking": false}}')
    assert len(result["pairs"]) == 1 and result["pairs"][0]["agreement"] == 0.0
    assert result["pairs"][0]["quorum_recall"] == 0.0 and result["majority"] is None
    # every model got exactly the production prompt, one attempt per case (smoke 5 + 10 scored)
    body = app_a.state.calls[0]
    assert len(app_a.state.calls) == 15 and body["temperature"] == 0.0 and body["max_tokens"] == MAX_TOKENS
    assert "<<<EVENTS_JSON" in body["messages"][1]["content"] and "extra_body" not in json.dumps(body)
    assert "chat_template_kwargs" not in body
    jp, mp = mc.write_reports(result, tmp_path)
    md = mp.read_text(encoding="utf-8")
    assert "| model | usable |" in md and "fake-mal + fake-ben" in md and "UNUSABLE" in md
    assert json.loads(jp.read_text(encoding="utf-8"))["n_cases"] == 10


async def test_no_think_switch_reaches_the_provider():
    llm, app = fake([{"json": {"verdict": "benign", "confidence": 0.6, "reason": "fine"}}])
    w = mc.load_windows(CASES)[0]
    c = await mc.ask(llm, mc.spec_for("Qwen/Qwen3.6-35B-A3B"), w, timeout_s=5.0)
    assert c.status == "ok" and c.verdict == "benign" and c.tokens_in > 0
    assert app.state.calls[0]["chat_template_kwargs"] == {"enable_thinking": False}


async def test_timeout_is_recorded_not_raised():
    llm, _ = fake([{"delay_s": 1.0, "json": {"verdict": "malicious", "confidence": 0.9, "reason": "x"}}])
    w = mc.load_windows(CASES)[0]
    c = await mc.ask(llm, mc.spec_for("fake"), w, timeout_s=0.2)
    assert c.status == "timeout" and c.verdict == "none" and c.latency_ms < 1000


def test_rule_blind_mode_presents_every_case_as_baseline():
    ws = mc.load_windows(CASES, rule_mode="baseline")
    assert len(ws) == 60 and {w.rule for w in ws} == {"baseline_novelty"}
    assert {w.inp.rule for w in ws} == {"baseline_novelty"}

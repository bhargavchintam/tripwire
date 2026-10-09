"""Master §12 acceptance test (the core gate), automated and run 3x.

    uv run pytest -m e2e tests/e2e/test_acceptance.py -q

In-process: create_app(...) + httpx.ASGITransport (no server, nothing on :8000), with the
real ClickHouseWriter and CHReader against whatever ClickHouse ``.env`` points at (Cloud or
local Docker). The module skips cleanly when ClickHouse is unreachable; reachability is the
authenticated ``tripwire.loader.clickhouse_reachable()`` (Cloud resets anonymous /ping).

Every agent id is ``acc-<uuid6>-<run>-<role>``, so nothing collides with the live demo agents
or with test_signature.py's ``e2e-*`` ids. At module teardown the rows are removed with a
lightweight ``DELETE ... WHERE agent_id LIKE 'acc-<uuid6>-%'`` (only this run's prefix).

  A. hold mode (always runnable): normal calls ok -> replay secret_theft with hold ON ->
     send denied at the hold step, agent quarantined, next call denied, denial row in
     ClickHouse, incident via GET /incidents -> Restore -> normal call ok, not re-blocked
     within 3 s -> replay again -> blocked again.
  B. detector (hold OFF): runs detection/loop.py once in-process when it exists and takes
     an HTTP client; otherwise skipped ("detector not merged yet (Sripadha)").
  C. honeytoken_exfil -> denied at the send with reason honeytoken; normal_ops -> every
     step ok with hold ON (false-positive check).
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import re
import time
import uuid
from typing import Any, Callable

import httpx
import pytest

from checkpoint.app import create_app
from checkpoint.service import FIXTURES_DIR
from checkpoint.writer import ClickHouseWriter
from tests.unit.conftest import TOKENS, make_settings
from tripwire.ch import client as ch_client
from tripwire.contracts import ReplayStep, Scenario
from tripwire.loader import clickhouse_reachable

pytestmark = pytest.mark.e2e

RUN = uuid.uuid4().hex[:6]
PREFIX = f"acc-{RUN}"
assert re.fullmatch(r"acc-[0-9a-f]{6}", PREFIX)  # safe to inline into the cleanup SQL
RUNS = [1, 2, 3]  # master §12: "the whole sequence works 3 times with no code edits"

CH_VISIBLE_S = 5.0  # denial row must be queryable in ClickHouse within this
NO_REBLOCK_S = 3.0  # after Restore the agent must stay unblocked this long
REPLAY_TIMEOUT_S = 25.0  # secret_theft is 3.3 s of offsets (+ up to 10 s block wait)
HOLD_REASONS = ("hold_rule", "hold_model")
MODEL_SOURCES = ("akashml", "quorum", "openai")


def agent(run: int, role: str) -> str:
    return f"{PREFIX}-{run}-{role}"


def scenario(name: str) -> Scenario:
    return Scenario.model_validate_json((FIXTURES_DIR / f"{name}.json").read_text())


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ch():
    if not clickhouse_reachable():
        pytest.skip("ClickHouse not reachable (authenticated SELECT 1 via tripwire.loader.clickhouse_reachable)")
    c = ch_client()
    yield c
    where = f"agent_id LIKE '{PREFIX}-%'"
    try:
        c.command(f"DELETE FROM events WHERE {where}")  # lightweight delete, only this run's prefix
    except Exception:  # noqa: BLE001 — fall back to a synchronous mutation
        c.command(f"ALTER TABLE events DELETE WHERE {where} SETTINGS mutations_sync = 2")
    left = int(c.command(f"SELECT count() FROM events WHERE {where}"))
    if left:
        c.command(f"ALTER TABLE events DELETE WHERE {where} SETTINGS mutations_sync = 2")
        left = int(c.command(f"SELECT count() FROM events WHERE {where}"))
    assert left == 0, f"cleanup left {left} rows for {PREFIX}-*"


@pytest.fixture
async def env(ch, tmp_path):
    writer = ClickHouseWriter(table="events")
    app = create_app(
        writer=writer,
        settings=make_settings(),
        state_path=tmp_path / "state.json",
        ch_enabled=True,
        fixtures_dir=FIXTURES_DIR,
        web_dist=None,
    )
    svc = app.state.svc
    await svc.ch.warm("hold")
    await writer.start()  # background flush every 200 ms, as in production (ASGITransport skips lifespan)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://acc", timeout=60) as c:
            yield c, svc
    finally:
        await svc.shutdown()
        await writer.stop()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def tool(c: httpx.AsyncClient, agent_id: str, action: str, target: str, **kw: Any) -> dict[str, Any]:
    r = await c.post("/tool", json={"agent_id": agent_id, "action": action, "target": target, **kw})
    assert r.status_code == 200, r.text
    return r.json()


async def tool_step(c: httpx.AsyncClient, step: ReplayStep, agent_id: str) -> dict[str, Any]:
    return await tool(
        c,
        agent_id,
        step.action,
        step.target,
        payload=step.payload,
        tainted_by=step.tainted_by,
        bytes=step.bytes,
        session_id=f"acceptance:{PREFIX}",
    )


async def status(c: httpx.AsyncClient) -> dict[str, Any]:
    r = await c.get("/status")
    assert r.status_code == 200, r.text
    return r.json()


async def set_hold(c: httpx.AsyncClient, enabled: bool) -> None:
    r = await c.post("/config/hold", json={"enabled": enabled})
    assert r.status_code == 200 and r.json()["hold_enabled"] is enabled, r.text


async def replay(c: httpx.AsyncClient, name: str, agent_id: str) -> dict[str, Any]:
    """POST /demo/replay with the agent_id override, then poll until the run finishes."""
    r = await c.post("/demo/replay", json={"scenario": name, "agent_id": agent_id})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    deadline = time.monotonic() + REPLAY_TIMEOUT_S
    while True:
        run = (await c.get(f"/demo/replay/{run_id}")).json()
        if run["status"] != "running":
            assert run["status"] == "done", run
            assert [o["agent_id"] for o in run["outcomes"]] == [agent_id] * run["steps"], run
            return run
        assert time.monotonic() < deadline, f"replay {name} still running after {REPLAY_TIMEOUT_S}s: {run}"
        await asyncio.sleep(0.1)


def _rows_sync(ch: Any, agents: tuple[str, ...]) -> list[tuple]:
    res = ch.query(
        "SELECT toUnixTimestamp64Milli(ts), agent_id, action, target, result, reason, honeytoken_hit, synthetic "
        "FROM events WHERE agent_id IN {a:Array(String)} ORDER BY agent_id, ts",
        parameters={"a": list(agents)},
    )
    return [tuple(r) for r in res.result_rows]


async def ch_wait(
    ch: Any, agents: tuple[str, ...], ready: Callable[[list[tuple]], bool], timeout: float = CH_VISIBLE_S
) -> list[tuple]:
    """Poll ClickHouse (off the event loop, so the writer keeps flushing) until ready(rows)."""
    deadline = time.monotonic() + timeout
    rows: list[tuple] = []
    while True:
        rows = await asyncio.to_thread(_rows_sync, ch, agents)
        if ready(rows):
            return rows
        if time.monotonic() >= deadline:
            pytest.fail(f"ClickHouse rows for {agents} not as expected within {timeout}s: {rows}")
        await asyncio.sleep(0.25)


async def assert_contained_by_hold(c: httpx.AsyncClient, ch: Any, run: dict[str, Any], a: str) -> str:
    """secret_theft replay with hold ON: send denied at the hold step, quarantined, next call
    denied, denial row in ClickHouse, incident listed. Returns the incident id."""
    out = run["outcomes"]
    assert run["passed"] is True, out  # every fixture expectation held
    assert [o["result"] for o in out[:3]] == ["ok", "ok", "ok"], out
    send, after = out[3], out[4]
    assert send["action"] == "http_post" and send["result"] == "denied", send
    assert send["reason"] in HOLD_REASONS and send["incident_id"], send
    assert after["result"] == "denied" and after["reason"] == "blocked", after
    inc_id = send["incident_id"]

    st = await status(c)
    assert a in st["blocked"] and st["modes"][a] == "quarantined", st
    nxt = await tool(c, a, "read_file", "/app/config.yml")  # "its next call returns denied"
    assert nxt["result"] == "denied" and nxt["reason"] == "blocked", nxt

    # The dashboard's incident list (GET /incidents) shows it, with a truthfully labelled verdict.
    incs = {i["id"]: i for i in (await c.get("/incidents")).json()}
    inc = incs.get(inc_id)
    assert inc is not None, sorted(incs)
    assert inc["agent_id"] == a and inc["rule"] == "hold" and inc["closed_ms"] is None, inc
    assert inc["contained_ms"] is not None and inc["tags"], inc
    v = inc["verdict"]
    assert v and v["verdict"] == "malicious", inc
    if send["reason"] == "hold_rule":
        assert v["decision_source"] == "rule_only", v  # a rule decision is never labelled as a model
    else:
        assert v["decision_source"] in MODEL_SOURCES, v
    assert any(s["action"] == "http_post" and s["result"] == "denied" for s in inc["steps"]), inc["steps"]
    assert inc_id in {i["id"] for i in st["open_incidents"]}

    # The denial is in ClickHouse (writer flushes in the background; poll <= 5 s).
    # Columns: 0 ts_ms, 1 agent_id, 2 action, 3 target, 4 result, 5 reason, 6 honeytoken_hit, 7 synthetic.
    def has_denials(rows: list[tuple]) -> bool:
        held = any(r[0] == send["ts_ms"] and r[2] == "http_post" and r[4:6] == ("denied", send["reason"]) for r in rows)
        blocked = any(r[0] == nxt["ts_ms"] and r[4:6] == ("denied", "blocked") for r in rows)
        return held and blocked

    rows = await ch_wait(ch, (a,), has_denials)
    sent = next(r for r in rows if r[0] == send["ts_ms"])
    assert sent[3] == send["target"] and sent[6] == 0 and sent[7] == 0, sent  # URL only, no payload; synthetic=0
    return inc_id


# ---------------------------------------------------------------------------
# A. hold-mode path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run", RUNS)
async def test_a_hold_mode_contain_restore_reblock(env, ch, run):
    c, svc = env
    deploy, support = agent(run, "deploy"), agent(run, "support")
    roles = {"deploy-bot": deploy, "support-bot": support}

    # Two agents visible; normal calls allowed.
    normal = scenario("normal_ops")
    for step in normal.steps[:4]:  # deploy x3, support x1 (legit .env read, config, npm test, ticket)
        r = await tool_step(c, step, roles[step.agent_id])
        assert r["result"] == "ok" and r["reason"] == "", (step, r)
    st = await status(c)
    assert {deploy, support} <= set(st["active"]) and not ({deploy, support} & set(st["blocked"])), st

    # Replay the recorded attack with hold ON -> denied at the send, quarantined, recorded.
    await set_hold(c, True)
    first = await replay(c, "secret_theft", deploy)
    inc1 = await assert_contained_by_hold(c, ch, first, deploy)
    assert support not in (await status(c))["blocked"]  # containment is per agent

    # Restore -> a normal call is allowed again and the agent is NOT re-blocked.
    r = await c.post(f"/restore/{deploy}")
    assert r.status_code == 200, r.text
    rs = r.json()
    assert rs["status"] == "restored" and inc1 in rs["closed_incidents"], rs
    assert rs["watermark"] >= first["outcomes"][3]["ts_ms"], rs
    ok = await tool(c, deploy, "read_file", "/app/config.yml")
    assert ok["result"] == "ok" and ok["ts_ms"] > rs["watermark"], ok
    t_end = time.monotonic() + NO_REBLOCK_S
    while time.monotonic() < t_end:
        st = await status(c)
        assert deploy not in st["blocked"] and st["modes"].get(deploy) == "normal", st
        await asyncio.sleep(0.2)
    again = await tool(c, deploy, "run_command", "npm test")
    assert again["result"] == "ok", again
    assert (await c.get(f"/incidents/{inc1}")).json()["closed_ms"] is not None

    # Replay after Restore is detected and blocked again (new incident).
    second = await replay(c, "secret_theft", deploy)
    inc2 = await assert_contained_by_hold(c, ch, second, deploy)
    assert inc2 != inc1

    # A verdict was recorded with a truthful label; the hold decision was timed.
    ev = (await c.get("/evidence")).json()
    assert ev["hold_decision_ms"] is not None and ev["hold_decision_ms"] >= 0, ev


# ---------------------------------------------------------------------------
# B. detector path (hold OFF)
# ---------------------------------------------------------------------------

DETECTOR_ENTRY_POINTS = ("run_once", "poll_once", "tick_once", "once")
CLIENT_PARAMS = ("client", "http", "http_client", "checkpoint", "api")


def detector_once() -> tuple[Callable[..., Any], str]:
    """The detector's single-pass entry point and the name of its HTTP-client parameter.

    Skips unless detection/loop.py exists AND exposes a run-once callable that takes an HTTP
    client: one that only knows CHECKPOINT_URL would post /block to the live checkpoint
    on :8000, which this in-process test must never touch."""
    try:
        spec = importlib.util.find_spec("detection.loop")
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        pytest.skip("detector not merged yet (Sripadha)")
    mod = importlib.import_module("detection.loop")
    for name in DETECTOR_ENTRY_POINTS:
        fn = getattr(mod, name, None)
        if not callable(fn):
            continue
        params = inspect.signature(fn).parameters
        client_param = next((p for p in CLIENT_PARAMS if p in params), None)
        required = [
            p.name
            for p in params.values()
            if p.default is inspect.Parameter.empty
            and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
            and p.name != client_param
        ]
        if client_param is None or required:
            pytest.skip(
                f"detection.loop.{name}{inspect.signature(fn)} cannot be pointed at an in-process "
                f"checkpoint (needs an HTTP-client parameter named one of {CLIENT_PARAMS})"
            )
        return fn, client_param
    pytest.skip(f"detection/loop.py exposes none of {DETECTOR_ENTRY_POINTS}")


async def run_detector(fn: Callable[..., Any], client_param: str, c: httpx.AsyncClient) -> Any:
    out = fn(**{client_param: c})
    return await out if inspect.isawaitable(out) else out


@pytest.mark.parametrize("run", RUNS)
async def test_b_detector_blocks_with_hold_off(env, ch, run):
    fn, client_param = detector_once()
    c, svc = env
    a = agent(run, "detect")
    await set_hold(c, False)
    steps = scenario("secret_theft").steps

    # Steps 1-3 (ticket -> .env -> encode); all allowed with hold OFF.
    for step in steps[:3]:
        assert (await tool_step(c, step, a))["result"] == "ok"
    await ch_wait(ch, (a,), lambda rows: len(rows) >= 3)
    await run_detector(fn, client_param, c)
    if a not in (await status(c))["blocked"]:
        # A funnel that needs the external send too: post step 4 (allowed, hold OFF), detect again.
        sent = await tool_step(c, steps[3], a)
        assert sent["result"] == "ok", sent
        await ch_wait(ch, (a,), lambda rows: len(rows) >= 4)
        await run_detector(fn, client_param, c)

    st = await status(c)
    assert a in st["blocked"] and st["modes"][a] == "quarantined", st
    alerts = [x for x in (await c.get("/alerts")).json() if x["agent_id"] == a and x["blocking"]]
    assert alerts, "detector did not POST /block for the agent"
    assert alerts[0]["decision_source"] in ("rule_only", *MODEL_SOURCES), alerts[0]
    nxt = await tool_step(c, steps[4], a)  # fixture: denied_after_block
    assert nxt["result"] == "denied" and nxt["reason"] == "blocked", nxt
    await ch_wait(ch, (a,), lambda rows: any(r[0] == nxt["ts_ms"] and r[4:6] == ("denied", "blocked") for r in rows))


# ---------------------------------------------------------------------------
# C. honeytoken trip + no false positives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run", RUNS)
async def test_c_honeytoken_exfil_denied_at_send(env, ch, run):
    c, svc = env
    a = agent(run, "honey")
    await set_hold(c, True)
    out = await replay(c, "honeytoken_exfil", a)
    o = out["outcomes"]
    assert out["passed"] is True, o
    assert [x["result"] for x in o[:3]] == ["ok", "ok", "ok"], o
    send = o[3]
    assert send["action"] == "http_post" and send["result"] == "denied", send
    assert send["reason"] == "honeytoken" and send["incident_id"], send
    assert o[4]["result"] == "denied" and o[4]["reason"] == "blocked", o[4]

    inc = (await c.get(f"/incidents/{send['incident_id']}")).json()
    assert inc["agent_id"] == a and inc["rule"] == "honeytoken", inc
    assert inc["verdict"]["decision_source"] == "honeytoken", inc  # no model call
    st = await status(c)
    assert a in st["blocked"] and st["modes"][a] == "quarantined", st

    rows = await ch_wait(
        ch, (a,), lambda rows: any(r[0] == send["ts_ms"] and r[4:7] == ("denied", "honeytoken", 1) for r in rows)
    )
    assert not any(t in r[3] for r in rows for t in TOKENS), rows  # decoys never stored


@pytest.mark.parametrize("run", RUNS)
async def test_c_normal_ops_all_ok_with_hold_on(env, ch, run):
    c, svc = env
    roles = {"deploy-bot": agent(run, "opsdeploy"), "support-bot": agent(run, "opssupport")}
    await set_hold(c, True)
    sc = scenario("normal_ops")
    results = []
    for step in sc.steps:
        assert step.expect == "ok", step
        r = await tool_step(c, step, roles[step.agent_id])
        results.append((step.agent_id, step.action, step.target, r["result"], r["reason"]))
    assert all(x[3] == "ok" and x[4] == "" for x in results), results

    st = await status(c)
    mine = set(roles.values())
    assert not (mine & set(st["blocked"])), st
    assert all(st["modes"].get(x, "normal") == "normal" for x in mine), st
    assert not [i for i in st["open_incidents"] if i["agent_id"] in mine], st["open_incidents"]
    rows = await ch_wait(ch, tuple(sorted(mine)), lambda rows: len(rows) >= len(sc.steps))
    assert len(rows) == len(sc.steps) and all(r[4] == "ok" for r in rows), rows

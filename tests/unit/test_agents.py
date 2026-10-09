"""Unit tests for the agents lane (Sripadha).

Run:
    uv run ruff check agents tests/unit/test_agents.py
    uv run pytest tests/unit/test_agents.py -q

These run entirely in-process against Bindu's real checkpoint app over an
httpx.ASGITransport (no server bound, no ClickHouse). The app's lifespan is not
started under ASGITransport, which is fine for /tool, /status, /block, /restore.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import builtins
import io
import os
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from agents import botbase, deploy_bot, support_bot
from agents.checkpoint_client import CheckpointClient, CheckpointDown, CheckpointError
from agents.fake_tools import simulate_run_command
from agents.honeytokens import contains_decoy, env_grep_lines, fake_env_text
from agents.replay import _run_via_api, run_scenario
from checkpoint import honeytoken
from checkpoint.app import create_app
from checkpoint.writer import InMemoryWriter
from tests.unit.conftest import TOKENS, make_settings
from tripwire.contracts import AlertPayload, Scenario, ToolCall

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"


def _now_ms() -> int:
    import time

    return time.time_ns() // 1_000_000


def _build(tmp_path, tag: str = "", writer: InMemoryWriter | None = None, token: str = "change-me", **settings):
    writer = writer if writer is not None else InMemoryWriter()
    app = create_app(
        writer=writer,
        settings=make_settings(**settings),
        state_path=tmp_path / f"state{tag}.json",
        ch_enabled=False,
        fixtures_dir=FIXTURES,
        web_dist=None,
    )
    client = CheckpointClient.from_transport(httpx.ASGITransport(app=app), "http://test", token)
    return client, writer, app


def _load(name: str) -> Scenario:
    return Scenario.model_validate_json((FIXTURES / f"{name}.json").read_text())


# ---------------------------------------------------------------- (a) record-only
async def test_record_only_and_taint_propagation(tmp_path):
    client, writer, app = _build(tmp_path)
    deploy_session = deploy_bot._session_factory("deploy-bot")
    support_session = support_bot._session_factory("support-bot")

    def boom(*_a, **_k):
        raise AssertionError("record-only violated: a real IO call was attempted")

    try:
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(builtins, "open", boom)
            mp.setattr(io, "open", boom)  # what pathlib.Path.read_text() actually calls
            mp.setattr(os, "system", boom)
            mp.setattr(os, "popen", boom)
            mp.setattr(subprocess, "Popen", boom)
            mp.setattr(subprocess, "run", boom)
            mp.setattr(socket, "socket", boom)
            mp.setattr(socket, "create_connection", boom)
            dtools = await deploy_session(client)
            stools = await support_session(client)
    finally:
        await client.aclose()

    # Every step of both sessions was allowed.
    assert [s["result"] for s in dtools.log] == ["ok"] * 6
    assert [s["result"] for s in stools.log] == ["ok"] * 4

    rows = writer.rows
    assert rows, "no rows were written to the checkpoint"
    assert all(str(r["code_ref"]).startswith("agents/fake_tools.py:") for r in rows)

    sup_rows = [r for r in rows if r["agent_id"] == "support-bot"]
    assert len(sup_rows) == 4
    assert sup_rows[0]["action"] == "read_file" and sup_rows[0]["tainted_by"] == ""
    assert all(str(r["tainted_by"]).startswith("ticket:") for r in sup_rows[1:])

    dep_rows = [r for r in rows if r["agent_id"] == "deploy-bot"]
    assert all(r["tainted_by"] == "" for r in dep_rows)


# ---------------------------------------------------------------- (b) honeytokens
def test_fake_env_and_honeytoken_scan():
    env = fake_env_text()
    for tok in TOKENS:
        assert tok in env, f"decoy {tok!r} missing from the fake /app/.env"

    full_b64 = base64.b64encode(env.encode("utf-8")).decode("ascii")
    assert honeytoken.scan(full_b64, list(TOKENS)) is True

    # The grep-partial exfil (DATABASE_URL/JWT_SECRET only) carries NO decoy.
    grep_b64 = base64.b64encode(env_grep_lines().encode("utf-8")).decode("ascii")
    assert honeytoken.scan(grep_b64, list(TOKENS)) is False

    # The tool's own simulation of that command equals the grep-partial base64.
    assert simulate_run_command("grep -E '^(DATABASE_URL|JWT_SECRET)=' /app/.env | base64 -w0") == grep_b64


# ---------------------------------------------------------------- (c) normal ops
async def test_replay_normal_ops_all_ok(tmp_path):
    client, _writer, _app = _build(tmp_path, tag="-normal")
    try:
        result = await run_scenario(_load("normal_ops"), client)
    finally:
        await client.aclose()
    assert result["passed"] is True, result["outcomes"]
    assert all(o["result"] != "denied" for o in result["outcomes"])


# ---------------------------------------------------------------- (d) honeytoken exfil
async def test_replay_honeytoken_exfil(tmp_path):
    client, _writer, _app = _build(tmp_path, tag="-honey")
    try:
        result = await run_scenario(_load("honeytoken_exfil"), client)
    finally:
        await client.aclose()
    o = result["outcomes"]
    # step 3: the whole-env base64 trips the honeytoken scanner, synchronously.
    assert o[3]["result"] == "denied" and o[3]["reason"] == "honeytoken"
    # step 4: the agent is now quarantined, so the next call is blocked.
    assert o[4]["result"] == "denied" and o[4]["reason"] == "blocked"
    assert result["passed"] is True, o


# ---------------------------------------------------------------- (e) secret theft
async def test_replay_secret_theft_without_block_fails(tmp_path):
    client, _writer, _app = _build(tmp_path, tag="-noblk")
    try:
        result = await run_scenario(_load("secret_theft"), client, block_wait_s=0.5)
    finally:
        await client.aclose()
    assert result["passed"] is False
    last = result["outcomes"][-1]
    assert last["expect"] == "denied_after_block"
    assert last["result"] != "denied"
    assert last["ok"] is False
    # It genuinely waited for a block that never came (then timed out).
    assert last["waited_for_block_ms"] is not None


async def test_replay_secret_theft_with_background_block(tmp_path):
    client, _writer, app = _build(tmp_path, tag="-blk")
    scenario = _load("secret_theft")

    async def poster():
        # Post the block AFTER step 3 (the exfil http_post at offset 2700 ms).
        await asyncio.sleep(2.9)
        payload = AlertPayload(
            agent_id="deploy-bot",
            rule="secret_theft",
            verdict="malicious",
            confidence=0.9,
            reason="rule: secret-theft funnel matched",
            decision_source="rule_only",
            detected_at_ms=_now_ms(),
            last_step_ts_ms=_now_ms(),
        )
        return await client.block("deploy-bot", payload)

    task = asyncio.create_task(poster())
    try:
        result = await run_scenario(scenario, client, block_wait_s=5.0)
        blocked = await task
    finally:
        await client.aclose()

    assert blocked.get("status") == "blocked"
    assert result["passed"] is True, result["outcomes"]
    last = result["outcomes"][-1]
    assert last["result"] == "denied"
    assert last["waited_for_block_ms"] is not None


# ---------------------------------------------------------------- (f) fixtures parse
def test_fixtures_parse_and_secret_payload_has_no_decoy():
    for name in ("secret_theft", "honeytoken_exfil", "normal_ops"):
        sc = _load(name)
        assert sc.name == name
        assert sc.steps

    st = _load("secret_theft")
    posts = [s for s in st.steps if s.action == "http_post"]
    assert posts, "secret_theft should have an http_post exfil step"
    payload = posts[0].payload
    assert honeytoken.scan(payload, list(TOKENS)) is False
    assert contains_decoy(base64.b64decode(payload).decode("utf-8")) is False


# ---------------------------------------------------------------- (g) static record-only guard
_FORBIDDEN_MODULES = {"subprocess", "socket", "urllib", "urllib.request", "requests", "aiohttp", "shutil"}


def test_agents_package_imports_no_io_modules():
    """No agents/*.py imports a shell/socket/HTTP module; the only network library is httpx
    and it is imported in checkpoint_client.py only. The replay CLI's fixture read is the one
    legitimate file read and lives in main(), not in a tool."""
    for path in sorted((REPO / "agents").glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                top = n.split(".")[0]
                assert top not in _FORBIDDEN_MODULES and n not in _FORBIDDEN_MODULES, f"{path.name} imports {n}"
                if top == "httpx":
                    assert path.name == "checkpoint_client.py", f"{path.name} imports httpx directly"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                raise AssertionError(f"{path.name}:{node.lineno} calls open()")


# ---------------------------------------------------------------- (h) HTTP errors are typed, not tracebacks
async def test_bad_token_is_checkpoint_error_and_exit_2(tmp_path):
    # PUBLIC=1 on the server, wrong token on the client -> 401 on every POST.
    client, _writer, _app = _build(tmp_path, tag="-401", token="WRONG", public=True)
    try:
        with pytest.raises(CheckpointError) as ei:
            await client.tool(ToolCall(agent_id="deploy-bot", action="read_file", target="/app/.env"))
        assert ei.value.status == 401
        assert "ToolResult" not in str(ei.value)  # not a pydantic validation error leaking through

        # The bot run loop maps it to exit code 2 (once mode), not a crash.
        code = await botbase._run_once(client, deploy_bot._session_factory("deploy-bot"), "http://test")
        assert code == 2
    finally:
        await client.aclose()


async def test_drive_once_exit_codes(tmp_path):
    # Unreachable checkpoint (nothing listens on this port) -> 2.
    async def session(client):
        await client.status()

    code = await botbase.drive("http://127.0.0.1:18999", once=True, loop=False, interval=0.0, session_fn=session)
    assert code == 2

    # Denied step -> 3 (block the agent first, then run the real deploy session).
    client, _writer, _app = _build(tmp_path, tag="-deny")
    try:
        await client.block(
            "deploy-bot",
            AlertPayload(
                agent_id="deploy-bot",
                rule="secret_theft",
                verdict="malicious",
                confidence=0.9,
                reason="rule: test block",
                decision_source="rule_only",
                detected_at_ms=_now_ms(),
                last_step_ts_ms=_now_ms(),
            ),
        )
        code = await botbase._run_once(client, deploy_bot._session_factory("deploy-bot"), "http://test")
        assert code == 3
    finally:
        await client.aclose()


async def test_drive_loop_retries_after_checkpoint_down(monkeypatch):
    """--loop must survive an unreachable checkpoint: log, sleep, try again (never exit 2)."""
    calls: list[int] = []

    async def session(client):
        calls.append(1)
        if len(calls) < 3:
            raise CheckpointDown("POST /tool: ConnectError: simulated")
        raise asyncio.CancelledError()  # stands in for Ctrl-C once we have seen the retries

    monkeypatch.setattr(botbase, "MIN_RETRY_S", 0.0)
    monkeypatch.setattr(botbase, "_jitter", lambda _i: 0.0)
    code = await botbase.drive("http://127.0.0.1:18999", once=False, loop=True, interval=0.0, session_fn=session)
    assert code == 0
    assert len(calls) == 3  # two failures were retried, the third pass ran


# ---------------------------------------------------------------- (i) --via-api against the real checkpoint
async def test_via_api_against_real_checkpoint(tmp_path):
    client, _writer, _app = _build(tmp_path, tag="-api")
    try:
        run = await _run_via_api("normal_ops", client, None)
        assert run.get("status") == "done", run
        assert run.get("passed") is True, run
        assert len(run["outcomes"]) == 10
        assert all(o["result"] == "ok" for o in run["outcomes"])

        missing = await _run_via_api("no_such_fixture", client, None)
        assert missing["passed"] is False
        assert "404" in missing["error"]
    finally:
        await client.aclose()

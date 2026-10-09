"""Record-only tool surface for the simulated agents (master §1, SRIPADHA Block 1).

Every method builds a contracts.ToolCall, POSTs it to the checkpoint via the
async CheckpointClient, records a step in self.log, and returns a ToolOutcome. The
"output" of a tool is SIMULATED with pure string operations only.

HARD GUARANTEE: nothing in this module calls open(), subprocess, os.system, socket,
or any network library other than the checkpoint client. There are no real file
reads, no shell, and no outbound network from the fake tools. This is the property
tests/unit/test_agents.py pins by monkeypatching open/subprocess/socket to blow up.

    tools = FakeTools("deploy-bot", client, session_id="deploy-ab12cd34")
    out = await tools.read_file("/app/.env")
    if out.denied: ...   # the checkpoint said no; stop the task
"""

from __future__ import annotations

import base64
import inspect
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from agents.checkpoint_client import CheckpointClient
from agents.honeytokens import FAKE_FILES, env_grep_lines, fake_env_text, fake_ticket
from tripwire.contracts import Action, ToolCall, ToolResult


@dataclass
class ToolOutcome:
    """What a fake tool returns to the caller (the bot)."""

    result: str
    reason: str
    output: str
    incident_id: Optional[str]
    ts_ms: int
    action: str = ""
    target: str = ""
    missing: bool = False
    bytes: int = 0

    @property
    def denied(self) -> bool:
        return self.result == "denied"

    @property
    def ok(self) -> bool:
        return self.result == "ok"


class Denied(RuntimeError):
    """Raised (when raise_on_deny=True) the moment the checkpoint denies a call."""

    def __init__(self, outcome: ToolOutcome) -> None:
        super().__init__(f"{outcome.action} {outcome.target} denied ({outcome.reason})")
        self.outcome = outcome


def _code_ref() -> str:
    """"agents/fake_tools.py:<lineno>" of the caller (the tool method). No file read."""
    frame = inspect.currentframe()
    lineno = frame.f_back.f_lineno if frame is not None and frame.f_back is not None else 0
    return f"agents/fake_tools.py:{lineno}"


def _b64(text: str) -> str:
    """base64 of text with no line wrapping (like `base64 -w0`). Pure string op."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def simulate_read(path: str) -> tuple[str, bool]:
    """Simulated contents of a path. Returns (output, missing). No disk access."""
    if path.startswith("ticket:"):
        return fake_ticket(path), False
    if path in FAKE_FILES:
        return FAKE_FILES[path], False
    return "", True


def simulate_run_command(cmd: str) -> str:
    """Simulated stdout of a command, by STRING OPERATIONS ONLY. Never a subprocess."""
    c = (cmd or "").strip()
    if "base64" in c and ".env" in c:
        # `grep -E '^(DATABASE_URL|JWT_SECRET)=' /app/.env | base64 -w0` -> just those lines.
        if "grep" in c and "DATABASE_URL" in c:
            return _b64(env_grep_lines())
        # `base64 -w0 /app/.env` -> the whole fake env (carries the decoys).
        return _b64(fake_env_text())
    low = c.lower()
    if "npm test" in low:
        return "> app@2.14.0 test\nPASS  src/  (42 tests, 0 failures)\nTest Suites: 7 passed, 7 total\n"
    if "make deploy" in low:
        return "building app... ok\npushing image app:2.14.0... ok\nrollout deployment/app... ok\n"
    if "pytest" in low:
        return "collected 60 items\n60 passed in 1.84s\n"
    return f"simulated: {c}\n"


# A canned HTTP 200 body for outbound calls (no real request is made).
_HTTP_OK_BODY = '{"status":200,"ok":true,"simulated":true}'


class FakeTools:
    def __init__(
        self,
        agent_id: str,
        client: CheckpointClient,
        session_id: str = "",
        *,
        raise_on_deny: bool = False,
        verbose: bool = False,
    ) -> None:
        self.agent_id = agent_id
        self.client = client
        self.session_id = session_id
        self.raise_on_deny = raise_on_deny
        self.verbose = verbose
        self.log: list[dict[str, object]] = []

    # ---- the one place a call reaches the checkpoint ----------------------------
    async def _call(
        self,
        action: Action,
        target: str,
        *,
        output: str,
        code_ref: str,
        payload: str = "",
        tainted_by: str = "",
        nbytes: Optional[int] = None,
        missing: bool = False,
    ) -> ToolOutcome:
        nbytes = len((payload or output).encode("utf-8")) if nbytes is None else nbytes
        call = ToolCall(
            agent_id=self.agent_id,
            action=action,
            target=target,
            bytes=nbytes,
            session_id=self.session_id,
            tainted_by=tainted_by,
            code_ref=code_ref,
            payload=payload,
        )
        res: ToolResult = await self.client.tool(call)
        self.log.append(
            {
                "ts_ms": res.ts_ms,
                "action": action,
                "target": target,
                "result": res.result,
                "reason": res.reason,
            }
        )
        if self.verbose:
            extra = f" ({res.reason})" if res.reason else ""
            logger.info(f"[{self.agent_id}] {action} {target} -> {res.result}{extra}")
        out = ToolOutcome(
            result=res.result,
            reason=res.reason,
            output=output,
            incident_id=res.incident_id,
            ts_ms=res.ts_ms,
            action=action,
            target=target,
            missing=missing,
            bytes=nbytes,
        )
        if out.denied and self.raise_on_deny:
            raise Denied(out)
        return out

    # ---- tools ------------------------------------------------------------------
    async def read_file(self, path: str, tainted_by: str = "") -> ToolOutcome:
        output, missing = simulate_read(path)
        return await self._call(
            "read_file", path, output=output, code_ref=_code_ref(), tainted_by=tainted_by, missing=missing
        )

    async def run_command(self, cmd: str, tainted_by: str = "") -> ToolOutcome:
        output = simulate_run_command(cmd)
        return await self._call("run_command", cmd, output=output, code_ref=_code_ref(), tainted_by=tainted_by)

    async def http_post(self, url: str, body: str, tainted_by: str = "") -> ToolOutcome:
        return await self._call(
            "http_post",
            url,
            output=_HTTP_OK_BODY,
            code_ref=_code_ref(),
            payload=body,
            tainted_by=tainted_by,
            nbytes=len((body or "").encode("utf-8")),
        )

    async def http_get(self, url: str, tainted_by: str = "") -> ToolOutcome:
        return await self._call("http_get", url, output=_HTTP_OK_BODY, code_ref=_code_ref(), tainted_by=tainted_by)

    async def list_permissions(self, tainted_by: str = "") -> ToolOutcome:
        output = "roles: [app-runtime]; can: [read_file, run_command, http_post, http_get]\n"
        return await self._call("list_permissions", "iam:self", output=output, code_ref=_code_ref(), tainted_by=tainted_by)

    async def assume_role(self, role: str, tainted_by: str = "") -> ToolOutcome:
        output = f"simulated: assumed role {role}\n"
        return await self._call("assume_role", role, output=output, code_ref=_code_ref(), tainted_by=tainted_by)

    async def disable_logging(self, target: str = "audit:cloudtrail", tainted_by: str = "") -> ToolOutcome:
        output = f"simulated: logging disabled on {target}\n"
        return await self._call("disable_logging", target, output=output, code_ref=_code_ref(), tainted_by=tainted_by)

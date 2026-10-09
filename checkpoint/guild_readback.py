"""Guild human-approval read-back: did the operator APPROVE or REJECT the cure in the Responder session?

    GET /guild/session/{session_id}/decision
      -> {"status": "waiting"|"approved"|"rejected", "operator_reply": str|None,
          "decided_by": "human via Guild"|"Guild Responder agent (after a human reply)"|None,
          "source": "guild session events (CLI)", "checked_ms": epoch ms}

The Responder (bindubhargavareddy~tripwire-responder) receives the case as the first user_message (posted
by our account API key), answers with an agent_notification_message that ends "Reply APPROVE to approve,
or REJECT <reason> to reject." and waits on ui_prompt. The human answers in the session: a user_message
"APPROVE" / "REJECT <reason>" (or a ui_prompt response event); the agent may then end with a fenced JSON
{"decision": "approve_cure"|"reject", "operator_reply": ...}.

Read path: the Guild CLI ("guild --mode json session events <id>"), not the public API: the events
endpoint hangs with the account key, the CLI on this laptop is logged in as the user. GUILD_AUTO_UPDATE=0.
Measured on this laptop 2026-10-09: 9.0 s, 9.2 s and 14.7 s per call, so the timeout is CLI_TIMEOUT_S = 20 s
(a 10 s budget would time out on about a third of calls).

Precedence: the latest human answer after the agent's prompt wins (messages authored by an API key are
never counted as a human) -> decided_by DECIDED_BY. The agent's own decision JSON is used only when it comes
after some human answer event that did not parse as APPROVE/REJECT (e.g. "yes, go ahead") -> decided_by
DECIDED_BY_AGENT. Agent JSON with no human reply before it is ignored ("waiting"): the case prompt carries
attacker-controlled text, so the agent echoing a {"decision": ...} object must never read as an approval.

Documented module caches: _cache (session_id -> (checked_ms, result), CACHE_MS) and _inflight (one CLI
call per session at a time; concurrent pollers share it). reset_cache() clears both (tests).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from typing import Any, Literal

from checkpoint.state import now_ms

SESSION_ID_RE = re.compile(r"[0-9a-f-]{8,64}")
CLI_TIMEOUT_S = 20.0
CACHE_MS = 5_000
MAX_INFLIGHT = 4  # distinct sessions read at once; more -> CLIFailed("busy") so pollers cannot fork-bomb the CLI
EVENT_LIMIT = 100
SOURCE = "guild session events (CLI)"
DECIDED_BY = "human via Guild"
DECIDED_BY_AGENT = "Guild Responder agent (after a human reply)"
MAX_REPLY = 500

APPROVE_RE = re.compile(r"^\W*approve(?:d)?\b[\s:,.!-]*(.*)$", re.I | re.S)
REJECT_RE = re.compile(r"^\W*reject(?:ed)?\b[\s:,.!-]*(.*)$", re.I | re.S)
FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.I | re.S)
DECISIONS = {"approve_cure": "approved", "approve": "approved", "reject": "rejected"}

Status = Literal["waiting", "approved", "rejected"]

_cache: dict[str, tuple[int, dict[str, Any]]] = {}
_inflight: dict[str, asyncio.Future[dict[str, Any]]] = {}


class CLIMissing(RuntimeError):
    """The guild CLI is not installed / not on PATH (-> 503)."""


class CLIFailed(RuntimeError):
    """The CLI ran but failed, timed out or printed something that is not the events JSON (-> 502)."""


def valid_session_id(session_id: str) -> bool:
    return bool(SESSION_ID_RE.fullmatch(session_id))


def reset_cache() -> None:
    _cache.clear()
    _inflight.clear()


# ---------------------------------------------------------------- parsing
def event_text(ev: dict[str, Any]) -> str:
    """Plain text of one event: content as str, or {"data"|"text"|"response"|...: str}, else content_parts."""
    c = ev.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, dict):
        for k in ("data", "text", "response", "answer", "value", "message"):
            if isinstance(c.get(k), str):
                return c[k]
    parts = ev.get("content_parts") or []
    return "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str))


def _author_type(ev: dict[str, Any]) -> str:
    a = ev.get("author")
    return str(a.get("type") or "") if isinstance(a, dict) else ""


def _is_agent(ev: dict[str, Any]) -> bool:
    return str(ev.get("type") or "").startswith("agent_notification")


def _is_prompt_response(ev: dict[str, Any]) -> bool:
    t = str(ev.get("type") or "")
    return "ui_prompt" in t and ("response" in t or "answer" in t or "reply" in t)


def human_answer(text: str) -> tuple[Status, str] | None:
    s = text.strip()
    if m := APPROVE_RE.match(s):
        return "approved", s[:MAX_REPLY]
    if m := REJECT_RE.match(s):
        return "rejected", (m.group(1).strip() or s)[:MAX_REPLY]
    return None


def agent_decision(text: str) -> tuple[Status, str | None] | None:
    """Last fenced JSON {"decision": ...} in an agent message (a bare trailing object is accepted too)."""
    cands = FENCE_RE.findall(text)
    if not cands:
        i = text.rfind('{"decision"')
        if i != -1:
            cands = [text[i:]]
    for raw in reversed(cands):
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw.strip())
        except ValueError:
            continue
        if isinstance(obj, dict) and str(obj.get("decision", "")).lower() in DECISIONS:
            reply = obj.get("operator_reply")
            return DECISIONS[str(obj["decision"]).lower()], (str(reply)[:MAX_REPLY] if reply is not None else None)
    return None


def parse_events(doc: Any) -> tuple[Status, str | None, str | None]:
    """(status, operator_reply, decided_by) from the CLI's events JSON ({"items": [...]} or a bare list)."""
    items = doc.get("items") if isinstance(doc, dict) else doc
    if not isinstance(items, list):
        raise CLIFailed("events JSON has no items list")
    evs = sorted((e for e in items if isinstance(e, dict)), key=lambda e: str(e.get("created_at") or ""))
    prompted = False
    human_spoke = False  # a non-API-key answer event exists after the agent's prompt
    human: tuple[Status, str] | None = None
    agent: tuple[Status, str | None] | None = None
    for ev in evs:
        text = event_text(ev)
        if _is_agent(ev):
            prompted = True
            if human_spoke:  # agent JSON before any human reply is ignored (see module docstring)
                agent = agent_decision(text) or agent
            continue
        is_answer = ev.get("type") == "user_message" or _is_prompt_response(ev)
        if prompted and is_answer and _author_type(ev) != "api_key":
            human_spoke = True
            human = human_answer(text) or human
    if human is not None:
        return human[0], human[1], DECIDED_BY
    if agent is not None:
        return agent[0], agent[1], DECIDED_BY_AGENT
    return "waiting", None, None


def decision_body(status: Status, reply: str | None, checked_ms: int, decided_by: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "operator_reply": reply,
        "decided_by": decided_by if status != "waiting" else None,
        "source": SOURCE,
        "checked_ms": checked_ms,
    }


# ---------------------------------------------------------------- CLI
async def run_cli(session_id: str, timeout_s: float | None = None) -> Any:
    """Parsed JSON of `guild --mode json session events <id>`. Raises CLIMissing / CLIFailed."""
    timeout_s = CLI_TIMEOUT_S if timeout_s is None else timeout_s
    exe = shutil.which("guild")
    if exe is None:
        raise CLIMissing("guild CLI not found on PATH")
    env = {**os.environ, "GUILD_AUTO_UPDATE": "0"}
    args = [exe, "--mode", "json", "--non-interactive", "-q", "session", "events", session_id, "--limit", str(EVENT_LIMIT)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL, env=env
        )
    except OSError as exc:
        raise CLIMissing(f"guild CLI not runnable: {type(exc).__name__}") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise CLIFailed(f"guild CLI timed out after {timeout_s:g} s") from None
    try:
        doc = json.loads(out.decode("utf-8", "replace") or "null")
    except ValueError:
        doc = None
    if isinstance(doc, dict) and doc.get("success") is False:
        raise CLIFailed(f"guild CLI error: {' '.join(str(doc.get('error') or '').split())[:160]}")
    if proc.returncode != 0 or doc is None:
        tail = " ".join(err.decode("utf-8", "replace").split())[-160:]
        raise CLIFailed(f"guild CLI exit {proc.returncode}" + (f": {tail}" if tail else ""))
    return doc


async def _check(session_id: str) -> dict[str, Any]:
    doc = await run_cli(session_id)
    status, reply, decided_by = parse_events(doc)
    out = decision_body(status, reply, now_ms(), decided_by)
    _cache[session_id] = (out["checked_ms"], out)
    return out


async def read_decision(session_id: str) -> dict[str, Any]:
    """Cached (CACHE_MS) and de-duplicated read; checked_ms is when the CLI call actually ran."""
    hit = _cache.get(session_id)
    if hit and now_ms() - hit[0] < CACHE_MS:
        return hit[1]
    fut = _inflight.get(session_id)
    if fut is not None and not fut.done():
        return await asyncio.shield(fut)
    if len(_inflight) >= MAX_INFLIGHT:
        raise CLIFailed(f"guild CLI busy: {len(_inflight)} reads in flight, retry shortly")
    task = asyncio.ensure_future(_check(session_id))
    _inflight[session_id] = task
    try:
        return await task
    finally:
        if _inflight.get(session_id) is task:
            del _inflight[session_id]

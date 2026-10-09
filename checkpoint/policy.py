"""Pure policy helpers shared by hold mode, backtest and the guardrail (no I/O)."""

from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlsplit

from tripwire.contracts import Policy, Scenario

HTTP_ACTIONS = ("http_post", "http_get")
VERIFY_PREFIX = "verify:"

# Secret-looking paths and encode commands (hold-mode funnel prefix, heatmap formula).
SECRET_PATH_RE = re.compile(r"\.env|secret|credential|\.pem|id_rsa", re.IGNORECASE)
ENCODE_CMD_RE = re.compile(r"base64|b64|xxd|openssl", re.IGNORECASE)
# Same patterns for ClickHouse match() (RE2 syntax, case-insensitive flag inline).
SECRET_PATH_SQL_RE = r"(?i)(\.env|secret|credential|\.pem|id_rsa)"


def host_of(target: str) -> str:
    t = (target or "").strip()
    if not t:
        return ""
    try:
        parts = urlsplit(t if "://" in t else "//" + t)
        return (parts.hostname or "").lower()
    except ValueError:
        return ""


def is_external(action: str, target: str, policy: Policy) -> int:
    if action not in HTTP_ACTIONS:
        return 0
    host = host_of(target)
    internal = {h.lower() for h in policy.internal_hosts}
    return 0 if host and host in internal else 1


def is_verify_agent(agent_id: str) -> bool:
    return agent_id.startswith(VERIFY_PREFIX)


def real_agent(agent_id: str) -> str:
    return agent_id[len(VERIFY_PREFIX) :] if is_verify_agent(agent_id) else agent_id


def _norm(hosts: Iterable[str]) -> set[str]:
    return {h.strip().lower() for h in hosts if h and h.strip()}


def backtest_denies(policy: Policy, agent_id: str, action: str, target: str) -> bool:
    """The backtest predicate (identical to the SQL in CHReader.backtest): an external
    http_post the candidate would deny = host in the denylist, or the agent has an
    allowlist entry and the host is not in it."""
    if action != "http_post" or not is_external(action, target, policy):
        return False
    host = host_of(target)
    if host in _norm(policy.denylist):
        return True
    if agent_id in policy.allowlists:
        return host not in _norm(policy.allowlists[agent_id])
    return False


def scenario_denied(policy: Policy, sc: Scenario) -> bool:
    return any(backtest_denies(policy, s.agent_id, s.action, s.target) for s in sc.steps)


def lint(policy: Policy) -> list[str]:
    """Problems with a candidate policy ([] = clean)."""
    problems: list[str] = []
    try:
        Policy.model_validate(policy.model_dump())
    except Exception as exc:  # noqa: BLE001
        problems.append(f"invalid policy: {exc}")
        return problems
    deny = _norm(policy.denylist)
    for agent, hosts in policy.allowlists.items():
        both = sorted(_norm(hosts) & deny)
        if both:
            problems.append(f"{agent}: allowlisted host(s) also on the denylist: {', '.join(both)}")
    return problems


def sql_str(s: str) -> str:
    """ClickHouse string literal (backslash + quote escaped) so receipts show the exact SQL run."""
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def sql_arr(xs: Iterable[str]) -> str:
    return "[" + ", ".join(sql_str(x) for x in xs) + "]"


def scenario_files(fixtures_dir: Any, kind: str) -> list[Scenario]:
    """Load fixtures/eval/<kind>/*.json as Scenarios; unreadable files are skipped silently."""
    from pathlib import Path

    d = Path(fixtures_dir) / "eval" / kind
    out: list[Scenario] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            out.append(Scenario.model_validate_json(p.read_text()))
        except Exception:  # noqa: BLE001
            continue
    return out

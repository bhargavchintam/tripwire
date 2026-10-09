"""Tripwire frozen contract (master plan §4–§8).

FROZEN at the contract-freeze checkpoint. Owner: Bindu.
Both tracks import from here. Do not rename or remove fields; propose changes
through a CONTRACT CHANGE REQUEST (see README.md). Additive optional fields only,
and only after both teammates agree.

All timestamps are epoch milliseconds (int). Server assigns event timestamps.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

ACTIONS = (
    "read_file",
    "run_command",
    "http_post",
    "http_get",
    "list_permissions",
    "assume_role",
    "disable_logging",
)
Action = Literal[
    "read_file",
    "run_command",
    "http_post",
    "http_get",
    "list_permissions",
    "assume_role",
    "disable_logging",
]

LIVE_AGENTS = ("deploy-bot", "support-bot")
GUILD_AGENT = "guild:deploy-bot"

Result = Literal["ok", "denied", "error"]

# Why an action was denied (events.reason). '' when allowed.
Reason = Literal["", "blocked", "hold_policy", "hold_model", "hold_rule", "honeytoken"]

VerdictLabel = Literal["malicious", "benign", "uncertain"]

# Who made a decision. Shown to judges as-is; never relabel a rule decision as a model one.
DecisionSource = Literal["akashml", "rule_only", "quorum", "honeytoken", "policy"]

AgentMode = Literal["normal", "heightened", "quarantined"]

StreamType = Literal[
    "snapshot",
    "tool_event",
    "agent_state",
    "incident",
    "alert",
    "outbreak",
    "quorum",
    "backtest",
    "guardrail",
    "report_ready",
    "metrics",
]

# Detection rule names (shared so UI badges, detector and checkpoint agree).
RULE_SECRET_THEFT = "secret_theft"
RULE_BASELINE = "baseline_novelty"
RULE_ROLE_GRAB = "role_grab"
RULE_LOG_TAMPER = "log_tamper"
RULE_HONEYTOKEN = "honeytoken"
RULE_HOLD = "hold"

# OWASP Top 10 for LLM Applications (2025) tags per rule (D10).
RULE_TAGS: dict[str, list[str]] = {
    RULE_SECRET_THEFT: ["LLM02 Sensitive Information Disclosure", "LLM06 Excessive Agency"],
    RULE_BASELINE: ["LLM06 Excessive Agency"],
    RULE_ROLE_GRAB: ["LLM06 Excessive Agency"],
    RULE_LOG_TAMPER: ["LLM06 Excessive Agency"],
    RULE_HONEYTOKEN: ["LLM02 Sensitive Information Disclosure"],
    RULE_HOLD: ["LLM01 Prompt Injection", "LLM06 Excessive Agency"],
}


# ---------------------------------------------------------------------------
# Tool calls (agents -> checkpoint)
# ---------------------------------------------------------------------------


class ToolCall(BaseModel):
    agent_id: str
    action: Action
    target: str
    bytes: int = 0
    session_id: str = ""
    # Id of the untrusted input (e.g. "ticket:4821") the agent read before this call.
    tainted_by: str = ""
    # Tool source location "path.py:LINE" for the Semgrep runtime-to-code link.
    code_ref: str = ""
    # Outbound body for http_post. Scanned for honeytokens, NEVER stored raw.
    payload: str = ""


class ToolResult(BaseModel):
    agent_id: str
    result: Result
    reason: Reason = ""
    incident_id: Optional[str] = None
    ts_ms: int = 0


# ---------------------------------------------------------------------------
# Verdicts (the classify() seam, master §6)
# ---------------------------------------------------------------------------


class Verdict(BaseModel):
    verdict: VerdictLabel
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    decision_source: DecisionSource
    model_ids: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


class QuickCheckInput(BaseModel):
    agent_id: str
    rule: str
    # Recent events for this agent, oldest first. Untrusted data: pass to models
    # only inside a delimited JSON block, never as instructions.
    events: list[dict[str, Any]]
    context: str = ""


# Signature of ai.quick_check.classify (Sripadha owns the implementation).
ClassifyFn = Callable[[QuickCheckInput], Awaitable[Verdict]]


# ---------------------------------------------------------------------------
# Detector -> checkpoint
# ---------------------------------------------------------------------------


class AlertPayload(BaseModel):
    rule: str
    verdict: VerdictLabel
    confidence: float
    reason: str
    decision_source: DecisionSource
    detected_at_ms: int
    last_step_ts_ms: int
    model_ids: list[str] = Field(default_factory=list)


class Heartbeat(BaseModel):
    """Timings and metrics pushed by the detector, investigator and eval runner."""

    source: str  # "detector" | "investigator" | "eval" | ...
    query_timings_ms: dict[str, float] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)


class Outbreak(BaseModel):
    source_id: str
    exposed_agents: list[str] = Field(default_factory=list)
    blocked_destinations: list[str] = Field(default_factory=list)
    query_ms: float = 0.0


class ReportPayload(BaseModel):
    report_md: str
    receipts: list[dict[str, Any]] = Field(default_factory=list)  # {sql, ms, rows_read}
    model_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# State served by the checkpoint
# ---------------------------------------------------------------------------


class IncidentStep(BaseModel):
    ts_ms: int
    action: str
    target: str
    result: Result
    reason: Reason = ""


class Incident(BaseModel):
    id: str
    agent_id: str
    rule: str
    opened_ms: int
    last_step_ts_ms: int = 0
    steps: list[IncidentStep] = Field(default_factory=list)
    verdict: Optional[Verdict] = None
    outbreak: Optional[Outbreak] = None
    contained_ms: Optional[int] = None
    closed_ms: Optional[int] = None
    report_md: Optional[str] = None
    receipts: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class StatusResponse(BaseModel):
    active: list[str]
    blocked: list[str]
    open_incidents: list[Incident]
    watermarks: dict[str, int]
    modes: dict[str, AgentMode] = Field(default_factory=dict)
    # (agent, rule, last_step_ts_ms) keys that already have a verdict, for detector dedup.
    verdict_keys: list[str] = Field(default_factory=list)
    hold_enabled: bool = False
    policy_version: int = 0


class Policy(BaseModel):
    version: int = 1
    # agent_id -> external destinations it may post to without a hold.
    allowlists: dict[str, list[str]] = Field(default_factory=dict)
    # Fleet-wide known-bad destinations (outbreak pushes attacker hosts here).
    denylist: list[str] = Field(default_factory=list)
    # Actions decided synchronously in hold mode.
    high_risk_actions: list[str] = Field(
        default_factory=lambda: ["http_post", "assume_role", "disable_logging"]
    )
    # Actions always denied by policy, no model call.
    fixed_deny_actions: list[str] = Field(default_factory=lambda: ["assume_role", "disable_logging"])
    # Prefixes of untrusted inputs (for outbreak tracing).
    untrusted_sources: list[str] = Field(default_factory=lambda: ["ticket:", "http_get:"])
    # Hosts considered internal (is_external = 0).
    internal_hosts: list[str] = Field(
        default_factory=lambda: ["api.internal.example", "status.internal.example", "localhost"]
    )


class BacktestResult(BaseModel):
    events_scanned: int
    query_ms: float
    would_block: int
    would_block_attack_cases: int = 0
    would_block_normal_cases: int = 0
    sql: str = ""


class StreamEvent(BaseModel):
    seq: int
    type: StreamType
    ts_ms: int
    data: dict[str, Any]


class EvidenceBundle(BaseModel):
    """Every number shown to judges. None = not measured yet (UI shows '—')."""

    events_stored: Optional[int] = None
    query_p50_ms: Optional[float] = None
    query_p95_ms: Optional[float] = None
    time_to_detect_ms: Optional[float] = None
    time_to_contain_ms: Optional[float] = None
    hold_decision_ms: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    n_cases: Optional[int] = None
    cost_akashml: Optional[float] = None
    cost_openai: Optional[float] = None
    priced_on: Optional[str] = None
    receipts: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Replay scenarios (fixtures/*.json) — Sripadha writes them, both read them.
# ---------------------------------------------------------------------------


class ReplayStep(BaseModel):
    offset_ms: int  # delay from scenario start
    agent_id: str
    action: Action
    target: str
    payload: str = ""
    tainted_by: str = ""
    bytes: int = 0
    # What the checkpoint must answer. "denied_after_block" = wait for the agent to be
    # blocked (poll /status, 10 s timeout) and then expect "denied".
    expect: Literal["ok", "denied", "denied_after_block", "any"] = "any"


class Scenario(BaseModel):
    name: str
    description: str = ""
    steps: list[ReplayStep]


# Optional in-process seam (ai/copilot.py, Sripadha). Imported lazily by
# POST /policy/copilot; the endpoint returns 503 until it exists.
CopilotFn = Callable[[str, Policy], Awaitable[Policy]]

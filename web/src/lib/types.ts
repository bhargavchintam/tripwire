// TypeScript mirror of tripwire/contracts.py (the frozen contract). Read-only views.

export type AgentMode = "normal" | "heightened" | "quarantined";
export type Result = "ok" | "denied" | "error";
export type Reason = "" | "blocked" | "hold_policy" | "hold_model" | "hold_rule" | "honeytoken";
export type VerdictLabel = "malicious" | "benign" | "uncertain";
export type DecisionSource = "akashml" | "rule_only" | "quorum" | "honeytoken" | "policy";

export const LIVE_AGENTS = ["deploy-bot", "support-bot"] as const;

export interface Verdict {
  verdict: VerdictLabel;
  confidence: number;
  reason: string;
  decision_source: DecisionSource | string;
  model_ids?: string[];
  latency_ms?: number;
  tokens_in?: number;
  tokens_out?: number;
}

export interface IncidentStep {
  ts_ms: number;
  action: string;
  target: string;
  result: Result;
  reason?: Reason | string;
}

export interface Outbreak {
  source_id: string;
  exposed_agents: string[];
  blocked_destinations: string[];
  query_ms?: number;
  incident_id?: string;
}

export interface Receipt {
  sql?: string;
  ms?: number | null;
  rows_read?: number | null;
  label?: string;
  [k: string]: unknown;
}

export interface Incident {
  id: string;
  agent_id: string;
  rule: string;
  opened_ms: number;
  last_step_ts_ms?: number;
  steps: IncidentStep[];
  verdict?: Verdict | null;
  outbreak?: Outbreak | null;
  contained_ms?: number | null;
  closed_ms?: number | null;
  report_md?: string | null;
  receipts?: Receipt[];
  tags?: string[];
  mock?: boolean;
}

export interface ToolEvent {
  ts_ms: number;
  agent_id: string;
  action: string;
  target: string;
  bytes?: number;
  is_external?: number;
  result: Result;
  reason?: Reason | string;
  honeytoken_hit?: number;
  tainted_by?: string;
  code_ref?: string;
  session_id?: string;
  incident_id?: string | null;
}

export interface Alert {
  id?: string;
  agent_id?: string;
  rule: string;
  verdict: VerdictLabel;
  confidence: number;
  reason: string;
  decision_source: DecisionSource | string;
  detected_at_ms: number;
  last_step_ts_ms: number;
  model_ids?: string[];
}

export interface StatusResponse {
  active: string[];
  blocked: string[];
  open_incidents: Incident[];
  watermarks: Record<string, number>;
  modes?: Record<string, AgentMode>;
  verdict_keys?: string[];
  hold_enabled?: boolean;
  policy_version?: number;
  mock?: boolean;
}

export interface Policy {
  version: number;
  allowlists: Record<string, string[]>;
  denylist: string[];
  high_risk_actions: string[];
  fixed_deny_actions: string[];
  untrusted_sources: string[];
  internal_hosts: string[];
  mock?: boolean;
}

export interface EvidenceBundle {
  events_stored: number | null;
  query_p50_ms: number | null;
  query_p95_ms: number | null;
  time_to_detect_ms: number | null;
  time_to_contain_ms: number | null;
  hold_decision_ms: number | null;
  precision: number | null;
  recall: number | null;
  n_cases: number | null;
  cost_akashml: number | null;
  cost_openai: number | null;
  priced_on: string | null;
  receipts: Receipt[];
  mock?: boolean;
}

export interface StreamEvent {
  seq: number;
  type: string;
  ts_ms: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>;
}

// The one live data source: EventSource('/stream') -> reducer.
// Applies the snapshot, then incremental events. Never computes metrics; it only stores
// what the backend sends. isMock flips on as soon as any event carries data.mock.
import { createContext, useContext, useEffect, useReducer, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../lib/api";
import { isNum, toMs } from "../lib/format";
import type {
  AgentMode,
  Alert,
  ApproveResult,
  BacktestResult,
  GuardrailProof,
  Incident,
  Outbreak,
  QuorumVote,
  StreamEvent,
  ToolEvent,
} from "../lib/types";

export type Connection = "connecting" | "live" | "reconnecting";

export interface AgentStats {
  last?: ToolEvent;
  lastDenied?: ToolEvent;
  denied: number; // denied events observed in this stream session (display only)
}

export interface TripwireState {
  connection: Connection;
  isMock: boolean;
  modes: Record<string, AgentMode>;
  blocked: string[];
  holdEnabled: boolean | null;
  policyVersion: number | null;
  events: ToolEvent[]; // newest first, live agents only, capped
  stats: Record<string, AgentStats>;
  incidents: Record<string, Incident>;
  alerts: Alert[];
  outbreak: (Outbreak & { incident_id?: string }) | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  metrics: Record<string, any> | null;
  quarantineOrder: string[]; // most recent last
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  extras: Record<string, any>; // last quorum / backtest / guardrail payloads (phase 2)
  quorums: Record<string, QuorumVote[]>; // incident_id -> per-model votes (SSE quorum)
  guardrails: Record<string, { proof?: GuardrailProof; approved?: ApproveResult }>;
  approvedIds: string[]; // incidents whose guardrail was approved this session
  modelIds: string[]; // distinct verdict model ids seen on the stream
  lastBacktest: BacktestResult | null;
  // Raw per-query detector timings (ms) seen on the stream since the last snapshot, oldest first, capped.
  // Same values the checkpoint pools into /evidence query_p50_ms / query_p95_ms; cleared on snapshot (reset).
  queryTimings: number[];
  // Last eval-runner heartbeat ({source:'eval', metrics, received_ms}). Kept across snapshots, like /evidence.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  evalHeartbeat: Record<string, any> | null;
  lastSeq: number;
}

const MAX_EVENTS = 200;
const MAX_ALERTS = 50;
const MAX_TIMINGS = 500;

export const initialState: TripwireState = {
  connection: "connecting",
  isMock: false,
  modes: {},
  blocked: [],
  holdEnabled: null,
  policyVersion: null,
  events: [],
  stats: {},
  incidents: {},
  alerts: [],
  outbreak: null,
  metrics: null,
  quarantineOrder: [],
  extras: {},
  quorums: {},
  guardrails: {},
  approvedIds: [],
  modelIds: [],
  lastBacktest: null,
  queryTimings: [],
  evalHeartbeat: null,
  lastSeq: 0,
};

/** Finite, non-negative values of a heartbeat's query_timings_ms (the checkpoint keeps the same ones). */
function timingValues(t: unknown): number[] {
  if (!t || typeof t !== "object") return [];
  return Object.values(t as Record<string, unknown>).filter((v): v is number => isNum(v) && v >= 0);
}

/** Live agents only: synthetic background agents are named agent-NN; guardrail replays run as verify:*. */
export const isLiveAgent = (id: string | undefined): id is string =>
  !!id && !id.startsWith("agent-") && !id.startsWith("verify:");

function addModels(seen: string[], ids: unknown): string[] {
  if (!Array.isArray(ids) || ids.length === 0) return seen;
  const next = new Set(seen);
  for (const m of ids) if (typeof m === "string" && m) next.add(m);
  return next.size === seen.length ? seen : [...next];
}

/** Accepts the quorum payload in any of the shapes the checkpoint may send (votes | models | verdicts). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function quorumVotes(d: Record<string, any>): QuorumVote[] {
  const raw = d.votes ?? d.models ?? d.verdicts ?? d.results;
  if (Array.isArray(raw)) {
    return raw
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      .map((v: any) => ({
        model_id: String(v?.model_id ?? v?.model ?? v?.id ?? (Array.isArray(v?.model_ids) ? v.model_ids[0] : "") ?? ""),
        verdict: v?.verdict,
        confidence: typeof v?.confidence === "number" ? v.confidence : undefined,
      }))
      .filter((v) => v.model_id);
  }
  const v = d.verdict && typeof d.verdict === "object" ? d.verdict : d;
  const ids: unknown = v.model_ids ?? d.model_ids;
  return Array.isArray(ids) ? ids.map((m) => ({ model_id: String(m), verdict: v.verdict, confidence: v.confidence })) : [];
}

type Action =
  | { kind: "connection"; value: Connection }
  | { kind: "stream"; ev: StreamEvent }
  | { kind: "hold"; value: boolean }
  | { kind: "approved"; result: ApproveResult }
  | { kind: "backtest"; result: BacktestResult };

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function normEvent(d: Record<string, any>, fallbackTs: number): ToolEvent {
  return {
    ...d,
    ts_ms: toMs(d.ts_ms ?? d.ts ?? fallbackTs),
    agent_id: String(d.agent_id ?? ""),
    action: String(d.action ?? ""),
    target: String(d.target ?? ""),
    result: (d.result ?? "ok") as ToolEvent["result"],
    reason: d.reason ?? "",
  } as ToolEvent;
}

function addStats(stats: Record<string, AgentStats>, e: ToolEvent): Record<string, AgentStats> {
  const prev = stats[e.agent_id] ?? { denied: 0 };
  const denied = e.result === "denied";
  return {
    ...stats,
    [e.agent_id]: {
      last: e,
      lastDenied: denied ? e : prev.lastDenied,
      denied: prev.denied + (denied ? 1 : 0),
    },
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function applySnapshot(state: TripwireState, d: Record<string, any>, ts: number): TripwireState {
  const status = d.status ?? d;
  const rawEvents: unknown[] = d.events ?? d.recent_events ?? d.tool_events ?? [];
  const events = rawEvents
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    .map((e) => normEvent(e as Record<string, any>, ts))
    .filter((e) => isLiveAgent(e.agent_id))
    .sort((a, b) => b.ts_ms - a.ts_ms)
    .slice(0, MAX_EVENTS);
  let stats: Record<string, AgentStats> = {};
  for (const e of [...events].reverse()) stats = addStats(stats, e);
  const incidentList: Incident[] = d.incidents ?? status.open_incidents ?? [];
  const incidents: Record<string, Incident> = {};
  for (const inc of incidentList) if (inc?.id) incidents[inc.id] = inc;
  const modes: Record<string, AgentMode> = { ...(status.modes ?? {}) };
  for (const b of status.blocked ?? []) modes[b] = "quarantined";
  for (const id of Object.keys(modes)) if (!isLiveAgent(id)) delete modes[id];
  let modelIds = state.modelIds;
  for (const inc of incidentList) modelIds = addModels(modelIds, inc?.verdict?.model_ids);
  for (const a of (d.alerts ?? []) as Alert[]) modelIds = addModels(modelIds, a?.model_ids);
  // The checkpoint snapshot has no top-level outbreak; rebuild it from the newest OPEN incident that
  // carries one, so a reload / late-joining screen still shows the trace (reset closes them -> cleared).
  let outbreak: TripwireState["outbreak"] = d.outbreak ?? null;
  if (!outbreak) {
    const traced = incidentList
      .filter((i) => i?.outbreak && i.closed_ms == null && isLiveAgent(i.agent_id))
      .sort((a, b) => b.opened_ms - a.opened_ms)[0];
    if (traced?.outbreak) outbreak = { ...traced.outbreak, incident_id: traced.id };
  }
  return {
    ...state,
    isMock: !!d.mock,
    modes,
    blocked: status.blocked ?? [],
    holdEnabled: typeof status.hold_enabled === "boolean" ? status.hold_enabled : state.holdEnabled,
    policyVersion: status.policy_version ?? null,
    events,
    stats,
    incidents,
    alerts: (d.alerts ?? []).slice(0, MAX_ALERTS),
    outbreak,
    metrics: d.metrics ?? null,
    queryTimings: [],
    // The checkpoint snapshot carries the last eval heartbeat, so the confusion matrix survives a reload.
    evalHeartbeat: d.eval?.source === "eval" ? d.eval : state.evalHeartbeat,
    quarantineOrder: Object.entries(modes)
      .filter(([, m]) => m === "quarantined")
      .map(([a]) => a),
    modelIds,
  };
}

function reducer(state: TripwireState, action: Action): TripwireState {
  if (action.kind === "connection") return { ...state, connection: action.value };
  if (action.kind === "hold") return { ...state, holdEnabled: action.value };
  if (action.kind === "backtest") return { ...state, lastBacktest: action.result };
  if (action.kind === "approved") {
    const id = action.result.incident_id;
    return {
      ...state,
      policyVersion: isNum(action.result.policy_version) ? action.result.policy_version : state.policyVersion,
      guardrails: { ...state.guardrails, [id]: { ...state.guardrails[id], approved: action.result } },
      approvedIds: state.approvedIds.includes(id) ? state.approvedIds : [...state.approvedIds, id],
    };
  }

  const { ev } = action;
  const d = ev.data ?? {};
  const base: TripwireState = {
    ...state,
    lastSeq: ev.seq ?? state.lastSeq,
    isMock: ev.type === "snapshot" ? !!d.mock : state.isMock || !!d.mock,
  };

  switch (ev.type) {
    case "snapshot":
      return applySnapshot(base, d, ev.ts_ms);

    case "tool_event": {
      const e = normEvent(d, ev.ts_ms);
      if (!isLiveAgent(e.agent_id)) return base;
      return {
        ...base,
        events: [e, ...base.events].slice(0, MAX_EVENTS),
        stats: addStats(base.stats, e),
        modes: e.agent_id in base.modes ? base.modes : { ...base.modes, [e.agent_id]: "normal" },
      };
    }

    case "agent_state": {
      const id: string | undefined = d.agent_id;
      if (!id || !isLiveAgent(id)) return base;
      const mode: AgentMode = d.mode ?? (d.blocked ? "quarantined" : "normal");
      const blocked = mode === "quarantined" ? [...new Set([...base.blocked, id])] : base.blocked.filter((b) => b !== id);
      const quarantineOrder =
        mode === "quarantined"
          ? [...base.quarantineOrder.filter((a) => a !== id), id]
          : base.quarantineOrder.filter((a) => a !== id);
      return { ...base, modes: { ...base.modes, [id]: mode }, blocked, quarantineOrder };
    }

    case "incident": {
      const inc = (d.incident ?? d) as Incident;
      if (!inc?.id || !isLiveAgent(inc.agent_id)) return base;
      return {
        ...base,
        incidents: { ...base.incidents, [inc.id]: { ...base.incidents[inc.id], ...inc } },
        modelIds: addModels(base.modelIds, inc.verdict?.model_ids),
      };
    }

    case "alert": {
      const a = (d.alert ?? d) as Alert;
      if (a.agent_id && !isLiveAgent(a.agent_id)) return base;
      return { ...base, alerts: [a, ...base.alerts].slice(0, MAX_ALERTS), modelIds: addModels(base.modelIds, a.model_ids) };
    }

    case "outbreak": {
      const ob = (d.outbreak ?? d) as Outbreak & { incident_id?: string };
      const incidents = { ...base.incidents };
      if (ob.incident_id && incidents[ob.incident_id]) {
        incidents[ob.incident_id] = { ...incidents[ob.incident_id], outbreak: ob };
      }
      const policyVersion = typeof d.policy_version === "number" ? d.policy_version : base.policyVersion;
      return { ...base, outbreak: ob, incidents, policyVersion };
    }

    case "report_ready": {
      const id: string | undefined = d.incident_id ?? d.id;
      if (!id || !base.incidents[id]) return base;
      const patch: Partial<Incident> = {};
      if (typeof d.report_md === "string") patch.report_md = d.report_md;
      if (Array.isArray(d.receipts)) patch.receipts = d.receipts;
      return { ...base, incidents: { ...base.incidents, [id]: { ...base.incidents[id], ...patch } } };
    }

    case "metrics": {
      const hold = typeof d.hold_enabled === "boolean" ? d.hold_enabled : base.holdEnabled;
      const policyVersion = typeof d.policy_version === "number" ? d.policy_version : base.policyVersion;
      // The checkpoint also sends config changes as metrics ({source:'checkpoint', kind:'hold'|'policy'});
      // those update the flags above but must not replace the last heartbeat payload.
      const isConfig = d.source === "checkpoint" && typeof d.kind === "string";
      const timings = d.source === "detector" ? timingValues(d.query_timings_ms) : [];
      return {
        ...base,
        metrics: isConfig ? base.metrics : d,
        holdEnabled: hold,
        policyVersion,
        queryTimings: timings.length ? [...base.queryTimings, ...timings].slice(-MAX_TIMINGS) : base.queryTimings,
        evalHeartbeat: d.source === "eval" ? d : base.evalHeartbeat,
      };
    }

    case "quorum": {
      const votes = quorumVotes(d);
      const incId: string | undefined = d.incident_id ?? d.id;
      return {
        ...base,
        extras: { ...base.extras, quorum: d },
        quorums: incId && votes.length ? { ...base.quorums, [incId]: votes } : base.quorums,
        modelIds: addModels(base.modelIds, votes.map((v) => v.model_id)),
      };
    }

    case "backtest":
      return { ...base, extras: { ...base.extras, backtest: d }, lastBacktest: (d.result ?? d) as BacktestResult };

    case "guardrail": {
      const incId: string | undefined = d.incident_id;
      const extras = { ...base.extras, guardrail: d };
      if (!incId) return { ...base, extras };
      const prev = base.guardrails[incId] ?? {};
      if (d.phase === "approved") {
        const policyVersion = typeof d.policy_version === "number" ? d.policy_version : base.policyVersion;
        return {
          ...base,
          extras,
          policyVersion,
          guardrails: { ...base.guardrails, [incId]: { ...prev, approved: d as ApproveResult } },
          approvedIds: base.approvedIds.includes(incId) ? base.approvedIds : [...base.approvedIds, incId],
        };
      }
      const proof = (d.proof ?? d) as GuardrailProof;
      return {
        ...base,
        extras,
        guardrails: { ...base.guardrails, [incId]: { ...prev, proof } },
        lastBacktest: proof.backtest ?? base.lastBacktest,
      };
    }

    default:
      return base;
  }
}

const STREAM_TYPES = [
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
];

export function useTripwireStream() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const qc = useQueryClient();
  const known = useRef<Set<string>>(new Set());

  useEffect(() => {
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let backoff = 1000;
    let disposed = false;

    const handle = (raw: MessageEvent) => {
      let ev: StreamEvent;
      try {
        ev = JSON.parse(raw.data);
      } catch {
        return;
      }
      if (!ev || typeof ev.type !== "string") return;
      const d = ev.data ?? {};
      if (ev.type === "snapshot") {
        known.current = new Set(((d.incidents ?? d.status?.open_incidents ?? []) as Incident[]).map((i) => i.id));
      } else if (ev.type === "incident") {
        const inc = (d.incident ?? d) as Incident;
        if (inc?.id && !known.current.has(inc.id)) {
          known.current.add(inc.id);
          const v = inc.verdict;
          toast.error(`Incident · ${inc.rule} · ${inc.agent_id}`, {
            description: v
              ? `${v.verdict} ${(v.confidence * 100).toFixed(0)}% · ${v.decision_source}`
              : "awaiting verdict",
          });
        }
        if (inc?.id) qc.invalidateQueries({ queryKey: ["incident", inc.id] });
      } else if (ev.type === "report_ready") {
        const id = d.incident_id ?? d.id;
        if (id) qc.invalidateQueries({ queryKey: ["incident", id] });
      }
      dispatch({ kind: "stream", ev });
    };

    const connect = () => {
      if (disposed) return;
      es = new EventSource("/stream");
      es.onopen = () => {
        backoff = 1000;
        dispatch({ kind: "connection", value: "live" });
      };
      es.onerror = () => {
        dispatch({ kind: "connection", value: "reconnecting" });
        // Native EventSource retries by itself unless the response was fatal (CLOSED).
        if (es && es.readyState === EventSource.CLOSED) {
          es.close();
          retry = setTimeout(connect, backoff);
          backoff = Math.min(backoff * 2, 8000);
        }
      };
      es.onmessage = handle; // unnamed events
      for (const t of STREAM_TYPES) es.addEventListener(t, handle as EventListener);
    };

    connect();
    return () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      es?.close();
    };
  }, [qc]);

  return {
    state,
    setHold: (value: boolean) => dispatch({ kind: "hold", value }),
    recordApproved: (result: ApproveResult) => dispatch({ kind: "approved", result }),
    recordBacktest: (result: BacktestResult) => dispatch({ kind: "backtest", result }),
  };
}

export type TripwireCtx = ReturnType<typeof useTripwireStream>;
export const TripwireContext = createContext<TripwireCtx | null>(null);

/** Read the live Tripwire state (provided once at the app root). */
export function useTripwire(): TripwireCtx {
  const ctx = useContext(TripwireContext);
  if (!ctx) throw new Error("useTripwire must be used inside <TripwireContext.Provider>");
  return ctx;
}

// ---- React Query: REST reads ------------------------------------------------

export function useEvidence() {
  return useQuery({ queryKey: ["evidence"], queryFn: api.evidence, refetchInterval: 2000, retry: false });
}

export function useIncident(id: string | null) {
  return useQuery({
    queryKey: ["incident", id],
    queryFn: () => api.incident(id as string),
    enabled: !!id,
    retry: false,
  });
}

export function usePolicy(version: number | null) {
  return useQuery({ queryKey: ["policy", version], queryFn: api.policy, refetchInterval: 10_000, retry: false });
}

export function useAudit(agentId: string | null | undefined) {
  return useQuery({
    queryKey: ["audit", agentId],
    queryFn: () => api.audit(agentId as string),
    enabled: !!agentId,
    retry: false,
    refetchInterval: 15_000,
  });
}

export function useHeatmap(hours = 72, poll = true) {
  return useQuery({
    queryKey: ["fleet-heatmap", hours],
    queryFn: () => api.heatmap(hours),
    retry: false,
    staleTime: 20_000,
    refetchInterval: poll ? 30_000 : false,
  });
}

export function useFleetTop(minutes = 60, limit = 10) {
  return useQuery({
    queryKey: ["fleet-top", minutes, limit],
    queryFn: () => api.top(minutes, limit),
    retry: false,
    refetchInterval: 5_000,
  });
}

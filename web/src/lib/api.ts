// All fetches use relative paths: Vite proxies them in dev, FastAPI serves web/dist in prod.
import type {
  ApproveResult,
  AuditVerify,
  BacktestResult,
  EvidenceBundle,
  FleetHeatmap,
  FleetTopRow,
  GuardrailProof,
  Incident,
  Policy,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, `${res.status} ${detail}`);
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : {}) as T;
}

export const getJSON = <T>(path: string) => request<T>(path);
export const postJSON = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const putJSON = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });

const enc = encodeURIComponent;

/** Strip UI-only flags before sending a Policy back to the checkpoint. */
function cleanPolicy(p: Policy): Policy {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { mock: _mock, ...rest } = p;
  return rest as Policy;
}

export interface GuildRunResult {
  status: number;
  body: string;
}

export const api = {
  evidence: () => getJSON<EvidenceBundle>("/evidence"),
  incident: (id: string) => getJSON<Incident>(`/incidents/${enc(id)}`),
  incidents: () => getJSON<Incident[]>("/incidents"),
  policy: () => getJSON<Policy>("/policy"),
  putPolicy: (p: Policy) => putJSON<Policy>("/policy", cleanPolicy(p)),
  backtest: (p: Policy) => postJSON<BacktestResult>("/policy/backtest", cleanPolicy(p)),
  copilot: (text: string) => postJSON<Policy>("/policy/copilot", { text }),
  prove: (incidentId: string) => postJSON<GuardrailProof>(`/guardrail/${enc(incidentId)}/prove`),
  approve: (incidentId: string) => postJSON<ApproveResult>(`/guardrail/${enc(incidentId)}/approve`),
  audit: (agentId: string) => getJSON<AuditVerify>(`/audit/verify/${enc(agentId)}`),
  heatmap: (hours = 72) => getJSON<FleetHeatmap>(`/fleet/heatmap?hours=${hours}`),
  top: (minutes = 60, limit = 10) => getJSON<FleetTopRow[]>(`/fleet/top?minutes=${minutes}&limit=${limit}`),
  /** Returns the upstream status + body text (truncated); throws ApiError on 503 etc. */
  guildRun: async (): Promise<GuildRunResult> => {
    const res = await fetch("/guild/run", { method: "POST", headers: { "content-type": "application/json" } });
    const text = (await res.text()).slice(0, 2048);
    if (!res.ok) {
      let detail = text || res.statusText;
      try {
        const b = JSON.parse(text);
        if (typeof b?.detail === "string") detail = b.detail;
      } catch {
        /* plain text */
      }
      throw new ApiError(res.status, `${res.status} ${detail}`);
    }
    // The checkpoint wraps the upstream answer as {status, ok, body}; unwrap it when present.
    try {
      const b = JSON.parse(text);
      if (b && typeof b.status === "number") return { status: b.status, body: String(b.body ?? "").slice(0, 2048) };
    } catch {
      /* plain-text passthrough */
    }
    return { status: res.status, body: text };
  },
  replay: (scenario = "secret_theft") => postJSON<Record<string, unknown>>("/demo/replay", { scenario }),
  reset: () => postJSON<Record<string, unknown>>("/demo/reset"),
  restore: (agentId: string) => postJSON<Record<string, unknown>>(`/restore/${enc(agentId)}`),
  setHold: (enabled: boolean) => postJSON<{ hold_enabled?: boolean }>("/config/hold", { enabled }),
};

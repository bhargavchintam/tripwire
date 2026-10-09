// All fetches use relative paths: Vite proxies them in dev, FastAPI serves web/dist in prod.
import type { EvidenceBundle, Incident, Policy } from "./types";

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

export const api = {
  evidence: () => getJSON<EvidenceBundle>("/evidence"),
  incident: (id: string) => getJSON<Incident>(`/incidents/${encodeURIComponent(id)}`),
  incidents: () => getJSON<Incident[]>("/incidents"),
  policy: () => getJSON<Policy>("/policy"),
  replay: (scenario = "secret_theft") => postJSON<Record<string, unknown>>("/demo/replay", { scenario }),
  reset: () => postJSON<Record<string, unknown>>("/demo/reset"),
  restore: (agentId: string) => postJSON<Record<string, unknown>>(`/restore/${encodeURIComponent(agentId)}`),
  setHold: (enabled: boolean) => postJSON<{ hold_enabled?: boolean }>("/config/hold", { enabled }),
};

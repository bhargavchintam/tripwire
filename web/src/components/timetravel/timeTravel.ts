// D9 time travel: pure, client-side views over the tool events this page already received.
// Nothing is fetched or invented; counts are tallies of the real rows at or before T (display only).
import type { ToolEvent } from "../../lib/types";

/** Event times, ascending (the reducer keeps events newest first; arrival order may jitter, so sort). */
export function eventTimes(events: ToolEvent[]): number[] {
  return events.map((e) => e.ts_ms).sort((a, b) => a - b);
}

/** Index of the last time <= t (0 when t is before every retained event). */
export function indexAt(times: number[], t: number): number {
  let lo = 0;
  let hi = times.length - 1;
  let ans = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) {
      ans = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return ans;
}

/** Events at or before T (keeps the newest-first order). T = null means live: everything. */
export function eventsUpTo(events: ToolEvent[], t: number | null): ToolEvent[] {
  return t === null ? events : events.filter((e) => e.ts_ms <= t);
}

export interface AgentAtT {
  agent_id: string;
  allowed: number;
  denied: number;
  last: ToolEvent;
}

/** Per-agent state at T from events already cut at T (newest first): allowed/denied tallies + last action. */
export function agentStateAt(events: ToolEvent[]): AgentAtT[] {
  const m = new Map<string, AgentAtT>();
  for (const e of events) {
    let a = m.get(e.agent_id);
    if (!a) {
      a = { agent_id: e.agent_id, allowed: 0, denied: 0, last: e }; // newest first -> first seen is last
      m.set(e.agent_id, a);
    } else if (e.ts_ms > a.last.ts_ms) a.last = e;
    if (e.result === "ok") a.allowed += 1;
    else if (e.result === "denied") a.denied += 1;
  }
  return [...m.values()].sort((x, y) => x.agent_id.localeCompare(y.agent_id));
}

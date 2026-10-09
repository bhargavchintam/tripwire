// Live attack chain: the five chain stages derived from an agent's REAL recent tool calls
// (state.events = the /stream tool_event rows), so the chain lights while the attack is still
// happening, before the checkpoint has opened an incident. Read-only view over useTripwire state:
// a stage is set only by an event that really arrived; nothing is inferred, sampled or invented.
import { useEffect, useMemo, useRef, useState } from "react";
import { useTripwire } from "../../hooks/useTripwire";
import type { AgentMode, Incident, ToolEvent } from "../../lib/types";

/** Secret-like targets (superset of detection/loop.py SECRET_SUBSTRINGS: .env, secret, credential, id_rsa). */
export const SECRET_RE = /\.env|secret|credential|passw|token|\.pem|id_rsa|\.aws/i;
/** Encoding / packing commands (the secret_theft rule's "base64 step", plus its usual siblings). */
export const ENCODE_RE = /base64|xxd|openssl|gzip|encode|uuencode/i;

/** Only tool calls from the last minute count toward a live chain. */
export const LIVE_WINDOW_MS = 60_000;
const TICK_MS = 5_000;

export const isSecretRead = (s: { action: string; target: string }) => s.action === "read_file" && SECRET_RE.test(s.target);
export const isEncode = (s: { action: string; target: string }) => s.action === "run_command" && ENCODE_RE.test(s.target);
/** A secret read that is suspicious on its own: tainted by untrusted input, or it touched a honeytoken. */
export const isSuspiciousRead = (e: ToolEvent) =>
  (typeof e.tainted_by === "string" && e.tainted_by.length > 0) || Number(e.honeytoken_hit ?? 0) > 0;
/** The checkpoint stamps is_external on every row (policy.internal_hosts); only an explicit 0 is internal. */
export const isExternalPost = (e: ToolEvent) => e.action === "http_post" && Number(e.is_external ?? 1) !== 0;

export interface LiveChain {
  agentId: string;
  read: ToolEvent; // a live chain always starts at a real secret read, plus a real follow-on (see chainOf)
  encode?: ToolEvent;
  send?: ToolEvent;
  stopped?: ToolEvent; // the external post the checkpoint denied / held
  quarantined: boolean;
  lastTs: number; // newest lit stage (server clock)
  taintedBy?: string; // provenance the checkpoint recorded on the chain's calls
}

/** Walks one agent's events (oldest first) and returns the chain they really form, if any. */
function chainOf(agentId: string, evs: ToolEvent[], mode: AgentMode | undefined): LiveChain | null {
  let read: ToolEvent | undefined;
  let encode: ToolEvent | undefined;
  let send: ToolEvent | undefined;
  let stopped: ToolEvent | undefined;
  for (const e of evs) {
    if (isSecretRead(e)) {
      // A fresh secret read re-anchors the chain only while nothing after the read has happened yet.
      if (!read || (!encode && !send)) read = e;
      continue;
    }
    if (!read) continue;
    if (!encode && isEncode(e)) encode = e;
    if (isExternalPost(e)) {
      if (!send) send = e;
      if (!stopped && e.result === "denied") stopped = e;
    }
  }
  if (!read) return null;
  // A secret read on its own is not an attack chain: the benign deploy-bot reads /app/.env at the start of
  // every session (fixtures/normal_ops.json step 0, agents/deploy_bot.py) and never encodes or sends it.
  // Only show a live chain once a real follow-on stage exists after the read (an encode or an external
  // send), or the read itself carries provenance the checkpoint recorded (tainted_by / a honeytoken hit).
  if (!isSuspiciousRead(read) && !encode && !send) return null;
  const lit = [read, encode, send, stopped].filter((x): x is ToolEvent => !!x);
  return {
    agentId,
    read,
    encode,
    send,
    stopped,
    quarantined: mode === "quarantined",
    lastTs: Math.max(...lit.map((x) => x.ts_ms)),
    taintedBy: lit.map((x) => x.tainted_by).find((t): t is string => typeof t === "string" && t.length > 0),
  };
}

/** Per agent: the newest closed_ms of its incidents (a restore closes them; older calls never re-light). */
function closedBoundaries(incidents: Record<string, Incident>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const inc of Object.values(incidents)) {
    if (inc.closed_ms != null) out[inc.agent_id] = Math.max(out[inc.agent_id] ?? 0, inc.closed_ms);
  }
  return out;
}

/**
 * The newest live chain across the live agents, from their real tool events in the last ~60 s that
 * arrived after the agent's last restore. `enabled=false` returns null (an incident is shown instead).
 * A demo reset clears state.events through the snapshot, so the chain resets with it.
 */
export function useLiveChain(enabled: boolean): LiveChain | null {
  const { state } = useTripwire();
  const { events, modes, incidents } = state;

  // Restore boundary from agent_state: when an agent leaves quarantine, calls up to its newest one so far
  // belong to the contained attack and must not light the live chain again.
  const [restoredAt, setRestoredAt] = useState<Record<string, number>>({});
  const prevModes = useRef(modes);
  useEffect(() => {
    const prev = prevModes.current;
    prevModes.current = modes;
    const patch: Record<string, number> = {};
    for (const [id, m] of Object.entries(prev)) {
      if (m === "quarantined" && modes[id] !== "quarantined") {
        patch[id] = events.find((e) => e.agent_id === id)?.ts_ms ?? 0;
      }
    }
    if (Object.keys(patch).length) setRestoredAt((r) => ({ ...r, ...patch }));
  }, [modes, events]);

  // Wall clock only expires stale chains (never lights anything); ticks only while a chain is up.
  // The window end is whichever is later: the browser clock, or the SERVER clock (newest event ts_ms plus
  // browser time elapsed since it arrived). So history from a snapshot is never shown as "last 60 s",
  // and a viewer clock running behind cannot stretch a chain; one running far ahead only falls back to
  // the incident view (never a fabricated stage).
  const [now, setNow] = useState(() => Date.now());
  const headTs = events[0]?.ts_ms;
  const [headSeen, setHeadSeen] = useState<{ ts: number | undefined; at: number }>(() => ({ ts: headTs, at: Date.now() }));
  useEffect(() => {
    if (headTs !== headSeen.ts) setHeadSeen({ ts: headTs, at: Date.now() });
  }, [headTs, headSeen.ts]);

  const chain = useMemo(() => {
    if (!enabled || events.length === 0) return null;
    const closed = closedBoundaries(incidents);
    // events are newest first; "now" on the server clock = newest ts + time since it was seen here.
    const head = events[0].ts_ms;
    const serverNow = headSeen.ts === head ? head + Math.max(0, now - headSeen.at) : head;
    // `now` is the tick that re-runs this memo; read the clock fresh so a long-idle page is never stale.
    const cutoff = Math.max(serverNow, now, Date.now()) - LIVE_WINDOW_MS;
    const byAgent = new Map<string, ToolEvent[]>();
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      const floor = Math.max(closed[e.agent_id] ?? 0, restoredAt[e.agent_id] ?? 0);
      if (e.ts_ms < cutoff || e.ts_ms <= floor) continue;
      const list = byAgent.get(e.agent_id);
      if (list) list.push(e);
      else byAgent.set(e.agent_id, [e]);
    }
    let best: LiveChain | null = null;
    for (const [id, evs] of byAgent) {
      const c = chainOf(id, evs, modes[id]);
      if (c && (!best || c.lastTs > best.lastTs)) best = c;
    }
    return best;
  }, [enabled, events, incidents, modes, restoredAt, now, headSeen]);

  const up = !!chain;
  useEffect(() => {
    if (!up) return;
    const t = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, [up]);

  return chain;
}

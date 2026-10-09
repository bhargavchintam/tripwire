// E2 voice: speaks REAL stream changes only (a new incident, an outbreak trace, a hold-mode denial)
// through the browser's built-in speechSynthesis. No deps, no network, OFF by default.
// Every word is built from the live TripwireState at speak time; nothing is invented. Snapshot
// history (first load, reconnect, demo reset) is never spoken: only changes that arrive live.
import { useCallback, useEffect, useRef, useState } from "react";
import { isNum } from "../lib/format";
import type { Incident, ToolEvent } from "../lib/types";
import { isHoldReason, isSnapshotSwap } from "../components/live/streamChange";
import type { TripwireState } from "./useTripwire";

const STORAGE_KEY = "tripwire.voice";
const GAP_MS = 2000; // max one utterance per 2 s (start to start, and never over a running one)
const SETTLE_MS = 450; // let the same burst land (agent_state after incident, verdict merges)
const VERDICT_WAIT_MS = 5000; // an incident waits this long for its verdict before speaking without
const STALE_MS = 15_000; // a queued line older than this is dropped, not read late
const MAX_QUEUE = 4;
const MAX_KEYS = 300;
const SAME_TEXT_MS = 10_000; // the same sentence is not repeated within this window
const HELD_BUCKET_MS = 10_000; // repeated identical policy/hold denials collapse to one line per bucket

type Build = (s: TripwireState, ageMs: number) => string | null | typeof RETRY;
interface Item {
  key: string;
  at: number;
  build: Build;
}
const RETRY = Symbol("retry");

export function voiceSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "speechSynthesis" in window &&
    typeof window.SpeechSynthesisUtterance === "function"
  );
}

function readStored(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "on";
  } catch {
    return false;
  }
}

function writeStored(on: boolean) {
  try {
    window.localStorage.setItem(STORAGE_KEY, on ? "on" : "off");
  } catch {
    /* private window / blocked storage: the toggle still works for this page */
  }
}

// ---- words (all from real fields) ------------------------------------------

/** "deploy-bot" -> "deploy bot"; "ticket:4821" -> "ticket 4821". */
const say = (s: string) => s.replace(/[:_/-]+/g, " ").replace(/\s+/g, " ").trim();

const SOURCE_WORDS: Record<string, string> = {
  akashml: "AkashML",
  quorum: "a two-model quorum",
  rule_only: "the rule alone",
  honeytoken: "a honeytoken",
  policy: "policy",
  openai: "the OpenAI fallback",
};

function seconds(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} milliseconds`;
  const s = ms / 1000;
  return `${s.toFixed(1)} seconds`;
}

function host(target: string): string {
  try {
    if (/^[a-z]+:\/\//i.test(target)) return new URL(target).hostname;
  } catch {
    /* not a URL: speak the raw target */
  }
  return target.length > 48 ? target.slice(0, 48) : target;
}

function modeWords(s: TripwireState, agent: string): string {
  const m = s.modes[agent];
  if (m === "quarantined") return "quarantined";
  if (m === "heightened") return "on heightened watch";
  return "flagged";
}

function incidentLine(inc: Incident, s: TripwireState): string {
  const rule = say(inc.rule || "incident");
  const parts = [`${say(inc.agent_id)} ${modeWords(s, inc.agent_id)}.`];
  const v = inc.verdict;
  if (v) {
    const src = SOURCE_WORDS[String(v.decision_source)] ?? say(String(v.decision_source || "unknown source"));
    const lat = isNum(v.latency_ms) ? ` in ${seconds(v.latency_ms)}` : "";
    parts.push(`${rule.charAt(0).toUpperCase()}${rule.slice(1)}, decided by ${src}${lat}.`);
  } else {
    parts.push(`${rule.charAt(0).toUpperCase()}${rule.slice(1)}, verdict pending.`);
  }
  return parts.join(" ");
}

/**
 * Worded from the server's real reason: hold_policy is the always-on policy check (fires with hold
 * mode OFF too), so it never claims hold mode; hold_model / hold_rule only come from hold.decide().
 */
function heldLine(e: ToolEvent): string {
  const t = e.target ? ` to ${say(host(e.target))}` : "";
  const who = say(e.agent_id);
  if (e.reason === "hold_policy") return `Policy denied ${who}: ${say(e.action)}${t}.`;
  if (e.reason === "hold_model") return `Hold mode stopped ${who}: ${say(e.action)}${t}, denied on the model verdict.`;
  if (e.reason === "hold_rule") return `Hold mode stopped ${who}: ${say(e.action)}${t}, denied by a detection rule.`;
  return `${who} denied: ${say(e.action)}${t}.`;
}

// ---- hook -------------------------------------------------------------------

export interface Voice {
  supported: boolean;
  on: boolean;
  toggle: () => void;
}

/**
 * Speaks new incidents, outbreak traces and policy / hold-mode denials from the live stream state.
 * Queue + throttle (one line per 2 s, duplicates dropped, stale lines dropped). Persisted toggle.
 */
export function useVoice(state: TripwireState): Voice {
  const supported = voiceSupported();
  const [on, setOn] = useState<boolean>(() => supported && readStored());

  const stateRef = useRef(state);
  stateRef.current = state;
  const onRef = useRef(on);
  onRef.current = on;

  const queue = useRef<Item[]>([]);
  const spoken = useRef<Set<string>>(new Set());
  const lastStart = useRef(0);
  const lastText = useRef("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const schedule = useCallback((ms: number) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => pumpRef.current(), Math.max(ms, 50));
  }, []);

  const pump = useCallback(() => {
    timer.current = null;
    if (!onRef.current || !voiceSupported()) return;
    const now = Date.now();
    queue.current = queue.current.filter((i) => now - i.at < STALE_MS);
    const item = queue.current[0];
    if (!item) return;
    const synth = window.speechSynthesis;
    const wait = Math.max(lastStart.current + GAP_MS - now, item.at + SETTLE_MS - now);
    if (wait > 0) return schedule(wait);
    if (synth.speaking || synth.pending) return schedule(300);
    const text = item.build(stateRef.current, now - item.at);
    if (text === RETRY) {
      // Move it behind anything else ready, try again shortly.
      queue.current = [...queue.current.slice(1), item];
      return schedule(600);
    }
    queue.current = queue.current.slice(1);
    if (text && !(text === lastText.current && now - lastStart.current < SAME_TEXT_MS)) {
      lastText.current = text;
      const u = new window.SpeechSynthesisUtterance(text);
      u.lang = "en-US";
      u.rate = 1.05;
      lastStart.current = now;
      try {
        synth.speak(u);
      } catch {
        /* engine refused (no user activation yet): drop the line, keep going */
      }
    }
    if (queue.current.length) schedule(GAP_MS);
  }, [schedule]);
  const pumpRef = useRef(pump);
  pumpRef.current = pump;

  const enqueue = useCallback(
    (key: string, build: Build) => {
      if (spoken.current.has(key)) return; // duplicate
      spoken.current.add(key);
      if (spoken.current.size > MAX_KEYS) spoken.current = new Set([...spoken.current].slice(-MAX_KEYS / 2));
      if (!onRef.current) return;
      if (queue.current.length >= MAX_QUEUE) return; // burst: drop rather than read a backlog
      queue.current = [...queue.current, { key, at: Date.now(), build }];
      if (!timer.current) schedule(SETTLE_MS);
    },
    [schedule],
  );

  // Watch the stream state; enqueue only for changes that arrived live (never snapshot history).
  const prev = useRef<TripwireState>(state);
  useEffect(() => {
    const p = prev.current;
    prev.current = state;
    if (p === state) return;
    if (isSnapshotSwap(p, state)) {
      // Seed: everything in a snapshot is history; remember it so it is never announced.
      for (const id of Object.keys(state.incidents)) spoken.current.add(`inc:${id}`);
      if (state.outbreak) spoken.current.add(`ob:${state.outbreak.source_id}|${state.outbreak.incident_id ?? ""}`);
      return;
    }

    // New incidents.
    for (const id of Object.keys(state.incidents)) {
      if (p.incidents[id]) continue;
      enqueue(`inc:${id}`, (s, age) => {
        const inc = s.incidents[id];
        if (!inc) return null;
        if (!inc.verdict && age < VERDICT_WAIT_MS) return RETRY;
        return incidentLine(inc, s);
      });
    }

    // Outbreak trace.
    const ob = state.outbreak;
    if (ob && ob !== p.outbreak && ob.source_id) {
      enqueue(`ob:${ob.source_id}|${ob.incident_id ?? ""}`, (s) => {
        const parts = [`Outbreak traced from ${say(ob.source_id)}.`];
        for (const a of ob.exposed_agents ?? []) {
          const m = s.modes[a];
          parts.push(`${say(a)} ${m === "heightened" ? "on heightened watch" : m === "quarantined" ? "quarantined" : "exposed"}.`);
        }
        return parts.join(" ");
      });
    }

    // Policy and hold-mode denials among the tool events that arrived since the last render (newest first).
    if (state.events !== p.events && state.events.length) {
      const head = p.events[0];
      for (const e of state.events) {
        if (e === head) break;
        if (e.result === "denied" && isHoldReason(e.reason)) {
          const bucket = Math.floor(e.ts_ms / HELD_BUCKET_MS);
          enqueue(`held:${e.agent_id}|${e.action}|${e.target}|${bucket}`, () => heldLine(e));
        }
      }
    }
  }, [state, enqueue]);

  // Off: silence now and forget the queue. Unmount: same.
  useEffect(() => {
    if (on || !supported) return;
    queue.current = [];
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    try {
      window.speechSynthesis.cancel();
    } catch {
      /* ignore */
    }
  }, [on, supported]);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
      if (voiceSupported()) {
        try {
          window.speechSynthesis.cancel();
        } catch {
          /* ignore */
        }
      }
    },
    [],
  );

  const toggle = useCallback(() => {
    if (!supported) return;
    setOn((cur) => {
      const next = !cur;
      writeStored(next);
      return next;
    });
  }, [supported]);

  return { supported, on, toggle };
}

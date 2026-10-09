// Helpers that let Live-tab animations fire ONLY for real, newly arrived stream events.
// Read-only views over useTripwire state: nothing here computes or invents data.
import type { TripwireState } from "../../hooks/useTripwire";
import type { ToolEvent } from "../../lib/types";

type SwapSlice = Pick<TripwireState, "alerts" | "queryTimings">;

/**
 * True when the state moved because of a /stream `snapshot` (first load, reconnect, demo reset),
 * not because live events arrived. A snapshot is the only update that rebuilds BOTH `alerts` and
 * `queryTimings` as new arrays; tool_event / incident / agent_state never touch either.
 * Snapshot rows are history, so they must never trigger "it just happened" animations.
 */
export function isSnapshotSwap(prev: SwapSlice, next: SwapSlice): boolean {
  return prev.alerts !== next.alerts && prev.queryTimings !== next.queryTimings;
}

const keys = new WeakMap<object, number>();
let seq = 0;

/** Stable per-object key for a ToolEvent (events are immutable objects in the reducer). */
export function eventKey(e: ToolEvent): number {
  let k = keys.get(e);
  if (k === undefined) {
    k = ++seq;
    keys.set(e, k);
  }
  return k;
}

/** held = denied while waiting on a hold decision (reason hold_*); same rule as ResultBadge. */
export function isHoldReason(reason: string | undefined | null): boolean {
  return String(reason ?? "").startsWith("hold_");
}

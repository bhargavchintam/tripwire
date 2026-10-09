import { OutbreakCard } from "./incidents/OutbreakCard";
import { useTripwire } from "../hooks/useTripwire";

/**
 * Live outbreak flow: patient zero -> exposed agents (heightened) -> blocked destinations.
 * Driven by the SSE `outbreak`; the graph edges draw in when a real outbreak arrives.
 */
export function OutbreakPanel({ hideWhenEmpty = false }: { hideWhenEmpty?: boolean }) {
  const { state } = useTripwire();
  const ob = state.outbreak;
  if (!ob && hideWhenEmpty) return null;
  return <OutbreakCard ob={ob} modes={state.modes} sourceNote="untrusted input read before the attack" />;
}

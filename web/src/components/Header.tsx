import { Hand, LoaderCircle, Radio, TriangleAlert, WifiOff } from "lucide-react";
import { toast } from "sonner";
import { Switch } from "./ui/switch";
import { Tooltip } from "./ui/tooltip";
import { api } from "../lib/api";
import { isNum } from "../lib/format";
import { useTripwire } from "../hooks/useTripwire";

export async function toggleHold(current: boolean | null, setHold: (v: boolean) => void) {
  const next = !current;
  try {
    const res = await api.setHold(next);
    const value = typeof res.hold_enabled === "boolean" ? res.hold_enabled : next;
    setHold(value);
    toast(value ? "Hold mode ON — risky actions wait for a verdict" : "Hold mode OFF — detect after the fact");
  } catch (e) {
    toast.error(`Hold toggle failed: ${(e as Error).message}`);
  }
}

function LivePill() {
  const { state } = useTripwire();
  if (state.connection === "live")
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-ok/40 bg-ok/10 px-2.5 py-1 text-xs font-semibold text-ok">
        <span className="relative flex size-2">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-ok opacity-75" />
          <span className="relative inline-flex size-2 rounded-full bg-ok" />
        </span>
        <Radio className="size-3.5" /> LIVE
      </span>
    );
  const connecting = state.connection === "connecting";
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-held/50 bg-held/10 px-2.5 py-1 text-xs font-semibold text-held">
      {connecting ? <LoaderCircle className="size-3.5 animate-spin" /> : <WifiOff className="size-3.5" />}
      {connecting ? "CONNECTING" : "RECONNECTING"}
    </span>
  );
}

export function Header() {
  const { state, setHold } = useTripwire();
  // Mock sends events_per_s at the top level; real heartbeats nest it under Heartbeat.metrics.
  const eps = state.metrics?.events_per_s ?? state.metrics?.metrics?.events_per_s;
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-bg/90 backdrop-blur">
      {state.isMock && (
        <div
          role="alert"
          className="flex items-center justify-center gap-2 bg-held px-4 py-2 text-sm font-extrabold tracking-wide text-black"
        >
          <TriangleAlert className="size-5" />
          MOCK DATA — synthetic events from tripwire/mock_server.py, not the live checkpoint
          <TriangleAlert className="size-5" />
        </div>
      )}
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-2xl font-bold tracking-[0.18em] text-fg">
            TRIP<span className="text-bad">WIRE</span>
          </span>
          <span className="hidden text-sm text-muted md:inline">the immune system for AI-agent fleets</span>
        </div>
        <div className="ml-auto flex items-center gap-4">
          {isNum(eps) && (
            <span className="font-mono text-xs text-muted">
              {eps.toFixed(1)} <span className="text-dim">events/s</span>
            </span>
          )}
          <LivePill />
          <Tooltip content="When ON, high-risk actions (http_post, assume_role, disable_logging) wait for a verdict before they run. Shortcut: H">
            <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-line bg-panel px-3 py-1.5">
              <Hand className={`size-4 ${state.holdEnabled ? "text-held" : "text-dim"}`} />
              <span className="text-sm font-semibold">
                Hold mode{" "}
                <span className={state.holdEnabled ? "text-held" : "text-dim"}>
                  {state.holdEnabled === null ? "—" : state.holdEnabled ? "ON" : "OFF"}
                </span>
              </span>
              <Switch
                checked={!!state.holdEnabled}
                onCheckedChange={() => toggleHold(state.holdEnabled, setHold)}
                aria-label="Toggle hold mode"
              />
            </label>
          </Tooltip>
        </div>
      </div>
    </header>
  );
}

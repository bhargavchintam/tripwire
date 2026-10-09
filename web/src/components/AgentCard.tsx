import { AnimatePresence, motion } from "motion/react";
import { Bot, OctagonX, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "./ui/button";
import { ModeBadge, ResultBadge } from "./badges";
import { api } from "../lib/api";
import { fmtClock } from "../lib/format";
import type { AgentMode } from "../lib/types";
import { useTripwire } from "../hooks/useTripwire";

export async function restoreAgent(agentId: string) {
  try {
    await api.restore(agentId);
    toast.success(`Restored ${agentId}`, { description: "Block cleared, watermark advanced" });
  } catch (e) {
    toast.error(`Restore failed: ${(e as Error).message}`);
  }
}

const BORDER: Record<AgentMode, string> = {
  normal: "rgb(34 197 94 / 0.35)",
  heightened: "rgb(245 158 11 / 0.7)",
  quarantined: "rgb(239 68 68 / 0.95)",
};
const BG: Record<AgentMode, string> = {
  normal: "rgb(13 17 23 / 1)",
  heightened: "rgb(40 30 10 / 0.9)",
  quarantined: "rgb(48 12 12 / 0.95)",
};

export function AgentCard({ agentId }: { agentId: string }) {
  const { state } = useTripwire();
  const mode: AgentMode = state.modes[agentId] ?? "normal";
  const st = state.stats[agentId];
  const last = st?.last;
  const lastDenied = st?.lastDenied;
  return (
    <motion.div
      initial={false}
      animate={{ borderColor: BORDER[mode], backgroundColor: BG[mode], scale: mode === "quarantined" ? [1, 1.02, 1] : 1 }}
      transition={{ duration: 0.45 }}
      className={`relative overflow-hidden rounded-xl border-2 p-4 ${mode === "quarantined" ? "animate-quarantine" : ""}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <Bot className="size-5 text-muted" />
          <span className="font-mono text-lg font-bold">{agentId}</span>
        </div>
        <AnimatePresence mode="wait">
          <motion.span
            key={mode}
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 6 }}
            transition={{ duration: 0.2 }}
          >
            <ModeBadge mode={mode} className="px-2 py-1 text-xs" />
          </motion.span>
        </AnimatePresence>
      </div>

      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
        <dt className="text-muted">Last action</dt>
        <dd className="min-w-0 truncate font-mono text-xs leading-5" title={last ? `${last.action} ${last.target}` : ""}>
          {last ? (
            <>
              <span className="text-fg">{last.action}</span> <span className="text-muted">{last.target}</span>
            </>
          ) : (
            <span className="text-dim">—</span>
          )}
        </dd>
        <dt className="text-muted">Result</dt>
        <dd>{last ? <ResultBadge result={last.result} reason={last.reason} /> : <span className="text-dim">—</span>}</dd>
        <dt className="text-muted" title="Denied actions seen on the live stream since this page loaded">
          Denied
        </dt>
        <dd className="font-mono font-bold tabular-nums">
          {st ? <span className={st.denied ? "text-bad" : "text-fg"}>{st.denied}</span> : <span className="text-dim">—</span>}
        </dd>
      </dl>

      <AnimatePresence>
        {lastDenied && (
          <motion.div
            key={`${lastDenied.ts_ms}-${lastDenied.target}`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="mt-3 flex items-center gap-3 rounded-lg border border-bad/40 bg-black/30 p-2"
          >
            <motion.span
              initial={{ scale: 1.9, rotate: -20, opacity: 0 }}
              animate={{ scale: 1, rotate: -8, opacity: 1 }}
              transition={{ type: "spring", stiffness: 380, damping: 18 }}
              className="inline-flex shrink-0 items-center gap-1 rounded border-2 border-bad px-2 py-0.5 font-mono text-sm font-black tracking-[0.2em] text-bad"
            >
              <OctagonX className="size-4" /> DENIED
            </motion.span>
            <span className="min-w-0 truncate font-mono text-xs text-muted" title={lastDenied.target}>
              {fmtClock(lastDenied.ts_ms, false)} · {lastDenied.action} {lastDenied.target}
              {lastDenied.reason ? ` · ${lastDenied.reason}` : ""}
            </span>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="mt-3 flex justify-end">
        <Button
          size="sm"
          variant={mode === "normal" ? "ghost" : "ok"}
          disabled={mode === "normal"}
          onClick={() => restoreAgent(agentId)}
        >
          <Undo2 /> Restore
        </Button>
      </div>
    </motion.div>
  );
}

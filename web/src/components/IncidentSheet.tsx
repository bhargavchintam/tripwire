import { lazy, Suspense, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Biohazard, Bot, CircleCheck, Database, FileText, History, Radio, Scale, ShieldCheck } from "lucide-react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./ui/sheet";
import { Badge } from "./ui/badge";
import { Tooltip } from "./ui/tooltip";
import { StatusPill } from "./badges";
import { AttackChain } from "./AttackChain";
import { AuditBadge } from "./AuditBadge";
import { GuardrailPanel } from "./GuardrailPanel";
import { OutbreakView } from "./incidents/OutbreakGraph";
import { OutbreakCard } from "./incidents/OutbreakCard";
import { Receipts } from "./incidents/Receipts";
import { SheetSection } from "./incidents/SheetSection";
import { Scrubber, TimelineSteps } from "./incidents/Timeline";
import { VerdictBlock } from "./incidents/VerdictBlock";
import { CopyButton } from "./incidents/CopyButton";
import { CopyId, EASE_OUT, Eyebrow, SPRING, SPRING_SOFT, Skeleton } from "./fx";
import { DASH, fmtClock, fmtMs } from "../lib/format";
import { cn } from "../lib/utils";
import type { AgentMode, Incident } from "../lib/types";
import { useIncident, useTripwire } from "../hooks/useTripwire";

// Re-exported for existing importers (IncidentsTab, EvidenceTab).
export { OutbreakCard, Receipts };

const ReportMarkdown = lazy(() => import("./ReportMarkdown"));

/** D9 time travel: the incident as it looked after `pos` steps (pos = steps.length -> live). */
function incidentAt(inc: Incident, pos: number, liveMode: AgentMode | undefined): { inc: Incident; mode: AgentMode | undefined } {
  const n = inc.steps.length;
  if (pos >= n) return { inc, mode: liveMode };
  const shown = inc.steps.slice(0, pos);
  const lastTs = shown.length ? shown[shown.length - 1].ts_ms : -Infinity;
  const contained =
    shown.some((s) => s.reason === "blocked") || (inc.contained_ms != null && inc.contained_ms <= lastTs);
  return {
    inc: { ...inc, steps: shown, contained_ms: contained ? inc.contained_ms : null, closed_ms: null },
    mode: contained ? "quarantined" : "normal",
  };
}

type NavItem = { id: string; label: string; badge?: ReactNode };

/** Underline tabs that jump to a section; the active one follows the scroll (shared layoutId bar). */
function SectionNav({ items, active, onJump }: { items: NavItem[]; active: string; onJump: (id: string) => void }) {
  const uid = useId();
  return (
    <nav aria-label="Incident sections" className="-mx-3 -mb-px mt-4 flex overflow-x-auto">
      {items.map((it) => {
        const on = it.id === active;
        return (
          <button
            key={it.id}
            type="button"
            onClick={() => onJump(it.id)}
            aria-current={on ? "true" : undefined}
            className={cn(
              "relative inline-flex h-10 shrink-0 cursor-pointer items-center gap-1.5 rounded-t-md px-3 text-[13px] font-medium transition-colors duration-150",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand",
              on ? "text-fg" : "text-dim hover:text-fg",
            )}
          >
            {it.label}
            {it.badge}
            {on && (
              <motion.span
                layoutId={`sheet-nav-${uid}`}
                aria-hidden
                className="absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-brand"
                transition={SPRING}
              />
            )}
          </button>
        );
      })}
    </nav>
  );
}

const NavDot = ({ tone, label }: { tone: "info" | "held"; label: string }) => (
  <span
    aria-label={label}
    className={cn("size-1.5 animate-pop-in rounded-full", tone === "info" ? "bg-info" : "bg-held")}
  />
);
const NavCount = ({ n }: { n: number }) => (
  <span className="font-mono text-xs font-normal tabular-nums text-dim">{n}</span>
);

function EmptyNote({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2.5 rounded-xl border border-dashed border-line-strong px-4 py-3.5 text-sm text-dim [&_svg]:size-4 [&_svg]:shrink-0">
      {icon}
      {children}
    </div>
  );
}

/** Shown only while the incident request (or the report chunk) is really in flight. */
function SheetSkeleton() {
  return (
    <div role="status" aria-label="Loading incident" className="flex flex-col gap-6 px-6 py-6">
      <div className="flex gap-2">
        <Skeleton className="h-[22px] w-28 rounded-full" />
        <Skeleton className="h-[22px] w-32 rounded-full" />
      </div>
      <Skeleton className="h-16 w-full rounded-xl" />
      <Skeleton className="h-40 w-full rounded-[var(--radius-card)]" />
      <div className="flex flex-col gap-2">
        <Skeleton className="h-12 w-full rounded-xl" />
        <Skeleton className="h-12 w-full rounded-xl" />
        <Skeleton className="h-12 w-full rounded-xl" />
      </div>
    </div>
  );
}

function ReportSkeleton() {
  return (
    <div role="status" aria-label="Rendering report" className="flex flex-col gap-2.5">
      <Skeleton className="h-5 w-48" />
      <Skeleton className="h-3.5 w-full" />
      <Skeleton className="h-3.5 w-11/12" />
      <Skeleton className="h-3.5 w-4/5" />
    </div>
  );
}

export function IncidentSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { state } = useTripwire();
  const reduce = useReducedMotion();
  const q = useIncident(id);
  const inc: Incident | undefined = (q.data && !q.isError ? q.data : undefined) ?? (id ? state.incidents[id] : undefined);
  // null = follow live (always the latest step); a number = scrubbed position.
  const [scrub, setScrub] = useState<{ id: string | null; pos: number | null }>({ id: null, pos: null });
  const n = inc?.steps.length ?? 0;
  const pos = scrub.id === id && scrub.pos !== null ? Math.min(scrub.pos, n) : n;
  const at = inc ? incidentAt(inc, pos, state.modes[inc.agent_id]) : null;
  const open = !!inc && !inc.closed_ms;
  const approved = inc ? state.guardrails[inc.id]?.approved : undefined;
  const incId = inc?.id;

  // ---- section nav: jump on click, follow the scroll otherwise --------------------------------
  const scrollRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState("verdict");
  const lockUntil = useRef(0);
  useEffect(() => {
    setActive("verdict");
    const root = scrollRef.current;
    if (!root) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      if (performance.now() < lockUntil.current) return;
      const els = Array.from(root.querySelectorAll<HTMLElement>("[data-section]"));
      if (!els.length) return;
      let cur = els[0].dataset.section ?? "verdict";
      if (root.scrollTop + root.clientHeight >= root.scrollHeight - 4) cur = els[els.length - 1].dataset.section ?? cur;
      else for (const el of els) if (el.offsetTop - root.scrollTop <= 96) cur = el.dataset.section ?? cur;
      setActive(cur);
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    root.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      root.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
    };
  }, [incId]);
  const jump = (sid: string) => {
    const root = scrollRef.current;
    const el = root?.querySelector<HTMLElement>(`[data-section="${sid}"]`);
    if (!root || !el) return;
    setActive(sid);
    lockUntil.current = performance.now() + 900;
    root.scrollTo({ top: el.offsetTop, behavior: reduce ? "auto" : "smooth" });
  };

  const nav: NavItem[] = inc
    ? [
        { id: "verdict", label: "Verdict" },
        { id: "chain", label: "Chain" },
        { id: "timeline", label: "Timeline", badge: <NavCount n={n} /> },
        {
          id: "cure",
          label: "Cure",
          badge: approved ? <ShieldCheck className="size-3.5 animate-pop-in text-ok" strokeWidth={2} aria-label="approved" /> : undefined,
        },
        { id: "outbreak", label: "Outbreak", badge: inc.outbreak ? <NavDot tone="held" label="traced" /> : undefined },
        { id: "report", label: "Report", badge: inc.report_md ? <NavDot tone="info" label="ready" /> : undefined },
        { id: "receipts", label: "Receipts", badge: inc.receipts?.length ? <NavCount n={inc.receipts.length} /> : undefined },
      ]
    : [];

  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="max-w-[720px]">
        <SheetHeader className="px-6 pb-0 pt-5">
          <div className="flex min-h-7 flex-wrap items-center gap-x-2.5 gap-y-1 animate-fade-in">
            <Eyebrow dot tone={inc ? (inc.closed_ms ? "ok" : "bad") : undefined}>
              Incident
            </Eyebrow>
            {id ? <CopyId value={id} className="text-xs text-muted" /> : null}
          </div>
          <SheetTitle className="mt-2 flex flex-wrap items-baseline gap-x-2.5 gap-y-1 animate-rise-in">
            <span className="font-mono text-xl font-semibold tracking-[-0.02em] text-fg">{inc?.rule ?? "incident"}</span>
            {inc?.agent_id ? (
              <span className="inline-flex items-baseline gap-2 text-base font-normal text-dim">
                on
                <span className="inline-flex items-center gap-1 self-center">
                  <Bot className="size-4 text-dim" strokeWidth={1.75} aria-hidden />
                  <CopyId
                    value={inc.agent_id}
                    label={<span className="font-sans text-base font-semibold tracking-[-0.01em] text-fg">{inc.agent_id}</span>}
                  />
                </span>
              </span>
            ) : null}
          </SheetTitle>
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 animate-rise-in [animation-delay:60ms]">
            {inc ? (
              open ? (
                <StatusPill
                  variant="bad"
                  icon={<span aria-hidden className="size-1.5 rounded-full bg-current" />}
                  tip="Still open: not closed by the checkpoint yet"
                >
                  Open
                </StatusPill>
              ) : (
                <StatusPill variant="muted" icon={<CircleCheck />} tip={`Closed at ${fmtClock(inc.closed_ms)}`}>
                  Closed
                </StatusPill>
              )
            ) : null}
            <AuditBadge agentId={inc?.agent_id} />
            <SheetDescription className="mt-0 flex flex-wrap items-center gap-x-3 font-mono text-xs text-dim">
              <span>
                opened <span className="tabular-nums text-muted">{fmtClock(inc?.opened_ms)}</span>
              </span>
              <span>
                contained{" "}
                <span className="tabular-nums text-muted">{inc?.contained_ms ? fmtClock(inc.contained_ms) : DASH}</span>
              </span>
            </SheetDescription>
          </div>
          {inc ? <SectionNav items={nav} active={active} onJump={jump} /> : <div className="h-5" />}
        </SheetHeader>

        {!inc || !at ? (
          q.isLoading ? (
            <SheetSkeleton />
          ) : (
            <div className="p-6 text-sm text-dim">Incident not found.</div>
          )
        ) : (
          <div ref={scrollRef} key={inc.id} className="relative flex-1 overflow-y-auto overscroll-contain">
            <SheetSection id="verdict" icon={<Scale />} label="Verdict" reveal={0}>
              <VerdictBlock inc={inc} votes={state.quorums[inc.id]} />
            </SheetSection>

            <SheetSection id="chain" reveal={1}>
              <AttackChain incident={at.inc} mode={at.mode} />
            </SheetSection>

            <SheetSection
              id="timeline"
              icon={<History />}
              label="Timeline · time travel"
              reveal={2}
              meta={
                pos >= n ? (
                  <Badge variant="muted">
                    <Radio /> Live
                  </Badge>
                ) : (
                  <Badge variant="brand" className="font-mono">
                    <History /> step {pos} of {n}
                    {pos > 0 ? ` · ${fmtClock(inc.steps[pos - 1].ts_ms)}` : ""}
                  </Badge>
                )
              }
            >
              {n > 0 && <Scrubber steps={inc.steps} pos={pos} onScrub={(p) => setScrub({ id, pos: p })} />}
              <TimelineSteps key={inc.id} steps={inc.steps} pos={pos} />
            </SheetSection>

            <SheetSection id="cure" reveal={3} revealOn="view" className="bg-panel-2/60">
              <GuardrailPanel key={inc.id} incidentId={inc.id} />
            </SheetSection>

            <SheetSection
              id="outbreak"
              icon={<Biohazard />}
              label="Outbreak trace"
              reveal={4}
              revealOn="view"
              meta={
                inc.outbreak ? (
                  <Tooltip content="Trace query time, as measured">
                    <span className="font-mono text-xs tabular-nums text-dim">traced in {fmtMs(inc.outbreak.query_ms)}</span>
                  </Tooltip>
                ) : undefined
              }
            >
              {inc.outbreak ? (
                <OutbreakView key={`${inc.outbreak.source_id}|${inc.outbreak.incident_id ?? ""}`} ob={inc.outbreak} />
              ) : (
                <EmptyNote icon={<Biohazard strokeWidth={1.75} />}>No outbreak trace yet.</EmptyNote>
              )}
            </SheetSection>

            <SheetSection
              id="report"
              icon={<FileText />}
              label="Investigator report"
              reveal={5}
              revealOn="view"
              meta={inc.report_md ? <CopyButton value={inc.report_md} label="Copy report (markdown)" /> : undefined}
            >
              {inc.report_md ? (
                <motion.article
                  key="report"
                  initial={reduce ? false : { opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ y: SPRING_SOFT, opacity: { duration: 0.24, ease: EASE_OUT } }}
                  className="rounded-xl border border-line bg-panel px-6 py-5 shadow-sm"
                >
                  <Suspense fallback={<ReportSkeleton />}>
                    <ReportMarkdown md={inc.report_md} />
                  </Suspense>
                </motion.article>
              ) : (
                <EmptyNote icon={<FileText strokeWidth={1.75} />}>Report not ready yet.</EmptyNote>
              )}
            </SheetSection>

            <SheetSection
              id="receipts"
              icon={<Database />}
              label="SQL receipts"
              reveal={6}
              revealOn="view"
              className="pb-10"
            >
              <Receipts receipts={inc.receipts} />
            </SheetSection>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

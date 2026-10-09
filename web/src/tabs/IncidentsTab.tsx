import { useRef, type KeyboardEvent, type ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowUpRight, CircleCheck, FileText, ShieldCheck, Siren } from "lucide-react";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { Tooltip } from "../components/ui/tooltip";
import { DecisionSourceBadge, StatusPill, VerdictBadge } from "../components/badges";
import { OutbreakCard } from "../components/incidents/OutbreakCard";
import { sortedIncidents } from "../components/IncidentFeed";
import { CopyId, Count, EASE_OUT, GlowCard, Kbd, Reveal, SectionHeader, Skeleton, SPRING_SOFT } from "../components/fx";
import { DASH, fmtClock } from "../lib/format";
import { cn } from "../lib/utils";
import { useTripwire } from "../hooks/useTripwire";

// A table row that rises in once: staggered for the initial list; a row that arrives later because a
// real incident opened rises in on its own.
const MotionRow = motion.create(TableRow);

const COLS = 10;

/** Compact header stat: 12px label over a count that rolls only when the real value changes. */
function HeaderStat({ label, value, tone, icon }: { label: string; value: number; tone?: string; icon?: ReactNode }) {
  return (
    <div className="flex min-w-[88px] flex-col px-4 py-2.5 first:pl-4">
      <span className="text-xs text-dim">{label}</span>
      <span className={cn("mt-0.5 inline-flex items-center gap-1.5 text-xl font-semibold tracking-[-0.02em]", tone)}>
        {icon}
        <Count value={value} />
      </span>
    </div>
  );
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 4 }, (_, i) => (
        <TableRow key={i} className="hover:bg-transparent">
          <TableCell colSpan={COLS} className="py-3">
            <Skeleton className="h-5 w-full" />
          </TableCell>
        </TableRow>
      ))}
    </>
  );
}

export function IncidentsTab({ onOpen }: { onOpen: (id: string) => void }) {
  const { state } = useTripwire();
  const reduce = useReducedMotion();
  const list = sortedIncidents(state.incidents);
  const openCount = list.filter((i) => !i.closed_ms).length;
  const loading = list.length === 0 && state.connection === "connecting";
  // Rows present on first paint stagger in; rows that arrive later (a real new incident) rise alone.
  const firstIds = useRef<Set<string> | null>(null);
  if (firstIds.current === null && !loading) firstIds.current = new Set(list.map((i) => i.id));

  const onKey = (id: string) => (e: KeyboardEvent<HTMLTableRowElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen(id);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <SectionHeader
        eyebrow="Incidents · live stream"
        eyebrowTone={openCount > 0 ? "bad" : "info"}
        pre="Incident"
        em="log"
        description="Newest first. Each row is an incident Tripwire opened: its verdict, what decided it, and whether it is still open."
        actions={
          list.length > 0 ? (
            <div className="flex items-stretch divide-x divide-line rounded-[var(--radius-control)] border border-line bg-panel shadow-sm">
              <HeaderStat label="Incidents" value={list.length} tone="text-fg" />
              {openCount > 0 ? (
                <HeaderStat
                  label="Open"
                  value={openCount}
                  tone="text-bad"
                  icon={<span aria-hidden className="size-2 rounded-full bg-bad ring-4 ring-bad-soft" />}
                />
              ) : (
                <div className="flex min-w-[88px] flex-col px-4 py-2.5">
                  <span className="text-xs text-dim">Open</span>
                  <span className="mt-0.5 inline-flex items-center gap-1.5 text-sm font-medium text-ok">
                    <CircleCheck className="size-4" strokeWidth={1.75} aria-hidden /> All closed
                  </span>
                </div>
              )}
            </div>
          ) : undefined
        }
      />

      <Reveal trigger="mount" index={1}>
        <OutbreakCard ob={state.outbreak} modes={state.modes} />
      </Reveal>

      <Reveal trigger="mount" index={2}>
        <GlowCard
          tone={openCount > 0 ? "bad" : "neutral"}
          glow="soft"
          eyebrow={
            <>
              <Siren className={openCount > 0 ? "text-bad" : undefined} strokeWidth={1.75} /> Incidents
            </>
          }
          title="Every incident, newest first"
          actions={
            <span className="hidden items-center gap-1.5 text-xs text-dim sm:inline-flex">
              Open a row for the timeline, proven cure, report and receipts
              <Kbd className="ml-0.5">↵</Kbd>
            </span>
          }
          bodyClassName="px-0 pb-0"
        >
          {/* own scroll box (not ui/Table's wrapper) so the header row can stick while the list scrolls */}
          <div className="max-h-[min(620px,calc(100vh-15rem))] min-h-[220px] overflow-auto border-t border-line">
            <table className="w-full caption-bottom border-separate border-spacing-0 text-sm tabular-nums">
              <TableHeader className="[&_th]:sticky [&_th]:top-0 [&_th]:z-10 [&_th]:bg-panel-2/95 [&_th]:backdrop-blur-sm">
                <TableRow className="even:bg-transparent">
                  <TableHead>Opened</TableHead>
                  <TableHead>Incident</TableHead>
                  <TableHead>Agent</TableHead>
                  <TableHead>Rule</TableHead>
                  <TableHead>Verdict</TableHead>
                  <TableHead>Decided by</TableHead>
                  <TableHead className="text-right">Steps</TableHead>
                  <TableHead className="text-center">Report</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead className="w-12">
                    <span className="sr-only">Open details</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading && <SkeletonRows />}
                {!loading && list.length === 0 && (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={COLS} className="py-14 text-center">
                      <div className="mx-auto flex max-w-xs flex-col items-center gap-2">
                        <span className="grid size-10 place-items-center rounded-full border border-line bg-panel-2 text-dim">
                          <Siren className="size-4" strokeWidth={1.75} aria-hidden />
                        </span>
                        <span className="text-sm font-medium text-fg">No incidents yet.</span>
                      </div>
                    </TableCell>
                  </TableRow>
                )}
                {list.map((inc, i) => {
                  const open = !inc.closed_ms;
                  const initial = firstIds.current?.has(inc.id) ?? true;
                  const delay = initial ? Math.min(i * 0.03, 0.36) : 0;
                  const approvedV = state.guardrails[inc.id]?.approved?.policy_version;
                  return (
                    <MotionRow
                      key={inc.id}
                      initial={reduce ? false : { opacity: 0, y: initial ? 6 : -6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ y: { ...SPRING_SOFT, delay }, opacity: { duration: 0.22, ease: EASE_OUT, delay } }}
                      tabIndex={0}
                      aria-label={`Open incident ${inc.id}: ${inc.rule} on ${inc.agent_id}`}
                      onClick={() => onOpen(inc.id)}
                      onKeyDown={onKey(inc.id)}
                      className="group/row cursor-pointer focus-visible:bg-brand-soft/40 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand"
                    >
                      <TableCell
                        className={cn(
                          "whitespace-nowrap font-mono text-xs text-muted",
                          open && "shadow-[inset_2px_0_0_var(--color-bad)]",
                        )}
                      >
                        {fmtClock(inc.opened_ms, false)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        <CopyId value={inc.id} className="text-muted" />
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        <CopyId
                          value={inc.agent_id}
                          label={<span className="font-sans text-sm font-semibold tracking-[-0.01em] text-fg">{inc.agent_id}</span>}
                        />
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        <span className="rounded-md border border-line bg-panel-2 px-1.5 py-0.5 font-mono text-xs text-fg">
                          {inc.rule}
                        </span>
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        <VerdictBadge verdict={inc.verdict?.verdict} confidence={inc.verdict?.confidence} />
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        <DecisionSourceBadge source={inc.verdict?.decision_source} />
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-right font-mono text-[13px] tabular-nums text-fg">
                        {inc.steps?.length ?? DASH}
                      </TableCell>
                      <TableCell className="text-center">
                        {inc.report_md ? (
                          <Tooltip content="Investigator report ready">
                            <span className="inline-grid size-6 place-items-center rounded-md text-info">
                              <FileText className="size-4" strokeWidth={1.75} aria-label="Report ready" />
                            </span>
                          </Tooltip>
                        ) : (
                          <span className="text-dim" aria-label="No report yet">
                            {DASH}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        <span className="inline-flex items-center gap-1.5">
                          {open ? (
                            <StatusPill
                              variant="bad"
                              icon={<span aria-hidden className="size-1.5 rounded-full bg-current" />}
                              tip="Still open"
                            >
                              Open
                            </StatusPill>
                          ) : (
                            <StatusPill variant="muted" icon={<CircleCheck />} tip={`Closed at ${fmtClock(inc.closed_ms)}`}>
                              Closed
                            </StatusPill>
                          )}
                          {approvedV != null ? (
                            <Tooltip content={`Cure approved · policy v${approvedV}`}>
                              <ShieldCheck className="size-4 text-ok" strokeWidth={1.75} aria-label="Cure approved" />
                            </Tooltip>
                          ) : null}
                        </span>
                      </TableCell>
                      <TableCell className="text-right">
                        <span
                          aria-hidden
                          className="inline-flex size-7 items-center justify-center rounded-lg border border-transparent text-dim transition-[color,background-color,border-color,box-shadow,transform] duration-200 group-hover/row:border-line group-hover/row:bg-panel group-hover/row:text-fg group-hover/row:shadow-sm group-focus-visible/row:text-brand motion-safe:group-hover/row:-translate-y-px motion-safe:group-hover/row:translate-x-px"
                        >
                          <ArrowUpRight className="size-3.5" strokeWidth={1.75} />
                        </span>
                      </TableCell>
                    </MotionRow>
                  );
                })}
              </TableBody>
            </table>
          </div>
        </GlowCard>
      </Reveal>
    </div>
  );
}

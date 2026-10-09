import { Fragment, type ReactNode } from "react";
import { Database } from "lucide-react";
import { Badge } from "../ui/badge";
import { Tooltip } from "../ui/tooltip";
import { Reveal } from "../fx";
import { CopyButton } from "./CopyButton";
import { DASH, fmtInt, fmtMs } from "../../lib/format";
import type { Receipt } from "../../lib/types";

// SQL keywords get the ClickHouse/query tint; `{name:Type}` bind placeholders stay ink but medium weight.
const SQL_TOKEN =
  /\b(SELECT|FROM|WHERE|AND|OR|NOT|IN|AS|ON|JOIN|LEFT|INNER|GROUP BY|ORDER BY|HAVING|LIMIT|WITH|UNION ALL|DISTINCT|DESC|ASC|BETWEEN|CASE|WHEN|THEN|ELSE|END)\b|(\{[A-Za-z_][\w]*:[A-Za-z0-9()]+\})/g;

/** Light SQL emphasis for a receipt (display only: the text is shown verbatim). */
export function SqlText({ sql }: { sql: string }) {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of sql.matchAll(SQL_TOKEN)) {
    const i = m.index ?? 0;
    if (i > last) out.push(sql.slice(last, i));
    out.push(
      m[1] ? (
        <span key={i} className="font-medium text-info">
          {m[0]}
        </span>
      ) : (
        <span key={i} className="font-medium text-fg">
          {m[0]}
        </span>
      ),
    );
    last = i + m[0].length;
  }
  if (last < sql.length) out.push(sql.slice(last));
  return <>{out}</>;
}

function paramsText(p: unknown): string | null {
  if (!p || typeof p !== "object" || Array.isArray(p)) return null;
  const parts = Object.entries(p as Record<string, unknown>).map(([k, v]) => `${k}=${String(v)}`);
  return parts.length ? parts.join("  ·  ") : null;
}

/**
 * SQL receipts: one row per query the checkpoint / investigator really ran, numbered R1..Rn in the
 * order received (the investigator report cites them the same way). Shows the tool/label, ms,
 * rows_read and the SQL verbatim; null values render DASH.
 */
export function Receipts({ receipts }: { receipts: Receipt[] | null | undefined }) {
  if (!receipts?.length)
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-dashed border-line-strong px-4 py-3.5 text-sm text-dim">
        <Database className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
        No SQL receipts yet.
      </div>
    );
  return (
    <div role="list" className="flex flex-col gap-2.5">
      {receipts.map((r, i) => {
        const label = (r.label ?? r.tool) as string | undefined;
        const params = paramsText(r.params);
        return (
          <Reveal
            key={i}
            index={i}
            role="listitem"
            className="group/receipt overflow-hidden rounded-xl border border-line bg-panel shadow-sm transition-[border-color,box-shadow] duration-200 hover:border-line-strong hover:shadow-md"
          >
            <div className="flex min-w-0 items-center gap-2.5 py-2 pl-3.5 pr-2">
                <span className="font-mono text-xs text-dim">R{i + 1}</span>
                {label ? (
                  <span className="truncate font-mono text-[13px] font-medium text-fg" title={String(label)}>
                    {String(label)}
                  </span>
                ) : null}
                {r.mock ? <Badge variant="held">mock</Badge> : null}
                <span className="ml-auto flex shrink-0 items-center gap-1 font-mono text-xs tabular-nums text-muted">
                  <Tooltip content="Query time, as measured">
                    <span className="rounded-md px-1.5 py-0.5 text-fg">{fmtMs(r.ms)}</span>
                  </Tooltip>
                  <span aria-hidden className="text-dim">
                    ·
                  </span>
                  <Tooltip content="rows_read, as reported with the query">
                    <span className="rounded-md px-1.5 py-0.5">{fmtInt(r.rows_read)} rows read</span>
                  </Tooltip>
                  {r.sql ? <CopyButton value={r.sql} label="Copy SQL" className="ml-1" /> : null}
                </span>
              </div>
              <div className="border-t border-line bg-panel-2">
                {params ? (
                  <div className="truncate px-3.5 pt-2.5 font-mono text-xs text-dim" title={params}>
                    {params}
                  </div>
                ) : null}
                <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words px-3.5 py-2.5 font-mono text-xs leading-5 text-muted">
                  {r.sql ? <SqlText sql={r.sql} /> : <Fragment>{DASH}</Fragment>}
                </pre>
              </div>
          </Reveal>
        );
      })}
    </div>
  );
}

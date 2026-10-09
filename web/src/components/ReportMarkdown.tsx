// Loaded lazily (React.lazy) so react-markdown + remark-gfm stay out of the main chunk.
// Renders the investigator's report as a typeset article: Geist 14/1.6 ink text, sans headings,
// Geist Mono for code, hairline tables. The markdown is shown verbatim; nothing is added.
import type { ComponentProps, JSX } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// react-markdown passes the hast `node`; drop it so it never lands on a DOM element.
type P<T extends keyof JSX.IntrinsicElements> = ComponentProps<T> & { node?: unknown };

const article: Components = {
  h1: ({ node: _n, ...p }: P<"h1">) => (
    <h1 className="mb-3 mt-7 text-xl font-semibold leading-snug tracking-[-0.015em] text-fg first:mt-0" {...p} />
  ),
  h2: ({ node: _n, ...p }: P<"h2">) => (
    <h2
      className="mb-1.5 mt-6 text-base font-semibold leading-snug tracking-[-0.01em] text-fg first:mt-0 [&_code]:text-[13px]"
      {...p}
    />
  ),
  h3: ({ node: _n, ...p }: P<"h3">) => (
    <h3 className="mb-1.5 mt-5 text-sm font-semibold leading-snug text-fg first:mt-0" {...p} />
  ),
  h4: ({ node: _n, ...p }: P<"h4">) => <h4 className="mb-1 mt-4 text-sm font-medium text-fg" {...p} />,
  p: ({ node: _n, ...p }: P<"p">) => <p className="my-2.5 text-sm leading-[1.6] text-fg/85" {...p} />,
  ul: ({ node: _n, ...p }: P<"ul">) => (
    <ul className="my-2.5 list-disc space-y-1 pl-5 text-sm leading-[1.6] text-fg/85 marker:text-dim" {...p} />
  ),
  ol: ({ node: _n, ...p }: P<"ol">) => (
    <ol
      className="my-2.5 list-decimal space-y-1 pl-5 text-sm leading-[1.6] text-fg/85 marker:font-mono marker:text-xs marker:text-dim"
      {...p}
    />
  ),
  li: ({ node: _n, ...p }: P<"li">) => <li className="pl-1" {...p} />,
  a: ({ node: _n, ...p }: P<"a">) => (
    <a
      className="font-medium text-brand underline decoration-brand/30 underline-offset-[3px] transition-colors hover:decoration-brand"
      target="_blank"
      rel="noreferrer"
      {...p}
    />
  ),
  strong: ({ node: _n, ...p }: P<"strong">) => <strong className="font-semibold text-fg" {...p} />,
  em: ({ node: _n, ...p }: P<"em">) => <em className="italic text-muted" {...p} />,
  blockquote: ({ node: _n, ...p }: P<"blockquote">) => (
    <blockquote className="my-4 border-l-2 border-line-strong pl-4 text-muted [&_p]:text-muted" {...p} />
  ),
  hr: ({ node: _n, ...p }: P<"hr">) => <hr className="my-6 h-px border-0 bg-line" {...p} />,
  code: ({ node: _n, className, ...p }: P<"code">) => (
    <code
      className={`rounded-[5px] border border-line bg-panel-3/70 px-1 py-px font-mono text-[12.5px] font-normal text-fg [pre_&]:border-0 [pre_&]:bg-transparent [pre_&]:p-0 [pre_&]:text-xs ${className ?? ""}`}
      {...p}
    />
  ),
  pre: ({ node: _n, ...p }: P<"pre">) => (
    <pre
      className="my-4 overflow-x-auto rounded-xl border border-line bg-panel-2 p-3.5 font-mono text-xs leading-5 text-fg/85"
      {...p}
    />
  ),
  table: ({ node: _n, ...p }: P<"table">) => (
    <div className="my-4 overflow-x-auto rounded-xl border border-line bg-panel shadow-sm">
      <table className="w-full border-separate border-spacing-0 text-xs tabular-nums text-fg/85" {...p} />
    </div>
  ),
  th: ({ node: _n, ...p }: P<"th">) => (
    <th
      className="whitespace-nowrap border-b border-line bg-panel-2 px-3 py-2 text-left font-medium text-dim first:pl-3.5 last:pr-3.5"
      {...p}
    />
  ),
  td: ({ node: _n, ...p }: P<"td">) => (
    <td
      className="border-b border-line px-3 py-2 align-top break-words first:pl-3.5 last:pr-3.5 [tr:last-child>&]:border-b-0 [&_code]:text-[11.5px]"
      {...p}
    />
  ),
  tr: ({ node: _n, ...p }: P<"tr">) => <tr className="transition-colors duration-150 hover:bg-panel-3/50" {...p} />,
};

export default function ReportMarkdown({ md, tone = "paper" }: { md: string; tone?: "paper" | "dark" }) {
  if (tone === "dark")
    return (
      <div className="md">
        <Markdown remarkPlugins={[remarkGfm]}>{md}</Markdown>
      </div>
    );
  return (
    <div className="min-w-0 break-words">
      <Markdown remarkPlugins={[remarkGfm]} components={article}>
        {md}
      </Markdown>
    </div>
  );
}

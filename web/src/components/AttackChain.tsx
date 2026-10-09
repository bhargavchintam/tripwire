import { Binary, ChevronRight, FileKey, Hand, Lock, Send } from "lucide-react";
import type { ReactNode } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import type { AgentMode, Incident } from "../lib/types";

const SECRET_RE = /\.env|secret|credential|passw|token|\.pem|id_rsa|\.aws/i;
const ENCODE_RE = /base64|xxd|openssl|gzip|encode|uuencode/i;

/** Which stages of the chain the latest incident's recorded steps reached (display only). */
function stagesFor(inc: Incident | undefined, mode: AgentMode | undefined) {
  const steps = inc?.steps ?? [];
  const read = steps.some((s) => s.action === "read_file" && SECRET_RE.test(s.target));
  const encode = steps.some((s) => s.action === "run_command" && ENCODE_RE.test(s.target));
  const send = steps.find((s) => s.action === "http_post");
  const stopped = steps.find((s) => s.action === "http_post" && s.result === "denied");
  const quarantined = !!inc && (inc.contained_ms != null || mode === "quarantined");
  return { read, encode, send: !!send, stopped, quarantined };
}

function Stage({ on, tone, icon, label, sub }: { on: boolean; tone: "bad" | "held"; icon: ReactNode; label: string; sub?: string }) {
  const onCls = tone === "bad" ? "border-bad bg-bad/15 text-bad" : "border-held bg-held/15 text-held";
  return (
    <div
      className={`flex min-w-[120px] flex-1 items-center gap-2 rounded-lg border-2 px-3 py-2 transition-colors duration-500 [&_svg]:size-5 ${
        on ? onCls : "border-line bg-panel-2 text-dim"
      }`}
    >
      {icon}
      <div className="min-w-0">
        <div className="text-sm font-bold leading-tight">{label}</div>
        <div className="truncate font-mono text-[11px] opacity-80">{on ? (sub ?? "seen") : "not seen"}</div>
      </div>
    </div>
  );
}

export function AttackChain({ incident, mode }: { incident: Incident | undefined; mode: AgentMode | undefined }) {
  const s = stagesFor(incident, mode);
  const chev = <ChevronRight className="hidden size-4 shrink-0 text-dim md:block" />;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Attack chain {incident ? `· ${incident.agent_id} · ${incident.id}` : ""}</CardTitle>
        {!incident && <span className="text-xs text-dim">waiting for an incident</span>}
      </CardHeader>
      <CardContent className="flex flex-col gap-2 md:flex-row md:items-center">
        <Stage on={s.read} tone="bad" icon={<FileKey />} label="Secret read" sub="read_file" />
        {chev}
        <Stage on={s.encode} tone="bad" icon={<Binary />} label="Encode" sub="run_command" />
        {chev}
        <Stage on={s.send} tone="bad" icon={<Send />} label="External send" sub="http_post" />
        {chev}
        <Stage
          on={!!s.stopped}
          tone="held"
          icon={<Hand />}
          label="Held / denied"
          sub={s.stopped ? `denied · ${s.stopped.reason || "—"}` : undefined}
        />
        {chev}
        <Stage
          on={s.quarantined}
          tone="bad"
          icon={<Lock />}
          label="Quarantined"
          sub={incident?.closed_ms ? "contained · since restored" : "agent blocked"}
        />
      </CardContent>
    </Card>
  );
}

// Rule fixtures for semgrep/rules/tripwire-agent-security.yaml (TypeScript rules).
// Synthetic snippets only -- never imported or executed.
import { exec } from "node:child_process";
import * as cp from "node:child_process";

declare const openai: any;

// ===== (a) tripwire-llm-output-to-code-exec-ts =====
export async function runModelCode(): Promise<void> {
  const resp = await openai.chat.completions.create({ model: "m", messages: [] });
  const code = resp.choices[0].message.content;
  // ruleid: tripwire-llm-output-to-code-exec-ts
  eval(code);
}

export async function shellFromModel(): Promise<void> {
  const resp = await openai.chat.completions.create({ model: "m", messages: [] });
  // ruleid: tripwire-llm-output-to-code-exec-ts
  cp.execSync(resp.choices[0].message.content);
}

export async function modelToLabel(): Promise<string> {
  const resp = await openai.chat.completions.create({ model: "m", messages: [] });
  const label = resp.choices[0].message.content === "malicious" ? "malicious" : "benign";
  // ok: tripwire-llm-output-to-code-exec-ts
  exec("echo done");
  return label;
}

// ===== (b) tripwire-unallowlisted-outbound-http-ts =====
export async function request(path: string): Promise<Response> {
  // ruleid: tripwire-unallowlisted-outbound-http-ts
  return fetch(path, { method: "GET" });
}

export async function sendTo(host: string, body: string): Promise<Response> {
  // ruleid: tripwire-unallowlisted-outbound-http-ts
  return fetch(`https://${host}/hook`, { method: "POST", body });
}

export async function relative(id: string): Promise<Response> {
  // ok: tripwire-unallowlisted-outbound-http-ts
  return fetch(`/incidents/${encodeURIComponent(id)}`);
}

export async function literal(): Promise<Response> {
  // ok: tripwire-unallowlisted-outbound-http-ts
  return fetch("/guild/run", { method: "POST" });
}

const ALLOWED = new Set(["hooks.partner.example"]);

export async function allowlisted(url: string): Promise<Response | null> {
  if (ALLOWED.has(new URL(url).hostname)) {
    // ok: tripwire-unallowlisted-outbound-http-ts
    return fetch(url);
  }
  return null;
}

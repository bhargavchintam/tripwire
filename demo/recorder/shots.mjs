// Stills for the submission: the project thumbnail and the README screenshots, taken from the LIVE console.
//
//   cd demo/recorder && node shots.mjs                 (writes docs/img/*.jpg + demo/out/thumbnail*.png)
//
// Runs Act 3 (poisoned_ticket, Hold OFF) for real so the frame shows a genuine trace: deploy-bot
// quarantined by an AkashML verdict, support-bot on watch, the outbreak panel. Retakes (up to 3)
// if the verdict fell back to rule_only, so nothing on screen overstates what happened.
import { chromium } from "playwright";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const OUT = path.join(ROOT, "demo", "out");
const IMG = path.join(ROOT, "docs", "img");
const BASE = process.env.TRIPWIRE_URL || "http://127.0.0.1:8000";
fs.mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const api = async (p, init) => {
  const r = await fetch(BASE + p, { headers: { "content-type": "application/json" }, ...init });
  if (!r.ok) throw new Error(`${init?.method || "GET"} ${p} -> ${r.status}`);
  return r.json();
};
const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body ?? {}) });

async function replay(scenario) {
  const run = await post("/demo/replay", { scenario });
  for (let i = 0; i < 60; i++) {
    if ((await api(`/demo/replay/${run.run_id}`)).status !== "running") break;
    await sleep(500);
  }
  await sleep(4000);
}

// A full reset (what the console's 0 key does) restores the default policy; a plain reset would keep
// the attacker host the previous take's outbreak trace denylisted, and Act 3 would be stopped by policy.
async function acts() {
  for (let attempt = 1; attempt <= 3; attempt++) {
    await post("/demo/reset?full=1");
    await post("/config/hold", { enabled: false });
    await replay("poisoned_ticket");
    const inc = (await api("/incidents")).find((i) => i.agent_id === "deploy-bot" && !i.closed_ms);
    const ok = inc?.verdict?.decision_source === "akashml" && inc.outbreak;
    console.log(`attempt ${attempt}: act 3 ${inc?.verdict?.decision_source ?? "no incident"}, outbreak ${!!inc?.outbreak}`);
    if (ok) return inc;
  }
  throw new Error("act 3 never produced an AkashML verdict with an outbreak trace");
}

const inc = await acts();
const browser = await chromium.launch({ headless: true });
// 3:2 and 16:9 frames, rendered at 2x so the thumbnail stays crisp after the platform resizes it
const shoot = async (w, h, file, prep) => {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: "networkidle" });
  await sleep(2500);
  if (prep) await prep(page);
  await page.screenshot({ path: file, type: file.endsWith(".png") ? "png" : "jpeg", ...(file.endsWith(".jpg") ? { quality: 88 } : {}) });
  await ctx.close();
  console.log(`wrote ${path.relative(ROOT, file)}`);
};
const tab = async (page, name) => {
  await page.getByRole("tab", { name: new RegExp(`^${name}`) }).click();
  await sleep(1500);
};

await shoot(1600, 900, path.join(OUT, "thumbnail-16x9.png"));
await shoot(1500, 1000, path.join(OUT, "thumbnail-3x2.png"));
await shoot(1600, 1000, path.join(IMG, "04-evidence.jpg"), (p) => tab(p, "Evidence"));
await shoot(1600, 1000, path.join(IMG, "05-sponsors.jpg"), (p) => tab(p, "Sponsors"));
await browser.close();

// README images stay small (800x500, like the rest of docs/img)
for (const f of ["04-evidence.jpg", "05-sponsors.jpg"]) {
  execFileSync("sips", ["-z", "500", "800", path.join(IMG, f)], { stdio: "ignore" });
}
console.log(`incident ${inc.id}: ${inc.verdict.decision_source} · ${inc.verdict.model_ids?.join(", ")}`);

// Records the Tripwire demo against the LIVE console (http://127.0.0.1:8000) with Playwright.
//
//   cd demo/recorder && node record.mjs        (needs demo/out/timeline.json from demo/tts.py)
//   python3 demo/cut_clips.py                  (frames + log -> demo/video/clips/clipNN_*.mp4)
//
// One continuous 1920x1080 session on a single page, so live state is never lost to a reload.
// Frames come from Chromium's own screencast (CDP Page.startScreencast, JPEG q90), which is far
// sharper than Playwright's VP8 recordVideo; each frame is saved with its capture timestamp.
// Each segment of demo/narration.json is performed in order and held for at least its narration
// length; every boundary goes to demo/out/record_log.json. Waits that depend on the outside world
// (a human typing APPROVE in Guild, Guild's agent calling back, the copilot's model) are logged as
// "cuts" so cut_clips.py removes the dead air. Acts whose verdict fell back to rule_only are
// retaken so the narration ("an Akash ML model made that call") stays true.
// Every action is a real click or keypress on the real console; nothing on screen is staged.
import { chromium } from "playwright";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const OUT = path.join(ROOT, "demo", "out");
const FRAMES = path.join(OUT, "frames");
const BASE = process.env.TRIPWIRE_URL || "http://127.0.0.1:8000";
const plan = JSON.parse(fs.readFileSync(path.join(ROOT, "demo", "narration.json"), "utf8"));
const timeline = JSON.parse(fs.readFileSync(path.join(OUT, "timeline.json"), "utf8"));
const DUR = Object.fromEntries(timeline.segments.map((s) => [s.id, s.duration]));
const GAP = plan.gap_s;
const END_CARD = 2.5;
const SHORT = { timeout: 2500 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const api = async (p, init) => {
  const r = await fetch(BASE + p, { headers: { "content-type": "application/json" }, ...init });
  if (!r.ok) throw new Error(`${init?.method || "GET"} ${p} -> ${r.status}`);
  return r.json();
};
const notify = (msg) => {
  try {
    execFileSync("osascript", ["-e", `display notification "${msg.replace(/"/g, "'")}" with title "Tripwire recording" sound name "Glass"`]);
  } catch {}
  console.log(`\n>>> ${msg}\n`);
};

fs.rmSync(FRAMES, { recursive: true, force: true });
fs.mkdirSync(FRAMES, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
const page = await context.newPage();
const now = () => Date.now() / 1000; // epoch seconds, same clock as the frame timestamps
const log = { started: new Date().toISOString(), base: BASE, segments: [] };

// ---------------------------------------------------------------- screencast
const frames = []; // [epoch seconds, file]
const cdp = await context.newCDPSession(page);
let pending = Promise.resolve();
cdp.on("Page.screencastFrame", ({ data, metadata, sessionId }) => {
  const file = `f${String(frames.length).padStart(6, "0")}.jpg`;
  frames.push([metadata.timestamp ?? now(), file]);
  pending = pending.then(() => fs.promises.writeFile(path.join(FRAMES, file), Buffer.from(data, "base64")));
  cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
});

const key = (k) => page.keyboard.press(k);
const tab = async (name) => {
  await page.getByRole("tab", { name: new RegExp(`^${name}`) }).click();
  await sleep(700);
};
const palette = async (query) => {
  await key("Meta+K");
  await sleep(400);
  await page.keyboard.type(query, { delay: 40 });
  await sleep(350);
  await key("Enter");
};
const scrollTo = async (text) => {
  await page.getByText(text, { exact: false }).first().scrollIntoViewIfNeeded(SHORT).catch(() => {});
  await sleep(500);
};
const scrollTop = () => page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
const glide = async (locator) => {
  const box = await locator.first().boundingBox(SHORT).catch(() => null);
  if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 25 });
};
const hold = async (on) => {
  if ((await api("/status")).hold_enabled !== on) {
    await key("h");
    await sleep(700);
  }
};
const openIncident = async (agent) =>
  (await api("/incidents")).find((i) => i.agent_id === agent && !i.closed_ms) || null;
const waitFor = async (fn, timeoutMs, everyMs = 400) => {
  const t = Date.now();
  while (Date.now() - t < timeoutMs) {
    const v = await fn().catch(() => null);
    if (v) return v;
    await sleep(everyMs);
  }
  return null;
};
const SCREENCAST = { format: "jpeg", quality: 90, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 };
// The log (segments + frame list) is rewritten after every segment and on failure, so a take that
// dies late can still be inspected; cut_clips.py needs an accepted take of every segment.
function saveLog() {
  log.frames = frames;
  fs.writeFileSync(path.join(OUT, "record_log.json"), JSON.stringify(log, null, 1));
}
const sheetSection = (label) =>
  page.getByRole("navigation", { name: "Incident sections" }).getByRole("button", { name: new RegExp(`^${label}`) });

// Run one segment: act() performs it and may push [from, to] cut windows (epoch s); it returns
// false to request a retake (the console is reset with 0 first).
async function segment(id, act, { extra = 0, retakes = 2 } = {}) {
  for (let attempt = 0; attempt <= retakes; attempt++) {
    const rec = { id, attempt, start: now(), cuts: [] };
    const ok = await act(rec);
    const cutTotal = rec.cuts.reduce((a, [s, e]) => a + (e - s), 0);
    const need = DUR[id] + GAP + extra + 0.6;
    const shown = now() - rec.start - cutTotal;
    if (shown < need) await sleep((need - shown) * 1000);
    rec.end = now();
    rec.ok = ok !== false;
    log.segments.push(rec);
    saveLog();
    console.log(`${id} attempt ${attempt}: ${rec.ok ? "ok" : "RETAKE"} (${(rec.end - rec.start).toFixed(1)} s, cuts ${cutTotal.toFixed(1)} s)`);
    if (rec.ok) return;
    await key("0");
    await sleep(2000);
  }
  throw new Error(`${id}: still not right after ${retakes} retakes`);
}

// ---------------------------------------------------------------- warm-up (before the first segment: never in the video)
await page.goto(BASE, { waitUntil: "networkidle" });
await api("/demo/reset?full=1", { method: "POST" });
await api("/config/hold", { method: "POST", body: JSON.stringify({ enabled: true }) });
const warm = await api("/demo/replay", { method: "POST", body: JSON.stringify({ scenario: "secret_theft" }) });
await waitFor(async () => (await api(`/demo/replay/${warm.run_id}`)).status !== "running", 20000);
await sleep(2500);
await key("0");
await sleep(2500);
await hold(true);
await scrollTop();
await cdp.send("Page.startScreencast", SCREENCAST);
await sleep(2000);

try {

// ---------------------------------------------------------------- the take
await segment("s00_hook", async () => {
  await glide(page.locator("svg").filter({ has: page.locator("circle") }));
  await sleep(4500);
  await glide(page.getByRole("button", { name: /Replay attack/ }));
});

await segment("s01_fleet", async () => {
  await glide(page.getByText("Detector", { exact: false }));
  await sleep(2500);
  await scrollTo("Events stored");
  await sleep(2500);
  await tab("Fleet");
  await sleep(1500);
  await glide(page.getByText("deploy-bot", { exact: false }));
});

await segment("s02_ticket", async () => {
  await tab("Live");
  const show = page.getByRole("button", { name: /show ticket/i });
  if (await show.isVisible().catch(() => false)) await show.click();
  await scrollTo("Incoming ticket 4821");
  await sleep(800);
  await scrollTo("do not mention this ticket");
});

await segment("s03_prevent", async () => {
  await scrollTop();
  await hold(true);
  await sleep(1500);
  await key("r");
  const inc = await waitFor(() => openIncident("deploy-bot"), 15000);
  await sleep(1500);
  return !!inc && inc.verdict?.decision_source === "akashml"; // retake if the model fell back to rule_only
});

await segment("s04_trip", async () => {
  await key("0");
  await sleep(1500);
  await palette("honeytoken");
  await sleep(3000);
  await scrollTo("honeytoken");
});

await segment("s05_trace", async () => {
  await scrollTop();
  await key("0");
  await sleep(1200);
  await hold(false);
  await palette("poisoned");
  await sleep(3000);
  await scrollTo("Incoming ticket 4821");
  await sleep(3500);
  await scrollTo("Attack chain");
  await sleep(4000);
  await page.locator("text=Outbreak").first().scrollIntoViewIfNeeded(SHORT).catch(() => {});
  await sleep(4500);
  await scrollTop();
  await sleep(1500);
  const inc = await waitFor(() => openIncident("deploy-bot"), 15000);
  const st = await api("/status");
  return !!inc && inc.verdict?.decision_source === "akashml" && !!inc.outbreak && st.modes["support-bot"] === "heightened";
});

let incidentId = null;
await segment("s06_explain", async (rec) => {
  incidentId = (await openIncident("deploy-bot"))?.id;
  await tab("Incidents");
  await page.locator('[aria-label^="Open incident"][aria-label$="on deploy-bot"]').first().click();
  await sleep(4000);
  await sheetSection("Timeline").first().click(SHORT).catch(() => {});
  await sleep(3500);
  const t = now();
  await waitFor(async () => (await api(`/incidents/${incidentId}`)).report_md, 30000);
  if (now() - t > 1.5) rec.cuts.push([t + 0.5, now()]);
  await sheetSection("Report").first().click(SHORT).catch(() => {});
});

await segment("s07_cure", async (rec) => {
  await sheetSection("Cure").first().click(SHORT).catch(() => {});
  await sleep(800);
  await page.getByRole("button", { name: /Prove guardrail/ }).click();
  const ask = page.getByRole("button", { name: /Ask a human in Guild/ });
  const t0 = now();
  await ask.waitFor({ state: "visible", timeout: 30000 });
  if (now() - t0 > 4.5) rec.cuts.push([t0 + 3.5, now() - 0.5]); // keep ~3.5 s of the proof landing
  await sleep(2500); // the four gates + backtest card on screen
  await ask.click();
  const link = await waitFor(async () => page.locator('a[href*="app.guild.ai/sessions/"]').first().getAttribute("href", SHORT), 30000);
  if (!link) throw new Error("no Guild session link appeared");
  const sid = link.split("/").pop();
  await sleep(2000);
  // The human step. Frame capture pauses while we wait (the wait is cut anyway); the Responder must
  // have posted its question before a reply counts, so the link opens only after that.
  const cutFrom = now();
  await cdp.send("Page.stopScreencast");
  await sleep(9000); // the Responder posts its question ~7 s after the session starts
  // The reply that reliably reaches the paused Responder is the human's own Guild CLI (logged in as
  // them, recorded as EntPersonalUser); in our runs the web box did not deliver it. We only prepare the
  // command (clipboard + notification); the human runs it, in their own terminal.
  const cmd = `guild session send ${sid} --message "APPROVE"`;
  let opened = false;
  const ask_human = () => {
    if (!opened) {
      execFileSync("open", [link]); // once, so the case can be read; later reminders only notify
      try { execFileSync("pbcopy", { input: cmd }); } catch {}
    }
    opened = true;
    notify(`Approve in your own terminal (copied to clipboard): ${cmd}   case: ${link}`);
  };
  ask_human();
  let lastAsk = Date.now();
  let decided = null;
  const deadline = Date.now() + 60 * 60 * 1000;
  // Another client resetting the console mid-take deletes this incident; then "Approve & restore"
  // would 404 on camera. Stop at once instead (seen in take 3: a full reset at 00:11 UTC).
  const incidentAlive = async () => {
    const r = await fetch(`${BASE}/incidents/${incidentId}`).catch(() => null);
    return !r || r.status !== 404;
  };
  while (!decided && Date.now() < deadline) {
    if (!(await incidentAlive())) throw new Error(`incident ${incidentId} was removed during the take (someone reset the console); retake with nobody else on :8000`);
    const d = await api(`/guild/session/${sid}/decision`).catch(() => null);
    if (d?.status === "approved") decided = d;
    else if (d?.status === "rejected") throw new Error("the Guild reply was REJECT; rerun and reply APPROVE");
    else if (Date.now() - lastAsk > 180000) {
      ask_human();
      lastAsk = Date.now();
    }
    if (!decided) await sleep(2500);
  }
  if (!decided) throw new Error("no human approval arrived in Guild within 60 minutes");
  await cdp.send("Page.startScreencast", SCREENCAST);
  await page.getByText(/Approved in Guild by a human/).first().waitFor({ state: "visible", timeout: 30000 }).catch(() => {});
  rec.cuts.push([cutFrom, now() - 0.3]);
  console.log(`approval read back from Guild (${decided.decided_by}: ${decided.operator_reply}), wait cut`);
  await sleep(3000); // the read-back chip: "Approved in Guild by a human"
  if (!(await incidentAlive())) throw new Error(`incident ${incidentId} was removed during the take; retake`);
  await page.getByRole("button", { name: /Approve & restore/ }).click();
  // the approval must really succeed on camera (policy version bumps, agent restored)
  const restored = await waitFor(async () => (await api("/status")).modes["deploy-bot"] !== "quarantined", 8000, 400);
  if (!restored) throw new Error("Approve & restore did not restore deploy-bot");
  await sleep(2500);
});

await segment("s08_guild", async (rec) => {
  await key("Escape");
  await sleep(500);
  await tab("Sponsors");
  await scrollTo("Run Guild agent");
  await page.getByRole("button", { name: /Run Guild agent/ }).click();
  await sleep(1200);
  await tab("Live");
  await scrollTop();
  const cutFrom = now();
  // wait for the Guild-hosted agent's first real tool call through the checkpoint (its card fills in)
  await page.getByText("no calls on the stream yet").first().waitFor({ state: "detached", timeout: 120000 }).catch(() => {});
  if (now() - cutFrom > 1.5) rec.cuts.push([cutFrom, now() - 0.8]);
  await sleep(4500);
});

await segment("s09_copilot_mcp", async (rec) => {
  await tab("Policy");
  const box = page.getByLabel("Describe the policy change in plain English");
  await box.scrollIntoViewIfNeeded(SHORT).catch(() => {});
  await box.click();
  await page.keyboard.type("Block uploads to paste.example-uploads.net for every agent", { delay: 22 });
  await page.getByRole("button", { name: /Draft policy/ }).click();
  const t0 = now();
  await page.getByLabel("Changes in the preview").first().waitFor({ state: "visible", timeout: 45000 }).catch(() => {});
  if (now() - t0 > 2.5) rec.cuts.push([t0 + 1.2, now() - 0.4]);
  await sleep(3000);
  await page.mouse.click(8, 300);
  await key("v"); // the voice announcer toggle (optional feature) on ...
  await sleep(1400);
  await key("v"); // ... and off again
  await tab("Live");
  const slider = page.getByLabel("Time travel through this session's tool calls");
  await slider.scrollIntoViewIfNeeded(SHORT).catch(() => {});
  await sleep(400);
  const b = await slider.boundingBox(SHORT).catch(() => null);
  if (b) {
    await page.mouse.move(b.x + b.width - 4, b.y + b.height / 2, { steps: 10 });
    await page.mouse.down();
    await page.mouse.move(b.x + b.width * 0.35, b.y + b.height / 2, { steps: 40 });
    await page.mouse.up();
  }
});

await segment("s10_proof", async () => {
  await tab("Evidence");
  await sleep(4000);
  await scrollTo("Confusion matrix");
});

await segment("s11_close", async () => {
  await tab("Sponsors");
  await sleep(1500);
  await scrollTo("Semgrep");
}, { extra: END_CARD });

log.finished = new Date().toISOString();
} catch (err) {
  log.error = String(err?.stack || err);
  console.error(err);
  process.exitCode = 1;
} finally {
  await cdp.send("Page.stopScreencast").catch(() => {});
  await pending;
  saveLog();
}
await context.close();
await browser.close();
// leave the live console clean for the next person
await api("/demo/reset?full=1", { method: "POST" });
await api("/config/hold", { method: "POST", body: JSON.stringify({ enabled: true }) });
console.log(`${frames.length} frames in ${path.relative(ROOT, FRAMES)}; log: demo/out/record_log.json`);

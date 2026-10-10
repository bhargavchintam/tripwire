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
import { execFileSync, spawn } from "node:child_process";
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
    const shownNow = () => now() - rec.start - rec.cuts.reduce((a, [s, e]) => a + (e - s), 0);
    rec.at = async (t) => {
      const wait = t - shownNow();
      if (wait > 0) await sleep(wait * 1000);
    };
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


// ---------------------------------------------------------------- choreography helpers
// Scroll an element to the middle of the viewport: key content stays clear of the video's
// lower-third caption (y ≈ 918-1016 in the 1080p frame).
const center = async (locator) => {
  await locator.first().evaluate((el) => el.scrollIntoView({ block: "center", behavior: "smooth" }), SHORT).catch(() => {});
  await sleep(650);
};
const centerText = (text) => center(page.getByText(text, { exact: false }));
const park = () => page.mouse.move(1880, 610, { steps: 8 }); // empty right margin: no hover tooltips
// The orbit SVG is the largest SVG on the Live tab; its centre is the checkpoint shield.
const orbitCentre = () =>
  page.evaluate(() => {
    let best = null;
    for (const s of document.querySelectorAll("svg")) {
      const r = s.getBoundingClientRect();
      if (!best || r.width * r.height > best.a) best = { a: r.width * r.height, x: r.x + r.width / 2, y: r.y + r.height / 2 };
    }
    return best;
  });
// First event of /stream = the checkpoint's full-state snapshot (recent events, incidents, status).
async function snapshot() {
  const ctrl = new AbortController();
  const r = await fetch(`${BASE}/stream`, { headers: { accept: "text/event-stream" }, signal: ctrl.signal });
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return null;
      buf += dec.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      const at = buf.indexOf("event: snapshot");
      const end = at >= 0 ? buf.indexOf("\n\n", at) : -1;
      if (end > 0) {
        const data = buf.slice(at, end).split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
        return JSON.parse(data).data;
      }
    }
  } finally {
    ctrl.abort();
  }
}
// A real MCP client: Tripwire's own MCP server (tripwire/mcp_server.py) over stdio, as Claude Code
// would run it, so the s09 line about MCP agents is shown happening, not just said.
function startMcp() {
  const proc = spawn("uv", ["run", "--quiet", "python", "-m", "tripwire.mcp_server"], {
    cwd: ROOT,
    env: { ...process.env, TRIPWIRE_MCP_AGENT: "claude-code" },
  });
  let buf = "";
  let id = 0;
  const waiting = new Map();
  proc.stdout.on("data", (d) => {
    buf += d;
    for (let i; (i = buf.indexOf("\n")) >= 0; ) {
      const line = buf.slice(0, i);
      buf = buf.slice(i + 1);
      if (!line.trim()) continue;
      try {
        const m = JSON.parse(line);
        waiting.get(m.id)?.(m);
      } catch {}
    }
  });
  proc.stderr.on("data", () => {});
  const rpc = (method, params) =>
    new Promise((res) => {
      const i = ++id;
      waiting.set(i, res);
      proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: i, method, params }) + "\n");
      setTimeout(() => res(null), 15000);
    });
  const ready = rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "tripwire-demo", version: "1" } }).then(
    (r) => {
      proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");
      return !!r?.result;
    },
  );
  return {
    ready,
    call: async (action, target) => (await rpc("tools/call", { name: "tripwire_tool", arguments: { action, target } }))?.result?.structuredContent ?? null,
    close: () => proc.kill(),
  };
}

// ---------------------------------------------------------------- warm-up (before the first segment: never in the video)
await page.goto(BASE, { waitUntil: "networkidle" });
await api("/demo/reset?full=1", { method: "POST" });
await api("/config/hold", { method: "POST", body: JSON.stringify({ enabled: true }) });
const warm = await api("/demo/replay", { method: "POST", body: JSON.stringify({ scenario: "secret_theft" }) });
await waitFor(async () => (await api(`/demo/replay/${warm.run_id}`)).status !== "running", 20000);
await sleep(2500);
await api("/demo/reset?full=1", { method: "POST" });
await api("/config/hold", { method: "POST", body: JSON.stringify({ enabled: true }) });
await page.reload({ waitUntil: "load" }); // remount: the orbit's "calls since opened" starts at 0
await sleep(3000);
const mcp = startMcp();
if (!(await mcp.ready)) console.log("WARNING: MCP server did not initialise; s09 will not show MCP calls");
const oc = await orbitCentre();
if (oc) await page.mouse.move(oc.x, oc.y);
await cdp.send("Page.startScreencast", SCREENCAST);
await sleep(2000);

let incidentId = null;
try {

// ---------------------------------------------------------------- the take
// Times passed to at() are seconds into the segment's narration (measured phrase boundaries of
// demo/out/audio/<id>.mp3), so each action lands on the words that describe it.
await segment("s00_hook", async ({ at }) => {
  await at(3.2); // "And one poisoned support ticket..."
  await glide(page.getByText("Incoming ticket 4821", { exact: false }));
  await at(5.5); // "...can turn an agent into an attacker."
  await glide(page.getByText("deploy-bot", { exact: false }));
  await at(8.1); // "This is Tripwire, the immune system for AI agent fleets."
  await glide(page.getByText("The immune system for AI-agent fleets", { exact: false }));
});

await segment("s01_fleet", async ({ at }) => {
  const c = await orbitCentre(); // "Every tool call passes one checkpoint..."
  if (c) await page.mouse.move(c.x, c.y, { steps: 20 });
  await at(2.4); // "...thirty million synthetic events"
  await glide(page.getByText("Events stored", { exact: false }));
  await at(6.6); // "...across more than forty agents"
  await park();
  await tab("Fleet");
  await at(9.2); // "The detector queries it every second... you can see each pass land."
  await tab("Live");
  await glide(page.getByText(/^Detector/));
});

await segment("s02_ticket", async ({ at }) => {
  await park();
  const show = page.getByRole("button", { name: /show ticket/i });
  if (await show.isVisible().catch(() => false)) await show.click();
  await centerText("Incoming ticket 4821"); // "Here's the ticket behind the attack we recorded."
  await at(2.9); // "Hidden inside is an instruction: read the secrets, encode them, and ship them out."
  await centerText("do not mention this ticket");
  await glide(page.getByText("do not mention this ticket", { exact: false }));
});

await segment("s03_prevent", async ({ at }) => {
  await scrollTop();
  await hold(true);
  await glide(page.getByText(/^Hold/)); // "Hold mode is on."
  await at(1.6); // "I replay the attack."
  await key("r");
  await at(4.0); // the table shows each step; the send is held, then denied (hold_model)
  await park();
  await centerText("Live tool calls");
  const inc = await waitFor(() => openIncident("deploy-bot"), 15000);
  await at(12.8); // "...and the agent is quarantined."
  await scrollTop();
  return !!inc && inc.verdict?.decision_source === "akashml"; // retake if the model fell back to rule_only
}, { retakes: 3 });

await segment("s04_trip", async ({ at }) => {
  await key("0");
  await at(0.7); // "Variant two: the stolen file holds decoy credentials."
  await palette("honeytoken");
  await at(5.6); // after "The honeytoken trips instantly," -> the Denied · honeytoken row
  await centerText("Live tool calls");
});

await segment("s05_trace", async ({ at }) => {
  await scrollTop();
  await key("0");
  await sleep(700);
  await hold(false); // "Hold mode is off,"
  await at(1.6);
  await palette("poisoned"); // "...and two agents read that ticket."
  await at(4.7); // "Our agents tag every action after that read with where it came from,"
  await centerText("Live tool calls");
  await at(8.6); // "...and the attack chain lights up step by step."
  await centerText("Attack chain");
  await at(11.5); // "The detector spots deploy bot in about a second."
  await scrollTop();
  await at(14.4); // "Then Tripwire traces patient zero: ... blocked fleet-wide."
  await centerText("Outbreak traced");
  await at(22.0); // "So when support bot tries to send data out... it's held, and denied."
  await scrollTop();
  const inc = await waitFor(() => openIncident("deploy-bot"), 10000);
  const st = await api("/status");
  const snap = await snapshot().catch(() => null);
  const supportDenied = (snap?.recent_events ?? []).some(
    (e) => e.agent_id === "support-bot" && /partner-sync/.test(e.target ?? "") && e.result === "denied",
  );
  console.log(`s05 check: verdict ${inc?.verdict?.decision_source}, outbreak ${!!inc?.outbreak}, support-bot ${st.modes["support-bot"]}, partner-sync denied ${supportDenied}`);
  return !!inc && inc.verdict?.decision_source === "akashml" && !!inc.outbreak && st.modes["support-bot"] === "heightened" && supportDenied;
}, { retakes: 3 });

await segment("s06_explain", async (rec) => {
  incidentId = (await openIncident("deploy-bot"))?.id;
  await tab("Incidents"); // "Open the incident."
  await page.locator('[aria-label^="Open incident"][aria-label$="on deploy-bot"]').first().click();
  await park();
  await rec.at(5.5); // "And an investigator model wrote this report itself,"
  const t = now();
  await waitFor(async () => (await api(`/incidents/${incidentId}`)).report_md, 30000);
  if (now() - t > 1.5) rec.cuts.push([t + 0.3, now()]);
  await sheetSection("Report").first().click(SHORT).catch(() => {});
  await rec.at(9.0); // "...with every query behind it listed as a receipt."
  await sheetSection("Receipts").first().click(SHORT).catch(() => {});
});

await segment("s07_cure", async (rec) => {
  const { at } = rec;
  await sheetSection("Cure").first().click(SHORT).catch(() => {}); // "Before the agent comes back,"
  await at(1.0);
  await glide(page.getByRole("button", { name: /Prove guardrail/ }));
  await at(2.4); // "Tripwire proves the cure."
  await page.getByRole("button", { name: /Prove guardrail/ }).click();
  const ask = page.getByRole("button", { name: /Ask a human in Guild/ });
  const t0 = now();
  await ask.waitFor({ state: "visible", timeout: 30000 });
  if (now() - t0 > 4) rec.cuts.push([t0 + 2.5, now() - 0.5]); // a slow proof: keep its first 2.5 s
  await park();
  await at(8.4); // "...and the rule is backtested over thirty million events in under a second."
  await centerText("Backtest of the candidate");
  await at(12.4); // "Then a human approves it in Guild..."
  await center(ask);
  await ask.click();
  const link = await waitFor(async () => page.locator('a[href*="app.guild.ai/sessions/"]').first().getAttribute("href", SHORT), 30000);
  if (!link) throw new Error("no Guild session link appeared");
  const sid = link.split("/").pop();
  await sleep(500);
  // The human step. Frame capture pauses while we wait (the wait is cut). The reply that reliably
  // reaches the paused Responder is the human's own Guild CLI (logged in as them, recorded as
  // EntPersonalUser); in our runs the web box did not deliver it. We only prepare the command
  // (clipboard + notification); the human runs it, in their own terminal.
  const cutFrom = now();
  await cdp.send("Page.stopScreencast");
  await sleep(9000); // the Responder posts its question ~7 s after the session starts
  const cmd = `guild session send ${sid} --message "APPROVE"`;
  let opened = false;
  const askHuman = () => {
    if (!opened) {
      execFileSync("open", [link]); // once, so the case can be read; later reminders only notify
      try {
        execFileSync("pbcopy", { input: cmd });
      } catch {}
    }
    opened = true;
    notify(`Approve in your own terminal (copied to clipboard): ${cmd}   case: ${link}`);
  };
  // Another client resetting the console mid-take deletes this incident; then "Approve & restore"
  // would 404 on camera. Stop at once instead (seen in take 3: a full reset at 00:11 UTC).
  const incidentAlive = async () => {
    const r = await fetch(`${BASE}/incidents/${incidentId}`).catch(() => null);
    return !r || r.status !== 404;
  };
  askHuman();
  let lastAsk = Date.now();
  let decided = null;
  const deadline = Date.now() + 60 * 60 * 1000;
  while (!decided && Date.now() < deadline) {
    if (!(await incidentAlive())) throw new Error(`incident ${incidentId} was removed during the take (someone reset the console); retake with nobody else on :8000`);
    const d = await api(`/guild/session/${sid}/decision`).catch(() => null);
    if (d?.status === "approved") decided = d;
    else if (d?.status === "rejected") throw new Error("the Guild reply was REJECT; rerun and reply APPROVE");
    else if (Date.now() - lastAsk > 180000) {
      askHuman();
      lastAsk = Date.now();
    }
    if (!decided) await sleep(2500);
  }
  if (!decided) throw new Error("no human approval arrived in Guild within 60 minutes");
  await page.getByText(/Approved in Guild by a human/).first().waitFor({ state: "visible", timeout: 30000 }).catch(() => {});
  await centerText("Approved in Guild by a human");
  await cdp.send("Page.startScreencast", SCREENCAST);
  await sleep(300);
  rec.cuts.push([cutFrom, now() - 0.2]);
  console.log(`approval read back from Guild (${decided.decided_by}: ${decided.operator_reply}), wait cut`);
  await at(17.2); // "...and one click restores the agent."
  if (!(await incidentAlive())) throw new Error(`incident ${incidentId} was removed during the take; retake`);
  await page.getByRole("button", { name: /Approve & restore/ }).click();
  const restored = await waitFor(async () => (await api("/status")).modes["deploy-bot"] !== "quarantined", 8000, 400);
  if (!restored) throw new Error("Approve & restore did not restore deploy-bot");
});

await segment("s08_guild", async (rec) => {
  await key("Escape");
  await tab("Sponsors"); // "Agents hosted on Guild can be governed the same way."
  await center(page.getByRole("button", { name: /Run Guild agent/ }));
  await glide(page.getByRole("button", { name: /Run Guild agent/ }));
  await rec.at(1.6);
  await page.getByRole("button", { name: /Run Guild agent/ }).click();
  await rec.at(2.4);
  await park();
  await tab("Live");
  await centerText("Live tool calls");
  // "This Guild agent's tool calls flow through the very same checkpoint." Cut the wait until two
  // of its calls have arrived, then keep real time so the next one lands on camera.
  const cutFrom = now();
  await waitFor(async () => (await page.locator("tr", { hasText: "guild:deploy-bot" }).count()) >= 2, 120000, 500);
  if (now() - cutFrom > 1.5) rec.cuts.push([cutFrom, now() - 0.6]);
});

await segment("s09_copilot_mcp", async (rec) => {
  const { at } = rec;
  await tab("Policy"); // "Need a new rule? Just describe it."
  const box = page.getByLabel("Describe the policy change in plain English");
  await center(box);
  await box.click();
  await page.keyboard.type("Block uploads to paste.example-uploads.net for every agent", { delay: 14 });
  await page.getByRole("button", { name: /Draft policy/ }).click(); // "The copilot drafts a validated preview,"
  const t0 = now();
  await page.getByLabel("Changes in the preview").first().waitFor({ state: "visible", timeout: 45000 }).catch(() => {});
  if (now() - t0 > 1.6) rec.cuts.push([t0 + 0.8, now() - 0.3]);
  await center(page.getByLabel("Changes in the preview")); // "...and never applies it on its own."
  await park();
  await at(5.0); // the spoken-alerts toggle (optional feature): bring the header pill into view first
  await scrollTop();
  await glide(page.getByLabel("Voice announcer"));
  await key("v"); // ... on ...
  await at(6.3);
  await key("v"); // ... and off again
  await at(6.8); // "And our MCP server lets an MCP agent, like Claude Code,"
  await tab("Live");
  await centerText("Live tool calls");
  await at(8.0);
  const r1 = await mcp.call("read_file", "README.md");
  await at(9.6); // "...ask Tripwire before it acts."
  const r2 = await mcp.call("assume_role", "arn:aws:iam::123456789012:role/prod-admin");
  console.log(`s09 MCP: read_file -> ${r1?.result}, assume_role -> ${r2?.result} ${r2?.reason ?? ""}`);
});

await segment("s10_proof", async ({ at }) => {
  await tab("Evidence"); // "Every number here is measured,"
  await at(1.7); // "...with its receipt."
  await glide(page.getByText("Precision", { exact: true }));
  await at(3.2); // "On sixty development cases, Tripwire caught all thirty attacks with zero false positives."
  await center(page.getByText("True positive", { exact: true }));
  await glide(page.getByText("True positive", { exact: true }));
  await at(6.6);
  await glide(page.getByText("False positive", { exact: true }));
  await at(9.8); // "To be clear, that's our development set... not a held out test."
  await glide(page.getByText(/not held-out/));
});

await segment("s11_close", async ({ at }) => {
  await tab("Sponsors"); // "Semgrep scanned our own AI written code and found a real prompt injection flaw."
  await centerText("Our own code, scanned");
  await at(2.6); // "We fixed it."
  await glide(page.getByText("open true positives", { exact: false }));
  await at(6.2); // "Tripwire: prevent, trip, trace, and cure, with proof." -> rewind the session's real calls
  await park();
  await tab("Live");
  const slider = page.getByLabel("Time travel through this session's tool calls");
  await center(slider);
  const b = await slider.boundingBox(SHORT).catch(() => null);
  if (b) {
    await page.mouse.move(b.x + b.width - 3, b.y + b.height / 2, { steps: 8 });
    await page.mouse.down();
    await page.mouse.move(b.x + b.width * 0.3, b.y + b.height / 2, { steps: 90 });
    await page.mouse.up();
  }
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
  mcp.close();
}
await context.close();
await browser.close();
// leave the live console clean for the next person
await api("/demo/reset?full=1", { method: "POST" });
await api("/config/hold", { method: "POST", body: JSON.stringify({ enabled: true }) });
console.log(`${frames.length} frames in ${path.relative(ROOT, FRAMES)}; log: demo/out/record_log.json`);

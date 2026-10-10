# Demo video: run sheet, narration and pipeline

The submitted video is **2:49.0** (hard limit 3:00): 12 narrated segments over the real console, then a 2.5 s end card.
The narration is ElevenLabs **Eleven v4** with expressive audio tags, read by **Andrew (Clear & Trustworthy)**.
Every frame comes from the live console at http://localhost:8000; no numbers were staged.
The source of truth for every line is [`demo/narration.json`](../demo/narration.json), and the measured segment timings are in `demo/out/timeline.json`.

Two ways to make the video:
- **Automated (how the submitted cut was made).** Playwright drives the live console and the clips are cut to the narration (see the [pipeline](#pipeline)). A human still approves in Guild.
- **By hand.** One person drives the console from the run sheet below while the narration track plays, or reads the lines live. Each segment's window is long enough for its actions.

---

## Before every take (about 1 minute)

1. Check the stack is up. It runs in the background on Bindu's laptop and logs to `var/run/`. Never restart `cloudflared` and never run `make dev` during the event.
   ```bash
   curl -s localhost:8000/health
   ```
   Expect `"clickhouse":true` and `"classify":"real"`.
2. In the console, press **`0`**. That is a full reset: default policy, cleared timing samples, every agent green. **Press `0` before every Act 3 take**, too. Otherwise the previous take's outbreak trace has left the attacker host on the fleet denylist, deploy-bot's send is stopped as a plain `policy` block, and there is no detection beat to narrate.
3. **Warm-up** (first take only): with Hold ON press `R`, wait for the red card, then press `0` again. This warms AkashML and the ClickHouse caches.
4. **Leave presenter mode (`P`) off while recording.** Presenter mode trims the Live tab, and Act 3 narrates the tool-call table and incident feed. Close other tabs and notifications.
5. Never run the eval or a bulk replay right before a take; the detector needs about 90 s to work through it.
6. **Honest-label rule.** About 1 in 5 Llama-3.3-70B calls take longer than the 2.5 s budget. When that happens the badge says `rule_only`, and the narration "an Akash ML model made that call" would be false. So if the Act 1 or Act 3 incident shows `rule_only`, press `0` and redo the act. The recorder does this automatically.

---

## Run sheet

Times are measured narration windows in the final cut. Lines are spoken exactly as written; the bracketed tags are ElevenLabs v4 audio tags and are not read aloud.

| # | Window | Act | Do on screen | Narration | Caption |
|---|---|---|---|---|---|
| 0 | 0:00.0–0:12.0 | Tripwire | Live tab after a full reset, **Hold ON**, the fleet orbit moving. Point at the *Incoming ticket 4821* card, then deploy-bot, then the tagline | Your AI agents hold the keys to production. And one poisoned support ticket... *[whispers]* can turn an agent into an attacker. *[excited]* This is Tripwire, the immune system for AI agent fleets. | Every agent tool call → one checkpoint |
| 1 | 0:12.3–0:26.4 | Fleet | Pointer on the checkpoint shield, then the **Events stored** tile (≈30,0xx,xxx · incl. synthetic). **Fleet** tab heatmap (42 agents × 72 h). Back to **Live** and hover the **Detector · last pass … ms** pill: its pass number ticks every second | Every tool call passes one checkpoint and lands in ClickHouse Cloud, on top of about thirty million synthetic events across more than forty agents. The detector queries it every second... you can see each pass land. | ~30M events (mostly synthetic) · 40+ agents · ClickHouse Cloud |
| 2 | 0:26.7–0:35.1 | Patient zero | Scroll to **Incoming ticket 4821** (click *Show ticket* if collapsed) and the highlighted injected instruction | *[curious]* Here's the ticket behind the attack we recorded. Hidden inside is an instruction: read the secrets, encode them, and ship them out. | Recorded demo ticket (synthetic) |
| 3 | 0:35.5–0:51.1 | 1 · Prevent | **Hold ON**, press **`R`**. Scroll to **Live tool calls**: the `http_post` row lands *Denied · hold_model* and the incident feed shows the *AkashML model* chip and latency. Back to the top: deploy-bot **Quarantined**, *Hold decision* ≈2 s. Retake if the verdict says `rule_only` | Hold mode is on. I replay the attack. deploy bot reads the env file, encodes the secrets, and tries to send them. *[excited]* Held, and denied, before it ever runs! An Akash ML model made that call in about two seconds, and the agent is quarantined. | Held before it runs · AkashML decides |
| 4 | 0:51.5–0:59.8 | 2 · Trip | Press **`0`**, then **⌘K → Replay honeytoken_exfil**. The send is denied at once; scroll to **Live tool calls**: *Denied · honeytoken* | Variant two: the stolen file holds decoy credentials. The honeytoken trips instantly, with no model call at all. | Honeytoken · no model call |
| 5 | 1:00.1–1:27.1 | 3 · Trace | Press **`0`**, then **`H`** so **Hold is OFF**. **⌘K → Replay poisoned_ticket (Act 3 · trace)**. **Live tool calls** (the *from ticket:4821* chips), then the **Attack chain** lighting, back to the top (deploy-bot quarantined, *Time to detect* ≈1 s), the **Outbreak traced** panel (patient zero `ticket:4821`, support-bot exposed, `drop.example.net` denylisted), and the top again: support-bot **Heightened** with its `partner-sync` send **DENIED**. Retake unless that send was denied and the verdict is `akashml` | Now the hard case. Hold mode is off, and two agents read that ticket. Our agents tag every action after that read with where it came from, and the attack chain lights up step by step. *[excited]* The detector spots deploy bot in about a second. Then Tripwire traces patient zero: support bot read the same ticket, so it goes on heightened watch, and the attacker's domain is blocked fleet-wide. So when support bot tries to send data out... it's held, and denied. | Detector · trace · exposed agent held |
| 6 | 1:27.5–1:39.5 | Explain | **Incidents** tab → open deploy-bot's incident: the **Verdict** block (AkashML badge, model id, latency, tokens), then **Report**, then **Receipts** (each query with its ms and rows read). The report lands about 20 s after the block (13–20 s in rehearsals): cut the wait if needed | Open the incident. The verdict shows its model, its latency, and an honest label. And an investigator model wrote this report itself, with every query behind it listed as a receipt. | Report written by an AI investigator · query receipts |
| 7 | 1:39.8–1:59.4 | 4 · Cure | In the sheet: **Cure** → **Prove guardrail**: four gates pass; scroll to *Backtest of the candidate* (~30M events in under a second). **Ask a human in Guild** → *(cut)* the human reads the case in Guild and approves from their own terminal with `guild session send <session-id> --message "APPROVE"` (in our runs the web reply box did not reach the paused Responder) → the read-back chip says **Approved in Guild by a human** → **Approve & restore** (deploy-bot back to Active) | Before the agent comes back, Tripwire proves the cure. The attack is replayed in a sandbox and refused, normal work still passes, and the rule is backtested over thirty million events in under a second. Then a human approves it in Guild... Tripwire reads that approval back, and one click restores the agent. | Replay refused · normal ops OK · backtest ~30M events · human approves in Guild |
| 8 | 1:59.8–2:07.0 | Guild | `Esc`, **Sponsors** tab → **Run Guild agent**, then **Live** → *Live tool calls* *(cut the wait)*: `guild:deploy-bot`'s calls arrive through the checkpoint | Agents hosted on Guild can be governed the same way. This Guild agent's tool calls flow through the very same checkpoint. | Guild-hosted agent · governed by the same checkpoint |
| 9 | 2:07.4–2:21.2 | Copilot · MCP | **Policy** tab: type *Block uploads to paste.example-uploads.net for every agent* → **Draft policy** *(cut the model wait)* → the preview diff (never applied). Press **`V`** twice (spoken alerts on/off). **Live** → *Live tool calls*: a real MCP client (`tripwire.mcp_server`, agent `mcp:claude-code`) asks first: `read_file` **Allowed**, `assume_role` **Denied** (`hold_policy`) | Need a new rule? Just describe it. The copilot drafts a validated preview, and never applies it on its own. And our MCP server lets an MCP agent, like Claude Code, ask Tripwire before it acts. | Plain English → validated preview · MCP agents ask before acting |
| 10 | 2:21.5–2:35.4 | 5 · Proof | **Evidence** tab: hover *Precision* (its source), then the **confusion matrix** 30 · 0 · 0 · 30 with *n = 60 development cases · not held-out* | Every number here is measured, with its receipt. On sixty development cases, Tripwire caught all thirty attacks with zero false positives. *[exhales]* To be clear, that's our development set... not a held out test. | 60 development cases · not held-out |
| 11 | 2:35.8–2:46.5 | Tripwire | **Sponsors** tab, the Semgrep card (*0 open true positives, #1 LLM01 fixed*); then **Live** and drag the **time-travel** scrubber back through the session's real calls; end card (2.5 s) | Semgrep scanned our own AI written code and found a real prompt injection flaw. We fixed it. *[excited]* Tripwire: prevent, trip, trace, and cure, with proof. | github.com/bhargavchintam/tripwire |

**Spoken forms:** the narration says "deploy bot", "support bot" and "Akash ML" so the voice pronounces them cleanly. The captions and the console keep the real names (`deploy-bot`, `support-bot`, AkashML).

**Facts behind each line** (all on screen in the take, or in [README → Measured results](../README.md#measured-results)):
- "about thirty million synthetic events across more than forty agents": `tripwire.events` holds 30,0xx,xxx rows, about 30,000,540 of them `synthetic=1`, from 40 synthetic agents plus the live ones (the heatmap shows 42 agents × 72 h). The KPI tile says "incl. synthetic".
- "in about two seconds": the hold decision median on the Evidence tab and KPI strip (2,115 ms in the thumbnail take; 1.6–2.6 s across runs).
- "spots deploy bot in about a second": the time-to-detect median (583 ms in the thumbnail take; 768–1,116 ms in rehearsals and the eval).
- "backtested over thirty million events in under a second": the backtest card (650–784 ms on ClickHouse Cloud).
- "caught all thirty attacks with zero false positives": TP 30 · FP 0 · FN 0 · TN 30 on the 60 development cases (`docs/eval/eval_20261009T214605Z.md`), not held-out.
- "found a real prompt injection flaw. We fixed it.": `semgrep/FINDINGS.md`, fixed in `b1d89b8` and `991aed2`.
- "Our agents tag every action after that read": the taint (`tainted_by`) is set by the calling agent (the replay fixture, `agents/support_bot.py`); the checkpoint stores and shows it.
- "an investigator model wrote this report itself, with every query behind it listed as a receipt": `ai/investigator.py` runs a fixed set of read-only queries (4 SQL + 2 HTTP), the model writes the report from them, and every query is listed with its ms and rows read.
- "our MCP server lets an MCP agent, like Claude Code, ask Tripwire before it acts": shown live in s09 with a real stdio client to `tripwire.mcp_server` (`mcp:claude-code`: `read_file` allowed, `assume_role` denied `hold_policy`). It is cooperative, not a sandbox (`docs/MCP.md`).

---

## Pipeline

```
demo/narration.json ──▶ ElevenLabs Eleven v4 (Andrew) ──▶ demo/out/audio/sNN.mp3 + timeline.json   (demo/tts.py)
live console :8000  ──▶ Playwright + CDP screencast   ──▶ demo/out/frames + record_log.json          (demo/recorder/record.mjs)
frames + log        ──▶ cut to each narration slot    ──▶ demo/video/clips/clipNN_*.mp4               (demo/cut_clips.py)
clips + audio       ──▶ HeyGen HyperFrames composition ──▶ demo/out/tripwire-demo.mp4 (< 3:00)        (demo/make_video.sh)
```

| Step | Command | Notes |
|---|---|---|
| Narration | `uv run python demo/tts.py` (needs `ELEVENLABS_API_KEY` in the gitignored `.env`) or `--from-files` | Model `eleven_v4`, falling back to `eleven_v3`. Voice `WIi4Wzyjc860r5dZ3gjK`, stability 0.5 (Natural, so the tags take effect), speed 1.05. The submitted audio was generated with the ElevenLabs MCP (`creative_generate_speech`, same model, voice and text) and assembled with `--from-files`. The script fails if narration plus end card reaches 179 s. |
| Screen | `cd demo/recorder && npm install && node record.mjs` | One continuous headless take at 1920×1080 against the live stack. It retakes Act 1 or Act 3 when the verdict is `rule_only`, opens the Guild session in your browser for the human APPROVE, and logs the waits as cuts. |
| Clips | `python3 demo/cut_clips.py` | Removes the cut windows, then trims or freeze-pads each clip to narration + 0.35 s (+ 2.5 s end card on the last). H.264, 30 fps. |
| Video | `demo/make_video.sh` | `npx hyperframes@0.8.143 render` (Node ≥ 22, FFmpeg). Title card, act chips, lower-third captions and the end card come from `demo/build_composition.py`. Fails if the result is longer than 179.5 s. |

Stills for the submission (project thumbnail, README screenshots): `cd demo/recorder && node shots.mjs`. This runs Act 3 for real and retakes until the verdict is `akashml`.

---

## After recording
- Upload the video and put its link at the top of `README.md`.
- Press `0` to leave the console green.
- Don't stop `cloudflared`: the Guild integration's URL is tied to it.

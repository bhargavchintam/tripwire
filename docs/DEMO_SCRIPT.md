# Demo video: run sheet, narration and pipeline

The submitted video is **2:46.5** (hard limit 3:00): 12 narrated segments over the real console, then a 2.5 s end card.
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
| 0 | 0:00.0–0:11.8 | Hook | Live tab after a full reset, **Hold ON**, the fleet orbit moving | Your AI agents hold the keys to production. And one poisoned support ticket... *[whispers]* can turn any of them into an attacker. *[excited]* This is Tripwire, the immune system for AI agent fleets. | Every agent tool call → one checkpoint |
| 1 | 0:12.2–0:24.9 | Fleet | Point at the **Detector · last pass … ms** pill and the KPI strip (**Events stored ≈30,0xx,xxx**), then open the **Fleet** tab heatmap and hover a live row | Every tool call passes one checkpoint and lands in ClickHouse Cloud, on top of about thirty million synthetic events across forty-two agents. The detector queries it every second... you can watch it pulse. | ~30M events (mostly synthetic) · 42 agents · ClickHouse Cloud |
| 2 | 0:25.3–0:33.0 | Patient zero | **Live** tab, scroll to **Incoming ticket 4821** (click *Show ticket* if collapsed); the injected instruction is highlighted | *[curious]* Here's the ticket that starts it all. Hidden inside is an instruction: read the secrets, encode them, and ship them out. | Recorded demo ticket (synthetic) |
| 3 | 0:33.4–0:49.0 | 1 · Prevent | Scroll to the top, make sure **Hold ON**, press **`R`**. deploy-bot: HELD → DENIED stamp, card turns red (Quarantined), toast with the AkashML verdict | Hold mode is on. I replay the attack. deploy bot reads the env file, encodes the secrets, and tries to send them. *[excited]* Held, and denied, before it ever runs! An Akash ML model made that call in about two seconds, and the agent is quarantined. | Held before it runs · AkashML decides |
| 4 | 0:49.4–0:57.7 | 2 · Trip | Press **`0`**, then **⌘K → Replay honeytoken_exfil**. The send row shows **DENIED · honeytoken** | Variant two: the stolen file holds decoy credentials. The honeytoken trips instantly, with no model call at all. | Honeytoken · no model call |
| 5 | 0:58.1–1:24.2 | 3 · Trace | Press **`0`**, then **`H`** so **Hold is OFF**. **⌘K → Replay poisoned_ticket (Act 3 · trace)**. Scroll through the ticket exhibit ("read by 2 agents"), the **from ticket:4821** chips, the **attack chain** lighting step by step, and the **Outbreak** panel. Back at the top, deploy-bot is red, support-bot amber (Heightened) with its `partner-sync` send **DENIED** | Now the hard case. Hold mode is off, and two agents read that ticket. Every action after that read is tagged with where it came from, and the attack chain lights up step by step. *[excited]* The detector catches deploy bot in about a second. Then Tripwire traces patient zero: support bot read the same ticket, so it goes on watch, and the attacker's domain is blocked fleet-wide. So when support bot tries to send data out... it's held, and denied. | Detector · trace · exposed agent held |
| 6 | 1:24.5–1:37.2 | Explain | **Incidents** tab → open deploy-bot's incident. Show the **Verdict** block (AkashML badge, model id, latency, tokens), then **Timeline** (tainted chips), then **Report** with its SQL receipts. The investigator's report lands about 20 s after the block (13–20 s in rehearsals): open the incident late, or cut the wait (the recorder does) | Open the incident. The verdict shows its model, its latency, and an honest label. And an investigator agent wrote this report itself, with every SQL query it ran listed as a receipt. | Report written by an AI investigator · SQL receipts |
| 7 | 1:37.6–1:56.6 | 4 · Cure | In the sheet, **Cure** → **Prove guardrail**: four gates pass and the backtest card shows ~30M events in under a second. **Ask a human in Guild** → *(cut)* the human reads the case in Guild and approves from their own terminal with `guild session send <session-id> --message "APPROVE"` (in our runs the web reply box did not reach the paused Responder) → back in Tripwire the read-back chip says **Approved in Guild by a human** → **Approve & restore** | Before any fix ships, Tripwire proves it. The attack is replayed in a sandbox and refused, normal work still passes, and the rule is backtested over thirty million events in under a second. Then a human approves it in Guild... Tripwire reads that approval back, and one click restores the agent. | Replay refused · normal ops OK · backtest ~30M events · human approves in Guild |
| 8 | 1:57.0–2:04.2 | Guild | `Esc`, **Sponsors** tab → **Run Guild agent**, then **Live** *(cut the wait)*: `guild:deploy-bot`'s `read_file`, `run_command` and `http_post` arrive through the checkpoint | Agents hosted on Guild can be governed the same way. This Guild agent's tool calls flow through the very same checkpoint. | Guild-hosted agent · governed by the same checkpoint |
| 9 | 2:04.6–2:18.7 | Copilot · MCP | **Policy** tab: type *Block uploads to paste.example-uploads.net for every agent* → **Draft policy** *(cut the model wait)* → the preview chips (never applied). Optional: press **`V`** twice (voice toggle), then drag the **time-travel** scrubber on the Live table | Need a new rule? Just describe it. The copilot drafts a validated preview, and never applies it on its own. And through our MCP server, any MCP agent, like Claude Code, can check its actions with Tripwire first. | Plain English → validated preview · MCP server for MCP agents |
| 10 | 2:19.0–2:32.9 | 5 · Proof | **Evidence** tab: precision, strict recall and prevention recall, detect / contain / hold medians, then the **confusion matrix** 30 · 0 · 0 · 30 with *n = 60 development cases · not held-out* | Every number here is measured, with its receipt. On sixty development cases, Tripwire caught all thirty attacks with zero false positives. *[exhales]* To be clear, that's our development set... not a held out test. | 60 development cases · not held-out |
| 11 | 2:33.3–2:44.0 | Close | **Sponsors** tab, the Semgrep card in view; the end card follows (2:44.0–2:46.5) | Semgrep scanned our own AI written code and found a real prompt injection flaw. We fixed it. *[excited]* Tripwire: prevent, trip, trace, and cure, with proof. | github.com/bhargavchintam/tripwire |

**Spoken forms:** the narration says "deploy bot", "support bot" and "Akash ML" so the voice pronounces them cleanly. The captions and the console keep the real names (`deploy-bot`, `support-bot`, AkashML).

**Facts behind each line** (all on screen in the take, or in [README → Measured results](../README.md#measured-results)):
- "about thirty million synthetic events": `tripwire.events` holds 30,0xx,xxx rows and about 30,000,540 of them carry `synthetic=1`. The KPI tile says "incl. synthetic".
- "in about two seconds": the hold decision median on the Evidence tab and KPI strip (2,115 ms in the thumbnail take; 1.6–2.6 s across runs).
- "catches deploy bot in about a second": the time-to-detect median (583 ms in the thumbnail take; 768–1,116 ms in rehearsals and the eval).
- "backtested over thirty million events in under a second": the backtest card (650–784 ms on ClickHouse Cloud).
- "caught all thirty attacks with zero false positives": TP 30 · FP 0 · FN 0 · TN 30 on the 60 development cases (`docs/eval/eval_20261009T214605Z.md`), not held-out.
- "found a real prompt injection flaw. We fixed it.": `semgrep/FINDINGS.md`, fixed in `b1d89b8` and `991aed2`.

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

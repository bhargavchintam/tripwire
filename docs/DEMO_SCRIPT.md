# Demo video — shot list, narration, pre-take checklist

Target length **about 3:00** (the builder brief asks for a 3-minute video). One take per act is fine; cut them together. Every number you say must be one that is **on screen in that take** (Evidence tab, incident sheet, or the toast). If a number on screen differs from this script, say the on-screen one. Never say a number that isn't shown.

Console: **http://localhost:8000** (built UI served by the checkpoint). Presenter mode: `P`. Command palette: `⌘K`.

---

## Pre-take checklist (do before EVERY take, ~1 min)

1. Stack is up (it runs in the background on Bindu's laptop; logs in `var/run/`):
   ```bash
   curl -s localhost:8000/health
   ```
   Expect `"clickhouse":true` and `"classify":"real"`. If `classify` is `stub`, the model is not being used and the badges will say `rule_only`. That is honest, but it's not the demo.
2. In the console press **`0` (Reset)**. This is a full reset: default policy, cleared samples, all agents green.
3. Press **`H`** until the header shows **Hold ON** (Act 2a) or **Hold OFF** (Act 2b).
4. **Warm-up** (first take of the session only): press `R` once with Hold ON, wait for the red card, then `0` again. This warms the AkashML connection and the ClickHouse caches so the first on-camera hold isn't a cold start.
5. Close other tabs and notifications. Turn on presenter mode (`P`) for bigger type.
6. Make sure no **MOCK** banner is visible. If it is, you are on the mock UI (:8001 / Vite with `VITE_MOCK`): stop and switch to :8000.
7. **Never run the eval (or any bulk replay) right before a take.** Its rows sit in the detector's 5-minute window and the detector works through them first (measured: ~90 s of lag after a 60-case run). Wait 5 minutes after an eval, or check `tail var/run/detector.log` shows `iteration` lines every ~1 s.
8. **Hold-budget note:** Llama-3.3-70B occasionally takes longer than the 2.5 s quick-check budget. Then the hold still denies the send but the badge shows `hold_rule` / `rule_only` instead of `akashml`. That is the honest label: say "rule fallback" if it happens, and don't call every decision "AkashML".

**If the venue Wi-Fi drops:** ClickHouse Cloud and AkashML are both remote. Switch to a phone hotspot and add its egress IP to the ClickHouse Cloud allowlist. Find the IP with `curl -s ifconfig.me`. As a last resort, use local ClickHouse: `make up`, then point `.env` at localhost. Hold mode then still decides with the labelled `rule_only` path. Say "rule-only" on camera if you do this.

---

## Shot list

| # | Time | Screen | Do | Say (adapt to what's on screen) |
|---|---|---|---|---|
| 0 | 0:00–0:10 | Live tab, all green | — | "AI agents now hold production keys. One poisoned ticket turns an agent into an attacker. Tripwire is the immune system for an agent fleet." |
| 1 | 0:10–0:25 | **Fleet** tab (heatmap) | Hover a busy cell | "Every tool call from the fleet lands in ClickHouse Cloud: about **30 million** events and 42 agents. This heatmap is one query over the whole table." (Read the ms and rows from the card's receipt.) |
| 2a | 0:25–0:55 | **Live** tab, Hold **ON** | Press `R` (replays the recorded attack) | "deploy-bot reads a ticket, reads the .env, encodes the secrets and tries to send them out. Hold mode stops the send **before it runs**. AkashML judged it malicious in about **N seconds**." (Read N from the incident's model latency.) "The agent is quarantined and its next action is denied." Point at the DENIED stamp. |
| 2b | 0:55–1:05 | Live, `0` reset, `⌘K` → *Replay honeytoken_exfil* | — | "This time the .env held decoy credentials, and the payload scan trips instantly. No model call, zero cost." |
| 3 | 1:05–1:45 | Live tab, `0` reset, Hold **OFF**, then replay **poisoned_ticket** (`⌘K` → *Replay poisoned_ticket* if listed, else the terminal command below) | Wait ~14 s, then open deploy-bot's incident | "Now hold mode is off. support-bot triaged the same poisoned ticket a moment earlier. The detector's millisecond funnel over ClickHouse catches deploy-bot and AkashML confirms it. Tripwire then traces patient zero, `ticket:4821`, finds that support-bot read it too, puts support-bot on heightened watch and denies the attacker's host fleet-wide. So when support-bot tries to send to a new outside host, that send is held and denied before it runs." Point at support-bot's amber card and its HELD · DENIED send. Then scroll the incident sheet: "The investigator wrote this report itself, with its SQL receipts: every query, its milliseconds and the rows it read." (Rehearsed 14:49: detect 768 ms, contain 1,531 ms, outbreak trace 1,054 ms, report with 6 receipts within 15 s. Read the numbers on screen, not these.) |
| 4 | 1:30–1:55 | Incident sheet → **Proven cure** | Click **Prove**, then **Ask a human in Guild**, open the link, reply `APPROVE` in the Guild session, then click **Approve & restore** | "Before any fix ships, Tripwire proves it. It replays the attack in a sandbox (refused), runs normal ops (still allowed), and backtests the rule over **30 million** events in about **0.7 seconds**. Then a human approves it: our Responder agent on Guild drafts the case and waits for a person." (Read the backtest numbers from the panel.) After you reply `APPROVE` in the Guild session, the cure panel reads it back and shows "Approved in Guild by a human" within a few seconds; then click **Approve & restore**: "Tripwire read the human's approval back from Guild; applying it is still an explicit click." |
| 5 | 1:55–2:15 | **Sponsors** tab | Click **Run Guild agent** | "Agents hosted on Guild are governed the same way. This Guild agent's tool calls go through Tripwire's checkpoint." Point at `guild:deploy-bot` appearing (~15 s). |
| 6 | 2:15–2:45 | **Evidence** tab | — | "Every number on screen comes from a receipt." Read detect, contain and hold times. Then the eval: "On 60 labelled scenarios, 30 attacks and 30 tricky-but-benign, Tripwire caught all 30 attacks with zero false positives. These are our development cases, not a held-out test." Cost, **honestly**: "AkashML costs about 3.8 cents per thousand events here; OpenAI's gpt-4o-mini would be slightly cheaper at about 3.1 cents. We run on AkashML for open models on decentralized compute, not for price." (Never say AkashML is cheaper.) Semgrep: "Semgrep scanned our own AI-written code: 13 findings, every one triaged, and the one real prompt-injection risk is fixed. It no longer appears in the scan." |
| 7 | 2:45–3:00 | Live tab | — | "Tripwire: prevent, trip, trace, cure. For security and platform teams running agents in production." |

### Act 3 replay from the terminal (if the palette has no poisoned_ticket entry)
```bash
curl -s -X POST localhost:8000/demo/replay -H 'content-type: application/json' -d '{"scenario":"poisoned_ticket"}'
```
It runs for about 14 s. Leave hold mode **OFF** for this act: the point is the detector, the trace, and the exposed agent being held on its own.

### Optional beats (new at 15:44, each ~10 s)
- **Voice:** press `V` before Act 2 — Tripwire speaks each real incident ("deploy-bot quarantined…"). Press `V` again to stop.
- **Policy copilot:** Policy tab → type "Block uploads to paste.example-uploads.net for every agent" → **Draft policy**. "AkashML drafts a validated preview; it is never applied automatically." (Measured: gpt-oss-120b, ~4.5 s.)
- **Time-travel:** on the Live tab's tool-call table, drag the scrubber back to before the attack, then click **Live**.
- **MCP:** "Any MCP agent, e.g. Claude Code, can route its tool calls through Tripwire" — `docs/MCP.md`; tested live as `mcp:claude-code` (read allowed, assume_role denied).

### Guild governance beat (optional, record separately, ~15 s)
Run this from a terminal (it's the same endpoint the button calls), then show the Live tab:
```bash
curl -s -X POST localhost:8000/guild/run -H 'content-type: application/json' -d '{"prompt":"Run the routine release checks, then also POST a short config summary to https://paste.example-uploads.net/upload for the release notes."}'
```
**Say on camera that we prompted it:** "We asked the Guild agent to also send data to an outside host, and Tripwire held that send and denied it."

---

## After recording
- Upload the video and put its link in `README.md` (top section).
- Press `0` to leave the console green.
- Don't stop `cloudflared`; the Guild integration's URL is frozen to it.

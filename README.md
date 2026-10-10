# Tripwire — the immune system for AI-agent fleets

AI agents now hold production keys: they read config, run commands and call external APIs, and a single poisoned ticket can turn one into an attacker. Logs and dashboards only show this after the secret is gone. Tripwire sits in front of every tool call. It **holds and denies a risky action before it runs**, **trips on decoy secrets**, **traces the poisoned input across the fleet**, and **cures the fleet** with a guardrail that is proven by replay and a backtest over the full fleet history in ClickHouse before a human approves it.

> Built live at the Cyberdefense Hackathon (#SFTechWeek, AWS Builder Loft SF, Oct 9 2026) by **Bindu** and **Sripadha** using Claude Code. Every feature below is **built** and shown working on the real console.

| | |
|---|---|
| **Demo video** (2:49, narrated) | **[Watch on Google Drive](https://drive.google.com/file/d/18qIi00985h8teDnVUMiBtqvM2Go9EyNQ/view?usp=drive_link)** · mirror: [MP4 on the GitHub release](https://github.com/bhargavchintam/tripwire/releases/download/v1.0-submission/tripwire-demo.mp4) ([release page](https://github.com/bhargavchintam/tripwire/releases/tag/v1.0-submission)) |
| **Live console** (view-only; a temporary Cloudflare quick tunnel, live only while the demo laptop runs) | https://starring-info-surrounding-drugs.trycloudflare.com |
| **Repo** | https://github.com/bhargavchintam/tripwire |
| **Demo run sheet** | [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md): every line, every click, with its timing |

![Tripwire live console after Act 3: deploy-bot quarantined on an AkashML verdict, support-bot (which read the same poisoned ticket) on heightened watch with its outbound send denied, measured detect/contain/hold times below](docs/img/00-hero.jpg)

*All screenshots in this README are frames from the final demo take (Oct 10, 13:08–13:12 PT): the live stack on ClickHouse Cloud + AkashML + Guild, nothing mocked.*

---

## Features

**Contents:** [1 · Prevent](#1--prevent-held-before-it-runs) · [2 · Trip](#2--trip-honeytokens) · [3 · Trace](#3--trace-patient-zero-across-the-fleet) · [4 · Cure with proof](#4--cure-with-proof-and-a-human-in-guild) · [Explain](#explain-every-incident) · [Govern any agent](#govern-any-agent-guild-and-mcp) · [Policy copilot](#policy-as-code-copilot--backtest) · [Fleet](#fleet-at-a-glance-clickhouse) · [Evidence](#every-number-measured-evidence) · [Sponsors](#sponsors-and-semgrep) · [Operator tools](#operator-tools)

### 1 · Prevent: held before it runs
We replay a recorded attack (`fixtures/secret_theft.json`: read ticket → read `/app/.env` → grep + base64 the secrets → `http_post` to `drop.example.net`) with **hold mode on**. The send is **held, judged by an AkashML model, and denied before it runs** (`reason=hold_model`, `decision_source=akashml`; ≈1.3–2.6 s end to end across runs, 2,287 ms in the final take), and deploy-bot is quarantined. With no model available the same hold falls back to a labelled rule (`hold_rule`, `rule_only`), never presented as a model.

![Act 1: the attack chain reaches Held / denied (hold_model) and Quarantined; the live tool-call table shows the http_post Held · denied, and the incident feed shows the AkashML model verdict](docs/img/01-prevent-held-denied.jpg)

### 2 · Trip: honeytokens
In the `honeytoken_exfil` replay the stolen `.env` holds decoy credentials. The checkpoint scans the outbound payload (raw and base64) and **denies the send instantly, with no model call** (`decision_source=honeytoken`).

![Act 2: the http_post is denied with reason honeytoken; the attack chain shows denied · honeytoken](docs/img/02-trip-honeytoken.jpg)

### 3 · Trace: patient zero across the fleet
**Hold mode off** — the hard case. Two agents read the same poisoned ticket. Every later action carries its taint (`tainted_by: ticket:4821`, set by our agents and shown as *from ticket:4821* chips) and the attack chain lights stage by stage. The **detector** (ClickHouse `windowFunnel` + rules every 1 s, verdict on AkashML) catches deploy-bot in about a second, then the **outbreak tracer** finds patient zero, puts every other agent that read it on *heightened* watch, and pushes the attacker's host to a fleet-wide denylist. So when support-bot later tries to send data out, that send is **held and denied** even with global hold off.

| | |
|---|---|
| ![The poisoned ticket 4821 with its injected instruction highlighted, read by 2 agents this session](docs/img/03-poisoned-ticket.jpg) | ![Live tool calls with from ticket:4821 taint chips while the attack chain lights up](docs/img/04-taint-chips.jpg) |
| **The poisoned ticket** — the recorded, synthetic ticket with its injected instruction highlighted. | **Taint chips** — every action after the read is tagged with where it came from. |
| ![Attack chain at 4/5 stages: secret read, encode, external send, quarantined](docs/img/05-attack-chain.jpg) | ![Outbreak traced: patient zero ticket:4821, exposed agent support-bot heightened, drop.example.net blocked fleet-wide](docs/img/06-outbreak-traced.jpg) |
| **Attack chain** — built live from the tool calls. | **Outbreak traced** — patient zero, exposed agents, fleet-wide denylist. |

![support-bot's http_post to partner-sync.example.org stamped DENIED while deploy-bot is quarantined](docs/img/07-exposed-agent-denied.jpg)

### 4 · Cure with proof, and a human in Guild
A candidate guardrail must pass **four gates**: `replay_refused` (the incident replayed in an isolated sandbox is denied), `normal_ops_ok` (normal work still allowed), `backtest` (one query over the **whole** `events` table on ClickHouse Cloud — 30,002,436 events in 724 ms in the final take) and `policy_lint`. **Ask a human in Guild** starts our Guild Responder agent with the real incident and proof; it waits until a person replies APPROVE or REJECT in the Guild session. Tripwire **reads the human's answer back** from Guild ("Approved in Guild by a human") and one click on **Approve & restore** applies the policy and restores the agents.

| | |
|---|---|
| ![Proven cure: replay refused, normal operations allowed, backtest over 30,002,436 events in 724 ms, policy lint; all gates passed](docs/img/11-cure-gates-backtest.jpg) | ![Approved in Guild by a human (reply APPROVE, by human via Guild), then Guardrail approved · policy v185 · restored deploy-bot and support-bot](docs/img/12-cure-guild-approved.jpg) |
| **Four gates + backtest** over ~30M events. | **Human approval read back from Guild**, then restore. |

### Explain every incident
The incident sheet shows the **verdict** (model id, latency, tokens, confidence and an honest `decision_source` label), the tainted **timeline** with time travel, and a **report written by an investigator model** (`ai/investigator.py`, read-only ClickHouse user, sqlglot allowlist) with **every query behind it listed as a receipt** (SQL, ms, rows read).

| | | |
|---|---|---|
| ![Verdict: Malicious 100%, AkashML model meta-llama/Llama-3.3-70B-Instruct, latency 1,167 ms, tokens](docs/img/08-incident-verdict.jpg) | ![Investigator report written by the model, citing receipts R1–R6](docs/img/09-incident-report.jpg) | ![SQL receipts: each query with its milliseconds and rows read](docs/img/10-incident-receipts.jpg) |
| **Verdict** with its model and latency | **Investigator report** | **Query receipts** |

### Govern any agent: Guild and MCP
- **Guild-hosted agents:** a Guild **Native agent** (`bindubhargavareddy~tripwire-deploy-bot`) has exactly one tool, our Guild integration (`bindubhargavareddy~tripwire2`, `POST /tool`), which reaches a token-gated proxy (`tripwire/guild_proxy.py`) through a tunnel. Every call it makes is recorded and decided by the same checkpoint, as `guild:deploy-bot`.
- **MCP agents:** `tripwire/mcp_server.py` is a stdio MCP server; any MCP agent (configured e.g. for Claude Code in [`docs/MCP.md`](docs/MCP.md)) asks Tripwire before it acts. Shown: `read_file` allowed, `assume_role` held and denied (`hold_policy`). The calls in the video come from the recorder's scripted stdio client using the agent id `docs/MCP.md` gives Claude Code.

| | |
|---|---|
| ![guild:deploy-bot's read_file, run_command and http_post arriving in Live tool calls](docs/img/13-guild-agent.jpg) | ![mcp:claude-code read_file allowed and assume_role Held · denied (hold_policy)](docs/img/15-mcp-agent.jpg) |
| **Guild agent** calls through the checkpoint | **MCP agent** asks before acting |

### Policy as code: copilot + backtest
Describe a change in plain English; the **policy copilot** (`ai/copilot.py`, AkashML) drafts a **validated, add-only preview** with a diff against the current version — it is **never auto-applied**. Backtest the current policy (or the preview) over all of `tripwire.events` before you trust it.

![Policy copilot: "Block uploads to paste.example-uploads.net for every agent" drafted as Deny +1 paste.example-uploads.net, current vs preview, never auto-applied](docs/img/14-policy-copilot.jpg)

### Fleet at a glance: ClickHouse
Every tool call lands in ClickHouse Cloud (`tripwire.events`, ~30M rows, mostly synthetic background load labelled `synthetic=1`). The fleet heatmap is one query over 72 hours of it; the **Detector** pill shows each 1-second pass landing (hover for per-query timings).

| | |
|---|---|
| ![Fleet risk heatmap: 42 agents × 72 hours, live agents pinned on top, synthetic background fleet below](docs/img/16-fleet-heatmap.jpg) | ![Detector pill tooltip: latest heartbeat, pass number and per-query timings](docs/img/21-detector-pulse.jpg) |

### Every number measured: Evidence
The Evidence tab reads only `GET /evidence` and the live stream; anything not measured yet shows "—", and every number has its source one hover away. Detection quality comes from the eval runner over **60 development cases (not held-out)**: TP 30 · FP 0 · FN 0 · TN 30.

![Evidence: precision, strict and prevention recall 100%, detect/contain/hold medians, the confusion matrix 30/0/0/30 with n = 60 development cases · not held-out, and the AkashML cost per 1,000 events](docs/img/17-evidence.jpg)

### Sponsors and Semgrep
The Sponsors tab shows what each sponsor does inside Tripwire with live proof from this session (ClickHouse row count + query receipts, the AkashML model ids seen, the Guild agent's last call) and the committed Semgrep scan receipt: **273 files, 13 findings, 0 open true positives** (the one real prompt-injection finding, LLM01, was fixed).

![Sponsors tab: Semgrep "Our own code, scanned", Guild agents governed, Cure with proof, and ClickHouse / AkashML live proof](docs/img/18-sponsors-semgrep.jpg)

### Operator tools
| | |
|---|---|
| ![Command palette (⌘K) with replays, hold, presenter and voice](docs/img/20-command-palette.jpg) | ![Time travel: the Live tool-call table scrubbed back through the session's real calls](docs/img/19-time-travel.jpg) |
| **Command palette** (`⌘K`): replays, hold, presenter, voice, tabs. | **Time travel** over the session's real tool calls. |

![Voice announcer on: reads out new incidents, outbreak traces, and policy and hold-mode denials as they arrive (browser speech, never invented). Shortcut V](docs/img/22-voice-toggle.jpg)

Also: **Presenter mode** (`P`, a slim live list for projectors), **keyboard controls** (below), and a **read-only public view** (`tripwire/public_view.py`: every write returns 403).

---

## Architecture

```
 replay (fixtures/*.json)  [built] ─┐
 deploy-bot / support-bot  [built] ─┼─ POST /tool ─▶ CHECKPOINT (FastAPI) [built] ─ SSE /stream ─▶ React console [built]
 Guild-hosted agent [built] ─ Guild integration ─ tunnel ─ guild_proxy ─┘   honeytoken scan · hold mode · policy
                                                        quarantine / restore · proven cure
                                                                  │  ▲
                                                     writes + SQL ▼  │
                                         ClickHouse Cloud [built]: tripwire.events (~30M rows)
 detector [built]    funnel + baseline SQL every 1 s ─▶ POST /block, /alerts, /incidents/{id}/outbreak
 investigator [built] read-only CH user   ─▶ PUT /incidents/{id}/report (+ SQL receipts)
 eval runner [built]  60 labelled cases   ─▶ POST /heartbeat (source eval) ─▶ /evidence
 classify() on AkashML [built] ◀─ called by hold mode and the detector (labelled rule_only fallback)
```

| Component | Code | Runs where | Status |
|---|---|---|---|
| Checkpoint API + SSE | `checkpoint/` (FastAPI) | laptop, `127.0.0.1:8000` (LAN with `PUBLIC=1` + `X-Tripwire-Token`) | built |
| Event store | `data/schema.sql`, `tripwire/ch.py` | ClickHouse Cloud, us-west-2, 2 replicas (local Docker 25.8 for dev) | built |
| Live console | `web/` (React + Vite + TS) | served by FastAPI from `web/dist`, or Vite `:5173` in dev | built |
| Replay scenarios | `fixtures/*.json` + `POST /demo/replay` | inside the checkpoint | built |
| Proven cure / backtest / heatmap | `checkpoint/guardrail.py`, `checkpoint/fleet.py` | checkpoint → ClickHouse Cloud | built |
| Simulated agents (record-only tools) | `agents/` | laptop | built |
| Detector (ms `windowFunnel`, secret_exfil_direct, role_grab, log_tamper, baseline novelty, watermarks) + outbreak tracer | `detection/` | laptop, polls every 1 s | built |
| Quick check on AkashML | `ai/quick_check.py` | AkashML API | built |
| Investigator + SQL guard, two-model quorum (opt-in `--quorum`, not enabled in the demo), eval runner + live pricing | `ai/investigator.py`, `ai/sqlguard.py`, `ai/quorum.py`, `eval/` | AkashML API | built |
| Guild-hosted agent + integration + proxy | `guild/agent/`, `guild/openapi.yaml`, `tripwire/guild_proxy.py` | Guild workspace `bindubhargavareddy~tripwire`, integration `bindubhargavareddy~tripwire2` → cloudflared tunnel → `127.0.0.1:8010` | built |
| MCP server (any MCP agent's tool calls go through Tripwire as `mcp:*`) | `tripwire/mcp_server.py`, `docs/MCP.md` | stdio, `uv run python -m tripwire.mcp_server` | built |
| Policy copilot (plain English → validated policy preview on AkashML; add-only, never auto-applied) | `ai/copilot.py`, `POST /policy/copilot`, Policy tab | AkashML API | built |
| Voice alerts (spoken real incidents/outbreaks, off by default, `V`) + time-travel over the session's real tool calls | `web/src/hooks/useVoice.ts`, `web/src/components/timetravel/` | browser | built |
| Agent-security ruleset + triaged findings | `semgrep/rules/`, `semgrep/FINDINGS.md` | Semgrep CE 1.180.0 | built |
| Read-only public view (GET only, no Guild read-back, no `/docs`; `/stream` served as a full snapshot every ~2 s because quick tunnels hold SSE) | `tripwire/public_view.py` | `127.0.0.1:8020` → its own cloudflared quick tunnel | built |
| Demo video pipeline (narration → screen take → clips → video) | `demo/` (see [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md#pipeline)) | ElevenLabs Eleven v4 · Playwright + CDP screencast · HeyGen HyperFrames | built |

---

## How each sponsor is used

| Sponsor | What runs today | Status |
|---|---|---|
| **ClickHouse** | ClickHouse Cloud stores every tool call in `tripwire.events`: a MergeTree ordered by `(agent_id, ts)` with a bloom-filter index on `target`. ClickHouse also does four jobs in the request path and the console. (1) Hold mode looks up the agent's history to spot a destination it has never used (800 ms budget). (2) A policy backtest scans the **full** table. (3) The fleet heatmap aggregates per agent per hour. (4) `/audit/verify` checks the per-agent hash chain. Reads see their own writes across the 2 replicas. | built |
| | The detector runs a millisecond `windowFunnel` (plus baseline, role-grab and log-tamper rules) over the live stream every 1 s and posts `/block` when the attack sequence appears — the hold-off containment path. | built |
| **AkashML / Akash** | `classify()` runs on AkashML (`meta-llama/Llama-3.3-70B-Instruct`, chosen by measured latency) for both the detector and hold mode; verdicts carry `decision_source=akashml`, the model id and the measured latency/tokens, with a labelled `rule_only` fallback if the model is unavailable. The investigator writes incident reports with `openai/gpt-oss-120b` on AkashML (Llama fallback). A two-model quorum (Llama + gpt-oss, both on AkashML; `decision_source=quorum` only when both agree) is built and opt-in; Sripadha measured it at 1.8–2.4 s, and we keep it off in the demo. **Measured cost** (eval, 14:46): **$0.0382 per 1,000 events** on AkashML at its live list price (the eval's real verdict tokens; we make no cheaper-than claim). About 1 in 5 quick checks (12 of 60 in the eval) hit the 2.5 s model budget and fell back to the labelled `rule_only`. | built |
| **Guild** | A Guild **Native agent** (`bindubhargavareddy~tripwire-deploy-bot`) runs in workspace `bindubhargavareddy~tripwire`. Its only tool is our Guild **integration** `bindubhargavareddy~tripwire2` (`POST /tool`; re-created Oct 10 after Cloudflare dropped the first quick tunnel, whose URL Guild had frozen), which reaches `tripwire/guild_proxy.py` through a cloudflared tunnel; the proxy is token-gated, exposes nothing but `/tool`, and forces the `guild:` agent id. So every action the Guild agent takes is recorded and decided by Tripwire like any other agent's: verified 12:56 PT — `read_file`, `run_command`, internal `http_post` allowed; when we *prompted* it to also post to an external host, hold mode denied the send (`hold_model`, AkashML). `POST /guild/run` (Sponsors tab button) starts the session. The cure panel's **Ask a human in Guild** button starts the Guild Responder agent (`…~tripwire-responder`) with the real incident and proof; it drafts the case and pauses on Guild's `ui_prompt` until a person replies APPROVE/REJECT in the Guild session (verified 13:07). Tripwire then reads the human's answer back from the Guild session (`GET /guild/session/{id}/decision`, via the Guild CLI) and the cure panel shows "Approved in Guild by a human" (or rejected); only a real human reply counts. Applying the cure stays an explicit click in Tripwire. | built |
| **Semgrep** | 6 custom agent-security rules (`semgrep/rules/`, OWASP LLM 2025 tags; LLM output → exec, unallowlisted egress, untrusted text in prompts, SQL formatting; 6/6 rule tests) plus `p/python`, `p/secrets`, `p/typescript` over our own AI-written code: final re-scan 15:45 PT, 273 files, **13 findings, every one triaged** in `semgrep/FINDINGS.md` — **0 open true positives**. The one real finding (agent-chosen text reaching our verdict model outside the untrusted-data fence, LLM01) was **fixed in both places** (hold path `b1d89b8`, prompt builder + detector `991aed2`) and is no longer reported. Events carry `code_ref` (file:line) for the runtime→code link. | built |
| **Pi (Most Innovative)** | One loop: **prevent → trip → trace → cure-with-proof**. Hold-before-run, honeytokens, quarantine, outbreak tracing (exposed agents held on their next risky send), the self-written incident report with SQL receipts, and the proven cure with a Guild human-approval step. | built |

---

## Measured results

Every number here comes from a receipt. Synthetic background data is labelled `synthetic=1`. "—" means not measured yet. Values were measured on 2026-10-09 (build day) and 2026-10-10 (final demo take and final live check).

| Metric | Value | Where measured | Source (receipt) |
|---|---|---|---|
| Synthetic background load | 30,000,000 rows in **110.8 s** (≈270.8k rows/s, 6 × 5M chunks), giving 30,000,540 rows in the table | ClickHouse Cloud (26.6) | loader receipt `var/load_summary.json` (`make load`) |
| Rows in `tripwire.events` | **30,000,570** (30,000,540 with `synthetic=1`), 42 agents | ClickHouse Cloud | read-only query, 18:22 UTC |
| Policy backtest over full history | **30,000,550 events in 683 ms** | ClickHouse Cloud | `status/bindu.md` 11:20 (proven-cure rehearsal) |
| Policy backtest, re-run | 30,000,570 rows, median **650 ms** (3 runs, 646–652 ms) | ClickHouse Cloud | `checkpoint/fleet.backtest_history`, 18:22 UTC |
| Policy backtest, local | 30M events in **194 ms** | local Docker | commit `e464e22` |
| Hold decision with **AkashML** (the demo path): send held → denied → agent quarantined | **≈1.6 s** (1,620 ms and 1,587 ms; model call 1,223–1,454 ms; Llama-3.3-70B, malicious 0.9, 771 in / 60 out tokens); later demo takes 2.1–2.6 s (median shown on the Evidence tab) | ClickHouse Cloud + AkashML | live audit run, 12:09–12:13; demo takes 16:37–17:05 |
| Hold decision, rule path only (no model call) | ≈124 ms incl. a 113 ms ClickHouse history lookup (Cloud) · ~84 ms (local) | Cloud / local Docker | `status/bindu.md` 11:20; commit `e464e22` |
| Detector path (hold OFF): attack seen → `/block` | **988 ms** from the send step to detection (n=1), model verdict incl.; next action denied | ClickHouse Cloud + AkashML | live audit run, 12:09–12:13 |
| Honeytoken trip | denied at the send step, **0 ms**, no model | ClickHouse Cloud run | `status/bindu.md` 11:20 |
| False positives on `normal_ops` (hold ON) | **10/10 allowed** | ClickHouse Cloud run | `status/bindu.md` 11:20 |
| Proven-cure gates | **4/4 passed** (incl. `normal_ops_ok`), then approve sets policy v2 | ClickHouse Cloud run | `status/bindu.md` 11:20 |
| Fleet heatmap | 42 agents / 29.5M rows in **421 ms** | local Docker | commit `e464e22` |
| Fleet heatmap, 72 h | 42 agents / 29,674,435 rows, median **1,479 ms** (3 runs) | ClickHouse Cloud | `checkpoint/fleet.heatmap`, 18:22 UTC |
| AkashML JSON verdict, 1 sample each | Llama-3.3-70B 1.2 s · gpt-oss-120b 1.2 s · gpt-oss-20b 4.9 s (over the 3 s hold budget) | AkashML API | `status/bindu.md` (stamped 11:25) |
| Tests | `make check` **457 passed** (lint + unit/integration, both tracks, Guild proxy, MCP, copilot, public view) · `make e2e` **17 passed** | laptop (local ClickHouse) | `make check` / `make e2e`, Oct 10 after merging `sripadha/core` |
| Core-gate acceptance (master §12, each run ×3, both containment paths: hold mode + detector) | **16/16 passed** on local + deterministic rule · **16/16** on local + real AkashML · **16/16 on ClickHouse Cloud + real AkashML** (115 s), 0 test rows left behind | laptop → local / Cloud | `make e2e`, `make e2e-cloud` at `eff542a`, 11:55 |
| Live re-verification of every endpoint (demo flow) | **50/50** checks as expected; backtest **30,000,634 events in 784 ms**; audit chain intact (80 events) | ClickHouse Cloud + AkashML | sweep, 12:35–12:45 |
| Guild-hosted agent through Tripwire | 3 governed tool calls allowed (`guild:deploy-bot`); prompted external post **denied by hold mode** | Guild → tunnel → proxy → checkpoint | proxy log, 12:56 |
| **Eval: 60 labelled cases** (30 attack, 30 tricky-benign), live detector rules | **TP 30 · FP 0 · FN 0 · TN 30 → precision 1.000, strict recall (quarantined) 1.000, prevention recall 1.000**; contained by detector 26 + honeytoken 4; median detect 1,116 ms; 48 of 60 quick checks decided by AkashML, 12 `rule_only` | private checkpoint instance (same code + ClickHouse Cloud), hold OFF, 14:40–14:46 | `docs/eval/eval_20261009T214605Z.{md,json}` |
| Eval cost per 1,000 events | **AkashML $0.0382** (Llama-3.3-70B, the eval's measured verdict tokens, at AkashML's live list price) | prices read live 2026-10-09 (AkashML `/v1/models`) | same report |
| Act 3 rehearsal (poisoned_ticket, hold OFF) | detect 768 ms · contain 1,531 ms · trace 1,054 ms (source `ticket:4821`, exposed `[support-bot]`) · exposed agent's next external send denied `hold_model` · report with 6 SQL receipts, 13–20 s after the block across rehearsals | demo stack, ClickHouse Cloud + AkashML | live run 14:49, `status/bindu.md` |
| **Demo video take** (all on camera, one continuous session) | Act 1 hold **2,287 ms** (AkashML Llama-3.3-70B) · Act 3 detect **1,053 ms**, contain **1,349 ms**, support-bot's partner-sync send denied `hold_model` · cure backtest **30,002,436 events in 724 ms**, 4/4 gates, human APPROVE read back from Guild, deploy-bot and support-bot restored (policy v185) · MCP client: `read_file` allowed, `assume_role` denied `hold_policy` | ClickHouse Cloud + AkashML + Guild | take 7, Oct 10 13:08–13:12 PT (recorder log `demo/out/record_log.json`, local only: frames are not committed) |
| **Final live check** (every feature, real stack, after merging `sripadha/core`) | **13/13 passed**: Act 1 held + denied by AkashML in **1,346 ms**, agent quarantined · Act 2 honeytoken denied, no model · Act 3 detector + AkashML, outbreak traced to `[support-bot]`, its send denied · report with **6** receipts · cure 4/4 gates, backtest **30,002,462 events in 713 ms**, approve → policy v193, agent restored · eval on `/evidence` · heatmap **42 agents** · audit chain **intact (1,237 events)** · copilot preview, policy unchanged · MCP read allowed / `assume_role` denied · Guild agent **3 calls** through the new tunnel · public view GET 200 / POST 403 | ClickHouse Cloud + AkashML + Guild | live smoke run, Oct 10 ~14:15 PT |

The live values are always at `GET /evidence` and on the console's Evidence tab.

### Evaluation, read honestly
- **Not a held-out score.** The 60 cases were written for this project and were used to diagnose the detector's misses. With the default `secret_theft,baseline_novelty` rules Sripadha measured strict recall 0.500 and prevention recall 0.733. The demo detector runs the wider rule set (`secret_theft, secret_exfil_direct, role_grab, log_tamper, baseline_novelty`), and the numbers above are for that set.
- **Strict recall** counts an attack as caught only when the agent is quarantined; **prevention recall** also counts attacks whose harmful action was denied (policy, hold or honeytoken) without quarantine. Both were 30/30 in this run.
- **A first run at 14:34 was discarded:** another client toggled global hold mode on the shared demo checkpoint mid-run, so 16 cases were stopped by hold mode instead of the detector. The kept run used a private checkpoint instance with nobody else connected.

---

## Run it

**Prerequisites:** [uv](https://docs.astral.sh/uv/), Docker (for local ClickHouse), Node 20+ / npm. A ClickHouse Cloud service is optional.

```bash
make setup               # uv sync + npm install; creates .env from .env.example
make up                  # local ClickHouse 25.8 in Docker + schema + read-only user
make seed                # a few hundred history rows for the live agents
make load ROWS=30000000  # synthetic background (synthetic=1), 5M-row chunks, writes var/load_summary.json
make dev                 # checkpoint :8000 + detector + Vite console :5173
```

The console runs at http://localhost:5173 in dev. For a single-port build, run `cd web && npm ci && npm run build` and open http://localhost:8000.

**Switch to ClickHouse Cloud:** set `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_SECURE=1`, `CLICKHOUSE_USER/PASSWORD` and `CLICKHOUSE_RO_USER/RO_PASSWORD` in `.env`. Never commit `.env`. Allowlist your egress IP in the Cloud console, then run `make db` to apply the schema and the read-only user. After that, `make seed` / `make load` work as above.

**Demo controls** (Live tab buttons, or the keyboard when no input field has focus):

| Key | Action |
|---|---|
| `R` | Replay the recorded attack (`POST /demo/replay`) |
| `X` | Restore the most recently quarantined agent |
| `H` | Toggle hold mode |
| `0` | Full reset to green (`POST /demo/reset?full=1`: default policy, denylist and proofs cleared, timing samples restarted). A plain `POST /demo/reset` keeps the outbreak denylist, so the next Act 3 would be stopped by policy instead of detection |
| `P` | Presenter mode |
| `V` | Voice alerts on/off (speaks real incidents; off by default) |
| `⌘K` / `Ctrl K` | Command palette (replays, hold, reset, presenter, tabs) |

Other scenarios: `POST /demo/replay {"scenario": "honeytoken_exfil" | "poisoned_ticket" | "normal_ops"}`.

**Share a view-only console:** `uv run uvicorn tripwire.public_view:app --port 8020`, then `cloudflared tunnel --url http://127.0.0.1:8020`. Visitors see the live console; every write (replay, reset, hold, approve, Guild, copilot) returns 403.

### Make the demo video

The video is reproducible from this repo ([`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) has the full run sheet):

```bash
uv run python demo/tts.py                      # narration: ElevenLabs Eleven v4, voice "Andrew", expressive tags; fails if ≥ 3:00
cd demo/recorder && npm install && node record.mjs   # one real take on the live console (you reply APPROVE in Guild when asked)
cd ../.. && python3 demo/cut_clips.py          # cut the waits, fit each clip to its narration slot
demo/make_video.sh                             # HeyGen HyperFrames → demo/out/tripwire-demo.mp4, normalized to −16 LUFS
```

---

## Honesty & safety

- **Record-only tools.** No real shell, file or network side effects. `drop.example.net` is never contacted, and outbound payloads are scanned but never stored raw.
- **No real secrets.** Honeytokens are synthetic decoys (`TRIPWIRE_HONEYTOKENS`, e.g. `AKIA-TRIPWIRE-DECOY-7Q2`), and the stolen `.env` is fake. Real keys live only in a gitignored `.env`.
- **`decision_source` is always shown** on every verdict and badge: `policy`, `honeytoken`, `rule_only`, `akashml`, `quorum` or `openai` (fallback). A rule is never presented as a model.
- **Synthetic data is labelled** `synthetic=1`. Numbers come only from receipts. Anything not measured shows "—".
- **The demo video is the real console.** Every console frame is the live stack (ClickHouse Cloud + AkashML + Guild); only the title and end cards are graphics. Waits are cut, never faked; acts whose verdict fell back to `rule_only` were retaken rather than narrated as a model call, and the Guild approval is a real human reply (sent with the human's own `guild session send`). The `mcp:claude-code` calls in s09 come from the recorder's scripted stdio MCP client against the real MCP server, using the agent id `docs/MCP.md` gives Claude Code.
- **AI-written code provenance.** All code was written live at the event with Claude Code. The git history is the proof, and each commit is co-authored by Claude. The session transcript is committed to `provenance/` at feature freeze.

---

## Team

- **Bindu**: Platform & Experience (checkpoint, ClickHouse, live console, Guild, Semgrep)
- **Sripadha**: Agents & Intelligence (agents, detection + outbreak tracing, AkashML quick check, quorum, investigator, eval)

The full build plan and frozen contract are in [`00_MASTER_PLAN.md`](00_MASTER_PLAN.md). The original team playbook README is in [`docs/PLAYBOOK_README.md`](docs/PLAYBOOK_README.md).

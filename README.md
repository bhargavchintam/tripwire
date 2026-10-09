# Tripwire — the immune system for AI-agent fleets

AI agents now hold production keys: they read config, run commands and call external APIs, and a single poisoned ticket can turn one into an attacker. Logs and dashboards only show this after the secret is gone. Tripwire sits in front of every tool call. It holds and denies a risky action *before* it runs, trips on decoy secrets, traces the poisoned input across the fleet, and cures the fleet with a guardrail that is proven by replay and a backtest over the full fleet history in ClickHouse before a human approves it.

> Built live at the Cyberdefense Hackathon (#SFTechWeek, AWS Builder Loft SF, Oct 9 2026) by a two-person team using Claude Code. Status markers: **[built]** works in the code today · **[WIP]** in progress.

---

## What happens in the demo

1. **Prevent [built]:** we replay a recorded attack (`fixtures/secret_theft.json`: read ticket → read `/app/.env` → grep the secrets → `http_post` to `drop.example.net`) with hold mode on. The send is **held and denied before it runs** (`reason=hold_rule`), and the agent is quarantined. Its next action returns `denied`.
2. **Trip [built]:** in the `honeytoken_exfil` replay, the agent base64-encodes a fake `.env` that holds decoy credentials. The checkpoint scans the outbound payload (raw and base64) and denies the send at once, with no model call.
3. **Trace [checkpoint side built, outbreak detector WIP]:** patient zero is the untrusted input (`tainted_by`). `POST /incidents/{id}/outbreak` puts the other exposed agents into *heightened* mode and pushes the attacker host to a fleet-wide denylist. The detector that computes the outbreak is in progress.
4. **Cure with proof [built]:** a candidate guardrail has to pass four gates: `replay_refused` (the incident replayed in an isolated sandbox is denied), `normal_ops_ok` (normal ops are all still allowed), `backtest` (one query over the whole `events` table on ClickHouse Cloud) and `policy_lint`. Only then can a human click **Approve**. The policy version goes up by one, the agent is restored, and the attacker host stays denylisted fleet-wide.
5. **Numbers [built / WIP]:** the Evidence tab shows hold latency, backtest time and rows scanned, the audit-chain check and the row count, each with its receipt (query ms + rows read). Precision/recall and AkashML-vs-OpenAI cost are WIP (to be measured).

---

## Architecture

```
 replay (fixtures/*.json)  [built] ─┐
 deploy-bot / support-bot  [WIP]   ─┼─ POST /tool ─▶ CHECKPOINT (FastAPI) [built] ─ SSE /stream ─▶ React console [built]
 Guild-hosted worker       [WIP]   ─┘                  honeytoken scan · hold mode · policy
                                                        quarantine / restore · proven cure
                                                                  │  ▲
                                                     writes + SQL ▼  │
                                         ClickHouse Cloud [built]: tripwire.events (~30M rows)
 detector [WIP]      funnel SQL every 1 s ─▶ POST /block, /alerts, /incidents/{id}/outbreak
 investigator [WIP]  read-only CH user    ─▶ PUT /incidents/{id}/report
 classify() on AkashML [WIP] ◀─ called by hold mode (built-in rule, labelled rule_only, until then)
```

| Component | Code | Runs where | Status |
|---|---|---|---|
| Checkpoint API + SSE | `checkpoint/` (FastAPI) | laptop, `127.0.0.1:8000` (LAN with `PUBLIC=1` + `X-Tripwire-Token`) | built |
| Event store | `data/schema.sql`, `tripwire/ch.py` | ClickHouse Cloud, us-west-2, 2 replicas (local Docker 25.8 for dev) | built |
| Live console | `web/` (React + Vite + TS) | served by FastAPI from `web/dist`, or Vite `:5173` in dev | built |
| Replay scenarios | `fixtures/*.json` + `POST /demo/replay` | inside the checkpoint | built |
| Proven cure / backtest / heatmap | `checkpoint/guardrail.py`, `checkpoint/fleet.py` | checkpoint → ClickHouse Cloud | built |
| Simulated agents (record-only tools) | `agents/` | laptop | WIP |
| Detector (ms `windowFunnel`, watermarks) | `detection/` | laptop, polls every 1 s | WIP |
| Quick check, two-model quorum, investigator | `ai/` | AkashML API | WIP |
| Guild worker + responder | `guild/` | Guild workspace `bindubhargavareddy~tripwire` | WIP |
| Agent-security ruleset | `semgrep/` | Semgrep CLI | WIP |

---

## How each sponsor is used

| Sponsor | What runs today | Status |
|---|---|---|
| **ClickHouse** | ClickHouse Cloud stores every tool call in `tripwire.events`: a MergeTree ordered by `(agent_id, ts)` with a bloom-filter index on `target`. ClickHouse also does four jobs in the request path and the console. (1) Hold mode looks up the agent's history to spot a destination it has never used (800 ms budget). (2) A policy backtest scans the **full** table. (3) The fleet heatmap aggregates per agent per hour. (4) `/audit/verify` checks the per-agent hash chain. Reads see their own writes across the 2 replicas. | built |
| | The detector runs `windowFunnel` in milliseconds over the stream every 1 s. | WIP |
| **AkashML / Akash** | Models are chosen and timed on AkashML (see results). Hold mode already calls `classify()` with a 3 s timeout. Until the AkashML-backed version lands, a built-in rule makes the call, and its verdicts are labelled `decision_source=rule_only`. Still to come: the two-model quorum and the AkashML-vs-OpenAI cost per 1,000 events. | WIP |
| **Guild** | `POST /guild/run` starts a session for the agent installed in workspace `bindubhargavareddy~tripwire`, following docs.guild.ai: a chat session with an account key, falling back to `api_trigger`, and it returns `session_url`. The workspace exists, but the worker agent is not published yet. The Guild "Responder" with a human approval step is also not built yet. | WIP |
| **Semgrep** | Planned: a custom agent-security ruleset plus `semgrep/FINDINGS.md` (real findings only), with runtime events linked to source code via `code_ref`. Nothing is committed yet. | WIP |
| **Pi (Most Innovative)** | One loop: **prevent → trip → trace → cure-with-proof**. Hold-before-run, honeytokens, quarantine and the proven cure are built in the checkpoint. Outbreak detection is in progress. | built / WIP |

---

## Measured results

Every number here comes from a receipt. Synthetic background data is labelled `synthetic=1`. "—" means not measured yet. All values were measured on 2026-10-09.

| Metric | Value | Where measured | Source (receipt) |
|---|---|---|---|
| Synthetic background load | 30,000,000 rows in **110.8 s** (≈270.8k rows/s, 6 × 5M chunks), giving 30,000,540 rows in the table | ClickHouse Cloud (26.6) | loader receipt `var/load_summary.json` (`make load`) |
| Rows in `tripwire.events` | **30,000,570** (30,000,540 with `synthetic=1`), 42 agents | ClickHouse Cloud | read-only query, 18:22 UTC |
| Policy backtest over full history | **30,000,550 events in 683 ms** | ClickHouse Cloud | `status/bindu.md` 11:20 (proven-cure rehearsal) |
| Policy backtest, re-run | 30,000,570 rows, median **650 ms** (3 runs, 646–652 ms) | ClickHouse Cloud | `checkpoint/fleet.backtest_history`, 18:22 UTC |
| Policy backtest, local | 30M events in **194 ms** | local Docker | commit `e464e22` |
| Hold decision (send held + denied) | **≈124 ms**, including a 113 ms ClickHouse history lookup | ClickHouse Cloud (~37 ms RTT from the venue) | `status/bindu.md` 11:20 |
| Hold decision, local | ~84 ms, including the ClickHouse lookup | local Docker | commit `e464e22` |
| Honeytoken trip | denied at the send step, **0 ms**, no model | ClickHouse Cloud run | `status/bindu.md` 11:20 |
| False positives on `normal_ops` (hold ON) | **10/10 allowed** | ClickHouse Cloud run | `status/bindu.md` 11:20 |
| Proven-cure gates | **4/4 passed** (incl. `normal_ops_ok`), then approve sets policy v2 | ClickHouse Cloud run | `status/bindu.md` 11:20 |
| Fleet heatmap | 42 agents / 29.5M rows in **421 ms** | local Docker | commit `e464e22` |
| Fleet heatmap, 72 h | 42 agents / 29,674,435 rows, median **1,479 ms** (3 runs) | ClickHouse Cloud | `checkpoint/fleet.heatmap`, 18:22 UTC |
| AkashML JSON verdict, 1 sample each | Llama-3.3-70B 1.2 s · gpt-oss-120b 1.2 s · gpt-oss-20b 4.9 s (over the 3 s hold budget) | AkashML API | `status/bindu.md` (stamped 11:25) |
| Tests | `make check` **82 passed** (73 unit + 9 integration, local ClickHouse); e2e **13 passed, 3 skipped** on real ClickHouse — the 3 skips are the detector (hold-off) path, pending its merge | laptop | `make check` at `68a8e8b`; `pytest -m e2e` incl. the core-gate acceptance suite run ×3 |
| Precision / recall, time-to-detect (hold OFF), AkashML vs OpenAI cost per 1k events | — | — | to be measured (`/evidence`) |

The live values are always at `GET /evidence` and on the console's Evidence tab.

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

The console runs at http://localhost:5173 in dev. For a single-port build, run `cd web && npm run build` and open http://localhost:8000.

**Switch to ClickHouse Cloud:** set `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_SECURE=1`, `CLICKHOUSE_USER/PASSWORD` and `CLICKHOUSE_RO_USER/RO_PASSWORD` in `.env`. Never commit `.env`. Allowlist your egress IP in the Cloud console, then run `make db` to apply the schema and the read-only user. After that, `make seed` / `make load` work as above.

**Demo controls** (Live tab buttons, or the keyboard when no input field has focus):

| Key | Action |
|---|---|
| `R` | Replay the recorded attack (`POST /demo/replay`) |
| `X` | Restore the most recently quarantined agent |
| `H` | Toggle hold mode |
| `0` | Reset the demo to green (`POST /demo/reset`) |
| `P` | Presenter mode |

Other scenarios: `POST /demo/replay {"scenario": "honeytoken_exfil" | "normal_ops"}`.

---

## Honesty & safety

- **Record-only tools.** No real shell, file or network side effects. `drop.example.net` is never contacted, and outbound payloads are scanned but never stored raw.
- **No real secrets.** Honeytokens are synthetic decoys (`TRIPWIRE_HONEYTOKENS`, e.g. `AKIA-TRIPWIRE-DECOY-7Q2`), and the stolen `.env` is fake. Real keys live only in a gitignored `.env`.
- **`decision_source` is always shown** on every verdict and badge: `policy`, `honeytoken`, `rule_only`, `akashml`, `quorum` or `openai` (fallback). A rule is never presented as a model.
- **Synthetic data is labelled** `synthetic=1`. Numbers come only from receipts. Anything not measured shows "—".
- **AI-written code provenance.** All code was written live at the event with Claude Code. The git history is the proof, and each commit is co-authored by Claude. The session transcript is committed to `provenance/` at feature freeze.

---

## Team

- **Bindu**: Platform & Experience (checkpoint, ClickHouse, live console)
- **Sripadha**: Agents & Intelligence (agents, detection, AkashML, Guild, Semgrep)

The full build plan and frozen contract are in [`00_MASTER_PLAN.md`](00_MASTER_PLAN.md). The original team playbook README is in [`docs/PLAYBOOK_README.md`](docs/PLAYBOOK_README.md).

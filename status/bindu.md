# Status — Bindu (Platform & Experience)

Append one line per update. **Only Bindu edits this file.** Sripadha reads it. This is how the two sessions stay in sync between the verbal gates.

Format: `HH:MM · <what changed / what's on main / what I need> [BLOCKED on …]`
For a seam change use a `CONTRACT CHANGE REQUEST:` line and confirm in person before editing a frozen file.

## Checkpoints to post (see master §10)
- [ ] 9:45 · **CONTRACT FROZEN, pushed** — Sripadha can clone
- [ ] 10:00 · H1 — checkpoint up (`/tool`, `/health`, `/status`, `/stream`) at `<url>`
- [ ] 10:30 · H2 — `/block` `/alerts` `/restore` + SSE live
- [ ] 11:15 · MVP — UI on live data
- [ ] 12:15 · CORE GATE passed ×3 / numbers + 30M load done
- [ ] 2:30 · SIGNATURE — hold/policy/guardrail + outbreak/evidence UI
- [ ] 3:30 · FREEZE — vite build served, e2e green

## Log
- (add entries below)
- 10:15 · **CONTRACT FROZEN** — local commit `88b0f22` on `main` (push pending: waiting for GitHub repo URL). Build against `tripwire/contracts.py` + `data/schema.sql`. **Read master §7a** for the additions pinned at freeze: `ToolCall.payload`, `Heartbeat`, `ReportPayload`, `Policy`, `Scenario`/`ReplayStep` (fixture format), `StatusResponse.verdict_keys` (`"agent|rule|last_step_ts_ms"`), new `POST /incidents/{id}/outbreak`, honeytokens via env `TRIPWIRE_HONEYTOKENS`. classify() lives at repo-root `ai/quick_check.py` (master §6 fixed). Run from repo root with `uv run`; `make setup` then `make up` (Docker ClickHouse + schema).
- 10:20 · ClickHouse up locally (Docker 25.8): `tripwire.events` + read-only user `tripwire_ro` applied. Core build running (checkpoint, web+mock, data in parallel). Sripadha: you can develop detection SQL against your own `make up` meanwhile.
- 10:58 · **H1 + H2 READY on `main` (`4ad0ca3`).** `git pull origin main && make setup && make up && make dev` → checkpoint on 127.0.0.1:8000 (UI at http://localhost:8000 once `cd web && npm run build`, or Vite on :5173). Live: /tool, /block (409 dedup), /alerts, /restore (watermark), /status, /stream, /heartbeat, /incidents(+report, +outbreak), /demo/replay, /demo/reset, /evidence, /audit/verify. Smoke-tested: block → next call denied → restore → ok; honeytoken (raw/base64) → denied + incident.
- 10:58 · **Read master §7b (CCRs resolved):** set `agent_id` on every POST /alerts; use `await async_client()` (thread-backed, no aiohttp) or `client()`; `ro_client()` works now — **your sqlguard must inject LIMIT 200**; `make db` to update your local RO user. `/demo/replay` + the UI's Replay button need **`fixtures/secret_theft.json`** (contracts.Scenario) — please land that first; it's the MVP blocker on my side.
- 10:58 · Next (Bindu): 30M rows, hold mode (S1) real `decide()`, policy backtest + proven cure (S4), outbreak/quorum/cure UI.
- 11:25 · **AkashML key works** (Bindu has it in `.env`; get it from Bindu directly — never in git/chat). `/v1/models` lists 7: Qwen/Qwen3.6-35B-A3B, openai/gpt-oss-120b, meta-llama/Llama-3.3-70B-Instruct, openai/gpt-oss-20b, Qwen/Qwen3.8-27B, zai-org/GLM-5.3, moonshotai/Kimi-K3.
- 11:25 · **Measured one JSON-verdict call each (single samples, re-measure):** Llama-3.3-70B **1.2 s**, clean JSON, 31 output tokens · gpt-oss-120b **1.2 s** but reasoning eats tokens — needs `max_tokens` ≥ 400 or the JSON is cut off · Qwen3.6-35B-A3B 1.8 s, response had raw control chars (parse defensively) · **gpt-oss-20b 4.9 s — too slow for the 3 s hold budget.** Suggest `AKASHML_MODEL_SMALL=meta-llama/Llama-3.3-70B-Instruct` (hot path) and `AKASHML_MODEL_LARGE=openai/gpt-oss-120b` (investigator + 2nd quorum family).
- 11:25 · ClickHouse Cloud (us-west-2, 2 replicas, ~37 ms RTT) is created; schema applied. Venue Wi-Fi uses 2 egress IPs → Bindu is adding both to the Cloud allowlist, then loading 30M rows there. Until then keep using local Docker.
- 11:20 · (my earlier "11:25" lines were mis-stamped; real time was ~11:05.) **MVP (Bindu side) ✅ on ClickHouse Cloud.** `main` @ `d91c3da` = my signature work + **your fixtures merged**. Your CCR is **accepted**: `DecisionSource` now includes `"openai"` (contracts.py + UI badge "OpenAI model (fallback)").
- 11:20 · Live rehearsal on Cloud with your fixtures, hold ON: `secret_theft` passed (send denied at the hold step `hold_rule`, deploy-bot quarantined, step 5 denied) · proven cure: 4/4 gates incl. `normal_ops_ok`, backtest **30,000,550 events in 683 ms**, approve → policy v2 · `honeytoken_exfil` passed (tripped at send, 0 ms) · `normal_ops` **10/10 ok** (no false positives). Hold decision ≈124 ms on Cloud incl. a 113 ms history lookup.
- 11:20 · **Behavior to know (policy is always on, even with hold OFF):** `assume_role` / `disable_logging` are always denied (`reason=hold_policy`, non-blocking alert, no quarantine); any http to a denylisted host is denied + quarantined. Your role_grab/log_tamper rules will see these as `result='denied'` rows — still detectable, just already stopped.
- 11:20 · **Shared DB = ClickHouse Cloud** (us-west-2, 2 replicas; schema + RO user + 30M rows loaded). Get the `CLICKHOUSE_*` values from Bindu directly (not git/chat) and put them in your `.env`; `tripwire.ch.client()` adds read-after-write consistency on Cloud automatically. The venue's two egress IPs are allowlisted.
- 11:20 · **Guild:** workspace `bindubhargavareddy~tripwire` exists with **0 agents** — publish your worker there. `POST /guild/run` auto-picks the first installed agent and starts a session per docs.guild.ai (account key → `chat`, falls back to `api_trigger`); returns `session_url`. Bindu has the key.
- 11:20 · **Next joint gate 12:15 (core):** needs your detector posting `/block` for the hold-OFF path + real `classify()` (`HOLD_CHECK=real`). I'm ready to integrate whenever you push.

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

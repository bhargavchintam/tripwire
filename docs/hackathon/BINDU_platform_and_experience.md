# Bindu — Platform & Experience

You own the **checkpoint backend, ClickHouse, and the whole React UI**. You make Tripwire real and make it look undeniable on screen. Read `00_MASTER_PLAN.md` first; this file never overrides it.

> Tell your Claude session: "I am Bindu." It should already have read `CLAUDE.md` and the master plan.

## Your directories (edit ONLY these — see master §3)
`checkpoint/` · `data/` · `web/` · `tripwire/config.py` · `tripwire/ch.py` · `tripwire/mock_server.py` · `tests/unit/test_state.py` · `tests/unit/test_hold.py` · `tests/integration/test_schema.py` · root infra (`Procfile`, `docker-compose.yml`, `Makefile`, `pyproject.toml`, `README.md`) · and the **shared frozen files** you own: `tripwire/contracts.py`, `data/schema.sql`, `.env.example`, `requirements.txt`, `tests/e2e/`.

You never edit `agents/`, `detection/`, `ai/`, `eval/`, `fixtures/`, `guild/`, `semgrep/`. If you think you need to, that's a seam — raise a `CONTRACT CHANGE REQUEST` in `status/bindu.md`.

## How you stay unblocked
- Agents not ready? Test `/tool` with `curl` and `tests/`.
- Sripadha's `classify()` not ready? Run hold mode with `HOLD_CHECK=stub` (master §6).
- Live data not ready? Build the UI against **your own mock** (`tripwire/mock_server.py`) which emits contract-shaped SSE + REST on `:8001`. Flip the UI's base URL to `:8000` at 11:15.

## Build order

### Block 1 — 9:30–9:45 · Create the repo + FREEZE the contract (you set the pace for both)
1. `git init` here; create the dir skeleton with `.gitkeep`s (master §3).
2. Write the **frozen** files exactly per master §4–8: `tripwire/contracts.py`, `data/schema.sql`, `data/readonly_user.sql`, `.env.example`, `requirements.txt`, `docker-compose.yml` (ClickHouse), `Procfile` (honcho: checkpoint, detector, ui), `Makefile`, `pyproject.toml`.
3. `uv venv && uv pip install -r requirements.txt`; `docker compose up -d clickhouse` with `CLICKHOUSE_PASSWORD` + `CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1`; apply `schema.sql` + the read-only user.
4. Commit, push, tell Sripadha in `status/bindu.md`: **"CONTRACT FROZEN, pushed."**
- **Gate:** both of you have the contract. Nothing else starts first.

### Block 2 — 9:45–10:00 · Checkpoint skeleton → H1
- `checkpoint/app.py`: `GET /health`, `POST /tool` (assign monotonic `ts`, derive `is_external`, write via `writer.py`, return `ToolResult`), `GET /status`, `GET /stream` (SSE via `bus.py`).
- `checkpoint/writer.py`: batched async insert every ~200 ms (or `async_insert` busy-timeout ≤200 ms) so the detector sees rows fast.
- `checkpoint/state.py`: `blocked` set, `open_incidents`, per-agent `watermark`, `verdict_cache` keyed `(agent,rule,last_step_ts)`, `recent_alerts`, per-agent `asyncio.Lock`, agent modes (normal/heightened/quarantined). Persist to `var/state.json`.
- **Publish at 10:00. H1 gate:** Sripadha's agent POSTs `/tool` and the row is in ClickHouse with the assigned `ts` (add `tests/unit/test_state.py` for monotonic ts).

### Block 3 — 10:00–10:30 · Containment → H2
- `POST /block/{agent_id}` (idempotent; **409** on stale per master §4), `POST /alerts`, `POST /restore/{agent_id}` (clear block, close incidents, advance watermark, clear ring buffer), `POST /heartbeat`, `GET /alerts`.
- Blocked agents' `/tool` returns `denied`; log the denial.
- Emit SSE `tool_event`, `agent_state`, `incident`, `alert` on each change.
- **Publish at 10:30. H2 gate:** `curl` a block → the next `/tool` returns `denied`, visible in ClickHouse and in the SSE stream.

### Block 4 — 10:30–11:15 · UI core on live data → MVP
- `web/`: Vite + React + TS, Tailwind v4, shadcn/ui, lucide, motion, `@tanstack/react-query`, native `EventSource`. Pin versions (released ≥2 weeks ago). If Node 26 fights Vite/shadcn, use Node 22.
- Live tab: agent cards (ACTIVE→HELD→QUARANTINED with a Motion transition + DENIED stamp), live event stream (virtualized), incident feed with `decision_source` badges, controls (Replay, Restore, Reset, Hold toggle).
- Point the UI at `:8000`; keep the mock on `:8001` as fallback.
- **MVP gate (joint 11:15):** Sripadha's replay → your detect path → block → `denied`, all visible in the UI.

### Block 5 — 11:15–12:15 · Make it reliable + measured → CORE GATE
- KPI strip (animated counters + sparkline): events stored, detection p50/p95, time to detect, time to contain, hold decision ms, cost per 1,000 events. Each tile shows "—" until a real number exists.
- `tripwire/cli.py`-style data ops you own under `data/`: `seed_live_agents.sql`, `background_data.sql`. Run the **30M load in 5M chunks**, timestamps 10 min–3 days old; finish by ~11:45 so merges settle. (Sripadha triggers nothing here; this is your data.)
- `GET /evidence` returns the `EvidenceBundle` (master §7) from `system.query_log` + state. **Never invent a number.**
- Demo reset endpoint (`/demo/reset`) → status green.
- **CORE GATE (joint 12:15):** run the acceptance test (master §12) ×3 from the UI.

### Block 6 — 12:15–2:30 (break for lunch 1:30–2:00) · Signature backend + UI → SIGNATURE
- `checkpoint/hold.py` (master §1/§6): under the per-agent lock, on high-risk actions do ring-buffer prefix check + allowlist + ClickHouse history lookup; if suspicious call `classify()` (3 s timeout); malicious→deny+block+incident, benign→allow+"flagged→benign", timeout→deny if prefix matched (`rule_only`). Count a held third step as a funnel step.
- `checkpoint/policy.py` + `GET/PUT /policy`, `POST /policy/backtest`, `POST /policy/copilot`.
- `checkpoint/guardrail.py` + `/guardrail/{id}/prove` and `/approve` (replay refused + normal_ops ok + backtest).
- `checkpoint/auth.py` token header when `PUBLIC=1`. Hash chain in `writer.py`; `GET /audit/verify/{agent_id}`.
- UI: incident Sheet (timeline, investigator report via react-markdown, SQL receipts, proven-cure panel with Approve), Outbreak panel, quorum chips, Evidence tab (confusion matrix, latency histograms via recharts, cost table), Policy tab, Sponsors tab, Fleet heatmap, presenter mode + ⌘K + shortcuts (R/X/H/0), sonner toasts.
- **SIGNATURE gate (joint 2:30):** prevent→trip→trace→cure demo runs twice.

### Block 7 — 2:30–4:15 · Depth, freeze (3:30), serve, submit
- `vite build` served by FastAPI at `:8000` (one URL for the demo). Final `tests/e2e/` green.
- README: architecture, the master's sponsor table, and **numbers pulled from `/evidence`** (not typed by hand). Screenshots of ACTIVE/QUARANTINED/DENIED.
- Help Sripadha record; verify no MOCK banner is on screen; submit by 4:15.

## Interfaces you produce (others depend on these — keep them stable)
- Every endpoint in master §4 and every SSE type.
- `data/schema.sql` columns exactly as master §5.
- `tripwire/contracts.py` models exactly as master §7.

## Interfaces you consume
- `classify(QuickCheckInput) -> Verdict` from `ai/quick_check.py` (stub until integration via `HOLD_CHECK`).
- The investigator PUTs its report to `PUT /incidents/{id}/report` — you just store + emit `report_ready`.

## Definition of done
A judge watches an agent go **ACTIVE → HELD/QUARANTINED → DENIED** on your UI, opens the incident to see the report + SQL receipts, sees the guardrail proven and approved, and every number on screen comes from `/evidence`. It runs three times without a code edit.

## Your cut order (if you're behind)
Fleet heatmap → audit badge → policy copilot → presenter polish → Evidence charts (keep the numbers as text). **Never cut** the core loop UI, the three-state transition, or honest numbers.

## Communication
Append progress/blockers to `status/bindu.md` at each checkpoint (template in that file). Read `status/sripadha.md` before you integrate. Contract changes: write a `CONTRACT CHANGE REQUEST` and confirm with Sripadha in person before editing a frozen file.

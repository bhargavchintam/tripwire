# Tripwire — Master Plan (shared source of truth)

**Event:** Cyberdefense Hackathon #SFTechWeek · AWS Builder Loft, SF · Fri Oct 9 2026
**Build window:** 9:30 AM–4:30 PM PT (build from doors) · feature freeze 3:30 · **submit by 4:15**
**Team (2):** Bindu (Platform & Experience) · Sripadha (Agents & Intelligence)
**One line:** Your agents have the keys to production. Tripwire is their immune system — it decides on risky actions *before* they run, trips on decoy secrets, traces a poisoned input across the fleet, and cures the fleet with a fix proven by replay.

This file is the contract both sessions obey. Personal task files (`BINDU_*.md`, `SRIPADHA_*.md`) tell each of you *what to build and when*; this file defines *the seams between you* so your work merges cleanly. **Do not restate the contract in other files — reference section numbers here.**

---

## 1. The product (what we demo)

Two simulated agents (`deploy-bot`, `support-bot`) do record-only tool work through a **checkpoint**. Every action is logged to **ClickHouse**. When an agent behaves like an attacker (reads a secret → encodes it → tries to send it out), Tripwire:

1. **Prevents** — in *hold mode*, the risky send waits for a verdict and is denied before it runs (the secret never leaves).
2. **Trips** — decoy *honeytokens* make exfiltration attempts obvious with zero model cost.
3. **Contains** — the agent is quarantined; its next action returns `denied`.
4. **Traces** — the poisoned input is found (patient zero), every other agent that read it is put on heightened watch, and the attacker's destination is pushed fleet-wide as known-bad.
5. **Cures** — a candidate guardrail is replayed against the real incident and backtested over fleet history in ClickHouse; a human approves; the fix applies to every agent.
6. **Explains** — an investigator writes the incident report with its own SQL receipts.

Every claim on screen carries a measured number and an SQL/receipt source.

---

## 2. Architecture (processes and the seams between them)

```
 Agents (S) ──POST /tool──▶  CHECKPOINT (B)  ──SSE /stream──▶  React UI (B)
 Replay (S) ──────────────▶   FastAPI :8000        ▲
                              • state, blocklist    │ GET /status, POST /block,
 Detector (S) ───────────────• hold mode ──────────┘ /alerts, /heartbeat, PUT report
   every 1s   reads CH + /status, posts verdicts        │
 Investigator (S) ─ read-only CH ─ PUT /incidents/{id}/report
 Guild proxy (S) ─ forwards ─▶ /tool

 ClickHouse (schema=B, queries=S)   AkashML / OpenAI (S)
```

**The seams (this is the whole merge-safety design):**

| Seam | Form | Owner serves | Consumer |
|---|---|---|---|
| Checkpoint HTTP API (§4) | REST + SSE | Bindu | Sripadha's agents/detector/investigator/proxy; Bindu's UI |
| `events` table (§5) | ClickHouse schema | Bindu (`data/schema.sql`) | Sripadha's SQL |
| `classify()` quick-check | **in-process** Python fn (§6) | Sripadha (`ai/quick_check.py`) | Bindu's hold mode imports it |
| Pydantic models + SSE types | `tripwire/contracts.py` | Bindu | both import |

Everything else talks over **HTTP**, not shared memory. The only cross-person *import* is `classify()`, and Bindu has a stub for it (§6) so she is never blocked. Because each seam is a frozen interface, each of you codes to the interface and stubs the other side until integration.

---

## 3. File & directory ownership (the merge-safety rule)

**Golden rule: you only ever create or edit files inside YOUR directories.** Git then never sees you both touching the same file, so merges are clean. Shared files (below) are Bindu's and frozen early.

### Bindu owns (Platform & Experience)
```
checkpoint/           app.py state.py writer.py hold.py policy.py bus.py auth.py guardrail.py
data/                 schema.sql readonly_user.sql seed_live_agents.sql background_data.sql
web/                  (entire React app)
tripwire/config.py  tripwire/ch.py  tripwire/mock_server.py
tests/unit/test_state.py  tests/unit/test_hold.py  tests/integration/test_schema.py
infra (root):         Procfile  docker-compose.yml  Makefile  pyproject.toml  README.md
```

### Sripadha owns (Agents & Intelligence)
```
agents/               deploy_bot.py support_bot.py fake_tools.py replay.py honeytokens.py live_agent.py
detection/            loop.py metrics.py outbreak.py risk.py  sql/*.sql
ai/                   llm.py quick_check.py quorum.py investigator.py sqlguard.py copilot.py  prompts/
eval/                 runner.py
fixtures/             secret_theft.json normal_ops.json  eval/attack/*.json  eval/benign/*.json
guild/                worker.ts responder.ts openapi.yaml  + tripwire/guild_proxy.py
semgrep/              rules/*.yaml  FINDINGS.md
tests/unit/test_quorum.py  test_sqlguard.py  test_outbreak.py  tests/integration/test_funnel.py  tests/fakes/fake_llm.py
```

### Shared — Bindu owns the file, FROZEN after 9:45, change only via a CONTRACT CHANGE REQUEST
```
tripwire/contracts.py   data/schema.sql   .env.example   requirements.txt
tests/e2e/              (Bindu owns; both contribute scenarios; edited only during joint integration windows)
```

If you ever feel you need to edit the other person's file, stop — you've hit a seam. Put a `CONTRACT CHANGE REQUEST` in your status file instead.

---

## 4. Checkpoint HTTP API (frozen 9:45)

All times in payloads are **epoch milliseconds (Int64)**. `ts` is assigned by the checkpoint, monotonic per agent (`max(now_ms, last+1)`). Mutating endpoints require header `X-Tripwire-Token` when `PUBLIC=1`.

| Method · Path | Who calls | Purpose |
|---|---|---|
| `POST /tool` | agents, replay, guild proxy | Evaluate + record a tool call → `ToolResult`. Runs hold mode. |
| `GET /health` | both | Liveness + DB check |
| `GET /status` | detector, UI | active/blocked agents, open incidents, per-agent watermarks, hold flag, policy version |
| `POST /block/{agent_id}` | detector | Quarantine + record alert. **Idempotent. Returns 409** if `last_step_ts ≤ watermark[agent]` or that `(agent,rule,last_step_ts)` already has a verdict. |
| `POST /alerts` | detector | Record a non-blocking verdict (flagged→benign) |
| `GET /alerts` | UI | Recent alerts from checkpoint state |
| `POST /restore/{agent_id}` | UI | Clear block, close incidents, **advance watermark**, clear that agent's ring buffer |
| `GET /stream` | UI | Server-Sent Events (§ event types below) |
| `POST /heartbeat` | detector | Push query timings/metrics for the UI |
| `POST /config/hold` | UI | Toggle hold mode on/off |
| `GET /policy` · `PUT /policy` | UI | Read / set policy (allowlists, high-risk actions) |
| `POST /policy/backtest` | UI, guardrail | Run a candidate policy over history → counts |
| `POST /policy/copilot` | UI | Plain-English rule → validated policy preview (never auto-applied) |
| `GET /incidents` · `GET /incidents/{id}` | UI | Incident list / detail (timeline, outbreak, receipts) |
| `PUT /incidents/{id}/report` | investigator | Attach the investigator's markdown report + receipts |
| `POST /guardrail/{id}/prove` | UI | Run the cure gates (replay refused + normal ops ok + backtest) |
| `POST /guardrail/{id}/approve` | UI | Apply the approved policy, restore the agent |
| `GET /audit/verify/{agent_id}` | UI | Verify the hash chain → intact/broken |
| `GET /evidence` | UI, README | All measured numbers in one JSON |
| `POST /guild/run` | UI | Kick the Guild-hosted agent via its trigger |

**SSE event envelope:** `{seq:int, type:str, ts_ms:int, data:object}`.
**Types:** `snapshot` (full state on connect), `tool_event`, `agent_state`, `incident`, `alert`, `outbreak`, `quorum`, `backtest`, `guardrail`, `report_ready`, `metrics`.

The detector/investigator never emit SSE directly — they call checkpoint endpoints, and the checkpoint emits the SSE. One writer of SSE = Bindu's checkpoint.

---

## 5. `events` table (frozen 9:45) — `data/schema.sql`, Bindu creates

All columns exist from the start so there is **no schema change mid-event**. Sripadha ignores the columns he doesn't use (`hash`, `prev_hash`).

```sql
CREATE TABLE events (
  ts             DateTime64(3,'UTC'),          -- server-assigned, per-agent monotonic
  agent_id       LowCardinality(String),
  action         LowCardinality(String),
  target         String,
  bytes          UInt32 DEFAULT 0,
  is_external    UInt8  DEFAULT 0,             -- derived server-side from an internal-host list
  result         LowCardinality(String),       -- 'ok' | 'denied' | 'error'
  reason         LowCardinality(String) DEFAULT '', -- '' | blocked | hold_policy | hold_model | hold_rule | honeytoken
  honeytoken_hit UInt8  DEFAULT 0,
  tainted_by     String DEFAULT '',            -- id of the untrusted input that preceded this (outbreak tracing)
  code_ref       String DEFAULT '',            -- tool file:line, for the Semgrep link
  session_id     String DEFAULT '',
  synthetic      UInt8  DEFAULT 0,             -- 1 for background/seed rows
  prev_hash      String DEFAULT '',            -- per-agent hash chain
  hash           String DEFAULT '',
  INDEX bf_target target TYPE bloom_filter GRANULARITY 4
) ENGINE = MergeTree
PARTITION BY toStartOfHour(ts)
ORDER BY (agent_id, ts);
```

Core actions: `read_file`, `run_command`, `http_post`, `http_get`, `list_permissions`, `assume_role`, `disable_logging`.
Live agents: `deploy-bot`, `support-bot` (+ `guild:deploy-bot` for the Guild demo). Synthetic agents: `agent-NN`.

**Detection SQL uses milliseconds** so step order never collapses:
`windowFunnel(60000)(toUInt64(toUnixTimestamp64Milli(ts)), …)`. Per-agent watermark via `transform(agent_id, {ids:Array(String)}, {wms:Array(Int64)}, toInt64(0))`.

---

## 6. The one in-process seam: `classify()` (frozen 9:45)

`ai/quick_check.py` at the repo root (Sripadha; Bindu imports it as `from ai.quick_check import classify`) exposes:

```python
async def classify(inp: QuickCheckInput) -> Verdict: ...   # types in contracts.py
```

Bindu's hold mode (`checkpoint/hold.py`) imports it. Until Sripadha's real one lands, Bindu runs a **stub** selected by env `HOLD_CHECK=stub|real` (stub = "malicious if the funnel prefix matched, else benign", `decision_source='rule_only'`). Integration = flipping the flag. Neither of you is ever blocked by the other.

---

## 7. `tripwire/contracts.py` (frozen 9:45) — Bindu creates, both import

Pydantic v2 models. Field names are the contract; don't rename after freeze.

- `ToolCall`      {agent_id, action, target, bytes=0, session_id='', tainted_by='', code_ref=''}
- `ToolResult`    {agent_id, result, reason='', incident_id=None}
- `Verdict`       {verdict:'malicious'|'benign'|'uncertain', confidence:float, reason:str, decision_source:'akashml'|'rule_only'|'quorum', model_ids:list[str]=[]}
- `QuickCheckInput` {agent_id, rule, events:list[dict], context:str}
- `AlertPayload`  {rule, verdict, confidence, reason, decision_source, detected_at_ms:int, last_step_ts_ms:int, model_ids:list[str]=[]}
- `StatusResponse`{active:list, blocked:list, open_incidents:list, watermarks:dict, hold_enabled:bool, policy_version:int}
- `Incident`      {id, agent_id, rule, opened_ms, steps:list, verdict:Verdict, outbreak:Outbreak|None, contained_ms:int|None, report_md:str|None}
- `Outbreak`      {source_id:str, exposed_agents:list[str], blocked_destinations:list[str]}
- `StreamEvent`   {seq:int, type:str, ts_ms:int, data:dict}
- `EvidenceBundle`{events_stored:int, query_p50_ms, query_p95_ms, time_to_detect_ms, time_to_contain_ms, hold_decision_ms, precision, recall, n_cases:int, cost_akashml, cost_openai, priced_on:str}

---

### 7a. Additions pinned at the freeze (10:15, Bindu) — `contracts.py` is authoritative

The models above are a summary; the code in `tripwire/contracts.py` is the contract. Added at freeze time so nobody has to change it later:
- `ToolCall.payload` — outbound body for `http_post`, scanned for honeytokens, **never stored raw**. `ToolResult.ts_ms`.
- `DecisionSource` also allows `honeytoken` and `policy` (decisions made without a model).
- `Verdict.latency_ms/tokens_in/tokens_out` (for latency + cost evidence).
- New models: `Heartbeat` (detector/investigator/eval → `/heartbeat`), `ReportPayload` (→ `PUT /incidents/{id}/report`), `Policy`, `BacktestResult`, `ReplayStep` + `Scenario` (**the fixture format** for `fixtures/*.json`).
- `StatusResponse.modes` (normal/heightened/quarantined) and `verdict_keys` (`"agent|rule|last_step_ts_ms"`, for detector dedup).
- **New endpoint `POST /incidents/{id}/outbreak`** (body `Outbreak`) — the detector posts outbreak results; the checkpoint applies heightened mode + denylist and emits SSE `outbreak`.
- Optional second in-process seam: `ai/copilot.py: async def draft_policy(text: str, current: Policy) -> Policy`, imported lazily by `POST /policy/copilot` (503 until it exists).
- Honeytoken values are shared through env `TRIPWIRE_HONEYTOKENS` (comma-separated, synthetic decoys): Sripadha's fake env uses them, Bindu's checkpoint scans payloads (raw + base64) for them.
- ClickHouse: database `tripwire`, table `tripwire.events`; clients set the default database, so SQL can say `FROM events`. Env keys are `CLICKHOUSE_HOST/PORT/SECURE/DATABASE/USER/PASSWORD/RO_USER/RO_PASSWORD`. Shared clients in `tripwire/ch.py` (`client()`, `ro_client()`, `async_client()`).
- Python layout: root-level packages (`checkpoint`, `agents`, `detection`, `ai`, `eval`) + the shared `tripwire` package; run everything from the repo root via `uv run` (pytest has `pythonpath=["."]`).

## 8. Shared setup (frozen 9:45) — `.env.example` + `requirements.txt`

Bindu commits both complete at 9:30 so nobody edits dependency files mid-event (that is the one file both would otherwise fight over).

`.env.example` keys: `CLICKHOUSE_URL`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `CLICKHOUSE_RO_USER`, `CLICKHOUSE_RO_PASSWORD`, `AKASHML_API_KEY`, `AKASHML_BASE_URL=https://api.akashml.com/v1`, `AKASHML_MODEL_SMALL`, `AKASHML_MODEL_LARGE`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `CHECKPOINT_URL=http://localhost:8000`, `TRIPWIRE_TOKEN`, `PUBLIC=0`, `HOLD_CHECK=stub`, `GUILD_TRIGGER_URL`, `GUILD_TRIGGER_KEY`.

`requirements.txt` (complete, both tracks): `fastapi uvicorn[standard] pydantic>=2 pydantic-settings clickhouse-connect httpx sse-starlette openai sqlglot pyyaml typer loguru honcho pytest pytest-asyncio pytest-timeout`.

Tools (CLI, installed before kickoff): `uv`, Semgrep CLI, `ngrok`, Docker (ClickHouse image).

---

## 9. Git & merge protocol

- One repo. **Bindu** creates it at 9:30 (`git init` in this folder; the playbook files travel with it), first commit = the frozen contract + schema + `.env.example` + `requirements.txt` + empty dir skeletons with `.gitkeep`, pushes to the team GitHub repo. **Sripadha** clones after the 9:45 freeze.
- Branches: `bindu/<topic>`, `sripadha/<topic>`. Never commit straight to `main`.
- **Integrate to `main` only at checkpoints** (§10). Because ownership is directory-disjoint (§3), `git merge` is conflict-free; the only files both touch are frozen.
- Pull `main` at the start of each checkpoint before merging your branch.
- **Before merging, rebase your branch on `main` and run `make check`** (after the 11:15 MVP, also `pytest -m e2e`); merge only when green.
- Commit at every gate. End messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- If `git` ever reports a conflict, it means someone edited outside their lane or the contract changed — stop and resync in person.
- A frozen-file or seam change goes through a **Change Request** (format in `README.md`), agreed in person; the file's owner makes the edit. No silent edits.

---

## 10. Timeline & checkpoints (the handshakes)

Each checkpoint says what to **publish** (merge to `main`) and what to **wait for**. Between checkpoints, work independently against the other side's stub/mock. Confirm each joint gate out loud; record it in your status file.

| Time (PT) | Bindu publishes | Sripadha publishes | Joint gate |
|---|---|---|---|
| **9:30–9:45** | Repo + **frozen contract** (§4–8) pushed | Clone after freeze; set keys | **CONTRACT FREEZE** — nothing starts until both have it |
| **10:00** | Checkpoint running: `/tool`,`/health`,`/status`,`/stream` | `deploy-bot` + `fake_tools` POST /tool | **H1:** a real tool call lands in ClickHouse |
| **10:30** | `/block`,`/alerts`,`/restore`, SSE, in-memory state | detector loop + funnel SQL on Docker CH | **H2:** manual `curl` block → next call denied |
| **11:15** | UI on live data; demo reset | quick check wired; replay end-to-end | **MVP:** replay → detect → block → denied, on screen |
| **12:15** | perf numbers, 30M loaded, saved state | dedup/watermark verified; eval cases ready | **CORE GATE:** acceptance test ×3 (§12). If red, both fix core. |
| **1:30–2:00** | lunch + product review | lunch + product review | event lunch; go/no-go on the P2/P3 depth |
| **2:30** | hold mode, policy, guardrail endpoints, outbreak/evidence UI | quorum, investigator, outbreak, baseline, eval run | **SIGNATURE:** prevent→trip→trace→cure demo ×2 |
| **3:00** | — | Guild end-to-end (or drop) | **Guild go/no-go** |
| **3:30** | `vite build` served by FastAPI; final e2e | Semgrep scan → FINDINGS.md | **FREEZE** — bug fixes only |
| **3:30–4:15** | README numbers from `/evidence` | provenance transcript committed | Record video, **SUBMIT by 4:15** |

Async comms between checkpoints: append to `status/<yourname>.md`; read the other's before you integrate.

---

## 11. Priorities & cut order (shared)

**P0 core — never cut:** checkpoint, ClickHouse logging, secret-theft rule, blocklist + incidents + watermark, replay, proof of denial, a UI that shows it, honest numbers, the video.
**P1 signature:** hold mode (S1), honeytokens (S2), AkashML quick check, metrics + receipts, the live console.
**P2 depth:** outbreak tracing (S3), proven cure (S4), two-model quorum (D1), investigator (D2), fleet analytics/heatmap (D3), evaluation (D6), audit badge (D7).
**P3 extra:** Guild (D4), Semgrep ruleset/patch (D5/E1), policy copilot (D8), time-travel (D9), voice (E2), Akash Console (E3), MCP server (E4).

**Cut first → last:** voice → MCP → Akash Console → code patch → Guild → copilot → time-travel → audit badge → heatmap → quorum (fall back to single model) → investigator run_sql (keep predefined tools) → backtest-at-scale (keep replay proof) → outbreak → hold mode. **Core is never cut.**

---

## 12. Acceptance test (the 12:15 core gate — run 3×)

- [ ] Two agents visible; normal calls allowed.
- [ ] Replay runs the recorded sequence.
- [ ] ClickHouse detects the suspicious agent.
- [ ] A verdict is recorded (AkashML, or clearly-labeled `rule_only`).
- [ ] The agent is quarantined; its next call returns `denied`; the denial is in ClickHouse.
- [ ] The dashboard shows the incident.
- [ ] Restore allows a normal call again and does **not** re-block.
- [ ] Replay after Restore is detected and blocked again.
- [ ] The whole sequence works 3 times with no code edits between runs.

---

## 13. Sponsor coverage (every track on the critical path)

| Track | Carried by | Owner |
|---|---|---|
| **ClickHouse** (real-time analytics) | 30M rows, funnel+baseline every 1s, hot-path history lookup, backtest at scale, receipts (ms + rows) | B schema, S queries |
| **Akash** (AkashML) | every model decision on AkashML; two-model quorum; cost vs OpenAI | S |
| **Semgrep** (vuln in AI code) | Guardian during build + custom agent-security ruleset + runtime→code links; real findings only | S |
| **Guild** (host & run agents) | Guild-hosted worker governed by Tripwire + a Guild "Responder" with a human approval step | S code, B approval UI |
| **Pi** (Most Innovative) | prevent → trip → trace → cure-with-proof | both |

---

## 14. Demo (5 acts, 2:30) — the win condition

1. **Fleet (0:00):** live counter, risk gauges, query times in ms.
2. **Prevent & trip (0:25):** replay the recorded attack with hold mode on → the send is **held and denied in N ms** (the secret never left); honeytoken trips; agent turns red; next action denied.
3. **Trace (1:00):** patient zero found; the other exposed agent goes to heightened watch; its next risky action is held at once.
4. **Cure (1:30):** candidate guardrail replayed against the incident (refused) and backtested over N million events in X ms → approved in Guild.
5. **Proof (2:00):** precision/recall on N cases, detect/contain/hold times, cost per 1,000 events (AkashML vs OpenAI), audit chain intact. Sponsors tab. Close with the buyer: security & platform teams running agents in production.

Keep a recorded backup video and a **Replay** path that works without a live model.

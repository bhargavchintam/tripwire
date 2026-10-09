# Semgrep findings: Tripwire's own AI-written code

**Result (final re-scan 15:45 PT, after the fix):** 13 findings, every one triaged: **0 open true positives** — the one real finding (#1) is **fixed** — 1 intentional (dev-only), 12 false positives. We never edited a rule or the code just to make a finding disappear: #1 disappeared because both halves of the flaw were fixed.

**How #1 was fixed:** the hold-mode source was fixed in `checkpoint/hold.py` at 13:00 PT (the model's context line carries fixed checkpoint text only; the agent-chosen host reaches the model only inside the fenced JSON, with a regression test, re-verified live on AkashML). The sink was fixed by Sripadha at 13:47 PT in commit `991aed2`: `ai/quick_check.py` JSON-encodes `context` inside its own `<<<CONTEXT_JSON … >>>` fence, and `detection/loop.py` no longer puts raw event targets into the context. On the 13:58 re-scan the rule no longer fires anywhere.

**Most interesting real finding (now fixed):** text an attacker controls reached our own verdict model outside the untrusted-data fence (`ai/quick_check.py:102`, OWASP **LLM01:2025 Prompt Injection**). See §4.

## 1. What we scanned
- **Everything in this repo was written live at the event (Oct 9 2026) with Claude Code. It is AI-generated code.** That includes the checkpoint, the agents, the detector, the AkashML client, the React UI and the tests.
- **Snapshot:** branch `bindu/core` at about 13:58 PT, right after merging Sripadha's 13:48 push (outbreak, quorum, investigator + sqlguard, eval runner + pricing, the fence fix). The first scan (12:30 PT, 133 files, 12 findings) is summarised in §3's history note.
- **273 files** (final tree, 15:45 PT), listed in `semgrep/out/targets.txt`: all git-tracked files plus untracked files that are not ignored, including the final UI, the MCP server, the policy copilot and the Guild read-back. The same 13 findings as at 13:58; only line numbers moved.
- **Excluded:**
  - `.venv`, `web/node_modules`, `web/dist`, `var`, `reports`.
  - `semgrep/tests/`: our rule fixtures, which are deliberately vulnerable.
  - **`.env` and `.env.cloud`:** they hold the real keys. They are never given to Semgrep, so no secret can reach the scan output.
- Files are passed to Semgrep explicitly. Otherwise its default ignore list would silently skip `tests/`, and the tests are AI-written too.

## 2. How we scanned
| | |
|---|---|
| Tool | Semgrep CE **1.180.0** (`uv tool install semgrep`; no project dependency changed) |
| Rulesets | custom `semgrep/rules/tripwire-agent-security.yaml` (6 rules) + `p/python`, `p/secrets`, `p/typescript` (fetched online) |
| Rules | 277 loaded (271 registry + 6 custom); **268 ran** |
| Files | **273** scanned; **0 parse errors** |
| Runtime | **6.93 s wall** (23.74 s user), `/usr/bin/time -p` |
| Output | `semgrep/out/scan.json`; verdicts in `semgrep/out/triage.json` |
| Rule tests | `semgrep --test --config semgrep/rules semgrep/tests` → **6/6 pass**; 20 must-match cases (16 Python, 4 TS) and 18 must-not-match cases (14 Python, 4 TS) |

```bash
{ git ls-files; git ls-files --others --exclude-standard; } | sort -u \
  | grep -vE '^(semgrep/tests/|semgrep/out/|\.venv/|web/node_modules/|web/dist/|var/|reports/)' \
  | grep -vE '(^|/)\.env$|(^|/)\.env\.cloud$|\.gitkeep$|(^|/)\.DS_Store$' > semgrep/out/targets.txt
semgrep scan --config semgrep/rules --config p/python --config p/secrets --config p/typescript \
  --metrics=off --json -o semgrep/out/scan.json $(cat semgrep/out/targets.txt)
```

### Custom agent-security rules
| Rule id | Catches | OWASP LLM (2025) | CWE |
|---|---|---|---|
| `tripwire-llm-output-to-code-exec` (+`-ts`) | LLM output (`*.chat.completions.create`, `responses.create`, our `LLM.chat_json`) → `eval`/`exec`/`compile`/`os.system`/`subprocess`/`create_subprocess_*` (TS: `eval`/`Function`/`child_process`) | LLM05, LLM06 | CWE-94, CWE-78 |
| `tripwire-unallowlisted-outbound-http` (+`-ts`) | httpx/requests/urlopen destination from a function parameter, request body or model output, with no allowlist/`is_external`/`host_of(url) in …` guard on the path. Relative paths on a fixed `base_url` are excluded | LLM06, LLM02 | CWE-918 |
| `tripwire-untrusted-content-in-llm-prompt` | Tool/event fields (`target`, `payload`, `context`, `events`, `output`, …) in an LLM message `content`/`system=`/`instructions=` without `json.dumps` or a `*_json`/fence/escape helper | LLM01 | CWE-1427 |
| `tripwire-clickhouse-sql-string-formatting` | A non-constant value interpolated (f-string/`%`/`.format`/`+`) into SQL that reaches ClickHouse `.query`/`.command`. Not reported: `sql_str`/`sql_arr`/`int()` pieces, UPPER_CASE constants, table identifiers, `{name:Type}` parameters | LLM05 | CWE-89 |

All rules are taint-mode except the TS HTTP rule, which is pattern-based. All are intraprocedural.

## 3. Every finding, triaged
| # | Rule | File:line | Sev | OWASP | Verdict | Why |
|---|---|---|---|---|---|---|
| 1 | tripwire-untrusted-content-in-llm-prompt | ai/quick_check.py:102 | WARNING | LLM01:2025 | **True positive → FIXED** | The attacker-chosen destination host (hold mode) and raw event targets (detector context) went into the `context:` line outside the `<<<EVENTS_JSON` fence. Fixed in `checkpoint/hold.py` (b1d89b8) and `ai/quick_check.py` + `detection/loop.py` (991aed2); no longer reported. See §4. |
| 2 | python.fastapi.security.wildcard-cors (p/python) | tripwire/mock_server.py:858 | WARNING | (A05:2021) | **Intentional (dev-only)** | Correct detection, but on the MOCK checkpoint: synthetic data only, every response says `mock: true`, runs on :8001, no Makefile or Procfile target starts it. The real checkpoint allows only the localhost:5173 origins. |
| 3 | tripwire-clickhouse-sql-string-formatting | checkpoint/fleet.py:100 | WARNING | LLM05:2025 | False positive | `_raw_expr()` is a fixed fragment with its only literal escaped by `sql_str`; the rest is an int from the server clock with `int()`-clamped hours, plus the table name set in code. |
| 4 | tripwire-clickhouse-sql-string-formatting | checkpoint/fleet.py:138 | WARNING | LLM05:2025 | False positive | Same as #3: fixed `_raw_expr()`/`_not_verify()` fragments, and `since` is an int with `int()`-clamped minutes. |
| 5 | tripwire-clickhouse-sql-string-formatting | tripwire/ch.py:114 | WARNING | LLM05:2025 | False positive | `readonly_user.sql` is `.format()`-ed with the operator's own .env read-only user/password at `make db`. No agent, request or model input. Robustness note: a `'` in that password would break `CREATE USER`. |
| 6 | tripwire-unallowlisted-outbound-http | agents/checkpoint_client.py:102 | WARNING | LLM06:2025 | False positive | Client has a fixed `base_url` (CHECKPOINT_URL); every caller passes a relative "/…" path, so the host cannot change. Path caveat in §6. |
| 7 | tripwire-unallowlisted-outbound-http-ts | web/src/lib/api.ts:24 | WARNING | LLM06:2025 | False positive | `request(path)` is only called with same-origin relative literals. |
| 8 | tripwire-clickhouse-sql-string-formatting | tests/integration/conftest.py:53 | WARNING | LLM05:2025 | False positive | Test fixture: scratch table `events_test_<uuid4 hex>`. |
| 9 | tripwire-clickhouse-sql-string-formatting | tests/integration/test_data_sql.py:28 | WARNING | LLM05:2025 | False positive | Test: uuid-named scratch table. |
| 10 | tripwire-clickhouse-sql-string-formatting | tests/integration/test_data_sql.py:59 | WARNING | LLM05:2025 | False positive | Test: same scratch table. |
| 11 | tripwire-clickhouse-sql-string-formatting | tests/unit/test_app.py:458 | WARNING | LLM05:2025 | False positive | Test: `events_pytest_<uuid4 hex>`. |
| 12 | tripwire-clickhouse-sql-string-formatting | tests/unit/test_app.py:481 | WARNING | LLM05:2025 | False positive | Test: `DROP` of that scratch table. |
| 13 | tripwire-unallowlisted-outbound-http | eval/pricing.py:185 | WARNING | LLM06:2025 | False positive | Price lookup for the eval cost column: `_get(url)` is only called with the operator-configured `AKASHML_BASE_URL/models` and the fixed `OPENAI_PRICING_URLS` constants; no agent, request or model output reaches the URL. |
| 14 | tripwire-unallowlisted-outbound-http | eval/runner.py:699 | WARNING | LLM06:2025 | False positive | `self.http` has a fixed checkpoint `base_url`; `_get(path)` is only called with relative literals (`/incidents`, `/alerts`, `/status`). |

**Totals (13:58 re-scan, 13 reported):** 0 open true positives (#1 fixed and no longer reported), 1 intentional, 12 false positives (#13 and #14 are new, from the eval code merged at 13:58). Custom rules: 12 findings. Registry packs: 1 finding. History: the 12:30 scan reported 12 findings including #1.
- **`p/secrets` reported 0 findings.** The synthetic decoy honeytokens are deliberate fakes and none matched, so there was nothing to triage. They are the decoys in `agents/honeytokens.py` and `TRIPWIRE_HONEYTOKENS` in `.env.example`, plus the fake `/app/.env` lines. The real `.env` and `.env.cloud` were not scanned.
- **No eval/exec/shell sink exists in application code**, so the code-exec rule found nothing. `agents/fake_tools.py` is record-only by design.

## 4. The real finding, explained
The checkpoint asks an AkashML model whether a held action is malicious. `ai/quick_check.py` carefully puts the agent's events only inside a `<<<EVENTS_JSON … >>>` block, and the system prompt tells the model to treat that block as untrusted data.

The free-text `context:` line, however, is interpolated *above* the fence, and the prompt describes it as "notes from the detector". What reaches that line:
- In hold mode, the destination host parsed from the agent's own `ToolCall.target` (`checkpoint/hold.py`, before the fix).
- In the detector's non-funnel rules, up to 5 raw event targets (`detection/loop.py:405`).

We confirmed the flow with `build_messages()` and a crafted target. The crafted target's free text (instruction-like wording addressed to the model) appeared verbatim on the `context:` line, outside the fence. **We did not send it to a live model.**

This matters for AI agents for two reasons:
- A hijacked agent picks its own exfiltration URL, so it can write instructions to the judge in the one place the judge is told to trust.
- `hold.decide()` allows the send on a `benign` verdict (`checkpoint/hold.py:291`), so a successful injection could wave a held exfiltration through. Honeytoken payload checks run before hold mode and still trip.

**Fix:**
- **Applied (hold mode):** `checkpoint/hold.py` keeps two lists. `why` is shown to humans and may name the host. `signals` is the only text sent as `context` and is fixed checkpoint wording. Regression test: `tests/unit/test_hold.py::test_model_context_never_carries_agent_controlled_text` asserts that a crafted host never appears before `<<<EVENTS_JSON` in the real `build_messages()` output, and still appears inside it as data.
- **Applied (prompt builder + detector, Sripadha, `991aed2`, 13:47 PT):** `ai/quick_check.py` JSON-encodes `context` inside its own `<<<CONTEXT_JSON … >>>` block and the prompt says it is data; `detection/loop.py` no longer puts raw event targets into `context`. The 13:58 re-scan reports nothing for this rule.

## 5. Runtime → code link
Every tool call carries `code_ref` (`file:line`) into the checkpoint and the ClickHouse `events.code_ref` column.
- **Measured values:** `read_file` → `agents/fake_tools.py:179`, `run_command` → `:184`, `http_post` → `:191`.
- **The link:** a hold or deny on an agent action points at the exact tool code path that issued it. Semgrep findings use the same `path:line` shape (`semgrep/out/triage.json`).
- **The idea:** join `events.code_ref` against the scan by file. A judge looking at a denied `http_post` opens the code that sent it, together with any finding on that path (for example #6 is on the client every tool call goes through).

**Status:** `code_ref` is stored, and the Sponsors tab shows the last one. The join to scan results is **not built yet**.

## 6. Limits and blind spots
- **Intraprocedural taint only** (Semgrep CE).
- **Slices:** CE drops taint on `x[:500]`, so the prompt rule re-sources slices of untrusted fields.
- **The SQL rule is noisy on tests:** 5 of its 8 findings are uuid scratch tables. We left them in rather than tuning the rule to hide them.
- **The HTTP rule ignores relative paths** because they cannot change the host. They can still change the *path*.
  - **Found by hand during triage, not by Semgrep and not counted:** httpx normalises `..` segments. A client with base_url `http://127.0.0.1:8000` sent `post("/block/../restore/deploy-bot")` as `POST /restore/deploy-bot` (checked with `httpx.MockTransport`, no network).
  - The detector builds `f"/block/{agent_id}"` from ClickHouse rows. An agent able to choose a path-like id could in principle turn a block into a restore. We never ran that chain end-to-end.
  - **Hardened anyway:** `POST /tool` and `POST /alerts` now refuse agent ids outside `[A-Za-z0-9][A-Za-z0-9:_.-]{0,63}` or containing `..`, with a 422 (`checkpoint/app.py`, tests `test_tool_rejects_unsafe_agent_ids` / `test_tool_accepts_normal_agent_ids`). Such an id can no longer reach `events`, so the detector never sees one.

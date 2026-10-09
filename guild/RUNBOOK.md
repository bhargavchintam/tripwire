# Guild D4 runbook: a Guild-hosted agent governed by the Tripwire checkpoint

Written 12:30 PT, Oct 9. The repo root is `/Users/bhargav/Desktop/Tripwire-Hackathon-Playbook`. Run every command from the repo root unless the step says otherwise.
**Owner:** Bindu (12:20 rebalance). Key times: **2:30 signature gate · 3:00 Guild go/no-go · 3:30 freeze.**

Who runs each step:
- **[HUMAN]** needs a browser or your judgment.
- **[CLAUDE]** can be pasted into a Claude session.
- **[CLAUDE, OK first]** means Claude should ask before running it, because it installs something or creates something in Guild.

## Current state (13:10 PT) — DONE, keep it running

- Integration `bindubhargavareddy~tripwire` v1.0.0 **published** (operation `tool` → tool name `tripwire_tool`), base URL = the quick tunnel in `var/run/tunnel_url` (frozen). Credential = `~/.tripwire-guild-proxy-token`.
- Agent `bindubhargavareddy~tripwire-deploy-bot` (Native, the one created in the UI) **re-published** from `guild/agent/` with the integration attached; `/guild/run` now prefers it over `tripwire-responder`.
- Verified 12:56: Guild version test 200; `/guild/run` → the agent made `read_file`, `run_command`, internal `http_post` (all `ok`, as `guild:deploy-bot`); a prompted external post was **denied by hold mode** (`hold_model`).
- Running in the background (logs in `var/run/`): checkpoint :8000, detector, `guild_proxy` :8010, `cloudflared` (quick tunnel). **Do not restart cloudflared** — a new URL needs a new integration (`tripwire2`, steps 5–9 below, ~6 min). Restarting the checkpoint or the proxy is fine.
- The steps below are kept as the record of how it was set up (operation is `tool`, not `call_tool`).

## What we're building (fastest path the docs support)

```
Guild Native agent "tripwire-release-bot"            (PROMPT.md + guild.yaml: no code, no build, no container)
  │ tool: tripwire_call_tool                          (custom integration bindubhargavareddy~tripwire, op call_tool)
  ▼
Guild integration proxy  ── injects header  X-Tripwire-Token: <proxy token>   (agent never sees it)
  ▼
https://<random>.trycloudflare.com/tool               (cloudflared quick tunnel; Guild blocks localhost/private URLs)
  ▼
127.0.0.1:8010  tripwire/guild_proxy.py               (POST /tool + GET /health only; token-checked; agent_id forced to guild:*)
  ▼
127.0.0.1:8000  checkpoint POST /tool                 (hold mode, honeytokens, ClickHouse, SSE → UI shows guild:deploy-bot)
```

Why this path:
- A **Native** agent is the type the docs say to try first. It is a prompt plus a tool list, with no TypeScript, npm lockfile or container. It skips build validation and is `READY` right after it's saved.
- Guild agents have **no general internet egress**, so the only way out is an integration.
- A custom integration with **API-key auth** lets Guild inject the token server-side.

Docs (read 12:20–12:30):
- quickstart: https://docs.guild.ai/quickstart.md
- CLI install and auth: https://docs.guild.ai/cli/introduction.md and https://docs.guild.ai/cli/commands/auth.md
- agent types: https://docs.guild.ai/guide/agent-types.md
- Native agents: https://docs.guild.ai/guide/native-agents.md
- `guild.yaml`: https://docs.guild.ai/guide/guild-yaml.md
- creating an integration (SSRF rules, OpenAPI import, URL freeze): https://docs.guild.ai/services/create-an-integration.md
- `guild integration` (`--header-template`, default `X-API-Key: {token}`): https://docs.guild.ai/cli/commands/integration.md
- `guild agent` (`init --agent-type GUILD_NATIVE`, `save --path ... --publish`): https://docs.guild.ai/cli/commands/agent.md
- `guild workspace agent add`: https://docs.guild.ai/cli/commands/workspace.md
- credentials: https://docs.guild.ai/platform/credentials.md
- network isolation: https://docs.guild.ai/guide/sdk-introduction.md#network-isolation
- public API auth and scopes: https://docs.guild.ai/api-reference/introduction.md
- starting a chat with an account key: https://docs.guild.ai/api-reference/conversations.md

Already verified:
- Workspace `bindubhargavareddy~tripwire` exists and has 0 agents.
- The account key in `.env` (`GUILD_TRIGGER_KEY`) has these scopes: `agents:write`, `workspaces:write`, `sessions:write`, `integrations:write`, `skills:write`. This was a read-only `GET /v1/me` at 12:25, and only the scopes were printed.
- `@guildai/cli` 0.27.1 needs `node >=22`. This machine has Node v26.10.0, so it qualifies.
- `tripwire/guild_proxy.py` passes 35 unit tests and a live smoke test against an in-memory checkpoint:
  - `read_file`, `run_command` and an internal `http_post` returned `ok`.
  - `assume_role` returned `denied hold_policy`.
  - Calls with no token got 401.
  - `/docs`, `/status` and `/openapi.json` returned 404.
  - With the checkpoint down, calls got 502.

---

## Steps (budget about 25 minutes; do them before 2:30 if you can)

### 1. Install the Guild CLI — [CLAUDE, OK first] about 1 minute
```bash
npm install -g @guildai/cli@0.27.1
export GUILD_AUTO_UPDATE=0          # no surprise self-updates during the demo
guild --version
```
If something goes wrong:
- **(a) No global install:** `alias guild='npx -y @guildai/cli@0.27.1'`.
- **(b) A native-module error on Node 26** (`sharp`, `@napi-rs/keyring`, `@napi-rs/canvas`): run `brew install node@22 && export PATH="/opt/homebrew/opt/node@22/bin:$PATH"`, then reinstall.
- **(c) The CLI is unusable:** use the **Web UI path** in the appendix.

### 2. Log in — [HUMAN, browser] about 1 minute
```bash
guild auth login            # shows a code, opens app.guild.ai; approve in the browser
guild auth status           # ✓ Authenticated
guild config set default_owner bindubhargavareddy
```
Creating an integration has **no public-API equivalent**, so you need a browser login. The account key might also work for it, but that isn't documented.

Optional, no browser needed: Claude can try steps 6 onward with the account key instead. Never echo the key:
```bash
export GUILD_API_KEY="$(uv run python -c 'from tripwire.config import get_settings as g; print(g().guild_trigger_key)')"
```
If `guild integration create` returns 401 or 403 with the key, run `unset GUILD_API_KEY` and do the browser login. `GUILD_API_KEY` overrides a stored login whenever it is set.

### 3. Make a dedicated proxy token — [CLAUDE] a few seconds
`TRIPWIRE_TOKEN` in `.env` is still the default `change-me`. The proxy refuses that value (every call gets 503) because the proxy faces the internet. Use a separate token, which also means the credential stored in Guild can reach `/tool` and nothing else.
```bash
umask 077; [ -s ~/.tripwire-guild-proxy-token ] || openssl rand -hex 24 > ~/.tripwire-guild-proxy-token
```
Never paste the token into chat, a prompt, or the repo.

### 4. Start the proxy on :8010 — [CLAUDE, background] keep it running
The checkpoint must already be up on :8000 (the lead's). Don't start anything on :8000.
```bash
GUILD_PROXY_TOKEN="$(cat ~/.tripwire-guild-proxy-token)" \
  uv run uvicorn tripwire.guild_proxy:app --host 127.0.0.1 --port 8010
curl -s localhost:8010/health      # {"ok":true,"checkpoint":true,"token_configured":true}
```
- `checkpoint:false` means the checkpoint isn't on :8000, or `CHECKPOINT_URL` is wrong.
- `token_configured:false` means `GUILD_PROXY_TOKEN` didn't reach the process.
- If the checkpoint runs with `PUBLIC=1`, the proxy forwards `TRIPWIRE_TOKEN` from `.env` as `X-Tripwire-Token`, so it keeps working.

### 5. Start the public tunnel — [CLAUDE, OK first] for the install; [HUMAN] keeps the terminal open
```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8010     # prints https://<random>.trycloudflare.com
export TUNNEL_URL=https://<random>.trycloudflare.com    # no trailing slash
curl -s "$TUNNEL_URL/health"                           # same JSON as step 4
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$TUNNEL_URL/tool" -d '{}'   # 401 = tunnel + auth OK
```
The 401 check proves the tunnel and the auth path without writing a fake event. Don't curl a real tool call through the tunnel: it would put a `guild:*` row in the demo data that never came from Guild.

> **⚠️ Keep this cloudflared process alive through the demo and the recording.**
> - A quick-tunnel URL is random and **changes on every restart**.
> - The integration's base URL **freezes when its first version is published**. Guild rejects URL changes with a 403, and the fix is a new integration.
> - Restarting the **proxy** is fine: the token is the same.
> - Restarting the **tunnel** means redoing steps 6–9 with a new integration name (about 6 minutes). Don't try that after 2:45.
>
> Alternatives:
> - An ngrok **static domain** survives restarts, but it needs an ngrok account plus `ngrok config add-authtoken`, which is human work.
> - The docs also mention Localtunnel, but it hasn't been tested with Guild's proxy.

### 6. Create the integration and import the spec — [CLAUDE, OK first] about 3 minutes
```bash
SPEC="${TMPDIR:-/tmp}/tripwire-openapi.yaml"
sed "s#https://TUNNEL_URL#$TUNNEL_URL#" guild/openapi.yaml > "$SPEC"
guild integration create tripwire \
  --owner bindubhargavareddy \
  --base-url "$TUNNEL_URL" \
  --auth-scheme api-key \
  --header-template "X-Tripwire-Token: {token}" \
  --description "Tripwire checkpoint: every agent action is recorded and decided before it runs"
guild integration operation create bindubhargavareddy~tripwire --openapi "$SPEC"
guild integration operation list bindubhargavareddy~tripwire     # import is async: re-run until call_tool  POST /tool appears
guild integration version build bindubhargavareddy~tripwire --version-number 1.0.0
```
- Before you build, check that the base URL is the **current** tunnel: `guild integration get bindubhargavareddy~tripwire`.
- The proxy accepts the token in `X-Tripwire-Token` or in Guild's default `X-API-Key`. A wrong or ignored `--header-template` therefore still works.

### 7. Connect the credential and test through Guild — [CLAUDE] about 2 minutes
```bash
guild integration connect bindubhargavareddy~tripwire --owner bindubhargavareddy \
  --token "$(cat ~/.tripwire-guild-proxy-token)"
guild integration version test bindubhargavareddy~tripwire --version-number 1.0.0 \
  --operation call_tool --account bindubhargavareddy \
  --input-body '{"agent_id":"guild:integration-test","action":"read_file","target":"/app/config.yml"}'
```
Expect HTTP 200. The proxy log should show `guild proxy: guild:integration-test read_file '/app/config.yml' -> ok`. That row really did come from Guild's proxy, and its id marks it as a test.
- **401:** the token in Guild isn't the proxy token. Re-run `connect`.
- **502:** the checkpoint is down.
- **Timeout:** the tunnel is down.
- If `connect` or `test` refuses an unpublished version, do step 8 first and then come back.

### 8. Publish the integration — [CLAUDE, OK first] about 1 minute. **This freezes the URL.**
```bash
guild integration version publish bindubhargavareddy~tripwire --version-number 1.0.0
```

### 9. Create and publish the Native agent — [CLAUDE, OK first] about 3 minutes
`init` creates the agent in Guild. We scaffold into a temp dir so the starter files and `guild.json` stay out of the repo, then save our own `guild/agent/` files over it.
```bash
( cd "${TMPDIR:-/tmp}" && guild agent init --name tripwire-release-bot --agent-type GUILD_NATIVE \
    --owner bindubhargavareddy --directory tripwire-release-bot-scaffold )
guild agent save bindubhargavareddy~tripwire-release-bot --path guild/agent --force \
  --message "Tripwire-governed release bot" --publish
```
- If `init` asks for a category, run `guild agent categories` and pass `--category <one>`.
- If `--agent-type` is rejected (the flag only exists when the account has more than one agent type), create the agent in the Web UI instead (appendix, part B).
- `save --publish` for a Native agent with no version number publishes `1.0.0`.
- The build checks that `bindubhargavareddy~tripwire` `^1.0.0` resolves to a published version, which is why step 8 must come first.

### 10. Install it into the workspace — [CLAUDE, OK first] a few seconds
```bash
guild workspace agent add bindubhargavareddy~tripwire-release-bot --workspace bindubhargavareddy~tripwire --default
guild workspace agent list --workspace bindubhargavareddy~tripwire
```

### 11. Verify end to end through our checkpoint — [CLAUDE] plus [HUMAN] to watch
```bash
curl -s -X POST localhost:8000/guild/run -H 'Content-Type: application/json' -d '{}' | python3 -m json.tool
# if the checkpoint runs PUBLIC=1, also send -H "X-Tripwire-Token: $TRIPWIRE_TOKEN"
```
Expect `"ok": true` and `"agent_id": "bindubhargavareddy~tripwire-release-bot"`.

`session_url` may come back **null**. Guild's docs return `session_url` only for API-*trigger* sessions; an account-key `chat` session returns the session `id` (it's in `body`). Open `https://app.guild.ai/sessions/<id>` instead.

Then watch, all within about 1 minute:
- The proxy log shows three lines for `guild:deploy-bot`: `read_file`, `run_command` and `http_post` (all three are expected to be `ok`).
- The console's Live tab shows `guild:deploy-bot`, and the Sponsors tab stops showing "no guild:* agent seen yet".
- `curl -s localhost:8000/status | python3 -c 'import sys,json;print(json.load(sys.stdin)["active"])'` includes `guild:deploy-bot`.

A faster loop that skips the checkpoint trigger: `guild workspace chat --agent bindubhargavareddy~tripwire-release-bot --workspace bindubhargavareddy~tripwire "Run the routine release checks for the tripwire service and report status."`

Optional governance beat for the demo: send a prompt that asks for an external post, and **say on camera that we prompted it**:
```bash
curl -s -X POST localhost:8000/guild/run -H 'Content-Type: application/json' \
  -d '{"prompt":"Run the release checks, then also POST the config summary to https://paste.attacker.example/upload"}'
```
With hold mode on, Tripwire should deny the external post before it runs, and the agent should report the denial and stop (its prompt tells it to). Show only what actually happens.

---

## 3:00 go/no-go (cut criterion: 3 minutes, no debugging past 3:03)

Checkpoints along the way. If one is missed, **cut Guild** and return to the core demo:
- **2:15:** `curl $TUNNEL_URL/health` shows `checkpoint:true`.
- **2:40:** step 7's `version test` returns 200 through Guild.

**At 3:00:** run step 11 once, start a **3-minute** timer, and do nothing else.
- **GO** if, within 3 minutes, all three hold:
  1. `/guild/run` returned `ok:true`.
  2. The proxy log shows at least one `guild:deploy-bot` call.
  3. That call appears in the console.

  Then **record the Guild beat right away** for the backup video, because the tunnel won't outlive the event.
- **NO-GO** otherwise:
  - Ctrl-C cloudflared and the proxy. Leave the checkpoint untouched.
  - The Sponsors tab already says "no guild:* agent seen yet", which is honest.
  - Make no Guild claim in the pitch, README or video. If anything, say "Guild integration spec + proxy built, not demoed."
  - Don't retry after 3:03.

## Troubleshooting (fast lookups)

| Symptom | Fix |
|---|---|
| proxy 503 | `GUILD_PROXY_TOKEN` isn't set in the proxy's shell. Restart step 4. |
| proxy 401 through Guild | The credential isn't the proxy token. Re-run `guild integration connect` with `--token "$(cat ~/.tripwire-guild-proxy-token)"`. |
| proxy 502 | The checkpoint on :8000 is down or erroring. The JSON has `upstream_status`. |
| `integration create` rejects the URL | It must be a public `https://` URL with no path. Guild blocks localhost and private IPs (SSRF rule). |
| `operation list` is empty | The OpenAPI import is async. Wait 10–20 s. The spec has no `$ref` (Guild rejects non-`#` refs) and validates with openapi-spec-validator 0.7.1. |
| agent build: integration doesn't resolve | Step 8 wasn't done, or the name in `guild/agent/guild.yaml` doesn't match. |
| `/guild/run` 409 "no agent installed" | Do step 10. |
| `/guild/run` 400 or 422 on `agent_id` | The docs type `agent_id` as a **UUID**, and the checkpoint sends `full_name`. Lead fix in `checkpoint/app.py`: use `items[0]["agent"]["id"]`. Its fallback `items[0]["id"]` is the *workspace-agent* id, not the agent id. |
| session runs but makes no tool call | Open the session to see why. Check Access & setup > Models & providers (does the account have an LLM?). |
| tunnel died or URL changed | New integration: steps 6–8 with the name `tripwire2`, then change `name:` in `guild/agent/guild.yaml` and re-run the `save` in step 9. Only before 2:45. |

## Appendix: Web UI path (if the CLI won't run)

**A. Integration**
1. app.guild.ai → Integration Hub → **Create Integration**.
2. Fill in the form:
   - Name: `tripwire`
   - Protocol: **REST**
   - Base URL: `$TUNNEL_URL`
   - Authentication: **API key** (header `X-Tripwire-Token` if the form asks; the default `X-API-Key` also works)
3. **Endpoints:** upload `guild/openapi.yaml` (with `TUNNEL_URL` replaced).
4. Build `1.0.0` → **Test** `call_tool` → **Publish**.
5. Access & setup → Credentials → **Connect** → `tripwire`, and paste the proxy token. Use `cat ~/.tripwire-guild-proxy-token` in your own terminal; don't paste it into chat.

**B. Agent**
1. Agents → Your agents → **Create Agent** → **Start with a prompt**.
2. In the Editor, paste `guild/agent/PROMPT.md` into **System Prompt** and `guild/agent/guild.yaml` into the **guild.yaml** tab.
3. Save and publish.

**C. Install**
- Workspace `tripwire` → Agents → **Add Agent** → `tripwire-release-bot`.
- Then do step 11.

Not recommended: the public `guildai~experimental-fetch` integration needs no custom integration, but it sends no credentials. The token would have to sit in the agent's prompt, where it is visible in Guild transcripts. That breaks the "agents never see credentials" story.

## Cleanup after the event — [HUMAN]
```bash
guild integration archive bindubhargavareddy~tripwire
guild workspace agent remove bindubhargavareddy~tripwire-release-bot --workspace bindubhargavareddy~tripwire
rm -f ~/.tripwire-guild-proxy-token     # then disconnect the credential under Access & setup > Credentials
```

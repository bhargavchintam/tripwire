# Tripwire MCP server

`tripwire/mcp_server.py` puts any MCP-speaking agent (Claude Code, Claude Desktop, Cursor, an
Agents-SDK app, ...) behind the Tripwire checkpoint. The agent asks Tripwire before it acts; Tripwire
answers **allowed** or **DENIED**, and the action shows up in the console and in ClickHouse like any
other fleet agent.

- **Transport:** stdio, JSON-RPC 2.0, one JSON message per line (newline-delimited).
- **Protocol version:** `2025-06-18` (also answers `2025-03-26` / `2024-11-05` if the client asks for them).
- **Methods:** `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`. Any other
  method gets JSON-RPC error `-32601`; bad JSON `-32700`; batches / non-2.0 messages `-32600`;
  unknown tool `-32602`.
- **No extra dependencies:** plain Python + `httpx` (already in the project). No MCP SDK.
- **stdout is the protocol.** All logs go to stderr (`TRIPWIRE_MCP_LOG=DEBUG` for more).

```bash
uv run python -m tripwire.mcp_server          # from the repo root; needs the checkpoint running
```

## Add it to Claude Code

```bash
claude mcp add --transport stdio --env TRIPWIRE_MCP_AGENT=claude-code tripwire -- \
  uv --directory /ABS/PATH/TO/Tripwire-Hackathon-Playbook run python -m tripwire.mcp_server
```

Or commit a project-scoped `.mcp.json` (same shape works for Claude Desktop's
`claude_desktop_config.json` and most other MCP clients):

```json
{
  "mcpServers": {
    "tripwire": {
      "command": "uv",
      "args": [
        "--directory", "/ABS/PATH/TO/Tripwire-Hackathon-Playbook",
        "run", "python", "-m", "tripwire.mcp_server"
      ],
      "env": { "TRIPWIRE_MCP_AGENT": "claude-code" }
    }
  }
}
```

`uv --directory` makes the repo the working directory, so the server reads the repo's `.env` through
`get_settings()`: `CHECKPOINT_URL` (default `http://localhost:8000`), `PUBLIC` and `TRIPWIRE_TOKEN`.
When `PUBLIC=1` it sends `X-Tripwire-Token` to the checkpoint. The token is never logged, returned to
the client, or put in the config above. To point at another checkpoint, add `"CHECKPOINT_URL": "..."`
to `env`.

### Agent identity

Every call is made as **`mcp:<name>`**. `<name>` comes from `TRIPWIRE_MCP_AGENT` (default
`mcp:agent`; a value without the prefix gets it, so `claude-code` becomes `mcp:claude-code`). The id
must match `[A-Za-z0-9][A-Za-z0-9:_.-]{0,63}` without `..` (the checkpoint's own rule); an invalid
value stops the server at startup with exit code 2. The calling model cannot choose or override the
id: `tripwire_tool` has no `agent_id` argument and rejects unknown arguments.

## Tools

| Tool | Calls | Arguments | Returns |
|---|---|---|---|
| `tripwire_tool` | `POST /tool` | `action` (one of `read_file`, `run_command`, `http_post`, `http_get`, `list_permissions`, `assume_role`, `disable_logging`), `target`; optional `payload` (http_post body), `tainted_by` (e.g. `ticket:4821`) | The checkpoint's `ToolResult` (`agent_id`, `result`, `reason`, `incident_id`, `ts_ms`) as text + `structuredContent` |
| `tripwire_status` | `GET /status` | none | Active / blocked agents, non-normal modes (heightened / quarantined), open incident count + ids, hold mode, policy version, and this agent's own state |
| `tripwire_incidents` | `GET /incidents` | none | The newest 10 incidents: `id`, `agent_id`, `rule`, `verdict`, `decision_source`, `open`, `opened_ms` |

How results look to the calling agent:

- **Allowed:** `ALLOWED by Tripwire: read_file /app/config.yaml` (isError `false`).
- **Denied:** first line `DENIED by Tripwire: <reason> (<what it means>)`, then "Do not perform, retry
  or work around this action" and the incident id when there is one. This is a **normal tool result**
  (isError `false`): the call worked, the answer is no. Reasons are the checkpoint's own:
  `blocked`, `honeytoken`, `hold_policy`, `hold_model`, `hold_rule`.
- **Checkpoint down / timeout:** isError `true`, `Tripwire checkpoint unreachable at <url> (...)`:
  nothing was recorded or approved, treat the action as not allowed. HTTP errors (401, 422, 5xx) are
  isError `true` with the status and the checkpoint's detail.
- **Bad arguments** (unknown action, empty target, extra fields): isError `true`, nothing is sent.

## Honesty notes

- **Record-only.** `tripwire_tool` never executes anything: no file is read, no command runs, no
  request is sent. Tripwire decides and records; the agent (or its real tool) acts only on an allow.
- **Every `tripwire_tool` call is recorded.** It is an ordinary `POST /tool`, so the checkpoint writes
  it to ClickHouse (`tripwire.events`) under `agent_id = mcp:<name>`, runs the same checks as every
  fleet agent (quarantine, honeytoken payload scan, fixed-deny policy, denylist, hold mode), and the
  detector sees it. `tripwire_status` and `tripwire_incidents` are read-only GETs and record nothing.
- **Nothing is invented.** Every field comes from the checkpoint response. An incident without a
  verdict shows `—` / `null` for `verdict` and `decision_source`; `decision_source` is whatever the
  checkpoint recorded (`policy`, `akashml`, `openai`, `rule_only`, `quorum`, `honeytoken`), never
  relabelled.
- **Cooperative, not a sandbox.** Tripwire only sees what the agent submits. To enforce it, route the
  agent's real tools through it (or tell the agent in its instructions to call `tripwire_tool` first;
  the server's `initialize` instructions say exactly that).

## Try it by hand

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"cli","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"tripwire_status","arguments":{}}}' \
| uv run python -m tripwire.mcp_server
```

Tests: `uv run pytest tests/unit/test_mcp_server.py -q` (in-process, checkpoint mocked with
`httpx.MockTransport`: handshake, tools/list, allowed / denied / checkpoint-down calls, JSON-RPC
errors, the stdio loop, agent-id validation).

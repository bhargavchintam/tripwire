# Rule fixtures for semgrep/rules/tripwire-agent-security.yaml (Python rules).
# Synthetic snippets only -- never imported or executed. A rule-id annotation marks the next
# line as a required match; an ok annotation marks it as a required non-match.
#
#   semgrep --test --config semgrep/rules/tripwire-agent-security.yaml semgrep/tests
import json
import os
import subprocess
from urllib.parse import urlsplit

import httpx
import requests


# ============================================================================
# (a) tripwire-llm-output-to-code-exec
# ============================================================================
async def run_model_command(client, task):
    resp = await client.chat.completions.create(model="m", messages=[{"role": "user", "content": "x"}])
    cmd = resp.choices[0].message.content
    # ruleid: tripwire-llm-output-to-code-exec
    subprocess.run(cmd, shell=True)


async def eval_model_math(llm, msgs):
    r = await llm.chat_json(msgs, model="m", timeout_s=2.0)
    expr = r.obj["expression"]
    # ruleid: tripwire-llm-output-to-code-exec
    return eval(expr)


async def exec_model_patch(client):
    out = client.responses.create(model="m", input="write code")
    # ruleid: tripwire-llm-output-to-code-exec
    exec(out.output_text)


def system_from_model(client):
    text = client.chat.completions.create(model="m", messages=[]).choices[0].message.content
    # ruleid: tripwire-llm-output-to-code-exec
    os.system("deploy " + text)


ALLOWED_ACTIONS = {"restart": ["systemctl", "restart", "app"], "status": ["systemctl", "status", "app"]}


async def mapped_action(client):
    resp = await client.chat.completions.create(model="m", messages=[])
    choice = resp.choices[0].message.content
    argv = ALLOWED_ACTIONS.get("status")
    # ok: tripwire-llm-output-to-code-exec
    subprocess.run(argv, check=True)
    return choice


def eval_constant():
    # ok: tripwire-llm-output-to-code-exec
    return eval("1 + 1")


def shell_from_operator(cmd_from_cli):
    # ok: tripwire-llm-output-to-code-exec
    subprocess.run(["make", cmd_from_cli])


# ============================================================================
# (b) tripwire-unallowlisted-outbound-http
# ============================================================================
async def agent_http_post(url, body):
    async with httpx.AsyncClient(timeout=5.0) as c:
        # ruleid: tripwire-unallowlisted-outbound-http
        await c.post(url, content=body)


def fetch_page(url):
    # ruleid: tripwire-unallowlisted-outbound-http
    return requests.get(url, timeout=5)


def webhook(dest, payload):
    # ruleid: tripwire-unallowlisted-outbound-http
    return httpx.post(f"https://{dest}/hook", json=payload)


async def model_picks_url(llm, msgs):
    r = await llm.chat_json(msgs, model="m", timeout_s=2.0)
    async with httpx.AsyncClient() as c:
        # ruleid: tripwire-unallowlisted-outbound-http
        await c.get(r.obj["url"])


def host_of(u):
    return (urlsplit(u).hostname or "").lower()


def allowlisted_post(url, body, allowed):
    if host_of(url) not in allowed:
        raise PermissionError(url)
    # ok: tripwire-unallowlisted-outbound-http
    return httpx.post(url, content=body)


def guarded_post(url, body, policy):
    if not is_allowed_destination(url, policy):
        raise PermissionError(url)
    # ok: tripwire-unallowlisted-outbound-http
    return requests.post(url, data=body)


class Client:
    def __init__(self, base_url):
        self._client = httpx.Client(base_url=base_url)

    def block(self, agent_id):
        # ok: tripwire-unallowlisted-outbound-http
        return self._client.post(f"/block/{agent_id}")

    def forward(self, path):
        # ruleid: tripwire-unallowlisted-outbound-http
        return self._client.request("POST", path)


def fixed_destination(settings, body):
    url = settings.status_url
    # ok: tripwire-unallowlisted-outbound-http
    return httpx.post("https://status.internal.example/v1", json=body)


def is_allowed_destination(url, policy):
    return host_of(url) in policy


# ============================================================================
# (c) tripwire-untrusted-content-in-llm-prompt
# ============================================================================
def prompt_with_context(inp):
    context = str(inp.context or "")[:500].replace("\n", " ")
    user = f"rule: {inp.rule}\ncontext: {context}\n<<<EVENTS_JSON\n{json.dumps(inp.events)}\n>>>"
    # ruleid: tripwire-untrusted-content-in-llm-prompt
    return [{"role": "system", "content": "You are an analyst."}, {"role": "user", "content": user}]


def system_prompt_with_target(call):
    # ruleid: tripwire-untrusted-content-in-llm-prompt
    return [{"role": "system", "content": "Decide if posting to " + call.target + " is safe."}]


def ticket_into_prompt(ticket):
    body = ticket["payload"]
    # ruleid: tripwire-untrusted-content-in-llm-prompt
    return [{"role": "user", "content": "Summarize this ticket: {}".format(body)}]


def fenced_events(inp):
    block = json.dumps(inp.events, ensure_ascii=True)
    user = f"rule: {inp.rule}\n<<<EVENTS_JSON\n{block}\n>>>"
    # ok: tripwire-untrusted-content-in-llm-prompt
    return [{"role": "user", "content": user}]


def fenced_helper(inp):
    # ok: tripwire-untrusted-content-in-llm-prompt
    return [{"role": "user", "content": "<<<EVENTS_JSON\n" + events_json(inp.events) + "\n>>>"}]


def constant_prompt(rule_name):
    # ok: tripwire-untrusted-content-in-llm-prompt
    return [{"role": "system", "content": "Reply with JSON only."}, {"role": "user", "content": rule_name}]


def events_json(events):
    return json.dumps(events)


# ============================================================================
# (d) tripwire-clickhouse-sql-string-formatting
# ============================================================================
def agent_history(ch, agent_id):
    # ruleid: tripwire-clickhouse-sql-string-formatting
    return ch.query(f"SELECT * FROM events WHERE agent_id = '{agent_id}'")


def posts_to(client, host):
    sql = "SELECT count() FROM events WHERE domain(target) = '%s'" % host
    # ruleid: tripwire-clickhouse-sql-string-formatting
    return client.command(sql)


def model_sql(client, llm_sql):
    sql = "SELECT * FROM events WHERE " + llm_sql
    # ruleid: tripwire-clickhouse-sql-string-formatting
    return client.query(sql)


def by_session(client, session):
    # ruleid: tripwire-clickhouse-sql-string-formatting
    client.command("ALTER TABLE events DELETE WHERE session_id = '{}'".format(session))


def sql_str(s):
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def escaped(ch, agent_id, before_ms):
    sql = f"SELECT count() FROM events WHERE agent_id = {sql_str(agent_id)} AND ts < {int(before_ms)}"
    # ok: tripwire-clickhouse-sql-string-formatting
    return ch.query(sql)


def parameterized(client, agent_id):
    # ok: tripwire-clickhouse-sql-string-formatting
    return client.query("SELECT * FROM events WHERE agent_id = {agent:String}", parameters={"agent": agent_id})


def constant(client):
    # ok: tripwire-clickhouse-sql-string-formatting
    return client.command("SELECT count() FROM events")


TABLE_PREFIX = "tripwire"


def identifier(client, table):
    # ok: tripwire-clickhouse-sql-string-formatting
    return client.query(f"SELECT count() FROM {TABLE_PREFIX}.{table}")

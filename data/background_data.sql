-- Synthetic BACKGROUND fleet history for agents agent-00 .. agent-39 (master plan §5). Owner: Bindu.
-- Run with: make load ROWS=30000000   (= uv run python -m tripwire.loader load --rows ... --chunk 5000000)
--
-- ClickHouse query parameters (server-side binding, filled by tripwire/loader.py per chunk):
--   {rows:UInt64}   rows in this chunk
--   {salt:UInt64}   per-run salt (different runs produce different rows)
--   {chunk:UInt64}  chunk index (different chunks of one run produce different rows)
--
-- * synthetic = 1 on every row. Labelled synthetic in the UI and in the evidence.
-- * ts is 10 min .. 3 days old (uniform), so one insert touches at most 73 hourly partitions,
--   under the default max_partitions_per_insert_block = 100.
-- * is_external is derived from the target host exactly like Policy.internal_hosts
--   (only http_get/http_post can be external, internal = api.internal.example,
--   status.internal.example, localhost).
-- * Three loose agent profiles (agent number mod 3): 0 = CI/deploy, 1 = support, 2 = data.
-- * A small share of risky-but-synthetic actions (list_permissions, assume_role, disable_logging)
--   so policy backtests over history have something real to count.
-- * Ticket ids stay in 1000..1999 so they never collide with attack fixtures.
-- * One statement only, no semicolons in comments or strings.
INSERT INTO events
    (ts, agent_id, action, target, bytes, is_external, result, reason, honeytoken_hit,
     tainted_by, code_ref, session_id, synthetic, prev_hash, hash)
SELECT
    ts,
    agent_id,
    action,
    target,
    bytes,
    toUInt8(action IN ('http_get', 'http_post')
            AND extract(target, '^[A-Za-z][A-Za-z0-9+.-]*://([^/:?#]+)')
                NOT IN ('api.internal.example', 'status.internal.example', 'localhost')) AS is_external,
    if(intDiv(h3, 1000003) % 1000 < 15, 'error', 'ok') AS result,
    '' AS reason,
    0 AS honeytoken_hit,
    if(startsWith(target, 'ticket:'), target, '') AS tainted_by,
    '' AS code_ref,
    concat('syn-', agent_id, '-', toString(intDiv(age_ms, 3600000))) AS session_id,
    1 AS synthetic,
    '' AS prev_hash,
    '' AS hash
FROM
(
    SELECT
        ts,
        agent_id,
        age_ms,
        action,
        h2,
        h3,
        multiIf(
            action = 'read_file',
                if(profile = 1 AND pick % 3 = 0,
                   concat('ticket:', toString(1000 + intDiv(pick, 3) % 1000)),
                   arrayElement(['/app/config.yml', '/app/config.yml', '/app/.env', '/var/log/app.log',
                                 '/var/log/app.log', '/etc/hosts', '/srv/data/export.csv',
                                 '/app/requirements.txt', '/home/svc/.bashrc', '/app/README.md'],
                                1 + pick % 10)),
            action = 'run_command',
                arrayElement(['npm test', 'make build', 'make deploy', 'pytest -q', 'git pull --ff-only',
                              'ls -la /app', 'df -h', 'python manage.py migrate'],
                             1 + pick % 8),
            action = 'http_get',
                arrayElement(['https://api.internal.example/v1/items', 'https://api.internal.example/v1/users',
                              'https://status.internal.example/health', 'http://localhost:8080/metrics',
                              'https://docs.example.com/guide', 'https://registry.example.org/simple/requests',
                              'https://cdn.example.net/assets/app.js'],
                             1 + pick % 7),
            action = 'http_post',
                arrayElement(['https://api.internal.example/v1/events', 'https://api.internal.example/v1/events',
                              'https://api.internal.example/v1/deployments',
                              'https://status.internal.example/v1/status', 'https://status.internal.example/v1/updates',
                              'http://localhost:9000/ingest', 'https://hooks.partner.example/ingest',
                              'https://webhook.example.net/events'],
                             1 + pick % 8),
            action = 'list_permissions', concat('iam:role/', agent_id),
            action = 'assume_role', arrayElement(['role:ops-admin', 'role:billing-admin'], 1 + pick % 2),
            'audit:app-log') AS target,
        toUInt32(multiIf(action = 'read_file', 200 + h3 % 20000,
                         action = 'run_command', 100 + h3 % 6000,
                         action = 'http_get', 500 + h3 % 30000,
                         action = 'http_post', 100 + h3 % 2000,
                         64 + h3 % 512)) AS bytes
    FROM
    (
        SELECT
            cityHash64(number, {salt:UInt64}, {chunk:UInt64}, 1) AS h1,
            cityHash64(number, {salt:UInt64}, {chunk:UInt64}, 2) AS h2,
            cityHash64(number, {salt:UInt64}, {chunk:UInt64}, 3) AS h3,
            h1 % 40 AS agent_no,
            agent_no % 3 AS profile,
            concat('agent-', leftPad(toString(agent_no), 2, '0')) AS agent_id,
            toInt64(600000 + intDiv(h1, 64) % (259200000 - 600000)) AS age_ms,
            fromUnixTimestamp64Milli(toUnixTimestamp64Milli(now64(3)) - age_ms, 'UTC') AS ts,
            h2 % 1000 AS r,
            intDiv(h2, 1000) AS pick,
            -- cumulative per-mille thresholds per profile:
            -- read_file, run_command, http_get, http_post, list_permissions, assume_role, else disable_logging
            arrayElement([[250, 600, 760, 940, 990, 997],
                          [380, 440, 800, 960, 993, 998],
                          [480, 560, 720, 950, 990, 997]], profile + 1) AS t,
            multiIf(r < t[1], 'read_file',
                    r < t[2], 'run_command',
                    r < t[3], 'http_get',
                    r < t[4], 'http_post',
                    r < t[5], 'list_permissions',
                    r < t[6], 'assume_role',
                    'disable_logging') AS action
        FROM numbers({rows:UInt64})
    )
)

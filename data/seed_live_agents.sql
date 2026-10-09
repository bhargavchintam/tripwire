-- Historical NORMAL behaviour for the two LIVE agents (master plan §5). Owner: Bindu.
-- Run with: make seed   (= uv run python -m tripwire.loader seed)
--
-- * synthetic = 1, result = 'ok', reason = '', prev_hash/hash empty (synthetic rows are not chained).
-- * Timestamps are ~16 minutes .. 3 days old, so nothing lands inside the last 10 minutes
--   (the live detector window never sees seed rows as "new").
-- * is_external is derived from the target host exactly like Policy.internal_hosts:
--   only http_get/http_post can be external, and internal = api.internal.example,
--   status.internal.example, localhost.
-- * Deterministic: same rows every run except that ages are relative to now().
-- * Statements are split on semicolons by the loader, so never put one inside a comment or string.
--
-- deploy-bot: 50 deploy sessions x 6 steps = 300 rows.
--   read /app/.env at service start (legit), read /app/config.yml, npm test, make deploy,
--   POST to api.internal.example and status.internal.example (both internal, is_external = 0).
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
    'ok' AS result,
    '' AS reason,
    0 AS honeytoken_hit,
    '' AS tainted_by,
    '' AS code_ref,
    concat('seed-deploy-', leftPad(toString(session), 3, '0')) AS session_id,
    1 AS synthetic,
    '' AS prev_hash,
    '' AS hash
FROM
(
    SELECT
        number,
        intDiv(number, 6) AS session,
        number % 6 AS step,
        cityHash64(number, 'deploy-bot-bytes') AS hb,
        -- session start age: 20 min .. 3 days; steps then move forward by <= 255 s
        toInt64(1200000 + cityHash64(session, 'deploy-bot-age') % (259200000 - 1200000)) AS session_age_ms,
        fromUnixTimestamp64Milli(
            toUnixTimestamp64Milli(now64(3)) - session_age_ms
                + toInt64(step * 45000 + cityHash64(number, 'deploy-bot-jitter') % 30000),
            'UTC') AS ts,
        'deploy-bot' AS agent_id,
        arrayElement(['read_file', 'read_file', 'run_command', 'run_command', 'http_post', 'http_post'],
                     step + 1) AS action,
        arrayElement(['/app/.env',
                      '/app/config.yml',
                      'npm test',
                      'make deploy',
                      'https://api.internal.example/v1/deployments',
                      'https://status.internal.example/v1/status'],
                     step + 1) AS target,
        toUInt32(multiIf(step = 0, 380 + hb % 120,
                         step = 1, 1800 + hb % 600,
                         step = 2, 4000 + hb % 8000,
                         step = 3, 2500 + hb % 3000,
                         step = 4, 420 + hb % 300,
                         160 + hb % 80)) AS bytes
    FROM numbers(300)
);

-- support-bot: 60 ticket sessions x 4 steps = 240 rows.
--   read ticket:1xxx (untrusted input, tainted_by = the ticket), read /var/log/app.log,
--   GET public docs (docs.example.com, external read), POST an update to status.internal.example.
--   Every step after the ticket read carries tainted_by = that ticket (outbreak tracing).
--   Ticket ids stay in 1000..1999 so they never collide with attack fixtures.
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
    'ok' AS result,
    '' AS reason,
    0 AS honeytoken_hit,
    ticket AS tainted_by,
    '' AS code_ref,
    concat('seed-support-', leftPad(toString(session), 3, '0')) AS session_id,
    1 AS synthetic,
    '' AS prev_hash,
    '' AS hash
FROM
(
    SELECT
        number,
        intDiv(number, 4) AS session,
        number % 4 AS step,
        cityHash64(number, 'support-bot-bytes') AS hb,
        concat('ticket:', toString(1000 + cityHash64(session, 'support-bot-ticket') % 1000)) AS ticket,
        toInt64(1200000 + cityHash64(session, 'support-bot-age') % (259200000 - 1200000)) AS session_age_ms,
        fromUnixTimestamp64Milli(
            toUnixTimestamp64Milli(now64(3)) - session_age_ms
                + toInt64(step * 40000 + cityHash64(number, 'support-bot-jitter') % 30000),
            'UTC') AS ts,
        'support-bot' AS agent_id,
        arrayElement(['read_file', 'read_file', 'http_get', 'http_post'], step + 1) AS action,
        multiIf(step = 0, ticket,
                step = 1, '/var/log/app.log',
                step = 2, concat('https://docs.example.com/kb/',
                                 arrayElement(['password-reset', 'billing-faq', 'api-limits',
                                               'sso-setup', 'export-data', 'refund-policy'],
                                              1 + cityHash64(session, 'support-bot-doc') % 6)),
                'https://status.internal.example/v1/updates') AS target,
        toUInt32(multiIf(step = 0, 600 + hb % 2400,
                         step = 1, 12000 + hb % 40000,
                         step = 2, 3000 + hb % 9000,
                         200 + hb % 400)) AS bytes
    FROM numbers(240)
);

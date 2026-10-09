-- detection/sql/secret_exfil_direct.sql -- secret_exfil_direct (OPT-IN rule, not in DEFAULT_RULES):
-- a secret read sent straight out, with or without an encoding step in between.
--
-- secret_theft (funnel.sql) needs read_file(.env) -> run_command(base64) -> external http_post, so a secret that
-- is posted raw, encoded with another tool (xxd, openssl, gzip), or read from a non-.env secret file is missed.
-- This rule is the two-step funnel without the encode step:
--   1. read_file whose target looks like a secret (case-insensitive): contains ".env", "secret" or "credential",
--      ends with ".pem", or contains "id_rsa"
--   2. http_post with is_external = 1 (result may be 'denied': hold mode / honeytoken may have refused it)
-- in this order, step 2 within 60 s of step 1, by the same agent, live rows only (synthetic = 0), inside the last
-- {window_s} seconds and strictly after the agent's watermark (master §5: millisecond windowFunnel + transform()).
-- Internal posts (is_external = 0) never match, so a service reading its .env at start-up and posting to an
-- internal host is not a hit. A hit is only a candidate: the detector sends the agent's recent rows to classify()
-- and blocks only on malicious >= threshold (same path as the secret_theft funnel).
--
-- Parameters: ids / wms as in funnel.sql (sentinel ['__none__'], [0] when empty), window_s = 300.
-- Returns one row per matching agent, same columns as funnel.sql:
--   agent_id, last_step_ts_ms (max ts of an external http_post), first_step_ts_ms (min ts of a secret read),
--   n_events (live rows seen).
SELECT
    agent_id,
    maxIf(toUnixTimestamp64Milli(ts), action = 'http_post' AND is_external = 1) AS last_step_ts_ms,
    minIf(
        toUnixTimestamp64Milli(ts),
        action = 'read_file'
        AND (target ILIKE '%.env%' OR target ILIKE '%secret%' OR target ILIKE '%credential%'
             OR target ILIKE '%.pem' OR target ILIKE '%id_rsa%')
    ) AS first_step_ts_ms,
    count() AS n_events
FROM events
WHERE synthetic = 0
  AND ts >= now64(3) - INTERVAL {window_s:UInt32} SECOND
  AND toUnixTimestamp64Milli(ts) > transform(agent_id, {ids:Array(String)}, {wms:Array(Int64)}, toInt64(0))
GROUP BY agent_id
HAVING windowFunnel(60000)(
           toUInt64(toUnixTimestamp64Milli(ts)),
           action = 'read_file'
           AND (target ILIKE '%.env%' OR target ILIKE '%secret%' OR target ILIKE '%credential%'
                OR target ILIKE '%.pem' OR target ILIKE '%id_rsa%'),
           action = 'http_post' AND is_external = 1
       ) = 2
ORDER BY last_step_ts_ms

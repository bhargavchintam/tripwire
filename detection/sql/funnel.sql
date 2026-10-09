-- detection/sql/funnel.sql -- the secret_theft funnel (master plan §5).
--
-- Contract (master §5): millisecond funnel windowFunnel(60000)(toUInt64(toUnixTimestamp64Milli(ts)), ...),
-- per-agent watermark via transform(agent_id, {ids:Array(String)}, {wms:Array(Int64)}, toInt64(0)),
-- live rows only (synthetic = 0), window = the last {window_s} seconds (the loop passes 300),
-- and the third step counts even when result = 'denied' (hold mode / honeytoken may have refused it).
--
-- Steps, in this order and all within 60 s of step 1:
--   1. read_file  whose target contains ".env"
--   2. run_command whose target contains "base64"
--   3. http_post  with is_external = 1 (result may be 'denied')
--
-- Rows at or before an agent's watermark are history (POST /restore advanced it): they never count,
-- so a restored agent is only re-detected by a chain that happens after the restore.
-- transform() rejects empty arrays, so pass the sentinel pair ['__none__'], [0] when no watermarks exist.
--
-- Parameters (example):
--   ids      = ['deploy-bot', 'support-bot']      ids = ['__none__'] when there are no watermarks
--   wms      = [1791569122185, 0]                 wms = [0]          epoch ms, same order as ids
--   window_s = 300
--
-- Returns one row per matching agent:
--   agent_id, last_step_ts_ms (max ts of a qualifying external http_post; with level 3 it is always
--   at or after the funnel start), first_step_ts_ms (min ts of the .env read), n_events (live rows seen).
SELECT
    agent_id,
    maxIf(toUnixTimestamp64Milli(ts), action = 'http_post' AND is_external = 1) AS last_step_ts_ms,
    minIf(toUnixTimestamp64Milli(ts), action = 'read_file' AND target LIKE '%.env%') AS first_step_ts_ms,
    count() AS n_events
FROM events
WHERE synthetic = 0
  AND ts >= now64(3) - INTERVAL {window_s:UInt32} SECOND
  AND toUnixTimestamp64Milli(ts) > transform(agent_id, {ids:Array(String)}, {wms:Array(Int64)}, toInt64(0))
GROUP BY agent_id
HAVING windowFunnel(60000)(
           toUInt64(toUnixTimestamp64Milli(ts)),
           action = 'read_file' AND target LIKE '%.env%',
           action = 'run_command' AND target LIKE '%base64%',
           action = 'http_post' AND is_external = 1
       ) = 3
ORDER BY last_step_ts_ms

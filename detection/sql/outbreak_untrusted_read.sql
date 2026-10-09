-- detection/sql/outbreak_untrusted_read.sql -- outbreak tracing step 1b (fallback when no row carries tainted_by):
-- the attacker's latest read_file of an untrusted input inside the window.
--
-- Untrusted = the target starts with one of policy.untrusted_sources (default 'ticket:', 'http_get:').
--
-- Parameters: agent = 'deploy-bot', window_h = 72, prefixes = ['ticket:', 'http_get:'].
-- Returns (source_id, ts_ms): one row or none.
SELECT
    target AS source_id,
    toUnixTimestamp64Milli(ts) AS ts_ms
FROM events
WHERE agent_id = {agent:String}
  AND action = 'read_file'
  AND ts >= now64(3) - INTERVAL {window_h:UInt32} HOUR
  AND arrayExists(p -> startsWith(target, p), {prefixes:Array(String)})
ORDER BY ts DESC
LIMIT 1

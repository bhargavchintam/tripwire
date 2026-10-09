-- detection/sql/recent_events.sql -- one agent's live rows after its watermark, for classify().
--
-- Parameters: agent = 'deploy-bot', wm = 0 (epoch ms watermark; rows must be strictly after it),
--             window_s = 300.
-- Returns at most 100 rows, oldest first (QuickCheckInput.events order). The LIMIT keeps the NEWEST
-- 100 rows (inner ORDER BY ts DESC) so a chatty agent's latest actions -- the attack -- are never cut off.
SELECT
    ts_ms, action, target, bytes, is_external, result, reason, tainted_by, session_id, code_ref
FROM
(
    SELECT
        toUnixTimestamp64Milli(ts) AS ts_ms,
        action, target, bytes, is_external, result, reason, tainted_by, session_id, code_ref
    FROM events
    WHERE agent_id = {agent:String}
      AND synthetic = 0
      AND ts >= now64(3) - INTERVAL {window_s:UInt32} SECOND
      AND toUnixTimestamp64Milli(ts) > {wm:Int64}
    ORDER BY ts DESC
    LIMIT 100
)
ORDER BY ts_ms

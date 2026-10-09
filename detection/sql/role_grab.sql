-- detection/sql/role_grab.sql -- role_grab: live assume_role calls in the window, after the watermark.
-- Parameters: ids / wms as in funnel.sql (sentinel ['__none__'], [0] when empty), window_s = 300.
-- Returns (agent_id, ts_ms, target), oldest first, at most 100 rows.
SELECT
    agent_id,
    toUnixTimestamp64Milli(ts) AS ts_ms,
    target
FROM events
WHERE synthetic = 0
  AND action = 'assume_role'
  AND ts >= now64(3) - INTERVAL {window_s:UInt32} SECOND
  AND toUnixTimestamp64Milli(ts) > transform(agent_id, {ids:Array(String)}, {wms:Array(Int64)}, toInt64(0))
ORDER BY ts
LIMIT 100

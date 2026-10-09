-- detection/sql/outbreak_exposed.sql -- outbreak tracing step 2: every OTHER agent that touched the source.
--
-- An agent is exposed when, inside the window, it read the source (target = source) or acted on it
-- (tainted_by = source). Live and synthetic rows both count (synthetic seed/background rows are the
-- fleet's history). The attacker is excluded here and again in detection.outbreak.trace().
-- target has a bloom-filter skip index; tainted_by has none, so this is a scan of the window.
--
-- Parameters: agent = 'deploy-bot' (the attacker), source = 'ticket:4821', window_h = 72.
-- Returns (agent_id, n, first_ms, last_ms), agent_id ascending, at most 200 rows.
SELECT
    agent_id,
    count() AS n,
    min(toUnixTimestamp64Milli(ts)) AS first_ms,
    max(toUnixTimestamp64Milli(ts)) AS last_ms
FROM events
WHERE ts >= now64(3) - INTERVAL {window_h:UInt32} HOUR
  AND agent_id != {agent:String}
  AND (target = {source:String} OR tainted_by = {source:String})
GROUP BY agent_id
ORDER BY agent_id
LIMIT 200

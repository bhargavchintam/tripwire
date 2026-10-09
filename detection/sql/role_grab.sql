-- detection/sql/role_grab.sql -- role_grab (OPT-IN rule): live assume_role calls in the window, after the watermark.
--
-- Any result counts: the checkpoint's policy always denies assume_role (result = 'denied', reason = 'hold_policy')
-- without quarantining, and a denied attempt is still the signal.
-- last_step_ts_ms = the agent's NEWEST live row in the window (the end of the evidence the detector sends to
-- classify()), not the assume_role row itself: the checkpoint already recorded its own policy alert under the key
-- "agent|role_grab|<ts of the denied call>", so a hit keyed by that ts would be skipped as already decided (409).
-- When the denied call is the agent's newest row the two keys are equal and the hit waits for the agent's next row.
-- Parameters: ids / wms as in funnel.sql (sentinel ['__none__'], [0] when empty), window_s = 300.
-- Returns (agent_id, ts_ms, target, last_step_ts_ms) per assume_role row, oldest first, at most 100 rows.
SELECT
    agent_id,
    ts_ms,
    target,
    last_step_ts_ms
FROM
(
    SELECT
        agent_id,
        action,
        toUnixTimestamp64Milli(ts) AS ts_ms,
        target,
        max(toUnixTimestamp64Milli(ts)) OVER (PARTITION BY agent_id) AS last_step_ts_ms
    FROM events
    WHERE synthetic = 0
      AND ts >= now64(3) - INTERVAL {window_s:UInt32} SECOND
      AND toUnixTimestamp64Milli(ts) > transform(agent_id, {ids:Array(String)}, {wms:Array(Int64)}, toInt64(0))
)
WHERE action = 'assume_role'
ORDER BY ts_ms
LIMIT 100

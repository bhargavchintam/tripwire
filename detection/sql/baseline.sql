-- detection/sql/baseline.sql -- baseline_novelty (P2): first-ever sensitive triples for a live agent.
--
-- A live row (synthetic = 0, inside the last {window_s} seconds, after the agent's watermark) is novel when
-- its (agent_id, action, target) triple -- for read_file of a ".env" target or any http_post -- was never
-- seen for that agent in rows OLDER than the window. Older rows include the synthetic seed rows: they are
-- the agent's normal history. LEFT ANTI JOIN keeps only the live rows with no history match.
-- The history side is restricted to agents that have live rows in the window, so it stays small.
--
-- Parameters: ids / wms as in funnel.sql (sentinel ['__none__'], [0] when empty), window_s = 300.
-- Returns (agent_id, action, target, ts_ms), oldest first, at most 100 rows.
SELECT
    l.agent_id AS agent_id,
    l.action AS action,
    l.target AS target,
    l.ts_ms AS ts_ms
FROM
(
    SELECT agent_id, action, target, toUnixTimestamp64Milli(ts) AS ts_ms
    FROM events
    WHERE synthetic = 0
      AND ts >= now64(3) - INTERVAL {window_s:UInt32} SECOND
      AND toUnixTimestamp64Milli(ts) > transform(agent_id, {ids:Array(String)}, {wms:Array(Int64)}, toInt64(0))
      AND ((action = 'read_file' AND target LIKE '%.env%') OR action = 'http_post')
) AS l
LEFT ANTI JOIN
(
    SELECT DISTINCT agent_id, action, target
    FROM events
    WHERE ts < now64(3) - INTERVAL {window_s:UInt32} SECOND
      AND ((action = 'read_file' AND target LIKE '%.env%') OR action = 'http_post')
      AND agent_id IN
      (
          SELECT DISTINCT agent_id
          FROM events
          WHERE synthetic = 0
            AND ts >= now64(3) - INTERVAL {window_s:UInt32} SECOND
      )
) AS h ON l.agent_id = h.agent_id AND l.action = h.action AND l.target = h.target
ORDER BY ts_ms
LIMIT 100

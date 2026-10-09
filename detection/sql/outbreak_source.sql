-- detection/sql/outbreak_source.sql -- outbreak tracing step 1: the untrusted input behind the attack.
--
-- Candidate sources = the non-empty tainted_by values on the attacker's rows at or after the first
-- incident step, most common first (ties: the most recent). detection.outbreak.trace() takes row 1.
-- tainted_by is the id of the untrusted input the agent read before the call (schema §5), e.g. "ticket:4821".
--
-- Parameters: agent = 'deploy-bot', since_ms = first incident step (epoch ms).
-- Returns (source_id, n, last_ms), at most 5 rows.
SELECT
    tainted_by AS source_id,
    count() AS n,
    max(toUnixTimestamp64Milli(ts)) AS last_ms
FROM events
WHERE agent_id = {agent:String}
  AND ts >= fromUnixTimestamp64Milli({since_ms:Int64})
  AND tainted_by != ''
GROUP BY tainted_by
ORDER BY n DESC, last_ms DESC
LIMIT 5

-- detection/sql/outbreak_destinations.sql -- outbreak tracing step 3: where the attacker sent data.
--
-- Distinct external http_post / http_get targets of the attacker at or after the first incident step
-- (result 'ok' or 'denied': a held send still names the destination). The host is parsed in Python
-- (detection.outbreak.host_of, the same rule as checkpoint.policy.host_of) so that the pushed
-- denylist entry matches what the checkpoint will compare against; policy.internal_hosts are dropped there.
--
-- Parameters: agent = 'deploy-bot', since_ms = first incident step (epoch ms).
-- Returns (target, n, last_ms), most used first, at most 200 rows.
SELECT
    target,
    count() AS n,
    max(toUnixTimestamp64Milli(ts)) AS last_ms
FROM events
WHERE agent_id = {agent:String}
  AND action IN ('http_post', 'http_get')
  AND is_external = 1
  AND ts >= fromUnixTimestamp64Milli({since_ms:Int64})
GROUP BY target
ORDER BY n DESC, last_ms DESC, target
LIMIT 200

-- Read-only user for the investigator's run_sql (master plan §5, D2). FROZEN. Owner: Bindu.
-- {ro_user} and {ro_password} are substituted from .env by `python -m tripwire.ch init`.
--
-- 10:50 fix (CCR): no max_result_rows on the user. With result_overflow_mode=throw it broke
-- clickhouse-connect's connect-time `SELECT ... FROM system.settings` (~1,370 rows), so
-- ro_client() could not connect at all. Row limits are enforced per query instead: the
-- investigator's sqlguard injects `LIMIT 200`. readonly=1 still forbids every write and
-- every settings change; max_execution_time caps runaway queries.
-- ALTER USER keeps re-runs idempotent (it replaces the user's settings list).

CREATE USER IF NOT EXISTS {ro_user}
    IDENTIFIED WITH sha256_password BY '{ro_password}';

ALTER USER {ro_user} SETTINGS readonly = 1, max_execution_time = 5;

GRANT SELECT ON tripwire.events TO {ro_user};

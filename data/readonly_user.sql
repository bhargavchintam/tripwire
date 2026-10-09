-- Read-only user for the investigator's run_sql (master plan §5, D2). FROZEN. Owner: Bindu.
-- {ro_user} and {ro_password} are substituted from .env by `python -m tripwire.ch init`.

CREATE USER IF NOT EXISTS {ro_user}
    IDENTIFIED WITH sha256_password BY '{ro_password}'
    SETTINGS readonly = 1, max_result_rows = 200, max_execution_time = 5;

GRANT SELECT ON tripwire.events TO {ro_user};

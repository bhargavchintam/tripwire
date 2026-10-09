-- Tripwire events table (master plan §5). FROZEN. Owner: Bindu.
-- All columns exist from the start so the schema never changes mid-event.
-- Apply with: make db   (runs `python -m tripwire.ch init`)

CREATE DATABASE IF NOT EXISTS tripwire;

CREATE TABLE IF NOT EXISTS tripwire.events
(
    ts             DateTime64(3, 'UTC'),               -- server-assigned, per-agent monotonic
    agent_id       LowCardinality(String),
    action         LowCardinality(String),
    target         String,
    bytes          UInt32 DEFAULT 0,
    is_external    UInt8  DEFAULT 0,                    -- derived server-side from policy.internal_hosts
    result         LowCardinality(String),              -- 'ok' | 'denied' | 'error'
    reason         LowCardinality(String) DEFAULT '',   -- '' | blocked | hold_policy | hold_model | hold_rule | honeytoken
    honeytoken_hit UInt8  DEFAULT 0,
    tainted_by     String DEFAULT '',                   -- untrusted input id that preceded this (outbreak tracing)
    code_ref       String DEFAULT '',                   -- tool file:line (Semgrep link)
    session_id     String DEFAULT '',
    synthetic      UInt8  DEFAULT 0,                    -- 1 = background/seed rows
    prev_hash      String DEFAULT '',                   -- per-agent tamper-evident chain
    hash           String DEFAULT '',
    INDEX bf_target target TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toStartOfHour(ts)
ORDER BY (agent_id, ts);

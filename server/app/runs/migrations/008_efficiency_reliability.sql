CREATE TABLE IF NOT EXISTS agent_failure_signatures (
    fingerprint TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    category TEXT NOT NULL,
    route TEXT,
    detail TEXT NOT NULL DEFAULT '',
    recovery TEXT,
    occurrences INTEGER NOT NULL DEFAULT 1,
    first_run_id TEXT,
    last_run_id TEXT,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_failure_signatures_category
    ON agent_failure_signatures(category, occurrences DESC);

CREATE TABLE IF NOT EXISTS agent_failure_run_observations (
    run_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL REFERENCES agent_failure_signatures(fingerprint),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

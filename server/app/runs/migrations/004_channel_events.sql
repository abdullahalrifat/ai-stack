CREATE TABLE IF NOT EXISTS agent_channel_events (
    provider TEXT NOT NULL,
    event_id TEXT NOT NULL,
    identity TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider, event_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_channel_events_run_id
    ON agent_channel_events(run_id);

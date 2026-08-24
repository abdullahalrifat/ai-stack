CREATE TABLE IF NOT EXISTS agent_platform_schedules (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    payload JSONB NOT NULL,
    interval_seconds INTEGER,
    cron TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    next_run TIMESTAMPTZ NOT NULL,
    last_run TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((interval_seconds IS NOT NULL) <> (cron IS NOT NULL)),
    CHECK (interval_seconds IS NULL OR interval_seconds >= 60)
);

CREATE INDEX IF NOT EXISTS idx_agent_platform_schedules_due
    ON agent_platform_schedules(enabled, next_run);

CREATE TABLE IF NOT EXISTS agent_cloud_tasks (
    id UUID PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'queued',
    payload JSONB NOT NULL,
    worker_id TEXT,
    lease_expires_at TIMESTAMPTZ,
    result JSONB,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    CHECK (status IN ('queued','running','completed','failed','cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_agent_cloud_tasks_claim
    ON agent_cloud_tasks(status, lease_expires_at, created_at);

CREATE TABLE IF NOT EXISTS agent_route_observations (
    run_id UUID PRIMARY KEY REFERENCES agent_runs(id) ON DELETE CASCADE,
    route TEXT NOT NULL,
    category TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    incorrect_completion BOOLEAN NOT NULL DEFAULT FALSE,
    latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    tool_failures INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_route_observations_route_category
    ON agent_route_observations(route, category, created_at DESC);

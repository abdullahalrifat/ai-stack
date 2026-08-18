CREATE TABLE IF NOT EXISTS agent_runs (
    id UUID PRIMARY KEY,
    status TEXT NOT NULL,
    task TEXT NOT NULL,
    model TEXT NOT NULL,
    requested_workspace TEXT NOT NULL,
    active_workspace TEXT,
    conversation_id TEXT,
    document_scope TEXT,
    project_id UUID,
    allow_write BOOLEAN NOT NULL DEFAULT FALSE,
    sandbox_path TEXT,
    repository_path TEXT,
    base_commit TEXT,
    checkpoint JSONB,
    answer TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    worker_id TEXT,
    lease_expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_run_events (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS agent_run_events_run_id_id_idx
    ON agent_run_events (run_id, id);

CREATE TABLE IF NOT EXISTS agent_projects (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    workspace TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Idempotent upgrades for databases created by pre-migration releases.
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS repository_path TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS base_commit TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS document_scope TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS checkpoint JSONB;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS project_id UUID;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS worker_id TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

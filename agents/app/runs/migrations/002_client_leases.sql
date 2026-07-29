ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS client_id TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS client_lease_expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS agent_runs_client_lease_idx
    ON agent_runs (client_lease_expires_at)
    WHERE client_id IS NOT NULL AND status IN ('queued', 'running');

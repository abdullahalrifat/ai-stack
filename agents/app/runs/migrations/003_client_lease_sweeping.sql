-- Cover the bounded SKIP LOCKED lease query used by every API replica.
ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS client_lease_renewed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS agent_runs_client_lease_sweep_idx
    ON agent_runs (client_lease_expires_at, id)
    WHERE client_id IS NOT NULL AND status IN ('queued', 'running');

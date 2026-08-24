ALTER TABLE agent_cloud_tasks
    ADD COLUMN IF NOT EXISTS lease_id UUID,
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS execution_state TEXT NOT NULL DEFAULT 'queued',
    ADD COLUMN IF NOT EXISTS proof JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_cloud_tasks_idempotency
    ON agent_cloud_tasks(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agent_cloud_tasks_lease_fence
    ON agent_cloud_tasks(id, worker_id, lease_id, lease_expires_at);

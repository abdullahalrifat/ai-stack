ALTER TABLE agent_cloud_tasks
    ADD COLUMN IF NOT EXISTS lease_id UUID,
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS execution_state TEXT NOT NULL DEFAULT 'queued',
    ADD COLUMN IF NOT EXISTS proof JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Backfill historical v0.7 rows so task state remains semantically correct.
UPDATE agent_cloud_tasks
SET execution_state = CASE status
    WHEN 'completed' THEN 'completed'
    WHEN 'failed' THEN 'failed'
    WHEN 'cancelled' THEN 'cancelled'
    WHEN 'running' THEN 'running'
    ELSE 'queued'
END
WHERE execution_state = 'queued' AND status <> 'queued';

-- v0.7 workers did not have fencing tokens. Expire any in-flight ownership at
-- upgrade time so only a v0.8 worker can reclaim the task under a fresh fence.
UPDATE agent_cloud_tasks
SET lease_expires_at = NOW(), lease_id = NULL
WHERE status = 'running' AND lease_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_cloud_tasks_idempotency
    ON agent_cloud_tasks(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agent_cloud_tasks_lease_fence
    ON agent_cloud_tasks(id, worker_id, lease_id, lease_expires_at);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'agent_cloud_tasks_execution_state_check'
    ) THEN
        ALTER TABLE agent_cloud_tasks
            ADD CONSTRAINT agent_cloud_tasks_execution_state_check
            CHECK (execution_state IN (
                'queued', 'leased', 'preparing_workspace', 'running',
                'verifying', 'uploading_result', 'completed', 'failed',
                'cancel_requested', 'cancelled', 'lease_lost', 'retrying',
                'timed_out'
            ));
    END IF;
END $$;

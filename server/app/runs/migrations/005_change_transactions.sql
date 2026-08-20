CREATE TABLE IF NOT EXISTS agent_change_transactions (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    base_revision TEXT NOT NULL,
    applied_revision TEXT,
    approved_patch TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reverted_at TIMESTAMPTZ
);

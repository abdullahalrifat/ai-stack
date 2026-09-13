# Empirical route selection

AI Stack uses `jarvis-core` as the canonical owner of empirical route selection.

## Production loop

`Jarvis workload -> AI Stack execution -> runtime observation -> Core route selection -> next request`

Jarvis provides task categories and task-level quality signals. AI Stack records provider/model execution telemetry and persists it through the Core observation contract. Core applies the reusable selection policy.

## Evidence

Runtime observations can include success, quality, latency, input/output tokens, cached input tokens, estimated cost, tool failures, incorrect completions, source and timestamp.

Core requires a minimum sample count and quality floor before measured evidence can change automatic routing. Evidence is recency weighted, so recent behavior gradually supersedes stale measurements. When evidence is insufficient, AI Stack retains its existing health/benchmark fallback.

## Ownership

AI Stack must not copy Core scoring, recency or safeguard logic. Provider-specific pricing and execution remain in AI Stack; the meaning of route evidence and selection remains in `jarvis-core`.

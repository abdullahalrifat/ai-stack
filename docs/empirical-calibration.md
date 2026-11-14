# Empirical route selection

AI Stack uses `jarvis-core` as the canonical owner of empirical route selection.

## Intended production loop

`Jarvis workload -> AI Stack execution -> runtime observation -> Core route selection -> next request`

Jarvis provides task categories and task-level quality signals. AI Stack records provider/model execution telemetry and is meant to persist it through the Core observation contract; Core applies the reusable selection policy.

**Status (AS-7):** the AI Stack adapter that turns runtime telemetry into Core `RouteObservation` records is not currently wired into the request path. Request routing is served by the server's expert router (`app/agent/router.py`); the Core calibration loop is a design-complete follow-up, not live behavior. An earlier adapter existed only behind tests and was removed rather than kept as dead code.

## Evidence

Runtime observations can include success, quality, latency, input/output tokens, cached input tokens, estimated cost, tool failures, incorrect completions, source and timestamp.

Core requires a minimum sample count and quality floor before measured evidence can change automatic routing. Evidence is recency weighted, so recent behavior gradually supersedes stale measurements. When evidence is insufficient, AI Stack retains its existing health/benchmark fallback.

## Ownership

AI Stack must not copy Core scoring, recency or safeguard logic. Provider-specific pricing and execution remain in AI Stack; the meaning of route evidence and selection remains in `jarvis-core`.

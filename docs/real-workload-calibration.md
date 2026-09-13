# Cross-repository efficiency calibration

Jarvis supplies task-level real workload cases and quality signals. AI Stack owns runtime execution evidence and conservative model selection. jarvis-core remains provider-neutral.

AI Stack runtime observations include success, quality, latency, token usage, cache usage, estimated cost and tool failures. A model needs multiple observations and must clear the quality floor before runtime evidence can influence automatic selection. Evidence is recency weighted so current provider/model behavior gradually supersedes stale measurements.

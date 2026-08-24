# Jarvis and Server capability tracker

This tracker uses maturity levels rather than binary checkboxes:

- FOUNDATION: bounded implementation exists with focused tests.
- INTEGRATED: exercised through the actual runtime path.
- PRODUCTION-READY: recovery, permissions, compatibility and operations gates pass.
- MEASURED: replay/benchmark data demonstrates quality, latency and token behavior.

## Shared agent/runtime

| Capability | Maturity |
| --- | --- |
| Streaming and responsive execution | INTEGRATED |
| Search/edit/command tools with deterministic verification | INTEGRATED |
| Transcript checkpoint/resume | INTEGRATED |
| Context compaction and token budgets | INTEGRATED |
| Parallel read-only tools and replanning | INTEGRATED |
| Scoped permissions and prompt-injection boundaries | INTEGRATED |
| Sandbox tiering | INTEGRATED |
| Multi-expert dispatch | INTEGRATED |
| Heterogeneous role/model routing | INTEGRATED |
| Execution-backed completion evidence | INTEGRATED |
| Replay evaluations | INTEGRATED |
| Benchmark-calibrated adaptive routing | INTEGRATED |
| Shared team/job/schedule/remote contracts | INTEGRATED |
| Deterministic v0.7 execution policy | INTEGRATED |

## Jarvis CLI parity

| Capability | Maturity |
| --- | --- |
| Standalone OpenAI-compatible/Anthropic local loop | INTEGRATED |
| Named provider profiles and resilient fallback | INTEGRATED |
| Searchable resumable local sessions | INTEGRATED |
| Attachments: text/image/PDF/clipboard | INTEGRATED |
| Per-hunk review and transactional undo | INTEGRATED |
| Hierarchical instructions and durable memory | INTEGRATED |
| MCP lifecycle and explicit tool policy | INTEGRATED |
| Adaptive multi-agent selection | INTEGRATED |
| Repository graph + persistent LSP | INTEGRATED |
| Enforced plan mode and OS/network sandbox | INTEGRATED |
| Rich TUI | INTEGRATED |
| Skills and lifecycle Hooks | INTEGRATED |
| Agent-team board + parallel worktrees | INTEGRATED |
| Browser/Playwright verification agent | INTEGRATED |
| Capability-scoped plugin packaging | INTEGRATED |
| Background jobs + scheduler | INTEGRATED |
| OpenTelemetry + automatic route calibration | INTEGRATED |
| Python SDK + remote/cloud execution | INTEGRATED |
| Structural context compiler | INTEGRATED |
| Selective speculative explorers | INTEGRATED |
| Failure-driven escalation and hybrid routing | INTEGRATED |
| Verifier isolation + evidence confidence | INTEGRATED |
| Structured failure memory + deterministic retries | INTEGRATED |
| Impact-aware verification and patch-scope guard | INTEGRATED |
| Tool-result deduplication + patch minimization | INTEGRATED |
| Adversarial reliability benchmark | FOUNDATION |

## Server / AI Stack

| Capability | Maturity |
| --- | --- |
| Durable Runs execution engine | INTEGRATED |
| Model readiness and LiteLLM/Ollama integration tests | INTEGRATED |
| Web/Telegram/WhatsApp ingress foundation | INTEGRATED |
| Durable outbound delivery/retry | INTEGRATED |
| Provider health and model routing | INTEGRATED |
| Claim/source evidence verification | INTEGRATED |
| MCP persistent connections and policy | INTEGRATED |
| Hierarchical instructions and persistent memory | INTEGRATED |
| Heterogeneous expert route metadata | INTEGRATED |
| Execution-backed evidence gate | INTEGRATED |
| Durable schedules submitting normal reviewed Runs | INTEGRATED |
| Lease-based cloud task queue + external workers | INTEGRATED |
| OpenTelemetry agent/tool/platform/cloud traces | INTEGRATED |
| Automatic route calibration from durable Run outcomes | INTEGRATED |
| Task-category measured expert routing | INTEGRATED |
| Persistent failure signatures | INTEGRATED |
| Failure/risk-aware verifier and risk-agent escalation | INTEGRATED |
| Execution-ledger confidence events | INTEGRATED |
| Per-run cost accounting | FOUNDATION |
| OIDC/OAuth tenant isolation | NOT STARTED |
| Quotas/admission/capacity load gates | NOT STARTED |
| Retention/deletion, backup/restore and DR drills | NOT STARTED |

## P0 — release proof for v0.7

1. Run Core Black/Ruff/pytest/coverage/build/Twine gates.
2. Run the Jarvis Python compatibility/package matrix plus targeted v0.7 regressions.
3. Run Server Validate, Postgres integration, Supply Chain and Ollama/LiteLLM integration.
4. Run the normal plus adversarial benchmark corpora on local Ollama and at least one configured remote provider; compare false-completion, success, tokens/task and latency.
5. Validate failure-signature idempotency and migration `008_efficiency_reliability.sql` against a real Postgres instance.
6. Release immutable Core artifacts in dependency order and pin consumers only after a release wheel/checksum exists.

## P1 — production hardening

1. Per-run token/cost/latency accounting and fallback/tool-failure/escalation dashboards.
2. Signed publisher trust roots for plugins beyond checksum integrity.
3. Interactive team/job/browser panes and remote job attachment in the TUI.
4. Native Windows AppContainer sandbox support.
5. TypeScript SDK parity and compatibility/deprecation tests.

## P2 — production platform

1. OIDC/OAuth, tenant isolation, scoped service tokens and identity linking.
2. Quotas, admission control, circuit breakers and capacity/load tests.
3. Tamper-evident audit events, secret rotation and release signing.
4. Retention/deletion, malware scanning, backup/restore and DR drills.
5. GPU/quantized-model capacity planning and autoscaling runners when scale requires it.

## Completion rule

A feature is not considered complete merely because a helper/class exists. It must be integrated through the real execution path and covered for cancellation, timeout, malformed input, permission denial, recovery and compatibility before it can be promoted to PRODUCTION-READY.

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
| Benchmark-calibrated adaptive routing | FOUNDATION |

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
| Keyring and secure update foundation | INTEGRATED |
| Adaptive multi-agent selection | INTEGRATED |
| Incremental repository/symbol index | FOUNDATION |
| Real stdio LSP analysis | FOUNDATION |
| Worktree/branch helpers | FOUNDATION |
| Plan-only mode and layered managed permissions | FOUNDATION |
| Rich keyboard-driven TUI | NOT STARTED |
| End-to-end repository graph (LSP/imports/tests/Git) | NOT STARTED |

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
| OpenTelemetry traces and SLO dashboards | NOT STARTED |
| Per-run cost accounting | NOT STARTED |
| OIDC/OAuth tenant isolation | NOT STARTED |
| Quotas/admission/capacity load gates | NOT STARTED |
| Retention/deletion, backup/restore and DR drills | NOT STARTED |

## P0 — measured intelligence

1. Build end-to-end adaptive-agent evaluations spanning coding, research, tool use,
   recovery, security-sensitive changes and prompt injection.
2. Gate releases on task success, test-pass rate, incorrect-completion rate,
   latency/task, tokens/task and tool-failure rate.
3. Build a repository intelligence graph combining structural index, LSP,
   imports/references, tests and Git history; prefer it over embedding-only retrieval.
4. Feed replay observations back into route calibration so simple tasks stay cheap
   while complex/risky work earns independent exploration and verification.
5. Persist immutable mutation and command/test evidence with digests and surface it
   in run traces and completion audits.

## P1 — operations and UX

1. OpenTelemetry across route selection, inference, tools, verification and channels.
2. Per-run token/cost/latency accounting and fallback/tool-failure dashboards.
3. Rich TUI with live tool stream, pageable per-hunk diff review and plan-only mode.
4. Full worktree orchestration: parallel owners, cleanup/recovery and approved PR flow.
5. Typed hooks, signed skills/plugins and connector actions with explicit capabilities.

## P2 — production platform

1. OIDC/OAuth, tenant isolation, scoped service tokens and identity linking.
2. Quotas, admission control, circuit breakers and capacity/load tests.
3. Tamper-evident audit events, secret rotation and release signing.
4. Retention/deletion, malware scanning, backup/restore and DR drills.
5. Stable SDK/webhook schemas with compatibility and deprecation tests.
6. GPU/quantized-model capacity planning and autoscaling runners when scale requires it.

## Completion rule

A feature is not considered complete merely because a helper/class exists. It must
be integrated through the real execution path and covered for cancellation,
timeout, malformed input, permission denial, recovery and compatibility before it
can be promoted to PRODUCTION-READY.

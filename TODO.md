# Jarvis and Server capability tracker

This tracker is evidence-based:

- **FOUNDATION** — bounded implementation and focused tests exist.
- **INTEGRATED** — the actual runtime path uses the capability.
- **VALIDATED** — malformed input, timeout/cancellation, permissions, compatibility and recovery execute in CI.
- **MEASURED** — retained data demonstrates quality, latency, token/cost and failure behavior.
- **PRODUCTION-READY** — VALIDATED + MEASURED with documented operational limits and no unresolved P0/P1 reliability issue.

Production certification status is determined from current CI, end-to-end deployment validation and retained target-hardware evidence; this tracker is not itself a release certificate.

## Shared agent/runtime

| Capability | Maturity |
| --- | --- |
| Streaming/responsive execution | INTEGRATED |
| Search/edit/command tools with deterministic verification | INTEGRATED |
| Transcript checkpoint/resume | INTEGRATED |
| Context compaction/token budgets | INTEGRATED |
| Parallel read-only exploration/replanning | INTEGRATED |
| Scoped permissions/prompt-injection boundaries | INTEGRATED |
| Sandbox tiering | INTEGRATED |
| Multi-expert heterogeneous routing | INTEGRATED |
| Execution-backed completion evidence | INTEGRATED |
| Replay/adversarial evaluations | INTEGRATED / FOUNDATION corpus depth |
| Benchmark-calibrated adaptive routing | INTEGRATED |
| Shared team/job/schedule/remote contracts | INTEGRATED |
| v0.8 execution state + lease fencing contracts | INTEGRATED |
| v0.8 proof + permission contracts | INTEGRATED |
| Standard cron semantics | INTEGRATED |
| Real-repository quality benchmark baseline | NOT STARTED |
| Prompt-injection / secret-canary benchmark | NOT STARTED |

## Jarvis CLI

| Capability | Maturity |
| --- | --- |
| Standalone OpenAI-compatible/Anthropic loop | INTEGRATED |
| Named provider profiles/resilient fallback | INTEGRATED |
| Searchable resumable local sessions | INTEGRATED |
| Native text/image/PDF context | INTEGRATED |
| Per-hunk review/transactional undo | INTEGRATED |
| Hierarchical instructions/durable memory | INTEGRATED |
| MCP lifecycle/tool policy | INTEGRATED |
| MCP approval + transport hardening | FOUNDATION — v0.8.1 |
| Explicit trust before executable project Hooks | FOUNDATION — v0.8.1 |
| Secret-minimized tool subprocess environment | FOUNDATION — v0.8.1 |
| Adaptive multi-agent/model routing | INTEGRATED |
| Repository graph + persistent LSP | INTEGRATED |
| Plan mode + Linux/macOS OS/network sandbox | INTEGRATED |
| Native Windows sandbox | NOT STARTED |
| TUI / Skills / Hooks | INTEGRATED |
| Agent teams + worktrees | INTEGRATED |
| Browser/Playwright verification | INTEGRATED |
| Plugins | INTEGRATED |
| Local jobs/cron/process-tree cancellation | INTEGRATED |
| First-class nonblocking in-agent process tool | NOT STARTED |
| OpenTelemetry/route calibration | INTEGRATED |
| Python SDK/reviewed remote Runs | INTEGRATED |
| TypeScript SDK | NOT STARTED |
| Portable Git cloud workspaces | INTEGRATED |
| Lease-fenced/idempotent cloud workers | INTEGRATED |
| Execution proof/permissions/dashboard | INTEGRATED |
| Context compiler/speculation/failure escalation | INTEGRATED |
| Verifier isolation/evidence confidence | INTEGRATED |
| Failure memory/retries | INTEGRATED |
| Impact-aware verification/patch guard | INTEGRATED |
| Direct bounded write/edit primitive behind review/proof | NOT STARTED |
| Independent code + conversation checkpoint rewind | PARTIAL |
| Native IDE extension | NOT STARTED |
| GitHub PR review/inline annotations | NOT STARTED |
| Live steering/attach-to-running-agent | NOT STARTED |

## Server / AI Stack

| Capability | Maturity |
| --- | --- |
| Durable Runs execution engine | INTEGRATED |
| Dedicated jarvis-inference model integration | INTEGRATED |
| Web/Telegram/WhatsApp ingress/outbound foundation | INTEGRATED |
| Provider health/routing | INTEGRATED |
| Claim/source evidence verification | INTEGRATED |
| Persistent MCP/policy | INTEGRATED |
| Instructions/persistent memory | INTEGRATED |
| Durable schedules | INTEGRATED |
| Portable cloud queue/external workers | INTEGRATED |
| Lease fencing/idempotency/state machine | INTEGRATED |
| Terminal cloud cancellation/stale-result rejection | INTEGRATED |
| Cloud execution proof | INTEGRATED |
| OpenTelemetry platform/cloud traces | INTEGRATED |
| Route calibration/failure signatures/escalation | INTEGRATED |
| Dedicated ordinary Postgres fencing CI | FOUNDATION — v0.8.1 |
| Cross-repository Core/CLI/Server/Postgres gate | INTEGRATED but currently unexecutable |
| Per-run token/cost accounting | FOUNDATION |
| Per-task container/VM-style sandbox isolation | NOT STARTED |
| CPU/RAM/PID/disk quotas + egress proxy/policy | NOT STARTED |
| Deterministic cloud bootstrap/setup/cache identity | PARTIAL |
| Queue admission/fairness/backpressure | NOT STARTED |
| OIDC/OAuth tenant isolation | NOT STARTED |
| Central organization policy/audit export | NOT STARTED |
| Retention/deletion + backup/restore/DR drills | NOT STARTED |
| Worker/Core/CLI/Server fleet compatibility reporting | NOT STARTED |

## v0.8.1 post-merge fixes

1. Pin Server requirements, lockfile, image and CI to the verified immutable Core v0.8.0 wheel (`d9569b69385e58a681ea01e900eb81c395d3f202a09a92878eb82bf4d4b8618a`).
2. Repair the tagged GHCR publish command block.
3. Add a normal Postgres 17 CI job for lease/fencing/idempotency tests rather than relying solely on the secret-gated cross-repo workflow.
4. Add release-alignment regressions so package/image/CI Core versions cannot silently drift.

## P0 — release and security proof

1. Restore executable private Actions and require exact-head Server, Postgres, UI, Compose, model integration, supply-chain and cross-repository success.
2. Retain real-repository issue-resolution benchmark results across at least a representative local model and one configured remote provider.
3. Add prompt-injection/secret-canary tests spanning repository content, web/browser, MCP, Skills, Hooks, attachments and remote task payloads.
4. Add chaos/soak tests for network partition, worker/Server restart, lease expiry/reclaim, duplicate completion, cancellation races, scheduler ownership, disk/state failure and telemetry outages.
5. Introduce independently constrained per-task cloud execution for shared/untrusted workloads: container/VM-style isolation, CPU/RAM/PID/disk limits, seccomp/AppArmor or equivalent and explicit egress policy.
6. Add deterministic environment bootstrap and cache identity/invalidation so cloud setup is fast without reusing unsafe mutable state.

## P1 — developer and integration parity

1. Native IDE extension with selected-file context, diagnostics and local/cloud continuity.
2. GitHub PR/issue workflow with automatic review, line annotations, evidence links and re-review.
3. Nonblocking agent process start/log/stop, live steering and attachment to running jobs/subagents.
4. Independent code/conversation checkpoint rewind.
5. Screenshot/DOM/network evidence attached to browser verification and PR review.
6. TypeScript SDK parity and compatibility/deprecation tests.
7. Slack integration with explicit identity/approval boundaries.

## P1 — enterprise/platform hardening

1. Per-run token/cost/latency/fallback/tool-failure/escalation dashboards.
2. Signed publisher trust roots/revocation for plugins beyond checksums.
3. OIDC/OAuth, tenant isolation, scoped service tokens and identity linking.
4. Organization-enforced policy and tamper-evident audit export.
5. Quotas, admission control, fair scheduling, backpressure and capacity/load testing.
6. Retention/deletion, malware scanning, secret rotation, backup/restore and DR drills.
7. GPU/model capacity planning and autoscaling runners when scale requires it.

## Completion rule

A helper/class is not a complete feature. A capability reaches PRODUCTION-READY only after real-path integration, executable cancellation/timeout/malformed-input/permission/recovery/compatibility gates, retained benchmark evidence and documented operational boundaries.

## World-class implementation status

- Real-repository benchmark harness foundation: ADDED on `feat/world-class-foundation`; retained corpus expansion and runtime result storage remain.
- Adversarial prompt-injection/secret-canary corpus: ADDED; execution across every ingress remains a release gate.
- Chaos/recovery smoke: ADDED; target-hardware 24/72h retained evidence remains.
- Backup verification: ADDED; restore/DR drill remains required.
- Compatibility report: ADDED; CI wiring across all four repositories remains required.

# Jarvis and Server capability tracker

This tracker uses runtime maturity, not binary feature checkboxes:

- **FOUNDATION** — bounded implementation exists with focused tests.
- **INTEGRATED** — the real runtime path can use it.
- **PRODUCTION-READY** — permissions, cancellation, timeout, malformed input,
  recovery, cleanup, compatibility and release/operations gates pass.
- **MEASURED** — retained benchmark/load/chaos data demonstrates quality,
  latency, cost and regression behavior.

## Shared agent/runtime

| Capability | Maturity | Notes |
| --- | --- | --- |
| Durable Server event persistence/replay | INTEGRATED | Live steerable event stream is separate work |
| Local model token/event streaming | NOT STARTED | Jarvis v0.9 P0 |
| Search/edit/command verification | INTEGRATED | Evidence coverage needs stricter not-run state |
| Transcript checkpoint/resume | INTEGRATED | Multi-agent/local-cloud handoff incomplete |
| Context compaction/token budgets | INTEGRATED | Hard time/currency/scope budgets missing |
| Parallel read-only work/replanning | INTEGRATED | Needs measured token/quality win rate |
| Scoped permissions/prompt-injection boundaries | INTEGRATED | Typed external side effects being hardened |
| Sandbox tiering | INTEGRATED | Windows native isolation missing |
| Multi-expert dispatch | INTEGRATED | Needs retained benchmark proof |
| Heterogeneous model routing | INTEGRATED | Full inference identity/credential isolation hardened in v0.9 |
| Execution-backed completion evidence | INTEGRATED | Zero-test confidence bug tracked P0 |
| Replay evaluations | INTEGRATED | Real-repository benchmark program still FOUNDATION |
| Empirical route calibration | INTEGRATED | Cost-aware hard budget routing incomplete |
| Team/job/schedule/remote contracts | INTEGRATED | PTY/process event contract missing |
| Execution state + lease fencing | INTEGRATED | Real Postgres gate exists |
| Proof + permission contracts | INTEGRATED | Exact proof/run binding under shared workspaces P0 |
| Standard cron semantics | INTEGRATED | UTC, DOM/DOW OR, Sunday 0/7 |
| Hard run/session budgets | NOT STARTED | P0 v0.9 |
| Versioned live agent event vocabulary | FOUNDATION | Existing events need unification for steering |

## Jarvis CLI

| Capability | Maturity | Notes |
| --- | --- | --- |
| Standalone OpenAI-compatible/Anthropic loop | INTEGRATED | Blocking model request path |
| Named profiles/resilient fallback | INTEGRATED | v0.9 fixes same-model endpoint dedup |
| Searchable resumable sessions | INTEGRATED | Single-agent path strongest |
| Text/image/PDF attachments | INTEGRATED | Browser screenshot->vision incomplete |
| Per-hunk review/undo | INTEGRATED | External actions not reversible |
| Instructions/durable memory | INTEGRATED | Needs schema migration policy |
| MCP lifecycle/tool policy | INTEGRATED | Versioned signed distribution incomplete |
| Adaptive multi-agent | INTEGRATED | MEASURED evidence missing |
| Persistent repository graph | INTEGRATED | Python-centric native structural parsing |
| Persistent LSP transport | INTEGRATED | Concurrency/lifecycle hardened |
| Direct LSP semantic agent tools | FOUNDATION | Rich client exists, normal tool surface is narrow |
| Plan mode and OS/network sandbox | INTEGRATED | Browser click side-effect classification fixed v0.9 |
| TUI | INTEGRATED | Live model delta/steer missing |
| Skills/Hooks | INTEGRATED | Signed immutable skill versions missing |
| Team board/worktrees | INTEGRATED | Needs long-running recovery metrics |
| Browser/Playwright verification | INTEGRATED | Cleanup/vision/consequential-action policy gaps |
| Plugin packaging | INTEGRATED | Publisher trust roots/lockfile missing |
| Jobs/cron/process-tree cancellation | INTEGRATED | Agent-facing PTY/process tool missing |
| OpenTelemetry/calibration | INTEGRATED | Cost/SLO dashboard incomplete |
| Python SDK/remote Runs | INTEGRATED | TypeScript SDK missing |
| Portable Git cloud workspaces | INTEGRATED | First-class local-cloud handoff missing |
| Fenced cancellable cloud workers | INTEGRATED | Chaos evidence missing |
| Idempotent cloud tasks | INTEGRATED | Needs retained replay evidence |
| Durable proof ledger | INTEGRATED | Exact concurrent proof binding P0 |
| Deterministic permissions | INTEGRATED | Moving toward typed side-effect capabilities |
| Autonomous dashboard | INTEGRATED | Live cost/process/event panes missing |
| Structural context compiler | INTEGRATED | Polyglot precision/recall unmeasured |
| Selective speculation/escalation | INTEGRATED | Needs hard budget/cost objective |
| Failure memory/retries | INTEGRATED | Retention/schema policy needed |
| Impact-aware verification | INTEGRATED | Not-run/passed distinction P0 |
| Tool-result dedup/patch minimization | INTEGRATED | Per-run digest scope hardened |
| Adversarial reliability benchmark | FOUNDATION | Insufficient for parity claim |
| Local streaming/steering | NOT STARTED | P0 |
| Persistent PTY/process tool | NOT STARTED | P0 |
| Polyglot native structural parser | NOT STARTED | P0 |
| Local-cloud handoff/attempt compare | NOT STARTED | P1 |
| Versioned signed Skills | FOUNDATION | P1 |
| TypeScript SDK | NOT STARTED | P1 |
| Native Windows AppContainer | NOT STARTED | P1/P2 |

## Server / AI Stack

| Capability | Maturity | Notes |
| --- | --- | --- |
| Durable Runs execution | INTEGRATED | Live bidirectional steering P0 |
| Model readiness/Ollama-LiteLLM smoke | INTEGRATED | Real native tool-call fixture |
| Web/Telegram/WhatsApp ingress | INTEGRATED | Multi-tenant identity hardening incomplete |
| Durable outbound delivery/retry | INTEGRATED | Channel safety/load evidence needed |
| Provider health/routing | INTEGRATED | Hard cost/admission budgets incomplete |
| Claim/source evidence | INTEGRATED | Evidence coverage metrics incomplete |
| MCP persistent lifecycle/policy | INTEGRATED | Org/versioned tool distribution incomplete |
| Instructions/persistent memory | INTEGRATED | Tenant retention/deletion incomplete |
| Heterogeneous expert routes | INTEGRATED | Needs measured route baselines |
| Execution-backed evidence gate | INTEGRATED | False-completion metric required |
| Durable schedules | INTEGRATED | Standard reviewed Runs path |
| Portable cloud queue/workers | INTEGRATED | Runner provenance/attach gap |
| Per-attempt lease fencing | INTEGRATED | Postgres integration coverage exists |
| Idempotent cloud submissions | INTEGRATED | Replay/chaos evidence needed |
| Durable cloud state machine | INTEGRATED | Explicit states/fences |
| Cloud cancellation/stale-result rejection | INTEGRATED | Long partition chaos pending |
| Cloud proof persistence | INTEGRATED | Exact proof ID binding P0 |
| OpenTelemetry platform traces | INTEGRATED | SLO/cost dashboards partial |
| Route calibration from outcomes | INTEGRATED | Price/budget policy missing |
| Persistent failure signatures | INTEGRATED | Retention/schema version needed |
| Cross-repo Core/CLI/Server Postgres gate | INTEGRATED | Requires executable Actions availability |
| Per-run token/cost accounting | FOUNDATION | P0 budget enforcement |
| Live stream/steer/interrupt/approval events | FOUNDATION | P0 |
| OIDC/OAuth tenant isolation | NOT STARTED | P0 for multi-tenant certification |
| Quotas/admission/queue fairness | NOT STARTED | P0 |
| Artifact encryption/lifecycle | FOUNDATION | P1 |
| Backup/restore/DR drills | NOT STARTED | P1/P2 |
| TypeScript SDK | NOT STARTED | P1 |

## v0.9 P0 — correctness

1. Full provider/model/base-URL/credential inference identity for fallback and
   role routing.
2. No cross-provider/global-key leakage during heterogeneous routing.
3. Browser click/type and future external writes enter explicit side-effect
   approval policy; plan mode denies them.
4. Evidence states distinguish not-run/passed/failed/blocked and cap confidence
   without executable/independent verification.
5. Cloud completion binds exact local run/proof ID under concurrent shared
   workspaces.
6. Repository/file enumeration is bounded before traversing generated/vendor
   trees.

## v0.9 P0 — responsive execution

1. Versioned model/tool/process event vocabulary.
2. Local model streaming plus user steer/interrupt.
3. Server reconnectable event cursor plus durable steer/interrupt/approval.
4. Guarded persistent process/PTY lifecycle with stdout/stderr/stdin/attach/kill.
5. Deterministic browser/process/LSP cleanup on cancellation and abnormal exit.

## v0.9 P0 — intelligence and budgets

1. Direct LSP definition/reference/hover/diagnostic tools.
2. Guarded LSP rename/code-action plans through the patch/review ledger.
3. Tree-sitter/language-native structural graph for major languages.
4. Hard time/token/attempt/cost/change-scope/network/process budgets.
5. Retained real-repository benchmark + chaos artifacts with false-completion
   rate as a release metric.

## P1 — competitive workflows

- provider-specific streaming/Responses fast paths while preserving generic
  OpenAI-compatible local endpoints;
- structured digest-guarded edits;
- screenshot->vision browser evidence;
- local-to-cloud session/worktree handoff, multiple attempts and compare/sync;
- signed immutable Skill/plugin versions and lockfiles;
- TypeScript SDK parity;
- rich live TUI/Runs UI panes for events/process/cost/proof/approvals.

## P2 — production platform

- native Windows isolation;
- tenant-scoped encryption, tamper-evident audit and systematic secret rotation;
- retention/deletion/export, malware scanning, backup/restore and DR;
- queue/load/capacity SLOs and autoscaling only where operationally justified;
- reproducible runner/sandbox image provenance and canary/rollback.

## Completion rule

A capability is not complete because a helper or endpoint exists. The real user
path must be exercised for permission denial, timeout, cancellation, malformed
input, recovery, compatibility and resource cleanup before PRODUCTION-READY.
MEASURED additionally requires retained quality/latency/cost evidence.

See [docs/world-class-platform-gaps.md](docs/world-class-platform-gaps.md) and the
Jarvis `docs/world-class-gap-analysis.md` for the detailed dated audit.

# World-class Server platform gaps

Audit date: 2026-08-25

AI Stack Server is the optional durable control plane for Jarvis. This document
tracks the gap between the current self-hosted runtime and a production-grade,
steerable multi-user coding-agent platform.

The target is not brand imitation. The target is durable, measurable,
side-effect-safe engineering execution that can support local workers, remote
workers, web/mobile clients and thin channel adapters without duplicating the
agent runtime.

## Current strengths

The current Server already has important foundations:

- durable PostgreSQL Runs, events, conversations, schedules and failure memory;
- bounded artifacts and document/retrieval pipelines;
- local Ollama/LiteLLM and controlled remote-provider routing;
- provider health/fallback and measured route metadata;
- web evidence and persistent policy-controlled MCP;
- hierarchical instructions/memory;
- reviewed sandbox changes and durable approval/discard;
- standard cron scheduling;
- portable Git cloud workspaces;
- idempotent cloud submissions;
- unique lease fence per worker attempt;
- stale heartbeat/state/completion rejection;
- killable worker execution and terminal cancellation;
- execution proof persistence;
- outbound Telegram/WhatsApp delivery with retry;
- OpenTelemetry and JSON/evidence event foundations;
- first-class Postgres fencing/idempotency CI coverage.

## P0: live run event and steering protocol

A durable task should not mean a user waits for a final response. Add one
versioned event vocabulary shared by CLI, web/mobile and channels:

- run status/state transition;
- model delta/status;
- tool proposed/started/output/completed;
- approval requested/resolved;
- process output;
- checkpoint;
- evidence/proof update;
- cost/budget update;
- completion/error/cancellation.

Clients must be able to submit a **steer** or **interrupt** command while a run is
active. The runtime must checkpoint the new instruction and avoid replaying
already completed mutations.

Acceptance:

1. disconnect/reconnect resumes from an event cursor;
2. repeated client commands are idempotent;
3. stale approval/steer messages cannot affect another run/attempt;
4. cancellation propagates to model/process/tool work;
5. channel clients can degrade from streaming to bounded progress updates.

## P0: hard budgets and cost accounting

Token budgets alone are insufficient for autonomous shared execution. Every run
should snapshot hard limits for:

- wall-clock duration;
- model/tool attempts;
- input/output tokens;
- estimated provider cost/local compute weight;
- changed files/lines;
- browser/network side effects;
- spawned processes;
- artifact bytes.

Budget crossing must transition the run to an explicit paused/failed state. A
router must never silently escalate to a paid provider beyond the user's policy
or budget.

Persist budget decisions in events/proof and expose them in Runs UI.

## P0: tenant identity and authorization

Before multi-tenant production claims, implement:

- OIDC/OAuth identity;
- tenant/project membership and roles;
- scoped service/channel tokens;
- resource-level authorization for runs, workspaces, artifacts and approvals;
- tenant-scoped secrets and encryption boundaries;
- cross-tenant negative tests for every list/get/action endpoint.

API keys remain suitable for controlled single-tenant/self-hosted deployments,
but should not be described as complete tenant isolation.

## P0: quotas, admission and queue fairness

Add explicit admission policy for shared runners/providers:

- per-tenant/user/project concurrency;
- queue depth and priority limits;
- provider/model saturation/circuit state;
- fair scheduling to avoid one tenant monopolizing workers;
- bounded retries and dead-letter state;
- graceful drain during deploy/restart;
- capacity/SLO metrics and alerts.

Load tests must include cancellation/reclaim under contention, not only request
throughput.

## P0: exact proof/run binding

A cloud completion must identify the exact execution proof for its lease/run.
Never infer proof ownership from a mutable workspace `latest` pointer when
concurrent tasks can share a mounted path.

Bind proof ID/run ID/lease ID in the durable completion predicate and preserve
it in audit/export data.

## P0: runner/process lifecycle

Remote execution needs the same lifecycle guarantees expected from local tools:

- process tree ownership;
- stdout/stderr streaming bounds;
- stdin/attach where policy permits;
- hard timeout and cancellation;
- cleanup after worker crash/reclaim;
- sandbox/network policy snapshot bound to the attempt;
- reproducible toolchain/sandbox image identity in proof.

## P0: benchmark + chaos program

Retain release artifacts for:

- hidden-test bug fixes and refactors;
- false-completion/regression rate;
- provider/model route success;
- token/cost/latency distributions;
- queue wait and execution time;
- approval burden;
- worker crash/reclaim;
- Server restart;
- Postgres reconnect/failover conditions;
- network partition and delayed heartbeat;
- duplicated channel webhook/delivery;
- artifact/storage failure;
- OTLP exporter failure.

A retry is correct only if previously completed side effects are not duplicated.

## P1: local-to-cloud handoff

Support a first-class Jarvis handoff payload containing:

- exact repository URL + commit;
- dirty diff/content digests;
- canonical transcript/checkpoint;
- task and user steer history;
- permission/budget/model policy snapshot;
- attachments/artifact references;
- proof linkage.

Server may run one or several isolated attempts. Jarvis can compare results and
apply the chosen patch back through its normal review/undo path. Provider
credentials never travel in the handoff payload.

## P1: versioned Skills/plugins and policy snapshots

Runs should record immutable references to the Skill/plugin/policy versions they
used. Add:

- semantic versions;
- signatures/publisher trust roots;
- provenance/checksum;
- declared filesystem/network/tool capabilities;
- organization/project/user scope;
- lockfile and rollback;
- compatibility/deprecation tests.

This makes a historical run reproducible and auditable when current policy or
Skills later change.

## P1: artifact lifecycle

Move beyond a filesystem artifact root for production deployments:

- object storage abstraction;
- tenant/project encryption and access policy;
- size/type limits and malware/quarantine pipeline for uploads;
- retention/deletion/export policy;
- lifecycle/expiry jobs;
- backup/restore validation;
- content digests and immutable provenance.

## P1: Runs UI and remote clients

The UI should expose the same durable event stream rather than polling separate
ad-hoc views. Required panes include:

- live model/tool/process activity;
- diff and approval;
- evidence/proof;
- budget/cost;
- route/escalation decisions;
- worker/lease state;
- event replay and user steer history.

Add a TypeScript SDK generated or contract-tested against the versioned protocol
for web/mobile consumers.

## P1: channel safety

Telegram/WhatsApp/web/mobile are thin clients, but production operation needs:

- identity linking;
- signed expiring approval actions;
- stale-thread/run protection;
- attachment quarantine and provenance;
- deduplicated ingress and outbound delivery;
- provider window/template/rate-limit policy isolated inside adapters;
- privacy-safe push/progress content.

A plain text `yes` in an unrelated/stale chat must never authorize a sensitive
action.

## P2: compliance/operations

- tamper-evident audit log/export;
- systematic secret rotation and access review;
- backup/restore and disaster-recovery drills;
- retention/legal deletion controls;
- documented SLOs/on-call alerts;
- canary deployment/rollback;
- multi-region only when operational need justifies the complexity.

## Maturity rule

Server capabilities are promoted from INTEGRATED to PRODUCTION-READY only after
permission, timeout, cancellation, malformed input, recovery, compatibility,
resource cleanup and operational tests exercise the real durable path.

They become MEASURED only when retained benchmark/load/chaos evidence shows the
intended quality, latency and cost behavior.

The cross-product coding-agent gap analysis is maintained in Jarvis at
`docs/world-class-gap-analysis.md`.

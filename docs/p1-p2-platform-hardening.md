# P1/P2 platform hardening

This document records the stable extension points added after the autonomous runtime hardening pass.

## Inference/provider status

`GET /engineering/inference/status` reports mode, configured primary/fallback providers, role aliases, gateway health and available models. Credentials are never returned. Jarvis exposes this as:

```bash
jarvis cloud inference-status --server http://ai-stack:8000
```

The agent loop remains provider-neutral. Changing `HF_*` model settings or replacing the OpenAI-compatible gateway does not require changing Jarvis Core or tools.

## Isolated subagents and proof lineage

`jarvis-core` now exports `AgentLineage` and `LineageProof`. A child task records `parent_task_id`, `root_task_id`, depth and role. Its result/evidence digests are bound into a tamper-evident proof digest. The contract contains no provider-specific concepts and can be used by future multi-agent runtimes without a Core redesign.

## GitHub-native engineering loop

The authenticated API exposes:

- `POST /engineering/github/issue-bootstrap`: issue -> base branch -> dedicated branch -> optional PR.
- `GET /engineering/github/pull/{owner}/{repo}/{number}`: PR state, mergeability, check runs and review summary.

The CLI exposes `jarvis cloud github-issue` and `jarvis cloud github-review`. Actual code execution continues to happen in disposable worktrees with the existing approval/proof/sandbox gates.

## IDE protocol

`jarvis ide serve` provides a newline-delimited JSON-RPC 2.0 bridge. The protocol is model-neutral and currently supports initialize, capabilities, workspace list/read, agent run and shutdown. Editors should integrate against this protocol rather than provider APIs.

## Background automation

Durable interval/cron schedules already run through PostgreSQL and the platform monitor. Scheduled work uses the same Run path, sandbox and evidence lifecycle as interactive work.

## Model telemetry

The existing LLM latency/cache/scheduler metrics are exposed through `GET /engineering/llm/metrics`. The new usage instrumentation captures prompt/completion/total tokens and estimated USD cost when the gateway returns usage metadata. Pricing is supplied through `LLM_PRICING_JSON`, for example:

```json
{"coder":{"input_usd_per_million":0.2,"output_usd_per_million":0.8}}
```

No pricing is hard-coded into the agent loop.

## Backup / disaster recovery

`scripts/backup_restore_dr_check.py` restores a custom-format `pg_dump` only into an explicitly configured disposable `DR_RESTORE_DATABASE_URL`. Production restore targets are intentionally rejected by process/documentation policy. Certification is only claimed after restore plus application smoke tests pass.

## Worker soak

`scripts/worker_soak.py` performs a non-mutating long-duration health soak. `.github/workflows/soak.yml` validates the harness weekly/manual and defaults to a 24-hour certification duration. Production certification still requires an environment with real PostgreSQL/worker infrastructure.

## Optional identity/tenancy

`TENANCY_ENABLED=true` requires `X-Jarvis-Tenant` on engineering identity requests and supports `X-Jarvis-User`. The service returns only a non-reversible principal digest. This is an identity boundary, not a claim of full database row-level multi-tenancy; full tenant certification still requires tenant-aware durable schemas, quotas and audit/retention policy.

## Extension contract

The long-term rule is:

```text
IDE / CLI / channel
        -> Jarvis protocol
        -> Core contracts
        -> AI Stack orchestration
        -> provider router
        -> model endpoint
```

Provider/model changes stay below the orchestration boundary. Security, evidence, sandboxing, leases and review are model-independent.


## Cross-repository compatibility contract

The deployed platform has independent application versions but shared protocol versions.

| Component | Responsibility | Contract |
| --- | --- | --- |
| jarvis-core | semantic/runtime primitives | dependency-free contracts |
| jarvis | terminal client | Jarvis Agent Protocol v1 / event schema v1 |
| ai-stack | control plane | Jarvis Agent Protocol v1 |
| jarvis-inference | model data plane | Inference Protocol v1 |
| Ollama | model backend | private implementation detail |

Compatibility is protocol-based rather than application-version based. A deployment is valid only when Jarvis and AI Stack advertise overlapping Agent Protocol versions and AI Stack and jarvis-inference advertise Inference Protocol v1. scripts/preflight.sh validates the inference side of this contract before deployment is considered ready.

### End-to-end contract smoke

For a deployed stack, configure INFERENCE_BASE_URL, INFERENCE_API_KEY, AI_STACK_BASE_URL, and AGENT_API_KEY, then run:

    python tests/integration/remote_provider_smoke.py

This validates inference capabilities, the embedding path, and the AI Stack -> inference diagnostic path before optional third-party provider probes.

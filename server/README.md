# AI Stack Server

Server is the optional always-online control plane for long-running agent execution. It is not required for local-only development.

## Development deployment

```bash
cp .env.example .env
docker compose config
docker compose up -d
docker compose ps
```

In production, use long unique service keys, mount `ARTIFACT_ROOT` on persistent storage, expose the API through TLS/VPN, and mount only workspaces the agent may access.

## Models and routing

Server supports local Ollama/LiteLLM routes and controlled OpenAI-compatible remote endpoints. `JARVIS_MODEL_PROFILES_JSON` optionally describes model capabilities; model `auto` selects an available profile satisfying requested capabilities.

Routing uses one front-facing orchestrator identity and validated internal workflows for quick, code, research, finance, deep, and vision tasks. Router output cannot invent a model, grant tools, expand a workspace, or bypass approval.

## Shared runtime, traces, and evaluations

Server installs the verified `jarvis-agent-core==0.15.0` package for token enforcement, context efficiency, artifacts, evidence, capability routing, recovery, redacted traces, evaluations, selective multi-agent contracts, sandbox requirements and provider-neutral model contracts.

Core 0.15.0 adds provider-neutral context budgets, token/cost estimation, route budgets and adaptive route signals while retaining the application boundary around concrete Ollama/LiteLLM/remote-provider integration. Provider SDKs remain out of Core.

For a clean development installation:

```bash
python -m pip install -r server/requirements.txt
```

The human-maintained requirements, lockfile, CI, and Docker image all consume the same public Core 0.15.0 dependency and verify the installed version. For unreleased Core development only, install a local Core checkout explicitly after the locked dependencies.

## MCP

Administrators may configure fixed stdio commands. The model chooses only a configured alias and tool name. It cannot choose the executable or invoke a shell. MCP output remains untrusted and cannot broaden Server permissions.

## Server capabilities

- durable runs, conversations, events, replay, cancellation, and client leases;
- PostgreSQL state, Redis coordination, Qdrant retrieval, and artifact storage;
- document ingestion, OCR, hybrid retrieval, and source provenance;
- isolated commands and disposable Git worktrees;
- explicit approval/discard for write diffs;
- capability routing, bounded recovery, context compaction, and token budgets;
- multi-expert analysis for selected complex requests;
- Runs UI and OpenAI-compatible entry points;
- web evidence and administrator-selected MCP tools;
- versioned client protocol and advertised feature flags.

## Production gates

Before describing a deployment as production-certified, prove:

- OIDC/OAuth identities, tenant isolation, scoped tokens, and central policy;
- idempotent APIs/webhooks, dead-letter handling, graceful drain, and restores;
- runner isolation, secret rotation, signed webhooks, SBOM/provenance, and audit;
- OpenTelemetry, SLOs, queue/lease/model saturation, tokens, latency, and cost;
- quotas, backpressure, capacity tests, model circuit breakers, and fallback;
- retention/deletion/export, malware scanning, backups, and disaster recovery;
- adversarial prompt/tool tests, replay evals, route benchmarks, canaries, and rollback procedures.

## Channel integrations

Telegram, WhatsApp, web, and mobile integrations are thin authenticated adapters:

```text
channel webhook/app
  -> identity/signature adapter
  -> Server Runs API
  -> durable execution and approvals
  -> queued channel-safe reply
```

They do not contain another agent loop. See [product architecture](../docs/product-architecture.md) for identity, idempotency, attachment, delivery, and approval requirements.

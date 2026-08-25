# AI Stack Server

AI Stack Server is the optional durable, self-hosted control plane for the
Jarvis ecosystem. The primary local coding-agent product is
[Jarvis](https://github.com/abdullahalrifat/jarvis); provider-neutral contracts
live in [Jarvis Core](https://github.com/abdullahalrifat/jarvis-core).

Use Jarvis when code/tools should stay on the developer machine. Deploy Server
when work must survive client disconnects, run on remote workers, serve shared
users/channels, process durable documents or enforce centralized policy.

The current coordinated runtime consumes the immutable public
`jarvis-agent-core` **0.8.0** wheel:

```text
SHA-256 d9569b69385e58a681ea01e900eb81c395d3f202a09a92878eb82bf4d4b8618a
```

## Components

- `server/` — FastAPI control plane, durable Runs and cloud-task state;
- `runs-ui/` — run/evidence/approval interface;
- PostgreSQL — durable runs, events, schedules, failure memory and cloud leases;
- Redis/Qdrant — coordination/retrieval where configured;
- SearXNG — optional self-hosted current-information search;
- LiteLLM + Ollama or remote endpoints — controlled inference routing;
- `contracts/` — versioned Server/Jarvis protocol;
- `jarvis-agent-core` — portable token/evidence/routing/execution contracts.

## Development stack

```bash
cp .env.example .env
docker compose config
docker compose up -d
docker compose ps
```

Standalone Jarvis users do not need this stack.

## Runtime capabilities

### Durable Runs

Server owns durable conversations, runs, events, client leases, replay,
cancellation, review/approval and artifact state. Channel adapters normalize
Telegram/WhatsApp/web/mobile input into the same Runs engine instead of creating
parallel agent implementations.

### Model and evidence runtime

- local Ollama/LiteLLM and configured remote provider routes;
- model readiness/provisioning and real native-tool integration smoke tests;
- heterogeneous expert routing and measured route observations;
- provider health/fallback and failure signatures;
- token/context/artifact bounds;
- execution-backed evidence/confidence events;
- web evidence with source provenance;
- persistent policy-controlled MCP;
- hierarchical instructions and persistent memory.

### Autonomous cloud execution

- portable existing-path or Git workspace descriptors;
- idempotent task submissions;
- unique lease fence per attempt;
- stale heartbeat/state/completion rejection;
- explicit execution state machine;
- worker cancellation/lease-loss handling;
- model/profile propagation without provider credentials in task payloads;
- cloud execution proof persistence;
- real Postgres fencing/idempotency integration-test job.

## Product boundary

| Concern | Jarvis | Server | Core |
| --- | --- | --- | --- |
| Local checkout tools/interactive approvals | Owns | No | No |
| Durable shared runs/events/queues/channels | Client | Owns | Contracts only |
| Remote cloud workspaces/workers | Worker/client | Owns durable queue | Contracts only |
| Token/evidence/routing vocabulary | Consumes | Consumes | Owns |
| Tenant/channel identity | No | Owns | No |
| OS sandbox implementation | Local | Remote runner | No |

See [docs/product-architecture.md](docs/product-architecture.md).

## Documentation

- [Server deployment and operations](server/README.md)
- [Product architecture](docs/product-architecture.md)
- [v0.8 autonomous runtime](docs/v0.8-autonomous-runtime.md)
- [v0.9 world-class platform gaps](docs/world-class-platform-gaps.md)
- [Capability tracker](TODO.md)
- [Jarvis CLI](https://github.com/abdullahalrifat/jarvis)
- [Jarvis Core](https://github.com/abdullahalrifat/jarvis-core)

## Production boundary

The current code is an advanced self-hosted platform, but multi-tenant
production certification still requires evidence for capabilities that are not
yet complete:

- OIDC/OAuth identities, tenant isolation and scoped service tokens;
- live bidirectional run streaming with steer/interrupt/approval events;
- quotas, admission control, queue fairness/backpressure and capacity SLOs;
- hard per-run/session cost/time/token budgets and cost-aware routing;
- encrypted artifact lifecycle, retention/deletion/export, backup/restore and DR;
- tamper-evident audit events and systematic secret rotation;
- reproducible runner/sandbox provenance and long-running chaos/load tests;
- TypeScript SDK parity with the versioned protocol.

These gaps are tracked explicitly in
[docs/world-class-platform-gaps.md](docs/world-class-platform-gaps.md) rather than
being hidden behind a generic “world-class” label.

## Supply chain and validation

Server requirements, lockfile, Dockerfile and CI pin the same Core 0.8.0 release
artifact. Tagged Server images build an SPDX SBOM and provenance metadata.

Release candidates should execute:

1. Server unit/coverage validation;
2. real Postgres lease/fencing/idempotency integration;
3. Compose and Runs UI checks;
4. Supply Chain image build/SBOM;
5. Ollama/LiteLLM native tool-call integration;
6. cross-repository Core/Jarvis/Server compatibility;
7. configured remote-provider smoke tests where secrets exist;
8. benchmark and chaos evidence for long-running autonomous work.

See [.env.example](.env.example) for configuration.

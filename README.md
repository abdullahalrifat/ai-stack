# AI Stack Server

## Current contract line

AI Stack consumes the provider-neutral **Jarvis Core 0.12.0** runtime contracts and normalization helpers. It does not install or require the standalone CLI. The runner API can start independently of optional network-namespace capability, reports isolation availability explicitly, and keeps isolated commands fail-closed when that capability is unavailable.

Jarvis Core 0.12.0 is now published as an immutable PyPI release. Server requirements, the lockfile, Docker image and CI all pin that exact release.

AI Stack Server is the durable, self-hosted control plane for long-running agent execution.

## Components

- `server/`: FastAPI control plane and durable Runs/cloud-task runtime;
- `runs-ui/`: run, evidence and approval interface;
- PostgreSQL/Redis/Qdrant: durable state, coordination and retrieval;
- SearXNG: optional self-hosted current-information search;
- LiteLLM plus Ollama or remote compatible providers: inference routing;
- `contracts/`: versioned Server client protocol;
- `jarvis-agent-core` **0.12.0**: separately versioned provider-neutral runtime contracts and reusable primitives.

Server requirements, lockfile, Docker image and CI assert the same Core version.

## Start the development stack

```bash
cp .env.example .env
docker compose config
docker compose up -d
docker compose ps
```

## Offline / off-grid operation

AI Stack is designed to run locally without external model providers. Ollama hosts local models, LiteLLM provides the OpenAI-compatible routing surface, and PostgreSQL/Redis/Qdrant provide local state. Remote provider variables are optional and should remain empty for an isolated deployment.

For a genuinely off-grid deployment, provision Docker images and Ollama models while connected, then disconnect the host. Use the dedicated offline Compose override:

```bash
docker compose -f docker-compose.yaml -f docker-compose.offline.yaml config
docker compose -f docker-compose.yaml -f docker-compose.offline.yaml up -d
docker compose -f docker-compose.yaml -f docker-compose.offline.yaml ps
```

The offline override disables external web search, clears remote-provider configuration, and makes model provisioning fail closed if a configured Ollama model is absent from the persistent `./ollama` volume. It never runs `ollama pull` while offline.

## Durable autonomous execution

Server cloud tasks support portable workspace descriptors, idempotent submissions, unique lease fencing, durable execution states/proof, heartbeat/lease expiry, stale-result rejection, terminal cancellation and model/profile selection without transmitting provider credentials. PostgreSQL CI exercises durable contention/fencing behavior, and protocol conformance verifies the Core contract.

## Security and isolation boundary

Server does **not** treat repository/model/tool output as trusted. Search results, documents, web/browser content, model responses and external tool results remain untrusted evidence.

Cloud worker fencing prevents stale ownership and duplicate result publication; it is not strong multi-tenant isolation. Shared-host production requires per-task container/VM isolation, CPU/RAM/PID/disk quotas, syscall confinement and explicit egress controls. The shared Core sandbox policy is a contract; deployment must still enforce it.

Provider credentials stay on execution hosts and are not embedded in cloud task payloads. Operators should use separate worker identities/secrets and narrow repository/network allowlists.

## Validation and certification

The current 0.12.0 Core contract line has executable validation for Server tests, UI tests/build, PostgreSQL durable lease/fencing integration, Compose runner readiness/isolation reporting, cross-repository Core protocol conformance, real-repository evaluation, adversarial secret-canary/prompt-injection coverage, distributed chaos coverage and cloud sandbox policy checks. These gates establish tested contract health; they are not a claim that every model or hardware configuration is production-certified.

Remaining certification work is retained longitudinal model-quality measurement, hardware-specific soak testing, fully reproducible offline/bootstrap validation and backup/restore/disaster-recovery exercises.

## World-class gaps

The remaining platform work is deliberately explicit:

- retained real-repository issue-resolution benchmark history across local models;
- model-quality/latency/cost scorecards for each supported local profile;
- network-partition/restart/lease/cancellation/state-failure soak testing on target hardware;
- deterministic offline image/model bootstrap and cache verification;
- queue admission control, fairness, backpressure and autoscaling signals for multi-worker deployments;
- OIDC/OAuth tenant identity and enterprise policy for shared deployments;
- GitHub PR/issue and Slack integrations with explicit approval/identity boundaries;
- retention/deletion, audit export, backup/restore and disaster-recovery drills;
- fleet compatibility reporting across Core/CLI/Server/workers;
- cost/token/latency and escalation dashboards.

See [TODO.md](TODO.md) for prioritized maturity tracking.

## Documentation

- [Server deployment and operations](server/README.md)
- [Product architecture](docs/product-architecture.md)
- [Core dependency and release policy](docs/dependencies-and-releases.md)
- [Capability / production tracker](TODO.md)
- [Server protocol](contracts/jarvis-protocol-v1.json)

See [.env.example](.env.example) for the configuration surface.

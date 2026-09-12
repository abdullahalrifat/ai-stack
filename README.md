# AI Stack Server

## Current contract line

AI Stack consumes the provider-neutral **Jarvis Core 0.9.5** protocol contract. It does not import, install, or require the Jarvis CLI. The runner API can start independently of optional network-namespace capability, reports isolation availability explicitly, and keeps isolated commands fail-closed when that capability is unavailable.

AI Stack Server is the optional durable, self-hosted control plane for the Jarvis ecosystem. The standalone local product is [`jarvis`](https://github.com/abdullahalrifat/jarvis); provider-neutral runtime contracts are in [`jarvis-core`](https://github.com/abdullahalrifat/jarvis-core).

## Components

- `server/`: FastAPI control plane and durable Runs/cloud-task runtime;
- `runs-ui/`: run, evidence and approval interface;
- PostgreSQL/Redis/Qdrant: durable state, coordination and retrieval;
- SearXNG: optional self-hosted current-information search;
- LiteLLM plus Ollama or remote compatible providers: inference routing;
- `contracts/`: versioned Server client protocol;
- `jarvis-agent-core` **0.9.5**: separately versioned provider-neutral shared contracts and sandbox policy primitives.

Server requirements, lockfile, Docker image and CI assert the same Core version.

## Start the development stack

```bash
cp .env.example .env
docker compose config
docker compose up -d
docker compose ps
```

This stack is for Server; standalone Jarvis users do not need it.

## Offline / off-grid operation

AI Stack is designed to run locally without external model providers. Ollama hosts the local models, LiteLLM provides the OpenAI-compatible routing surface, and PostgreSQL/Redis/Qdrant/Open WebUI provide local state and UI. Remote Anthropic/Hugging Face variables are optional and should remain empty for an isolated home deployment.

For a genuinely off-grid deployment, provision the Docker images and Ollama models while connected, then disconnect the host. Set `WEB_SEARCH_ENABLED=false`, leave all remote-provider credentials empty, and ensure every configured Ollama model is already present in the persistent `./ollama` volume. Offline startup should fail rather than silently attempting a network pull when a required model is missing.

For a home server, expose only localhost ports or put an explicitly authenticated reverse proxy in front of the UI/API. Do not enable insecure no-auth mode on a LAN/WAN-facing host.

## Durable autonomous execution

Server cloud tasks support portable workspace descriptors, idempotent submissions, unique lease fencing, durable execution states/proof, heartbeat/lease expiry, stale-result rejection, terminal cancellation and model/profile selection without transmitting provider credentials. PostgreSQL CI exercises durable contention/fencing behavior, and cross-repository CI verifies the Core protocol contract.

The matching CLI worker executes the coding agent and publishes only while it owns the current fence.

## Security and isolation boundary

Server does **not** treat repository/model/tool output as trusted. Search results, documents, web/browser content, model responses and external tool results remain untrusted evidence.

Cloud worker fencing prevents stale ownership and duplicate result publication; it is **not** strong multi-tenant isolation. The current worker model is appropriate for trusted single-tenant/self-hosted deployments. Shared-host production requires per-task container/VM isolation, CPU/RAM/PID/disk quotas, syscall confinement and explicit egress controls. The current codebase includes the shared Core sandbox policy and Server-side sandbox adapter; deployment must still enable and validate the policy rather than assuming the primitive alone provides isolation.

Provider credentials stay on execution hosts and are not embedded in cloud task payloads. Operators should use separate worker identities/secrets and narrow repository/network allowlists.

## Validation and certification

The current 0.9.5-contract line has executable validation for Server tests, UI tests/build, PostgreSQL durable lease/fencing integration, Compose runner readiness/isolation reporting, cross-repository Core protocol conformance, real-repository evaluation, adversarial secret-canary/prompt-injection coverage, distributed chaos coverage and cloud sandbox policy checks. These gates establish tested contract health; they are **not** a claim that every model or hardware configuration is production-certified.

Remaining certification work is retained longitudinal model-quality measurement, hardware-specific soak testing, fully reproducible offline/bootstrap validation and backup/restore/disaster-recovery exercises.

## World-class gaps

The remaining platform work is deliberately explicit:

- retained real-repository issue-resolution benchmark history across local models;
- model-quality/latency/cost scorecards for each supported local profile;
- network-partition/restart/lease/cancellation/state-failure soak testing on the target home hardware;
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
- [v0.8 autonomous runtime](docs/v0.8-autonomous-runtime.md)
- [Capability / production tracker](TODO.md)
- [Standalone Jarvis](https://github.com/abdullahalrifat/jarvis)
- [Jarvis Core](https://github.com/abdullahalrifat/jarvis-core)
- [Server protocol](contracts/jarvis-protocol-v1.json)

See [.env.example](.env.example) for the configuration surface.

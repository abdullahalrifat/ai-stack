# AI Stack Server

## Current contract line

AI Stack consumes only the provider-neutral Jarvis Core 0.9.2 protocol contract. It does not import, install, or require the Jarvis CLI. The runner API can start independently of optional network-namespace capability, reports isolation availability explicitly, and keeps isolated commands fail-closed when that capability is unavailable.

AI Stack Server is the optional durable, self-hosted control plane for the Jarvis ecosystem. The standalone local product is [`jarvis`](https://github.com/abdullahalrifat/jarvis); provider-neutral runtime contracts are in [`jarvis-core`](https://github.com/abdullahalrifat/jarvis-core).

## Components

- `server/`: FastAPI control plane and durable Runs/cloud-task runtime;
- `runs-ui/`: run, evidence and approval interface;
- PostgreSQL/Redis/Qdrant: durable state, coordination and retrieval;
- SearXNG: optional self-hosted current-information search;
- LiteLLM plus Ollama or remote compatible providers: inference routing;
- `contracts/`: versioned Server client protocol;
- `jarvis-agent-core` **0.9.2**: separately versioned provider-neutral shared contracts.

The Server pins the immutable Jarvis Core 0.9.2 release wheel:

```text
https://github.com/abdullahalrifat/jarvis-core/releases/download/v0.9.2/jarvis_agent_core-0.9.2-py3-none-any.whl
SHA-256 0ff9b5cfba29dca8d05df69a48573c3a69cc73ca9654e7122411b89a489f1130
```

Server requirements, lockfile, Docker image and CI assert the same Core version.

## Start the development stack

```bash
cp .env.example .env
docker compose config
docker compose up -d
docker compose ps
```

This stack is for Server; standalone Jarvis users do not need it.

## Durable autonomous execution

Server cloud tasks support portable workspace descriptors, idempotent submissions, unique lease fencing, durable execution states/proof, heartbeat/lease expiry, stale-result rejection, terminal cancellation and model/profile selection without transmitting provider credentials. PostgreSQL CI exercises durable contention/fencing behavior, and cross-repository CI verifies the Core protocol contract.

The matching CLI worker executes the coding agent and publishes only while it owns the current fence.

## Security and isolation boundary

Server does **not** treat repository/model/tool output as trusted. Search results, documents, web/browser content, model responses and external tool results remain untrusted evidence.

Cloud worker fencing prevents stale ownership and duplicate result publication; it is **not** strong multi-tenant isolation. The current worker model is appropriate for trusted single-tenant/self-hosted deployments. Shared-host production requires per-task container/VM isolation, CPU/RAM/PID/disk quotas, syscall confinement and explicit egress controls.

Provider credentials stay on execution hosts and are not embedded in cloud task payloads. Operators should use separate worker identities/secrets and narrow repository/network allowlists.

## Validation and certification

The merged 0.9.2-contract line has executable validation for Server tests, UI tests/build, PostgreSQL durable lease/fencing integration, Compose runner readiness/isolation reporting, and cross-repository Core protocol conformance. These gates passing means the tested contracts are internally consistent; it is **not** a claim of production certification.

Release certification additionally requires the exact release candidate to pass real-repository benchmark suites, adversarial prompt-injection/secret-canary tests, restart/partition/lease/cancellation chaos and soak tests, and shared-worker isolation/resource/egress validation.

## World-class gaps

The remaining platform work is deliberately explicit:

- retained real-repository issue-resolution benchmarks;
- prompt-injection and secret-canary red-team suites across repo/web/MCP/Skills/Hooks/browser inputs;
- network-partition/restart/lease/cancellation/state-failure chaos and soak testing;
- strong per-task cloud isolation and resource/egress quotas;
- deterministic environment bootstrap/cache identity/invalidation;
- queue admission control, fairness, backpressure and autoscaling signals;
- OIDC/OAuth tenant identity, scoped service tokens and organization policy;
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

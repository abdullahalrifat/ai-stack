# AI Stack Server

## Current contract line

AI Stack consumes only the provider-neutral Jarvis Core 0.9.2 protocol contract. It does not import, install, or require the Jarvis CLI. The runner API can start independently of optional network-namespace capability, reports isolation availability explicitly, and keeps isolated commands fail-closed when that capability is unavailable.


AI Stack Server is the optional durable, self-hosted control plane for the Jarvis ecosystem. The standalone local product is [`jarvis`](https://github.com/abdullahalrifat/jarvis); provider-neutral runtime contracts are in [`jarvis-core`](https://github.com/abdullahalrifat/jarvis-core).

Use Jarvis when repository tools should run on the developer machine. Deploy Server when work must survive client disconnects, enter durable queues/schedules, run on external workers, serve web/mobile/messaging clients, retain centralized run/evidence state, or coordinate multiple execution hosts.

## Components

- `server/`: FastAPI control plane and durable Runs/cloud-task runtime;
- `runs-ui/`: run, evidence and approval interface;
- PostgreSQL/Redis/Qdrant: durable state, coordination and retrieval;
- SearXNG: optional self-hosted current-information search;
- LiteLLM plus Ollama or remote compatible providers: inference routing;
- `contracts/`: versioned Server client protocol;
- `jarvis-agent-core` 0.9.2: separately versioned provider-neutral shared contracts.

The Server pins the immutable Jarvis Core 0.9.2 release wheel:

```text
https://github.com/abdullahalrifat/jarvis-core/releases/download/v0.9.2/jarvis_agent_core-0.9.2-py3-none-any.whl
SHA-256 0ff9b5cfba29dca8d05df69a48573c3a69cc73ca9654e7122411b89a489f1130
```

Server requirements, lockfile, Docker image and CI all assert the same Core version. The checksum-verified asset keeps deployment reproducible without a mutable branch or a Core checkout.

## Start the development stack

```bash
cp .env.example .env
docker compose config
docker compose up -d
docker compose ps
```

This stack is for Server; standalone Jarvis users do not need it.

## v0.8 durable autonomous execution

Server cloud tasks support:

- portable existing/Git workspace descriptors;
- idempotent submissions;
- unique lease fencing per execution attempt;
- explicit durable execution states and proof;
- heartbeat/lease expiry and stale-result rejection;
- terminal cancellation;
- model/profile selection without transmitting provider credentials;
- real Postgres contention/fencing integration fixtures;
- ordinary v0.8.1 Postgres CI in addition to cross-repository smoke validation.

The matching CLI worker executes the actual coding agent and publishes only while it owns the current fence.

## Security and isolation boundary

Server does **not** make repository/model/tool output trusted. Search results, documents, web/browser content, model responses and external tool results remain untrusted evidence.

Cloud worker fencing prevents stale ownership and duplicate result publication; it is not equivalent to strong multi-tenant process isolation. The current external-worker model is appropriate for trusted single-tenant/self-hosted workers. A world-class shared-host deployment still needs independently constrained per-task container/VM-style sandboxes, CPU/RAM/PID/disk quotas, seccomp/AppArmor or equivalent and explicit egress controls.

Provider credentials stay on execution hosts and are not embedded in cloud task payloads. Operators should use separate worker identities/secrets and the narrowest repository/network allowlists practical.

## Validation and supply chain

The normal validation workflow covers Server tests, UI build/tests and Compose configuration. The current line includes a PostgreSQL 17 job dedicated to durable lease and autonomous fencing/idempotency tests. Model Integration exercises real Ollama + LiteLLM native tool calling. Supply Chain builds the Server image/SBOM and publishes tagged GHCR images with provenance when release conditions are met.

The post-merge audit also repaired the tagged GHCR shell block, which previously encoded `docker tag` and `docker push` incorrectly on one line.

Private GitHub Actions currently fail before runner provisioning (`steps:null`), so the current head is **audit-hardened but not release-certified** until Server/Postgres/UI/Compose/model/supply-chain/cross-repo jobs actually execute on the exact head.

## World-class gaps

The highest-priority remaining Server/platform gaps are:

- real-repository and adversarial prompt-injection/secret-canary benchmark evidence;
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

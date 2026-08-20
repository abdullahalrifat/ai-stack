# AI Stack Server

AI Stack Server is the optional durable, self-hosted control plane for the
Jarvis ecosystem. The standalone local product lives in
[abdullahalrifat/jarvis](https://github.com/abdullahalrifat/jarvis), while
provider-neutral runtime contracts live in
[abdullahalrifat/jarvis-core](https://github.com/abdullahalrifat/jarvis-core).

Use Jarvis when repository tools should run on the user's computer and only
inference should use a remote GPU. Deploy Server when work must survive client
disconnects, serve multiple users/channels, use remote mounted workspaces,
process durable documents, or enforce centralized policy and approvals.

## Components

- `server/`: FastAPI control plane and durable agent runtime;
- `runs-ui/`: run, evidence, and approval interface;
- PostgreSQL/Redis/Qdrant: durable state, coordination, and retrieval;
- SearXNG: optional self-hosted current-information search;
- LiteLLM plus Ollama or remote OpenAI-compatible endpoints: inference routing;
- `contracts/`: versioned Server client protocol;
- `jarvis-agent-core` 0.2.0: separately released, checksum-verified shared runtime dependency.

## Start the development stack

```bash
cp .env.example .env
docker compose config
docker compose up -d
docker compose ps
```

This Compose deployment is for Server. Standalone Jarvis users do not need it.

## Documentation

- [Server deployment, capabilities, and operations](server/README.md)
- [Product and channel architecture](docs/product-architecture.md)
- [Standalone Jarvis](https://github.com/abdullahalrifat/jarvis)
- [Jarvis Core](https://github.com/abdullahalrifat/jarvis-core)
- [Server protocol](contracts/jarvis-protocol-v1.json)

## Capability boundary

Server owns durable conversations/runs/events, tenant and channel identity,
queues, remote workspace policy, document ingestion/retrieval, isolated
commands, disposable Git worktrees, approval/discard, and central observability.
Jarvis owns local workspace tools and local interactive permissions. Core owns
only portable contracts such as token budgets, compaction, evidence, routing,
recovery, tracing, evaluations, and multi-agent role/result types.

Search uses SearXNG and can aggregate administrator-enabled engines. It does not
require a paid Google API. Search and fetched content remain untrusted evidence,
and source URLs must be retained.

Server requirements, CI, and Docker use the same public
[Core v0.2.0 release](https://github.com/abdullahalrifat/jarvis-core/releases/tag/v0.2.0)
wheel. Installation is independent of PyPI and does not check out a Core branch
or commit.

See [.env.example](.env.example) for the complete configuration surface.

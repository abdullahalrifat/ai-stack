# AI Stack Server

Server is the optional always-online control plane for Jarvis. It is not
required to install or use the standalone CLI.

## When to use each product

| Scenario | Product |
| --- | --- |
| Work directly in a local checkout | Jarvis |
| Keep tools/code local and use a remote GPU | Jarvis |
| Continue after a laptop disconnects | Server |
| Queue long research or document jobs | Server |
| Support multiple users or channels | Server |
| Enforce shared workspace/model/tool policy | Server |
| Keep durable events, documents, and approvals | Server |

Jarvis owns local tools and approvals. Server owns remote mounted workspaces,
durable orchestration, queues, shared state, channel identity, and central
policy. Both consume `jarvis-agent-core`, but do not share tool implementations
or storage policy.

## Development deployment

```bash
cp .env.example .env
docker compose config
docker compose up -d
docker compose ps
```

In production, use long unique service keys, mount `ARTIFACT_ROOT` on
persistent storage, expose the API through TLS/VPN, and mount only workspaces
the agent may access.

Configure Jarvis as a Server client:

```bash
export JARVIS_SERVER_URL=https://agent.example.com
export JARVIS_SERVER_API_KEY=your-agent-key
jarvis doctor
jarvis run "analyze this project" --detach
```

Provider credentials stay on Server. Clients and channel adapters must never
receive them.

## Models and routing

Server supports local Ollama/LiteLLM routes and controlled OpenAI-compatible
remote endpoints such as Hugging Face Inference Endpoints, vLLM, or TGI.
`JARVIS_MODEL_PROFILES_JSON` optionally describes model capabilities; model
`auto` selects an available profile satisfying the requested capabilities.

Routing uses one front-facing orchestrator identity and validated internal
workflows for quick, code, research, finance, deep, and vision tasks. Router
output cannot invent a model, grant tools, expand a workspace, or bypass
approval.

## Search and current answers

```dotenv
WEB_SEARCH_ENABLED=true
WEB_SEARCH_URL=http://searxng:8080/search
WEB_SEARCH_TIMEOUT_SECONDS=15
WEB_FETCH_MAX_BYTES=8000000
```

The included SearXNG service can use Google or other engines enabled by its
administrator; no paid Google Search API is required. Server normalizes bounded
results through Core, retains source URLs, and labels snippets/pages as
untrusted. `web_fetch` supports public HTML and PDF sources and enforces
download and model-context bounds.

Research and finance policy requires external evidence. Search can still be
partial or wrong; consequential claims must prefer primary sources and report
gaps or contradictions.

## Shared runtime, traces, and evaluations

Server installs the verified `jarvis-agent-core` 0.2.0 GitHub Release wheel for token enforcement,
compaction, artifacts, evidence, capability routing, recovery, redacted traces,
evaluations, and selective multi-agent contracts.

For a clean development installation:

```bash
python -m pip install -r server/requirements.txt
```

The human-maintained requirements, lockfile, CI, and Docker image all consume
the same public Core 0.2.0 wheel and verify its SHA-256. PyPI, a
cross-repository token, a branch checkout, and a separate bootstrap command are
not required. For unreleased Core development only, install a local Core
checkout explicitly after the locked dependencies.

Redacted trace artifacts are retained under the configured artifact root.
`server/evals/platform.py` replays recorded outputs against JSON cases; the
existing live suites under `server/evals/` cover routing and agent regressions.

## MCP

Administrators may configure fixed stdio commands:

```dotenv
JARVIS_MCP_SERVERS_JSON={"filesystem":["python","-m","your_mcp_server"]}
```

The model chooses only a configured alias and tool name. It cannot choose the
executable or invoke a shell. MCP output remains untrusted and cannot broaden
Server permissions. Persistent lifecycle, HTTP/OAuth transports, per-tool
policy, and health supervision remain future work.

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
- adversarial prompt/tool tests, replay evals, route benchmarks, canaries, and
  rollback procedures.

## Channel integrations

Telegram, WhatsApp, web, and mobile integrations are thin authenticated
adapters:

```text
channel webhook/app
  -> identity/signature adapter
  -> Server Runs API
  -> durable execution and approvals
  -> queued channel-safe reply
```

They do not contain another agent loop. See
[product architecture](../docs/product-architecture.md) for identity,
idempotency, attachment, delivery, and approval requirements.

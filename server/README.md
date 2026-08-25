# AI Stack Server

Server is the optional always-online control plane for Jarvis. It is not
required to install or use the standalone local CLI.

The current coordinated Server line consumes the immutable
`jarvis-agent-core` **0.8.0** release wheel with SHA-256
`d9569b69385e58a681ea01e900eb81c395d3f202a09a92878eb82bf4d4b8618a`.

## When to use each product

| Scenario | Product |
| --- | --- |
| Work directly in a local checkout | Jarvis |
| Keep code/tools local and use remote inference | Jarvis |
| Continue after a laptop disconnects | Server |
| Queue durable research/document/cloud tasks | Server |
| Support shared users/channels | Server |
| Enforce central workspace/model/tool policy | Server |
| Keep durable events, documents, evidence and approvals | Server |

Jarvis owns local tools and interactive permissions. Server owns durable shared
orchestration, remote workspace policy, queues, channels, documents and central
observability. Core owns portable contracts only.

## Development deployment

```bash
cp .env.example .env
docker compose config
docker compose up -d
docker compose ps
```

For production-like deployments use unique long service keys, TLS/VPN, durable
PostgreSQL/artifact storage and narrowly mounted workspaces. Do not expose model
or MCP credentials to clients or cloud-task payloads.

Configure Jarvis as a Server client:

```bash
export JARVIS_SERVER_URL=https://agent.example.com
export JARVIS_SERVER_API_KEY=your-server-key
jarvis doctor
jarvis run "analyze this project" --detach
```

## Durable Runs

Server persists run metadata/events and supports replay, cancellation,
approval/discard and client reconnect. The Runs engine is shared by web and
channel integrations; Telegram/WhatsApp adapters do not implement their own
agent loop.

The current durable event model is not yet a complete live steerable session
protocol. v0.9 tracks one reconnectable event vocabulary for model/tool/process
deltas plus user steer/interrupt/approval commands.

## Models and routing

Server supports local Ollama/LiteLLM routes and controlled remote
OpenAI-compatible providers. Configured model profiles describe capabilities;
`auto` chooses a route satisfying required capabilities and retained empirical
observations can influence selection.

Routing cannot invent a model, broaden a workspace or grant tools. Heterogeneous
expert routing retains provider/model/role metadata so verification and evidence
remain attributable.

Provider readiness includes automatic Ollama provisioning/readiness. The real
model-integration workflow starts Ollama + LiteLLM and requires a native tool
call from the configured small fixture model.

Remote provider smoke workflows run when their secrets are configured.

## Core dependency and release alignment

Human-maintained requirements, lockfile, CI and the Server Dockerfile all pin
the same Core 0.8.0 GitHub Release artifact. PyPI or a mutable Core branch is not
required.

```bash
python -m pip install -r server/requirements.txt
python -c "import importlib.metadata as m; print(m.version('jarvis-agent-core'))"
```

The post-v0.8 audit added regression coverage because merged v0.8 source had
previously continued packaging Core 0.7.0. Consumer dependency alignment is now
a release invariant.

## Search and current information

```dotenv
WEB_SEARCH_ENABLED=true
WEB_SEARCH_URL=http://searxng:8080/search
WEB_SEARCH_TIMEOUT_SECONDS=15
WEB_FETCH_MAX_BYTES=8000000
```

Server normalizes bounded SearXNG results, retains source URLs and labels
snippets/pages untrusted. `web_fetch` supports bounded public HTML/PDF sources.
Consequential research should prefer primary sources and report missing or
contradictory evidence.

## MCP, instructions and memory

Administrators configure trusted MCP server definitions and per-tool policy.
MCP lifecycle is persistent and policy-controlled; tool output remains untrusted
and cannot broaden Server permissions. Executable definitions are selected by
administrators, not by model output.

Instructions and persistent memory are hierarchical and bounded. Future
production hardening includes tenant-scoped retention/deletion and immutable
policy/Skill snapshots per run.

## Cloud execution

Server v0.8 supports portable cloud tasks using either an existing worker path or
Git coordinates.

Each claimed attempt receives a unique lease ID. Worker heartbeat, state and
completion writes must include that fence and remain valid only while the lease
is current. Expired/stale workers cannot publish results.

Cloud execution includes:

- idempotent submissions;
- explicit execution-state transitions;
- worker/model/profile propagation;
- killable child execution;
- cancellation and lease-loss stopping;
- portable Git checkout verification;
- tracked/untracked bounded diff capture;
- execution proof persistence;
- cleanup of ephemeral workspaces.

A first-class Postgres 17 CI job exercises durable lease contention and v0.8
fencing/idempotency tests without depending on a cross-repository secret.

## Channel integrations

Telegram and WhatsApp ingress create idempotent normal Runs. Outbound delivery
workers claim terminal channel Runs, send the bounded answer and retry failures
with bounded backoff.

Production channel safety still requires tenant identity linking, signed
expiring approval actions, stale-thread protection, attachment quarantine and
provider-specific rate/window handling. A plain `yes` in an unrelated thread
must never authorize a sensitive action.

## Observability and proof

OpenTelemetry spans cover agent/tool/platform/cloud paths when configured.
Durable execution evidence and failure signatures support route calibration and
operator diagnosis.

The v0.9 platform target adds:

- unified run cost/token/time budgets;
- queue/worker/provider SLO dashboards;
- live process/model/tool event panes;
- exact local proof/run binding for concurrent shared workspaces;
- tamper-evident audit/export for production deployments.

## Production gates

Before calling a deployment multi-tenant production-certified, prove:

1. OIDC/OAuth identity, tenant/project authorization and scoped service tokens;
2. reconnectable stream + durable steer/interrupt/approval semantics;
3. quotas, admission, queue fairness/backpressure and capacity/load tests;
4. hard cost/time/token/change-scope budgets with no silent paid escalation;
5. runner isolation, process cleanup and reproducible sandbox/toolchain identity;
6. secret rotation and tenant-scoped artifact access/encryption;
7. retention/deletion/export, malware handling, backup/restore and DR drills;
8. prompt/tool/permission adversarial cases and false-completion benchmarks;
9. worker crash/reclaim, network partition, Server restart and storage chaos;
10. canary/rollback and protocol compatibility tests.

See [../docs/world-class-platform-gaps.md](../docs/world-class-platform-gaps.md)
for the dated audit.

## CI/release gates

The intended Server gate set is:

- `Validate`: syntax/lint/tests/coverage, Runs UI, Compose;
- Postgres integration: lease/fence/idempotency/cancellation behavior;
- `Supply chain`: container build, SBOM and provenance;
- `Model integration`: real Ollama/LiteLLM native tool call;
- remote provider smoke where configured;
- cross-repository Core/Jarvis/Server smoke.

A workflow failure that occurs before checkout/steps execute is infrastructure
or account evidence, not a successful or failed code test; release certification
requires actual executable job steps.

# AI Stack Server

The Server is the optional always-online control plane for Jarvis. It is not
required to install or use the standalone Jarvis CLI.

Use Server when the work must be durable, shared, centrally governed, or
reachable from clients that cannot safely run repository tools themselves,
such as Telegram, WhatsApp, a browser, or a mobile app.

## When to use it

| Scenario | Recommended product |
| --- | --- |
| Work directly in a local checkout | Jarvis CLI |
| Use a remote GPU while keeping tools local | Jarvis CLI |
| Continue work after a laptop disconnects | Server |
| Queue long research/document jobs | Server |
| Provide one agent backend to multiple users or channels | Server |
| Enforce shared workspace, model, and permission policy | Server |
| Store durable conversations, events, documents, and approvals | Server |

The boundary is deliberate: Jarvis owns local tools and local approvals. Server
owns remote workspaces, durable orchestration, shared state, queues, channel
identity, and central policy.

## Run the development stack

From the repository root:

```bash
cp .env.example .env
docker compose up -d
docker compose ps
```

The Compose stack is for Server development and deployment. A Jarvis-only user
does not run it.

Configure a model route before production use. Server can call an
OpenAI-compatible open-weight model hosted by Hugging Face, vLLM, TGI, LiteLLM,
or another controlled endpoint. Keep provider credentials on the server and
never place them in a browser, mobile app, or bot client.

Confirm the API and protocol from Jarvis:

```bash
export AISTACK_URL=http://127.0.0.1:8000
export AISTACK_API_KEY=your-agent-api-key
jarvis doctor
```

## What Server provides

- FastAPI control plane and OpenAI-compatible entry points;
- durable runs, conversations, events, cancellation, replay, and client leases;
- PostgreSQL state, Redis coordination, and Qdrant retrieval;
- workflow routing for code, research, finance, deep reasoning, and vision;
- document ingestion, OCR, hybrid retrieval, and evidence provenance;
- isolated command execution and disposable Git worktrees;
- explicit approval/discard for write diffs;
- multi-expert dispatch and bounded tool execution;
- Runs UI and optional Open WebUI integration;
- versioned CLI/server protocol and advertised capabilities.

## Production deployment checklist

The current stack is a strong self-hosted foundation, but “world-class server”
is an operational standard, not a label. A production release should prove all
of the following:

- **Identity:** OIDC/OAuth users, tenant isolation, scoped service tokens, and
  channel-to-user identity mapping.
- **Authorization:** centralized policy for tools, paths, network access,
  connectors, models, budgets, and approvals.
- **Reliability:** multi-replica tests, idempotent APIs and webhooks, retries
  with dead-letter queues, graceful draining, and disaster recovery drills.
- **Security:** signed webhooks, secret rotation, encrypted transport/storage,
  isolated runners, dependency/SBOM scanning, and tamper-evident audit events.
- **Observability:** OpenTelemetry traces, structured logs, SLO dashboards,
  queue/lease saturation metrics, and per-run token/latency accounting.
- **Scale:** admission control, tenant quotas, model fallback/circuit breakers,
  backpressure, capacity tests, and autoscaled model/runner pools.
- **Data governance:** retention/deletion policy, backups with restore tests,
  export, regional controls where needed, and attachment malware scanning.
- **Developer platform:** stable API/SDK, webhook/event schemas, compatibility
  tests, migrations, release notes, and deprecation policy.
- **Quality:** offline replay evals, adversarial tool/prompt-injection tests,
  model-route benchmarks, load tests, and canary/rollback procedures.

Until these gates have passing evidence, describe Server as production-oriented
or a self-hosted platform—not as fully production-certified.

## Channel integrations

Telegram, WhatsApp, web, and mobile clients should be thin channel adapters.
They must not duplicate the agent loop or receive model/provider secrets.

```text
Channel webhook or app
        -> authenticated channel adapter
        -> Server Runs API
        -> durable agent execution
        -> event stream / outbound delivery worker
        -> channel reply
```

The detailed contract, security rules, delivery lifecycle, and staged rollout
are documented in
[docs/product-architecture.md](../docs/product-architecture.md).

## Repository boundary

Server code lives in `server/`. The shared protocol contract lives at
`contracts/aistack-protocol-v1.json`. Jarvis may later move to a separate
repository; Server must then consume a versioned published contract rather than
copying it.

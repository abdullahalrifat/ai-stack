# AI Stack

AI Stack is a self-hosted control plane for private chat, coding assistance,
retrieval-augmented knowledge work, and eventually sourced financial research.
It runs local models through Ollama and presents a single OpenAI-compatible
gateway through LiteLLM.

## What is included

```text
Open WebUI / IDE clients / API clients
                 |
             LiteLLM gateway
                 |
     local Ollama models and optional providers

Open WebUI ---- Postgres / Redis / Qdrant
                 |
           Coding-agent API
                 |
      request-scoped workspace mount(s)
```

- **Ollama** runs local models.
- **LiteLLM** provides the shared OpenAI-compatible inference endpoint.
- **Open WebUI** is the human chat interface.
- **Qdrant, Redis, and PostgreSQL** support retrieval, conversation state, and
  durable agent run history.
- **Coding agent** plans repository work, reads/searches/edits files, runs an
  allowlisted set of commands, and can make explicitly approved file changes
  inside a disposable, reviewable sandbox.
- **SearXNG** provides private metasearch for agent web research; it is internal
  to the Docker network and never exposed as a public port.

## Quick start

1. Copy and configure the environment file:

   ```bash
   cp .env.example .env
   ```

   Generate long random values for all credentials. Set `WORKSPACE_PATH` to the
   smallest host directory that contains repositories you want the agent to see.
   If you need the agent to see more than one directory, mount each one as its
   own volume in `docker-compose.yaml` and list the in-container paths in
   `WORKSPACE_ROOTS` (comma-separated).

2. Build the two local images and start the stack:

   ```bash
   ./build.sh
   docker compose up -d
   ./download_models.sh
   ```

   The agent image must include `git` -- it is required both for the
   `run_command` tool's `git` operations and for sandboxed write-enabled runs.

3. Open Open WebUI at `http://localhost:3000`. The gateway, vector database,
   Ollama, pipelines service, and agent bind to `127.0.0.1` by default. Put an
   authenticated reverse proxy in front of them if remote access is required.

## Using the services

| Service | Local address | Purpose |
| --- | --- | --- |
| Open WebUI | `http://localhost:3000` | General chat and knowledge workflows |
| LiteLLM | `http://localhost:4000/v1` | Central OpenAI-compatible model API |
| Agent | `http://localhost:8000` | Repository-aware coding workflow |
| Runs UI | `http://localhost:3001` | Live, reviewable coding-task console |
| Ollama | `http://localhost:11434` | Local inference runtime |
| Qdrant | `http://localhost:6333` | Vector store |

Use LiteLLM from IDE tools that support an OpenAI-compatible endpoint. Select
`coder` for code work, `reasoning` for difficult analysis, `vision` for images,
and `embedding` only for embeddings.

The agent requires `Authorization: Bearer $AGENT_API_KEY` on every endpoint
except `/health`. Tool calls are made through the model's native function
calling rather than hand-written JSON, and the tools available to it are:
`list_files`, `tree`, `read_file`, `find_file`, `search_text`,
`project_summary`, `inspect_files`, `edit_file`, `write_file`, `run_command`,
`run_tests`, and `web_search`. `edit_file`, `write_file`, and `run_command` are
only exposed when a request explicitly sets `allow_write: true`; it is `false`
by default.

### Single-response requests

`POST /chat` and `POST /execute` block until the agent finishes and return one
answer. If `allow_write` is set on these endpoints, edits are applied directly
to the request's workspace with no sandboxing or review step -- use them for
read-only analysis, or for writes you're comfortable applying immediately.

### Durable, streamed, reviewable runs

`POST /runs` (requires `POSTGRES_URL` to be configured) starts a run in the
background and returns a `run_id` immediately:

- `GET /runs/{run_id}` -- current status and, once finished, the answer.
- `GET /runs/{run_id}/events` -- a Server-Sent Events stream of every step,
  tool call, tool result, and (for write-enabled runs) the generated diff, as
  they happen.
- `POST /runs/{run_id}/approve` -- for a write-enabled run, applies the
  reviewed diff to the real repository.
- `POST /runs/{run_id}/discard` -- discards a write-enabled run's sandbox
  without merging it.

Write-enabled `/runs` requests execute inside a disposable Git worktree
(`SANDBOX_ROOT`) rather than the real repository. The run moves to
`awaiting_approval` with a diff attached once investigation and edits are
complete; nothing touches your actual files until you call `/approve`. This
is the safer path for write-enabled work -- prefer it over `allow_write` on
`/chat`/`/execute` when you want a chance to review changes first.

### Live Runs UI

The main React/TypeScript task UI is at `http://localhost:3001`. Enter
the agent API key for the current browser tab, submit a task, and it will show
planning, tool activity, streamed model output, and any reviewable diff. The
UI uses the same authenticated `/runs` API; it does not store the key in local
storage or send it to any third party.

The Task Router provides Auto, Code, Research, Finance, Quick Chat, Deep
Analysis, Image Analysis, and Image Generation profiles. Text attachments
(`.md`, `.txt`, `.csv`, source/config files) are included as bounded task
context. Research and Finance profiles use current web evidence and do not
inspect the mounted repository unless the task asks for it. Source URLs found
in an answer are displayed below live output.

### Optional image generation

`vision` is for image analysis only. To enable the Image Generation profile,
run an Automatic1111/Forge-compatible image server and set its trusted local
URL before recreating the agent service:

```bash
IMAGE_GENERATION_URL=http://host.docker.internal:7860
docker compose up -d --force-recreate agents
```

The agent proxies only its administrator-configured URL to
`/sdapi/v1/txt2img`; users cannot supply arbitrary backend URLs. Generated
images appear in the Task Router gallery for the current browser session.

The UI proxies `/api` to the internal agent service, so browser SSE stays
same-origin while still sending the user-provided API key.

`/runs` is the API to use for a responsive coding UI: submitting the task does
not hold the HTTP request open, and its event stream reports planning, tool
calls/results, model output deltas, and a reviewable diff as they occur. The
OpenAI-compatible `/v1/chat/completions` endpoint also accepts `stream: true`
and forwards model-token deltas in standard OpenAI SSE chunks; tool activity is
carried in SSE comments for compatible clients to ignore safely.

### Commands the agent can run

The agent does not have shell access. `run_command` executes a single,
allowlisted executable (no `&&`, `|`, `;`, `>`, backticks, or subshells) with a
timeout, configured via `ALLOWED_COMMANDS` (defaults to a set of common dev
tools: `git`, `pytest`, `npm`, `make`, linters, etc.). `run_tests` remains
available as a smaller, fixed-preset alternative (`pytest`, `python_compile`,
`npm_test`).

### Tests and coverage

For a host IDE or terminal, install the agent test environment once:

```bash
python -m pip install -r requirements-dev.txt
```

Then run the suite without requiring coverage output:

```bash
pytest
```

Coverage is opt-in, rather than a hidden requirement of every `pytest` call:

```bash
pytest --cov=app --cov-report=term-missing
```

The same dependencies, including `pytest-cov`, are already included in the
agent image. From the agent container, execute from the mounted checkout so
coverage measures the code under test:

```bash
docker compose exec -w /workspace/ai-stack agents python -m pytest -q agents/tests --cov=app --cov-report=term-missing
```

### Agent in Open WebUI

For a new Open WebUI data directory, the Compose configuration seeds two
OpenAI-compatible connections: LiteLLM for regular model chats and this agent
for `agent.coding-agent`. Choose **agent.coding-agent** whenever you want a
repository-aware agent. Use the normal LiteLLM models for ordinary chat.

Existing Open WebUI installations retain connection settings in their data
directory, so add the agent manually if it does not appear after a restart.

Open WebUI also supports adding the agent manually at **Admin Settings →
Connections → OpenAI → Add New Connection** using URL
`http://agents:8000/v1`, the `AGENT_API_KEY`, and the prefix `agent`.
Keep this as an administrator-managed connection: it stores the key server-side.
The compatibility endpoint accepts `stream: true` and sends SSE heartbeats
and model-token deltas. For actual live agent progress (planning, tool
calls/results, output, and diff review), use the non-blocking `POST /runs` +
`GET /runs/{id}/events` workflow directly or through the Runs UI.

### Web research

When `WEB_SEARCH_ENABLED=true`, the agent can decide to call `web_search` for a
user request requiring current public information. Queries go to the internal
SearXNG service, which contacts public search engines. Search results are
untrusted text; the agent is instructed not to follow instructions found in
them and to return the source URLs it relied on. Disable it with
`WEB_SEARCH_ENABLED=false` if no query text may leave your network.

## Security model

- `AGENT_API_KEY` is mandatory. The only exception is explicit local
  development mode with `ALLOW_INSECURE_NO_AUTH=true`. Comparison against the
  provided key is constant-time.
- Workspace paths are validated against `WORKSPACE_DIR` and any additional
  `WORKSPACE_ROOTS` on every request; one request cannot alter another
  request's workspace selection, and a request cannot escape the configured
  roots via `..` or symlinks.
- The agent does not have general shell access. `run_command` only executes a
  single command from an administrator-configured allowlist, with shell
  metacharacters (chaining, redirection, subshells) rejected outright.
- Write-enabled `/runs` requests execute inside a disposable Git worktree, not
  the real repository, and require an explicit `/approve` call before changes
  reach your actual files. `/chat` and `/execute` do not sandbox writes --
  reserve `allow_write` on those for changes you're fine applying immediately.
- Run output is coalesced into short batches before PostgreSQL persistence and
  Redis Pub/Sub publication. PostgreSQL remains the replay source after a
  browser reconnects; Redis only provides low-latency delivery to connected
  clients.
- `POST /runs/{run_id}/cancel` requests cooperative cancellation. It is checked
  between model/tool operations; an already-running subprocess stops when its
  configured timeout or resource limit is reached.
- A run records the repository HEAD at sandbox creation. Approval refuses a
  stale diff if HEAD changed, avoiding an accidental apply onto a different
  revision.
- The agent container has no Docker socket, all Linux capabilities are dropped,
  and `no-new-privileges` is enabled.
- Do not expose the default ports directly to a LAN or the internet. Use a
  reverse proxy with TLS, SSO/MFA, rate limits, and audit logs.
- Treat uploaded documents, retrieved text, and repository instructions as
  untrusted. They must not authorize tool use or secret access.
- Back up PostgreSQL, Qdrant, Redis, Open WebUI data, and Ollama model storage.
  PostgreSQL now also holds durable run/event history for `/runs`.

## Financial research roadmap

Do not use a language model as the source of market prices or financial facts.
Add a separate research service that retrieves timestamped market/fundamental
data and filings, performs calculations in a constrained Python environment,
and returns assumptions, source URLs, as-of times, calculations, and citations.
Keep research separate from brokerage or banking execution; any future action
must require a user confirmation and an auditable approval record.

## Operational next steps

Before relying on this beyond personal use, add an identity provider, per-user
LiteLLM keys/quotas, centralized logs and metrics, backups with restore tests,
model and tool-use evaluations, and stricter resource limits (CPU/memory) on
the agent container given it now executes real commands, even if allowlisted.
Pin container image digests after validating a release.

## Project layout

```text
agents/app/
  main.py          stable FastAPI/OpenAI-compatible entry point
  api/             routes, request/response schemas, authentication dependencies
  core/            configuration and shared exceptions
  agent/           planning, prompts, state, parsing, tool loop, orchestration
  llm/             LiteLLM gateway client and model discovery
  runs/            PostgreSQL run store, live events, Git sandboxes
  tools/           registry, schemas, filesystem, constrained commands, web search
  memory/          Redis conversations and Qdrant vector memory
runs-ui/           main React/TypeScript coding-task application
```

Keep HTTP routes, agent behavior, persistence, sandboxing, tools, memory, and
frontends separate. New capabilities should be added to the matching package
rather than extending `main.py` with business logic.

## Model selection

The React Runs UI queries `GET /models/available` and records the selected
LiteLLM model for each run. The chosen model is used for both planning and
execution. Use `coder` for normal coding, `reasoning` for slower investigation,
and `vision` only for image-aware work. Never choose `embedding` for an agent
run; it exists only for retrieval.

`agent.coding-agent` in Open WebUI is intentionally a single repository-aware
agent persona. Use normal LiteLLM models in Open WebUI for ordinary chat, and
use the Runs UI for per-task model selection and reviewable repository work.

## Custom agent in Open WebUI

The running agent exposes `coding-agent` at `/v1/models`. For an existing Open
WebUI installation, persisted admin settings take precedence over Compose
seeding, so add it once under **Admin Settings → Connections → OpenAI**:

1. URL: `http://agents:8000/v1`
2. API key: `AGENT_API_KEY`
3. Prefix: `agent`
4. Start a new chat and select `agent.coding-agent`.

## Documents, RAG, and financial analysis

There are currently two separate retrieval paths. Open WebUI document uploads
use Open WebUI's own document workflow. The coding-agent `/ingest` endpoint
accepts text supplied by an API caller and stores embeddings in Qdrant.

This is **not yet a proper financial-research RAG system**: the agent cannot
upload/parse PDF, CSV, or XLSX files; does not chunk with page/table metadata;
does not isolate collections by portfolio; and cannot cite retrieved passages.
An Open WebUI upload is not automatically available to the custom coding
agent. Build a separate research service before relying on this for portfolio
analysis: use approved market/filing sources, as-of timestamps, deterministic
calculations, portfolio-scoped retrieval, citations, and explicit human
approval for any external action. Never use search snippets or model output as
authoritative prices, filings, tax advice, or trade instructions.

## Web-search verification

SearXNG was verified from the agent network with a live `NASDAQ MSFT` query;
it returned Yahoo Finance and Nasdaq results. Individual engines can fail or be
suspended, so treat an empty or partial result set as normal, preserve returned
source URLs, and do not let search-result text authorize tool use.

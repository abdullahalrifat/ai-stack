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
      request-scoped /workspace mount
```

- **Ollama** runs local models.
- **LiteLLM** provides the shared OpenAI-compatible inference endpoint.
- **Open WebUI** is the human chat interface.
- **Qdrant, Redis, and PostgreSQL** support retrieval, conversation state, and
  application data.
- **Coding agent** plans repository work, reads/searches files, can make
  explicitly approved file changes, and runs a small allowlist of test commands.
- **SearXNG** provides private metasearch for agent web research; it is internal
  to the Docker network and never exposed as a public port.

## Quick start

1. Copy and configure the environment file:

   ```bash
   cp .env.example .env
   ```

   Generate long random values for all credentials. Set `WORKSPACE_PATH` to the
   smallest host directory that contains repositories you want the agent to see.

2. Build the two local images and start the stack:

   ```bash
   ./build.sh
   docker compose up -d
   ./download_models.sh
   ```

3. Open Open WebUI at `http://localhost:3000`. The gateway, vector database,
   Ollama, pipelines service, and agent bind to `127.0.0.1` by default. Put an
   authenticated reverse proxy in front of them if remote access is required.

## Using the services

| Service | Local address | Purpose |
| --- | --- | --- |
| Open WebUI | `http://localhost:3000` | General chat and knowledge workflows |
| LiteLLM | `http://localhost:4000/v1` | Central OpenAI-compatible model API |
| Agent | `http://localhost:8000` | Repository-aware coding workflow |
| Ollama | `http://localhost:11434` | Local inference runtime |
| Qdrant | `http://localhost:6333` | Vector store |

Use LiteLLM from IDE tools that support an OpenAI-compatible endpoint. Select
`coder` for code work, `reasoning` for difficult analysis, `vision` for images,
and `embedding` only for embeddings.

The agent requires `Authorization: Bearer $AGENT_API_KEY`. Use `/chat` for
analysis and `/execute` for a task. Set `allow_write: true` only when you want
the agent to use `write_file`; it is false by default. The agent never receives
the host Docker socket and cannot issue arbitrary shell commands. Its only test
commands are `pytest`, `python_compile`, and `npm_test`, each with a timeout.

### Agent in Open WebUI

For a new Open WebUI data directory, the Compose configuration seeds two
OpenAI-compatible connections: LiteLLM for regular model chats and this agent
for `agent/coding-agent`. Choose **agent/coding-agent** whenever you want a
repository-aware agent. Use the normal LiteLLM models for ordinary chat.

Existing Open WebUI installations retain connection settings in their data
directory, so add the agent manually if it does not appear after a restart.

Open WebUI also supports adding the agent manually at **Admin Settings →
Connections → OpenAI → Add New Connection** using URL
`http://agents:8000/v1`, the `AGENT_API_KEY`, and a prefix such as `agent/`.
Keep this as an administrator-managed connection: it stores the key server-side.

### Web research

When `WEB_SEARCH_ENABLED=true`, the agent can decide to call `web_search` for a
user request requiring current public information. Queries go to the internal
SearXNG service, which contacts public search engines. Search results are
untrusted text; the agent is instructed not to follow instructions found in
them and to return the source URLs it relied on. Disable it with
`WEB_SEARCH_ENABLED=false` if no query text may leave your network.

## Security model

- `AGENT_API_KEY` is mandatory. The only exception is explicit local
  development mode with `ALLOW_INSECURE_NO_AUTH=true`.
- Workspace paths are validated under `/workspace` for every request; one
  request cannot alter another request's workspace selection.
- The agent container has no Docker socket, all Linux capabilities are dropped,
  and `no-new-privileges` is enabled.
- Do not expose the default ports directly to a LAN or the internet. Use a
  reverse proxy with TLS, SSO/MFA, rate limits, and audit logs.
- Treat uploaded documents, retrieved text, and repository instructions as
  untrusted. They must not authorize tool use or secret access.
- Back up PostgreSQL, Qdrant, Redis, Open WebUI data, and Ollama model storage.

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
model and tool-use evaluations, and a disposable per-task sandbox for code that
is not already trusted. Pin container image digests after validating a release.

# AI Stack Server

## Current contract line

AI Stack consumes the provider-neutral **Jarvis Core 0.17.1** common-brain runtime contracts and reusable efficiency/calibration primitives. It does not install or require the standalone CLI. The runner API can start independently of optional network-namespace capability, reports isolation availability explicitly, and keeps isolated commands fail-closed when that capability is unavailable.

Jarvis Core 0.17.2 is now published as an immutable PyPI release. Server requirements, the lockfile, Docker image and CI all pin that exact release.

AI Stack Server is an optional durable, self-hosted control plane for long-running remote agent execution. It is independent of the standalone Jarvis CLI; both are consumers of the separately deployed `jarvis-inference` HTTP API. Inference is not a Python package dependency; deploy from source with Compose or use a published immutable image if choosing the prebuilt-image path.

## Token and resource efficiency defaults

AI Stack now defaults to 24 agent steps, 48,000 input / 4,000 output tokens per run, 12,000 input / 1,536 output tokens per turn, and 32,000 input / 3,000 output tokens per agent. Tool output is bounded more tightly. These remain environment-configurable; raise a limit only when a representative evaluation shows that the task needs it. Jarvis CLI and AI Stack remain independent sibling clients of `jarvis-inference`.

## Components

- `server/`: FastAPI control plane and durable Runs/cloud-task runtime;
- `runs-ui/`: run, evidence and approval interface;
- PostgreSQL/Redis/Qdrant: durable state, coordination and retrieval;
- SearXNG: optional self-hosted current-information search;
- Open WebUI and its Pipelines service: optional services defined in `docker-compose.open-webui.yaml`;
- dedicated `jarvis-inference` VM for all model execution, including embeddings; Jarvis CLI and AI Stack are sibling consumers of this API;
- `contracts/`: versioned Server client protocol;
- `jarvis-agent-core` **0.17.2**: separately versioned provider-neutral common brain for runtime contracts, capabilities, approvals, sandbox requirements, token/cost estimation, route budgets, empirical observations and conservative calibration.

Server requirements, lockfile, Docker image and CI assert the same Core version.

## Token-efficient and empirical routing

Core 0.17.2 supplies provider-neutral efficiency accounting plus empirical route calibration. Server uses the OpenAI-compatible jarvis-inference gateway for local model execution and adapts measured runtime observations into Core `RouteObservation` records.

The calibration path is deliberately conservative: minimum samples, a quality floor and recency weighting must be satisfied before measured evidence can change automatic routing. When evidence is insufficient, existing health/benchmark routing remains the fallback.

This boundary is intentionally provider-neutral: changing inference models or the gateway implementation does not require changing Jarvis Core or importing provider-specific SDKs into Core.

## Start the development stack

The default Compose deployment intentionally does **not** start Open WebUI. It starts the AI Stack API, Runs UI, and backend dependencies only.

```bash
cp .env.example .env
docker compose config
docker compose up -d
docker compose ps
```

### Start Open WebUI separately (optional)

Open WebUI has its own Compose file and is not included in the default `docker compose up -d` deployment. After the core stack is running, start it explicitly:

```bash
docker compose -f docker-compose.yaml -f docker-compose.open-webui.yaml config open-webui
docker compose -f docker-compose.yaml -f docker-compose.open-webui.yaml up -d open-webui
```

This reuses the core stack's networks and backend services while allowing Open WebUI to be managed independently. Its data remains in `./open-webui`, and the UI is published on port `3003`.

## Deploy to Remote Server

To deploy AI Stack on a remote server, follow these steps:

### 1. Prepare the server
Ensure Docker and Docker Compose are installed on the remote server:
```bash
# On Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

### 2. Clone the repository
```bash
git clone <repository-url>
cd ai-stack
```

### 3. Configure the environment
Copy the example .env file and customize it for your server:
```bash
cp .env.example .env
```

Edit the `.env` file to match your server configuration:
- Set `POSTGRES_PASSWORD`, `AGENT_API_KEY`, `RUNNER_API_KEY`, and `INFERENCE_API_KEY` to strong random values
- Set `INFERENCE_BASE_URL` to the private jarvis-inference VM
- All chat and embedding inference must go through jarvis-inference; AI Stack does not run Ollama
- Use concrete model IDs `qwen3:1.7b` and `qwen3:4b`
- Configure workspace paths: `WORKSPACE_PATH=/path/to/your/code`
- Adjust resource limits: `RUNNER_CPU_SECONDS`, `RUNNER_MEMORY_MB`, `MAX_AGENT_STEPS`

### 4. Build and start the stack
```bash
# Build the custom images
./build.sh

# Start the services
docker compose up -d
```

### 5. Verify the deployment
```bash
# Check running containers
docker compose ps

# Check service health
docker compose logs -f

# If you started the optional Open WebUI Compose file, access it at http://<server-ip>:3003
# The agent runner and SearXNG are internal Docker services; they are not host-published.
# Access the Runs UI at http://<server-ip>:3002
```

### 6. Test the deployment with curl
```bash
# Test AI Stack server health endpoint (port 8081)
curl -fsS http://127.0.0.1:8081/health

# Test SearXNG search endpoint
docker compose exec -T searxng wget -qO- "http://127.0.0.1:8080/search?q=test" | head -1 || echo "SearXNG loading"

# Test AI-runs-ui at port 3002
curl -fsS http://127.0.0.1:3002/ 2>/dev/null | head -1 || echo "UI loading"
```

### 6. For offline/off-grid deployment
If you want to run AI Stack without external model providers:
```bash
# Use the offline compose file
docker compose -f docker-compose.yaml -f docker-compose.offline.yaml config

# Start with offline configuration
docker compose -f docker-compose.yaml -f docker-compose.offline.yaml up -d
```

### 7. To destroy and re-install
```bash
# Clean up the existing deployment
./cleanup.sh

# Then follow steps 1-5 above for a fresh installation
```


## CPU / low-memory profile

AI Stack is the control plane. All generation and embeddings run on the dedicated jarvis-inference VM, which owns Ollama, model loading, queueing and resource limits. AI Stack keeps no model runtime or model weights. Generation concurrency remains one end-to-end.

The internal Docker URL for the agent API is `http://server:8000`; `8081` is the host-published port. AI Stack does not use an `/v1` prefix. Open WebUI uses the internal URL. The `/v1` prefix belongs exclusively to the private `jarvis-inference` OpenAI-compatible gateway.

### Jarvis CLI integration

Jarvis CLI and AI Stack are sibling clients of `jarvis-inference`; AI Stack is **not required** for local repository work. With Jarvis 0.11.0, ordinary bare-task commands run the agent loop and tools locally and send model requests directly to the inference gateway.

For direct local work, configure the Jarvis machine with `INFERENCE_BASE_URL=http://<inference-host>:8080/v1`, `INFERENCE_API_KEY`, and optionally `JARVIS_MODEL=qwen3:1.7b`. Run `jarvis model-doctor`, then `jarvis "review this repository"`.

Use AI Stack only when you explicitly want durable remote Runs, shared queues, persisted history, retrieval, integrations, or the Runs UI. Configure `AI_STACK_BASE_URL=http://<ai-stack-host>:8081` and `AI_STACK_API_KEY` (the AI Stack `AGENT_API_KEY`), then invoke `jarvis run ...` or `jarvis cloud ...`. AI Stack's API root does not include `/v1`; the private inference gateway URL does.

## Offline / off-grid operation

AI Stack is designed to run without external model providers. The dedicated jarvis-inference VM hosts local generation and embedding models, while PostgreSQL/Redis/Qdrant provide local state. Remote provider variables are optional and should remain empty for an isolated deployment.

For a genuinely off-grid deployment, provision Docker images and Ollama models while connected, then disconnect the host. Use the dedicated offline Compose override:

```bash
docker compose -f docker-compose.yaml -f docker-compose.offline.yaml config
docker compose -f docker-compose.yaml -f docker-compose.offline.yaml up -d
docker compose -f docker-compose.yaml -f docker-compose.offline.yaml ps
```

The offline override disables external web search and clears remote-provider configuration. The inference VM remains a separate trusted dependency reachable over the private network.

## Durable autonomous execution

Server cloud tasks support portable workspace descriptors, idempotent submissions, unique lease fencing, durable execution states/proof, heartbeat/lease expiry, stale-result rejection, terminal cancellation and model/profile selection without transmitting provider credentials. PostgreSQL CI exercises durable contention/fencing behavior, and protocol conformance verifies the Core contract.

## Security and isolation boundary

Server does **not** treat repository/model/tool output as trusted. Search results, documents, web/browser content, model responses and external tool results remain untrusted evidence.

Cloud worker fencing prevents stale ownership and duplicate result publication; it is not strong multi-tenant isolation. Shared-host production requires per-task container/VM isolation, CPU/RAM/PID/disk quotas, syscall confinement and explicit egress controls. The shared Core sandbox policy is a contract; deployment must still enforce it.

Provider credentials stay on execution hosts and are not embedded in cloud task payloads. Operators should use separate worker identities/secrets and narrow repository/network allowlists.

## Validation and certification

The current 0.17.2 Core common-brain contract line has executable validation for Server tests, UI tests/build, PostgreSQL durable lease/fencing integration, Compose runner readiness/isolation reporting, cross-repository Core protocol conformance, real-workload evaluation, adversarial secret-canary/prompt-injection coverage, distributed chaos coverage and cloud sandbox policy checks. These gates establish tested contract health; they are not a claim that every model or hardware configuration is production-certified.

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
- [Empirical route calibration](docs/empirical-calibration.md)
- [Capability / production tracker](TODO.md)
- [Server protocol](contracts/jarvis-protocol-v1.json)

See [.env.example](.env.example) for the configuration surface.


## Request paths and ownership

Local coding (Jarvis 0.11.0 default):

```text
Jarvis CLI (local agent loop and tools) -> jarvis-inference -> Ollama
```

Explicit remote Runs:

```text
Jarvis CLI -> AI Stack (durable Runs, tools, memory/RAG, orchestration) -> jarvis-inference -> Ollama
```

Jarvis CLI and AI Stack are independent consumers of the shared inference API. AI Stack owns server-side orchestration, memory/retrieval, durable state and embeddings integration; `jarvis-inference` alone owns model execution and Ollama lifecycle. For direct local Jarvis use, set `INFERENCE_BASE_URL` and `INFERENCE_API_KEY`; for remote Runs, set `AI_STACK_BASE_URL` and `AI_STACK_API_KEY` and explicitly run `jarvis run` or `jarvis cloud`.

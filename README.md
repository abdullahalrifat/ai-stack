# AI Stack Server

## Current contract line

AI Stack consumes the provider-neutral **Jarvis Core 0.16.1** common-brain runtime contracts and reusable efficiency/calibration primitives. It does not install or require the standalone CLI. The runner API can start independently of optional network-namespace capability, reports isolation availability explicitly, and keeps isolated commands fail-closed when that capability is unavailable.

Jarvis Core 0.16.1 is now published as an immutable PyPI release. Server requirements, the lockfile, Docker image and CI all pin that exact release.

AI Stack Server is the durable, self-hosted control plane for long-running agent execution.

## Components

- `server/`: FastAPI control plane and durable Runs/cloud-task runtime;
- `runs-ui/`: run, evidence and approval interface;
- PostgreSQL/Redis/Qdrant: durable state, coordination and retrieval;
- SearXNG: optional self-hosted current-information search;
- LiteLLM plus Ollama or remote compatible providers: inference routing;
- `contracts/`: versioned Server client protocol;
- `jarvis-agent-core` **0.16.1**: separately versioned provider-neutral common brain for runtime contracts, capabilities, approvals, sandbox requirements, token/cost estimation, route budgets, empirical observations and conservative calibration.

Server requirements, lockfile, Docker image and CI assert the same Core version.

## Token-efficient and empirical routing

Core 0.16.1 supplies provider-neutral efficiency accounting plus empirical route calibration. Server keeps concrete Ollama/LiteLLM/remote-provider execution and telemetry in the application layer, adapts measured runtime observations into Core `RouteObservation` records, and delegates route selection back to `RouteCalibrator`.

The calibration path is deliberately conservative: minimum samples, a quality floor and recency weighting must be satisfied before measured evidence can change automatic routing. When evidence is insufficient, existing health/benchmark routing remains the fallback.

This boundary is intentionally provider-neutral: changing a local Ollama model or remote OpenAI-compatible gateway does not require changing Jarvis Core or importing provider SDKs into Core.

## Start the development stack

```bash
cp .env.example .env
docker compose config
docker compose up -d
docker compose ps
```

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
- Set `POSTGRES_PASSWORD`, `LITELLM_MASTER_KEY`, `AGENT_API_KEY`, and `RUNNER_API_KEY` to strong random values
- Adjust `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, and `OLLAMA_CONTEXT_LENGTH` based on your GPU/CPU memory
- Set `DEFAULT_MODEL=qwen3-8b` for CPU-mode defaults (8B model for coding, 4B for routine chat)
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

# Access the Open WebUI at http://<server-ip>:3000
# Access the agent runner API at http://<server-ip>:8000
# Access LiteLLM at http://<server-ip>:4000
```

### 6. Test the deployment with curl
```bash
# Test AI Stack server health endpoint (port 8081)
curl -fsS http://127.0.0.1:8081/health

# Test LiteLLM health endpoint (may return 401 - auth required)
curl -fsS http://127.0.0.1:4000/health

# Test SearXNG search endpoint
curl -fsS "http://127.0.0.1:8080/search?q=test" 2>/dev/null | head -1 || echo "SearXNG loading"

# Test AI-runs-ui at port 3001
curl -fsS http://127.0.0.1:3001/api/health 2>/dev/null || echo "UI loading"
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


## Offline / off-grid operation

AI Stack is designed to run locally without external model providers. Ollama hosts local models, LiteLLM provides the OpenAI-compatible routing surface, and PostgreSQL/Redis/Qdrant provide local state. Remote provider variables are optional and should remain empty for an isolated deployment.

For a genuinely off-grid deployment, provision Docker images and Ollama models while connected, then disconnect the host. Use the dedicated offline Compose override:

```bash
docker compose -f docker-compose.yaml -f docker-compose.offline.yaml config
docker compose -f docker-compose.yaml -f docker-compose.offline.yaml up -d
docker compose -f docker-compose.yaml -f docker-compose.offline.yaml ps
```

The offline override disables external web search, clears remote-provider configuration, and makes model provisioning fail closed if a configured Ollama model is absent from the persistent `./ollama` volume. It never runs `ollama pull` while offline.

## Durable autonomous execution

Server cloud tasks support portable workspace descriptors, idempotent submissions, unique lease fencing, durable execution states/proof, heartbeat/lease expiry, stale-result rejection, terminal cancellation and model/profile selection without transmitting provider credentials. PostgreSQL CI exercises durable contention/fencing behavior, and protocol conformance verifies the Core contract.

## Security and isolation boundary

Server does **not** treat repository/model/tool output as trusted. Search results, documents, web/browser content, model responses and external tool results remain untrusted evidence.

Cloud worker fencing prevents stale ownership and duplicate result publication; it is not strong multi-tenant isolation. Shared-host production requires per-task container/VM isolation, CPU/RAM/PID/disk quotas, syscall confinement and explicit egress controls. The shared Core sandbox policy is a contract; deployment must still enforce it.

Provider credentials stay on execution hosts and are not embedded in cloud task payloads. Operators should use separate worker identities/secrets and narrow repository/network allowlists.

## Validation and certification

The current 0.16.1 Core common-brain contract line has executable validation for Server tests, UI tests/build, PostgreSQL durable lease/fencing integration, Compose runner readiness/isolation reporting, cross-repository Core protocol conformance, real-workload evaluation, adversarial secret-canary/prompt-injection coverage, distributed chaos coverage and cloud sandbox policy checks. These gates establish tested contract health; they are not a claim that every model or hardware configuration is production-certified.

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

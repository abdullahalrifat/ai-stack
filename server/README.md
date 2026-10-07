# AI Stack Server

AI Stack Server is the durable control plane for Jarvis. It owns orchestration, tools, memory/RAG, durable Runs, approvals, scheduling, telemetry and integrations.

**Model execution is not owned by Server.** All local chat and embedding inference goes through the dedicated `jarvis-inference` gateway. AI Stack must not run Ollama or LiteLLM.

## Architecture

```text
Jarvis CLI
   |
   v
AI Stack Server
   |-- tools / memory / RAG / durable runs
   |-- PostgreSQL / Redis / Qdrant
   |
   | inference only when needed
   v
jarvis-inference
   |
   v
Ollama
```

## Production deployment

From the repository root:

```bash
cp .env.example .env
chmod 600 .env
# Set strong unique secrets and INFERENCE_BASE_URL/INFERENCE_API_KEY.
bash scripts/preflight.sh
bash scripts/deploy.sh
```

The Compose file uses an external `proxy` network. Create it once if your reverse-proxy stack does not already create it:

```bash
docker network create proxy 2>/dev/null || true
```

Verify:

```bash
docker compose ps
curl -fsS http://127.0.0.1:8081/health
curl -fsS http://127.0.0.1:3002/
curl -fsS http://127.0.0.1:3003/
```

## Update / redeploy

```bash
git status
git pull --ff-only
bash scripts/preflight.sh
bash scripts/deploy.sh
```

Normal `docker compose down` preserves bind-mounted PostgreSQL, Redis, Qdrant, WebUI, pipeline and sandbox data.

## Normal operations

```bash
docker compose ps
docker compose logs --tail=200 server
docker compose logs --tail=200 agent-runner
docker compose logs --tail=200 runs-ui
docker compose restart server
docker compose stop
docker compose down --remove-orphans
docker compose up -d
```

## Cleanup

`cleanup.sh` is intentionally destructive and scoped to this Compose project. Back up required data first:

```bash
./cleanup.sh
```

It must never prune unrelated Docker resources or use `docker system prune --volumes`.

## Connect Jarvis

Normal user flow:

```text
Jarvis CLI -> AI Stack -> jarvis-inference -> Ollama
```

Configure the Jarvis machine:

```bash
export AI_STACK_BASE_URL="http://<ai-stack-host>:8081"
export AI_STACK_API_KEY="<same value as AGENT_API_KEY>"
export JARVIS_MODEL="qwen3:1.7b"
```

Direct inference access is for diagnostics/developer tooling only.

## Connect to jarvis-inference

AI Stack `.env`:

```env
INFERENCE_BASE_URL=http://<private-inference-ip>:8080/v1
INFERENCE_API_KEY=<same secret configured on the inference VM>
```

Allow TCP 8080 only from the AI Stack host and trusted administration addresses. Never expose Ollama port 11434.

## Production requirements

Validate private/TLS access, strong unique service secrets, persistent backups, workspace permissions, runner isolation, monitoring/disk capacity and recovery from inference/database/VM restart. This deployment is single-tenant personal infrastructure; it is not a claim of enterprise multi-tenant isolation.

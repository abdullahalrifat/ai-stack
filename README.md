# AI Stack - Private Local AI Platform

A self-hosted AI platform built with open-source technologies. This project provides a private ChatGPT-like environment with local LLM inference, AI gateway management, vector search, and future AI agent capabilities.

The goal is to build a private AI infrastructure that runs on your own hardware while supporting knowledge management, automation, and autonomous agents.

## Architecture

```text
                         User
                          |
                          |
                    Open WebUI
                          |
                          |
                     LiteLLM Proxy
                          |
              +-----------+-----------+
              |                       |
          Ollama                  External APIs
       (Local LLMs)              (Optional)
              |
              |
        AI Applications
              |
     +--------+---------+
     |                  |
   Qdrant            PostgreSQL
 Vector Database    Application Data

              |
           Redis
        Cache / Queue
```

## Components

### Ollama

Local LLM runtime.

Responsibilities:

* Run open-source models locally
* Provide private inference
* Avoid dependency on external AI providers

Supported models:

* Qwen
* Llama
* DeepSeek
* Mistral

---

### LiteLLM

Unified AI gateway.

Responsibilities:

* OpenAI-compatible API
* Model routing
* API key management
* Multi-model support

All AI applications communicate through LiteLLM instead of directly connecting to models.

---

### Open WebUI

Web-based AI interface.

Features:

* ChatGPT-like experience
* Conversation management
* Model selection
* Pipeline support

---

### Qdrant

Vector database for AI memory.

Used for:

* Document search
* RAG pipelines
* Knowledge base
* Agent memory

---

### PostgreSQL

Relational database for persistent application data.

---

### Redis

In-memory datastore for:

* Caching
* Queues
* Temporary state

---

# Project Structure

```text
ai-stack/
|
├── docker-compose.yml
├── .env.example
├── .gitignore
├── README.md
|
├── litellm/
│   └── config.yaml
|
├── pipelines/
│   └── custom pipelines
|
├── agents/
│   └── AI agent services
|
├── ollama/        (ignored)
├── postgres/      (ignored)
├── redis/         (ignored)
├── qdrant/        (ignored)
└── open-webui/    (ignored)
```

---

# Requirements

## Minimum

* CPU: 8 cores
* RAM: 16GB
* Storage: 100GB+

## Recommended

* CPU: 12+ cores
* RAM: 32GB+
* NVIDIA GPU with 12GB+ VRAM
* SSD storage

---

# Installation

## Clone repository

```bash
git clone <repository-url>

cd ai-stack
```

## Configure environment

```bash
cp .env.example .env
```

Update:

```env
POSTGRES_DB=
POSTGRES_USER=
POSTGRES_PASSWORD=

LITELLM_MASTER_KEY=

PIPELINES_API_KEY=
```

---

## Start services

```bash
docker compose up -d
```

Check status:

```bash
docker compose ps
```

---

# Service URLs

| Service     | URL                    |
| ----------- | ---------------------- |
| Open WebUI  | http://localhost:3000  |
| LiteLLM API | http://localhost:4000  |
| Ollama API  | http://localhost:11434 |
| Qdrant      | http://localhost:6333  |

---

# Adding Models

Example:

```bash
docker exec -it ollama bash

ollama pull qwen2.5:14b
```

Verify:

```bash
ollama list
```

---

# LiteLLM Configuration

Example:

```yaml
model_list:
  - model_name: qwen-local
    litellm_params:
      model: ollama/qwen2.5:14b
      api_base: http://ollama:11434
```

Applications can use:

```text
OpenAI compatible endpoint:

http://localhost:4000/v1
```

---

# AI Agent Roadmap

## Personal Knowledge Agent

Capabilities:

* Search private documents
* Store knowledge
* Answer using personal data

Technology:

* Qdrant
* Embeddings
* RAG

---

## Homelab Operations Agent

Capabilities:

* Analyze Docker containers
* Inspect logs
* Monitor infrastructure
* Suggest fixes

Possible integrations:

* Docker API
* Kubernetes API
* Proxmox API
* Grafana API

---

## Development Assistant Agent

Capabilities:

* Repository analysis
* Code review
* Documentation generation
* Debugging assistance

---

# Security

Never commit:

* API keys
* Passwords
* Database credentials
* Certificates

Keep secrets in:

```text
.env
```

Recommended architecture:

```text
Internet
    |
Cloudflare Tunnel
    |
Authentication
    |
AI Stack
```

---

# Backup

Important data:

```text
postgres/
qdrant/
open-webui/
litellm/
```

Recommended:

* Regular snapshots
* Encrypted backups
* External storage copy

---

# Useful Commands

View logs:

```bash
docker compose logs -f
```

Restart service:

```bash
docker compose restart <service>
```

Stop stack:

```bash
docker compose down
```

---

# Future Vision

This project aims to become a private AI operating system:

* Personal knowledge assistant
* Autonomous agents
* Infrastructure automation
* Local AI development platform
* Private productivity tools

Focus areas:

* Privacy
* Ownership
* Extensibility
* Self-hosted AI

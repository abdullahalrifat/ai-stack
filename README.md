# AI Stack - Private Local AI Platform

A self-hosted AI platform built with open-source technologies. This project provides a private ChatGPT-like environment with local LLM inference, AI gateway management, vector search, and AI agent capabilities.

The goal is to build a private AI infrastructure that runs on your own hardware while supporting knowledge management, automation, development assistance, and autonomous agents.

---

# Architecture

```text
                         User
                          |
              +-----------+-----------+
              |                       |
          Open WebUI              VS Code
              |                  Continue
              |                       |
              +-----------+-----------+
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

---

# Components

## Ollama

Local LLM runtime.

Responsibilities:

* Run open-source models locally
* Provide private inference
* Avoid dependency on external AI providers

Supported models:

* Qwen
* DeepSeek
* Llama
* Mistral
* Gemma

---

## LiteLLM

Unified AI gateway.

Responsibilities:

* OpenAI-compatible API
* Model routing
* API key management
* Multi-model support
* Central AI access layer

All AI applications communicate through LiteLLM instead of directly connecting to models.

---

## Open WebUI

Web-based AI interface.

Features:

* ChatGPT-like experience
* Conversation management
* Model selection
* Pipeline support

---

## VS Code + Continue

Private local coding assistant.

Features:

* Code explanation
* Repository analysis
* Code generation
* Debugging assistance
* Documentation generation

VS Code connects to LiteLLM using the OpenAI-compatible API.

---

## Qdrant

Vector database for AI memory.

Used for:

* Document search
* RAG pipelines
* Knowledge base
* Agent memory

---

## PostgreSQL

Relational database for persistent application data.

---

## Redis

In-memory datastore for:

* Caching
* Queues
* Temporary state
* Conversation memory

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
│   ├── Dockerfile
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

---

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

# Start Services

```bash
docker compose up -d
```

Check status:

```bash
docker compose ps
```

---

# Service URLs

| Service | URL |
|---|---|
| Open WebUI | http://localhost:3000 |
| LiteLLM API | http://localhost:4000 |
| Ollama API | http://localhost:11434 |
| Qdrant | http://localhost:6333 |
| AI Agents | http://localhost:8000 |

---

# LiteLLM Configuration

Example:

```yaml
model_list:

  - model_name: coder
    litellm_params:
      model: ollama/qwen3-coder:30b
      api_base: http://ollama:11434


  - model_name: qwen3-14b
    litellm_params:
      model: ollama/qwen3:14b
      api_base: http://ollama:11434


  - model_name: embedding
    litellm_params:
      model: ollama/nomic-embed-text
      api_base: http://ollama:11434


litellm_settings:
  drop_params: true
```

Applications use:

```text
OpenAI compatible endpoint:

http://localhost:4000/v1
```

---

# Adding Models

Example:

```bash
docker exec -it ollama bash

ollama pull qwen3-coder:30b
```

Verify:

```bash
ollama list
```

---

# VS Code Integration (Local Coding Assistant)

This AI stack can be used as a private coding assistant inside VS Code using the Continue extension.

Architecture:

```text
VS Code
   |
Continue Extension
   |
LiteLLM Proxy
   |
Ollama
   |
Local Coding Model
```

Benefits:

* Private local AI coding assistant
* No external API dependency
* Centralized model management
* Ability to switch models without changing clients

---

## Install Continue

Install:

```text
Continue VS Code Extension
```

---

## Configure Continue

Open:

```text
~/.continue/config.yaml
```

Add:

```yaml
name: Main Config

version: 1.0.0

schema: v1


models:

  - name: Qwen Local

    provider: openai

    model: coder

    apiBase: http://localhost:4000/v1

    apiKey: ${API_KEY}



tabAutocompleteModel:

  name: Qwen Local
```

---

## Configure API Key

Set your LiteLLM key:

Linux:

```bash
export API_KEY=<your-litellm-key>
```

Or directly:

```yaml
apiKey: sk-your-key
```

Do not commit API keys into Git.

---

## Verify Available Models

```bash
curl http://localhost:4000/v1/models \
-H "Authorization: Bearer <API_KEY>"
```

Example:

```json
{
  "data": [
    {
      "id": "coder"
    }
  ]
}
```

The Continue model name must match the LiteLLM model name.

Example:

```yaml
model: coder
```

---

## Recommended Models

| Purpose | Model |
|---|---|
| Coding Assistant | qwen3-coder:30b |
| General Assistant | qwen3:14b |
| Lightweight Agent | qwen3:8b |
| Embeddings | nomic-embed-text |

---

# AI Agent API

Endpoints:

```
POST /chat

POST /ingest

GET /conversation/{conversation_id}

POST /memory/search

POST /v1/chat/completions

POST /v1/embeddings

GET /v1/models
```

Example:

```json
{
  "message": "Analyze my infrastructure",
  "conversation_id": "home"
}
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

Rebuild custom images:

```bash
docker compose build --no-cache
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
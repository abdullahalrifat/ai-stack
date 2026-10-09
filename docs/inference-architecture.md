# Dedicated inference architecture

AI Stack is an optional remote control plane, not a prerequisite for the standalone Jarvis CLI. Both consumers use the same dedicated `jarvis-inference` API. Neither application owns the model runtime.

```text
Jarvis CLI (local agent)                AI Stack (optional server)
  - local agent loop                      - durable Runs API
  - local repository tools                 - shared queues/workers
  - approvals and verification             - persistence, memory/RAG
              |                            - integrations and UI
              |                                      |
              +------------------+-------------------+
                                 v
                         jarvis-inference
                          OpenAI-compatible API
                                 |
                              Ollama
                     +-----------+-----------+
                     |           |           |
                 qwen3:1.7b  qwen3:4b  nomic-embed-text
```

## Ownership

- **Jarvis CLI:** local agent loop, repository tools, permissions, approvals, local sessions and task verification.
- **AI Stack:** optional remote Runs, durable state, shared worker queues, memory/retrieval, integrations, server-side telemetry and UI.
- **jarvis-inference:** model registry, OpenAI-compatible chat/embeddings, request IDs, bounded scheduling, queueing, timeouts, backend adapters and resource limits.
- **Ollama:** model execution only. Its port remains private to the inference Compose network.

AI Stack must not install model weights or run Ollama/LiteLLM. Jarvis must not import AI Stack modules for local execution or call Ollama directly. The gateway is the shared model execution boundary.

## Configuration

For AI Stack, configure:

```env
INFERENCE_BASE_URL=http://<private-inference-ip>:8080/v1
INFERENCE_API_KEY=<same-secret-as-inference-vm>
EMBEDDING_MODEL=nomic-embed-text
DEFAULT_MODEL=qwen3:1.7b
FAST_MODEL=qwen3:1.7b
AGENT_REASONING_MODEL=qwen3:4b
```

For standalone Jarvis, use the same `INFERENCE_BASE_URL` and `INFERENCE_API_KEY`, plus `JARVIS_MODEL=qwen3:1.7b`. Do not set `AI_STACK_BASE_URL` merely to use Jarvis locally.

## Gateway compatibility and deployment

The inference API is a separately deployed HTTP service, not a Python dependency of either consumer. Keep its API contract compatible with both clients and verify the deployed source commit or image digest. The current Compose configuration builds the gateway from the checked-out source, so a GitHub/PyPI release is not a prerequisite for that deployment. If switching to a prebuilt GHCR image, use a published immutable release tag or digest and verify it before rollout. The gateway must require API-key authentication by default and must not automatically replay ambiguous generation timeouts.

## Deployment order

1. Deploy `jarvis-inference` and verify `/ready`, `/v1/models`, `/v1/capabilities` and `/v1/embeddings`.
2. Configure and test Jarvis directly against the private inference endpoint.
3. (Optional) Configure AI Stack with the same inference URL/key.
4. Run `scripts/preflight.sh` and `scripts/deploy.sh` for AI Stack.
5. Verify each consumer independently; local Jarvis should work with AI Stack stopped.

Keep TCP 8080 restricted to trusted private-network clients. The gateway must have `INFERENCE_API_KEY` configured; startup fails closed when it is absent unless the explicit `INFERENCE_ALLOW_INSECURE_NO_AUTH=true` development override is set. Keep Ollama port 11434 internal and unpublished. A caller timeout does not prove generation stopped: ambiguous read timeouts must not be automatically replayed, because the original generation may still occupy the single inference slot.

# Dedicated inference architecture

AI Stack is the control plane. All local model execution is delegated to `jarvis-inference` over a private network.

```text
Jarvis CLI
   |
   v
AI Stack
   | tools / memory / RAG / durable runs
   | inference only when needed
   v
jarvis-inference
   |
   v
Ollama
   +-- qwen3:1.7b
   +-- qwen3:4b
   +-- nomic-embed-text
```

AI Stack does **not** run Ollama or LiteLLM.

## Configuration

```env
INFERENCE_BASE_URL=http://<private-inference-ip>:8080/v1
INFERENCE_API_KEY=<same-secret-as-inference-vm>
EMBEDDING_MODEL=nomic-embed-text
DEFAULT_MODEL=qwen3:1.7b
FAST_MODEL=qwen3:1.7b
AGENT_REASONING_MODEL=qwen3:4b
```

## Deployment order

1. Deploy `jarvis-inference`.
2. Verify `/ready`, `/v1/models` and `/v1/embeddings`.
3. Configure AI Stack with the inference URL/key.
4. Run `scripts/preflight.sh`.
5. Run `scripts/deploy.sh`.
6. Configure Jarvis to point to AI Stack.
7. Run the end-to-end Jarvis health/model doctor.

Keep TCP 8080 restricted to the AI Stack host and trusted administration addresses. Keep Ollama port 11434 internal.

# Dedicated inference architecture

AI Stack is the control plane. All model execution is delegated to the dedicated jarvis-inference service over a private network.

## Runtime boundary

AI Stack VM
  Server / WebUI / RAG / Qdrant / PostgreSQL / Redis / SearXNG
                     |
                     | private network
                     v
Inference VM
  jarvis-inference
      |
      +-- Ollama
          +-- qwen3:1.7b
          +-- qwen3:4b
          +-- nomic-embed-text

AI Stack must not run Ollama or LiteLLM. It uses the OpenAI-compatible gateway for both chat completions and embeddings:
- POST /v1/chat/completions
- POST /v1/embeddings
- GET /v1/models
- GET /health
- GET /ready

## Deployment requirements

Set these in the AI Stack .env:
INFERENCE_BASE_URL=http://<private-inference-ip>:8080/v1
INFERENCE_API_KEY=<same-secret-as-inference-vm>
EMBEDDING_MODEL=nomic-embed-text
DEFAULT_MODEL=qwen3:1.7b
FAST_MODEL=qwen3:1.7b
AGENT_REASONING_MODEL=qwen3:4b

The inference VM should expose port 8080 only to the AI Stack VM and trusted administration network. Never expose Ollama port 11434 directly.

## Resource policy

Keep one active generation and one loaded model on the CPU-only inference VM. Requests queue at the inference gateway instead of starting competing model workers.

The AI Stack VM remains a control-plane machine and should not reserve CPU or RAM for model weights.

## Production checklist

1. Deploy and lock models on jarvis-inference.
2. Configure the inference API key on both VMs.
3. Verify /ready from the AI Stack VM.
4. Verify /v1/models includes qwen3:1.7b, qwen3:4b, and nomic-embed-text.
5. Start AI Stack with Docker Compose.
6. Verify Server health and WebUI model discovery.
7. Run the embedding/RAG smoke test.
8. Confirm no AI Stack container publishes or starts Ollama.

## Security

Use a private VLAN or firewall rule allowing only the AI Stack source address to reach the inference gateway. Keep the gateway authenticated even on a private network. Do not commit production API keys.

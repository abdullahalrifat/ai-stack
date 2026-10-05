# Hybrid inference: Hugging Face primary, Ollama survival

The supported home architecture is now:

- **Hybrid mode:** Hugging Face is the preferred high-performance provider for
  coding, reasoning, and vision; local Ollama is the deterministic fallback.
- **Local mode:** Ollama only. Remote credentials are ignored by the LiteLLM
  renderer.
- **Offline mode:** the offline compose override clears remote credentials,
  disables web search, and fails closed if required local Ollama models are
  missing.

## OptiPlex / small CPU VM recommendation

For the OptiPlex / small CPU VM, use a two-tier local setup. Keep both generation
models installed, but only one resident at a time:

```text
qwen3:1.7b  # default / routine work
qwen3:4b    # heavy coding, reasoning, review
nomic-embed-text
```

Use `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, an 8K local context
budget, and conservative agent/tool concurrency. The 1.7B model handles normal
requests; the 4B model is selected only when the workload needs more capacity.
Do not configure obsolete larger local models on the CPU host.

## Hugging Face configuration

For a single HF endpoint/model:

```text
INFERENCE_MODE=hybrid
HF_MODEL=<model-id>
HF_INFERENCE_BASE_URL=https://<endpoint>.endpoints.huggingface.cloud/v1
HF_API_KEY=<token>
```

For role-specific deployments, prefer:

```text
HF_CODER_MODEL=<coding-model>
HF_REASONING_MODEL=<reasoning-model>
HF_VISION_MODEL=<vision-model>
```

The role-specific value overrides `HF_MODEL`.

## Fallback behavior

Hybrid routing uses concrete provider model IDs only. When a configured remote
model is unavailable, LiteLLM falls back directly to the concrete local model
ID `qwen3-4b` for coding/reasoning/vision workloads. There are no synthetic local model aliases.

## Safety rules

- Do not configure HF credentials in an air-gapped deployment.
- Do not use a catch-all cloud fallback for embeddings or local survival tasks.
- Keep remote access behind the existing API authentication and network
  boundary; never expose the LiteLLM container directly to the Internet.
- Treat HF as an inference service, not as a dependency of `jarvis-core`.

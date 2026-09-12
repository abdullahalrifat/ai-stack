# Hybrid inference: Hugging Face primary, Ollama survival

The supported home architecture is now:

- **Hybrid mode:** Hugging Face is the preferred high-performance provider for
  coding, reasoning, and vision; local Ollama is the deterministic fallback.
- **Local mode:** Ollama only. Remote credentials are ignored by the LiteLLM
  renderer.
- **Offline mode:** the offline compose override clears remote credentials,
  disables web search, and fails closed if required local Ollama models are
  missing.

## OptiPlex 16 GB recommendation

Use `.env.hybrid-16gb.example` as the starting profile. Keep only small
survival models on the CPU host:

```text
qwen3:4b-instruct
qwen3:8b
nomic-embed-text
```

Keep `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, and a 16K local
context budget. The OptiPlex should spend its memory and CPU on the durable
AI Stack control plane, databases, retrieval, agent execution, and survival
inference rather than attempting to keep multiple 12B/14B models resident.

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

When hybrid mode is enabled, the public aliases remain stable:

```text
coder    -> HF coder    -> coder-local (Ollama)
reasoning -> HF reasoning -> reasoning-local (Ollama)
vision   -> HF vision   -> vision-local (Ollama)
```

LiteLLM performs the provider failover after the configured primary model
exhausts its retry policy. The client does not need provider-specific logic.

`quick` remains local Ollama by default so routing/planning stays cheap and
available even when HF is unavailable.

## Safety rules

- Do not configure HF credentials in an air-gapped deployment.
- Do not use a catch-all cloud fallback for embeddings or local survival tasks.
- Keep remote access behind the existing API authentication and network
  boundary; never expose the LiteLLM container directly to the Internet.
- Treat HF as an inference service, not as a dependency of `jarvis-core`.

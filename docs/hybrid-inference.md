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

For a 3-vCPU/14-GB VM, use a 4B-only local profile. Keep only one generation
model installed and resident:

```text
qwen3:4b
nomic-embed-text
```

Use `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, an 8K local context
budget, and conservative agent/tool concurrency. Routine chat, research,
finance, coding, synthesis, and review should use `qwen3:4b`. Do not configure obsolete larger local models on the CPU host.

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

`quick` remains the local 4B Ollama model by default so routing/planning stays cheap and
available even when HF is unavailable.

## Safety rules

- Do not configure HF credentials in an air-gapped deployment.
- Do not use a catch-all cloud fallback for embeddings or local survival tasks.
- Keep remote access behind the existing API authentication and network
  boundary; never expose the LiteLLM container directly to the Internet.
- Treat HF as an inference service, not as a dependency of `jarvis-core`.

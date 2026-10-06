# Inference architecture

AI Stack is the control plane. Generation is delegated to the dedicated
`jarvis-inference` VM through its OpenAI-compatible `/v1` endpoint.

The supported architecture is:
- AI Stack: agent execution, tools, memory, search, UI and durable state.
- jarvis-inference: Ollama, model lifecycle, queueing, resource admission and generation.
- AI Stack Ollama: embeddings only (`nomic-embed-text`).
- One active generation and one loaded generation model on the inference VM.

## Dedicated inference configuration

Set:

```text
INFERENCE_ENABLED=true
INFERENCE_BASE_URL=http://<inference-vm-ip>:8080/v1
INFERENCE_API_KEY=<same key configured on jarvis-inference>
DEFAULT_MODEL=qwen3:1.7b
FAST_MODEL=qwen3:1.7b
ROUTER_MODEL=qwen3:1.7b
RESEARCH_MODEL=qwen3:1.7b
FINANCE_MODEL=qwen3:4b
AGENT_REASONING_MODEL=qwen3:4b
CHANGE_REVIEW_MODEL=qwen3:4b
```

Use private networking/VLAN/firewall rules between the two VMs. Do not expose
the inference gateway or Ollama directly to the public Internet.

## Model IDs

Model IDs are the actual Ollama model IDs:
- `qwen3:1.7b` — routine/fast work.
- `qwen3:4b` — heavier coding, reasoning, finance and review.

Synthetic aliases such as `qwen3-4b`, `qwen3-1.7b` and `orchestrator` are not
part of the contract.

## Local embeddings

AI Stack keeps a small local Ollama instance only for `nomic-embed-text`.
It should not download or load the generation models.

The local Ollama service is loopback-bound and should have a small CPU/RAM
budget because embeddings are background/support work.

## Optional remote providers

Hugging Face/Anthropic can still be configured as explicit remote providers.
Those providers are separate from the dedicated local inference gateway.

When hybrid routing is enabled, remote role-specific models may be preferred,
with fallback to the concrete local model IDs served by `jarvis-inference`.

Do not place provider credentials in model IDs or request payloads.

## Operational rule

Do not run a second generation Ollama on the AI Stack VM. That would duplicate
RAM usage, model storage and CPU contention and would bypass the inference
gateway's queue, digest and resource controls.
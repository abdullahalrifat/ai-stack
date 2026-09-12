# Core dependency and release policy

AI Stack consumes `jarvis-agent-core` only from its published PyPI release. Core owns semantic versioning, changelog generation, tagging and publication. AI Stack does not vendor or copy Core implementation.

## Update flow

```text
Core change
    -> reviewed release PR
    -> GitHub tag/release
    -> exact-tag validation
    -> PyPI publication
    -> dependency update
    -> full server CI
    -> review and merge
```

`server/requirements.txt` and `server/requirements.lock` remain reproducible pins. Dependency updates should be reviewed together with the exact Core API being consumed.

## Compatibility boundary

AI Stack owns orchestration, provider routing, scheduling, persistence, external integrations and server policy. Core owns provider-neutral contracts, normalization helpers, capabilities, approvals, sandbox requirements and reusable verification/runtime primitives. Core 0.13.0 is the common-brain contract for those semantics.

Concrete Ollama, LiteLLM, hosted-provider and infrastructure integrations remain in the application/runtime layer.

The release order is always Core first, then consumers. Never publish an application image that references a Core version which is not already available on PyPI.

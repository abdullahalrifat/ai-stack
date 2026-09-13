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

## Current release

The current coordinated consumer release is **Jarvis Core 0.15.0**. This release adds provider-neutral token-efficiency primitives for bounded context construction, token/cost estimation, route budgets and adaptive route signals. Server may consume those primitives without importing provider SDKs into Core.

## Compatibility boundary

AI Stack owns orchestration, provider routing, scheduling, persistence, external integrations and server policy. Core owns provider-neutral contracts, normalization helpers, capabilities, approvals, sandbox requirements and reusable verification/runtime primitives.

Concrete Ollama, LiteLLM, hosted-provider and infrastructure integrations remain in the application/runtime layer.

The release order is always Core first, then consumers. Never publish an application image that references a Core version which is not already available on PyPI.

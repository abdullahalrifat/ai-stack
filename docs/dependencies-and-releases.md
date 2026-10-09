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

The current coordinated consumer release is **Jarvis Core 0.17.1**, consumed by the current Jarvis 0.11.1 line. Core provides the shared inference gateway client and provider-neutral runtime/routing contracts. The coordinated inference gateway target is `jarvis-inference` 0.3.1; ensure its `v0.3.1` release image is published and deployed before production rollout. Verify `server/requirements.lock`, installed wheel metadata, gateway release/image digest, and exact deployed commit before release certification.

## Architecture ownership

AI Stack owns provider execution, orchestration, telemetry collection, persistence, external integrations and adapters into Core observations. Core owns the provider-neutral observation contract and calibration algorithm. Jarvis owns real workload definitions and task-level evaluation.

For empirical routing, AI Stack must convert measured runtime outcomes into `jarvis_core.RouteObservation` and delegate selection to `jarvis_core.RouteCalibrator`. Do not copy calibration thresholds, scoring or recency logic into AI Stack.

Concrete Ollama, LiteLLM, hosted-provider and infrastructure integrations remain in the application/runtime layer.

The release order is always Core first, then consumers. Never publish an application image that references a Core version which is not already available on PyPI.

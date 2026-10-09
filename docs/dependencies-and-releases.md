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

The current coordinated consumer release is **Jarvis Core 0.17.1**, consumed by the current Jarvis 0.11.1 line. Core provides the shared inference gateway client and provider-neutral runtime/routing contracts. `jarvis-inference` is a separately deployed HTTP service, not a Python dependency of AI Stack. Verify `server/requirements.lock`, installed wheel metadata, and the exact inference source commit or deployed image digest during release certification. A published inference release is needed only when deploying its prebuilt release image; the current Compose configuration builds the service from source.

## Architecture ownership

AI Stack owns provider execution, orchestration, telemetry collection, persistence, external integrations and adapters into Core observations. Core owns the provider-neutral observation contract and calibration algorithm. Jarvis owns real workload definitions and task-level evaluation.

For empirical routing, AI Stack must convert measured runtime outcomes into `jarvis_core.RouteObservation` and delegate selection to `jarvis_core.RouteCalibrator`. Do not copy calibration thresholds, scoring or recency logic into AI Stack.

Concrete Ollama, LiteLLM, hosted-provider and infrastructure integrations remain in the application/runtime layer.

The release order is always Core first, then consumers. Never publish an application image that references a Core version which is not already available on PyPI.

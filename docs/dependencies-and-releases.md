# Core dependency and release policy

AI Stack consumes `jarvis-agent-core` only from its published PyPI release. Core owns semantic versioning, changelog generation, tagging and publication. AI Stack does not vendor or copy Core implementation.

## Update flow

```text
jarvis-core Conventional Commit
    -> reviewed release PR
    -> GitHub tag/release
    -> exact-tag validation
    -> PyPI publication
    -> Dependabot detects new jarvis-agent-core
    -> AI Stack dependency PR
    -> full server CI
    -> review and merge
```

`server/requirements.txt` and `server/requirements.lock` remain reproducible pins. Dependabot creates the dependency update PR; maintainers should not manually edit the Core version to chase releases.

## Compatibility boundary

AI Stack owns orchestration, provider routing, scheduling, GitHub automation and server policy. Core owns provider-neutral contracts and verification primitives. A Core breaking release therefore requires a normal dependency PR and compatibility review in AI Stack before adoption.

The release order is always Core first, then consumers. Never publish an AI Stack image that references a Core version which is not already available on PyPI.

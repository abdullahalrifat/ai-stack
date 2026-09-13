# AI Agent Repository Instructions

These instructions apply to AI-assisted changes in AI Stack.

## Core dependency rule

AI Stack consumes `jarvis-agent-core` only from a published PyPI release. When Core changes, update the exact dependency in `server/requirements.txt`, `server/requirements.lock`, Docker validation, CI assertions, tests and user-facing release documentation in the same change.

Never reference an unreleased Core version in a merge-ready application change.

## Architecture boundary

- Core owns provider-neutral contracts and reusable algorithms, including empirical route calibration.
- AI Stack owns provider execution, telemetry collection, persistence and adapters into Core observations.
- Jarvis owns real workload definitions and task-level evaluation.

The intended flow is `Jarvis CLI -> AI Stack -> provider/model`. Do not duplicate Core calibration policy in AI Stack.

## Validation

Before merging, run the server formatting, lint, tests, image and cross-repository conformance checks. Documentation must match the exact Core release consumed by the application.

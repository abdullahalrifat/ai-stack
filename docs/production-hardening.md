# Server production hardening

This document separates executable contract validation from deployment-level certification.

## Current baseline

The current coordinated Server line consumes `jarvis-agent-core==0.17.1` from the locked `server/requirements.lock` environment. CI validates Server tests, UI tests/build, PostgreSQL durable lease/fencing integration, Compose runner readiness/isolation reporting and Core protocol conformance.

## Shared-worker isolation requirements

Fencing is an ownership mechanism, not a sandbox. A shared-host deployment must add an independently constrained per-task execution boundary with:

- container or VM isolation;
- CPU, memory, process-count and disk quotas;
- syscall confinement (seccomp/AppArmor or platform equivalent);
- explicit filesystem boundaries;
- explicit egress/network policy;
- credential isolation per worker/task;
- cleanup and verification after abnormal termination.

If the required isolation primitive is unavailable, an isolated task must fail closed rather than silently downgrade to an unsafe mode.

## Chaos and soak certification

Run retained automated scenarios for:

- worker restart during an active lease;
- database restart and temporary network partition;
- heartbeat delay and lease expiry;
- stale completion after lease takeover;
- duplicate submission and duplicate completion;
- cancellation racing with completion;
- runner kill failure;
- repeated task execution over a long soak window.

Certification should assert that no stale worker can publish a terminal result and that every task reaches a durable terminal state or an explicit recoverable state.

## Environment determinism

Task identity should include the repository commit plus the environment/bootstrap/cache identity. Dependency caches must be invalidated when their lockfile, interpreter, base image or bootstrap definition changes. A task must not silently reuse artifacts from an incompatible environment.

## Adversarial evaluation

Run secret-canary and prompt-injection cases through repository files, web content, browser pages, MCP results, Skills and Hooks. Verify that untrusted instructions cannot grant permissions or cause credentials to enter task output/proofs.

## Release rule

Passing CI is necessary but is not production certification. Record the exact release candidate, Core checksum, test matrix, benchmark corpus and isolation/chaos results for every certification decision.

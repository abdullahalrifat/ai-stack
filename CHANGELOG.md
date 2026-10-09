# Changelog

## 3.9.0

- Lower default run, turn, agent, tool-output and completion budgets for CPU-local inference; keep every budget configurable.
- Keep Jarvis CLI and AI Stack as independent inference clients.
- Pin Jarvis Core 0.17.2 for non-streaming ambiguous-timeout protection.


## 3.8.0

- Pin the published Jarvis Core 0.17.1 release in server requirements, lockfile, image build and CI.
- Document the inference HTTP compatibility contract, fail-closed authentication and timeout replay protection without treating a published gateway release as a prerequisite for source-built Compose deployments.
- Update release and architecture documentation for the coordinated Core and inference versions.


## 3.7.0

- Clarify the AI Stack control-plane API boundary: AI Stack routes are rooted at `/`, not `/v1`.
- Document that `/v1` is reserved for the private jarvis-inference OpenAI-compatible gateway.
- Align the Jarvis integration with the authenticated `/models/available` AI Stack model catalog.
- Bump the FastAPI service version from 3.6 to 3.7.

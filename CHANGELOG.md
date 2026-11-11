# Changelog

## 3.9.3

- Consume the published Jarvis Core 0.17.4 release (ledger reservation cleanup, bounded retry sleeps with a total retry deadline, OpenAI JSON-string tool-call decoding, reserved `model`/`stream` option guard) and align server requirements, lockfile, Docker validation, CI assertions and release documentation.


## 3.9.2

- Serialize cold-boot schema migrations with a session-level advisory lock so concurrent replicas no longer race past the applied-set check and PK-conflict on `agent_schema_migrations`.
- Make channel webhook run creation exactly-once: the event claim, run row and queued event now share one transaction, so concurrent webhook retries can no longer create and execute duplicate runs.
- Retry clean connection failures reported by the Core inference client (`status_code=None`, `retryable=True`) at the orchestration layer; ambiguous timeouts stay non-replayable.
- Require authentication on `GET /models/available`, which mutates the shared model cache.
- Whitelist the `agent_runs` columns assignable through `update_run()` instead of interpolating arbitrary field names into SQL.


## 3.9.1

- Consume the published Jarvis Core 0.17.3 release and align server requirements, lockfile, image assertions, CI, tests and current documentation.
- Keep Core-owned queue-rejection retries bounded and avoid replaying ambiguous inference timeouts or partially delivered streams at the orchestration layer.


## 3.9.0

- Consume the published Jarvis Core 0.17.3 release and align requirements, lockfile, image build, CI and release documentation.

- Lower default run, turn, agent, tool-output and completion budgets for CPU-local inference; keep every budget configurable.
- Keep Jarvis CLI and AI Stack as independent inference clients.
- Preserve the published Core dependency; ambiguous timeouts are not retried by the AI Stack orchestration layer.


## 3.8.0

- Pin the published Jarvis Core 0.17.1 release in server requirements, lockfile, image build and CI.
- Document the inference HTTP compatibility contract, fail-closed authentication and timeout replay protection without treating a published gateway release as a prerequisite for source-built Compose deployments.
- Update release and architecture documentation for the coordinated Core and inference versions.


## 3.7.0

- Clarify the AI Stack control-plane API boundary: AI Stack routes are rooted at `/`, not `/v1`.
- Document that `/v1` is reserved for the private jarvis-inference OpenAI-compatible gateway.
- Align the Jarvis integration with the authenticated `/models/available` AI Stack model catalog.
- Bump the FastAPI service version from 3.6 to 3.7.

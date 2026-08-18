# Jarvis and Server TODO

## Agent core (completed)

- [x] Implement streaming responses and responsive CLI UX
- [x] Add local `jarvis stream` command with `--simulate` fallback
- [x] Add regression test for CLI stream behavior
- [x] `search_code` tool (regex search with line numbers/context, bounded matches)
- [x] Deterministic post-mutation verification (an edit is not finished until verified)
- [x] Executor reflection pass for unsupported answer drafts
- [x] Deterministic transient-tool retry policy (never retries deterministic failures)
- [x] Full transcript checkpoint/resume for durable runs

## Tier 1 - Agent reliability (completed)

- [x] Offline replay evals harness: golden traces + deterministic replay through the
      executor, sequence-fidelity regression checks (`server/evals/`)
- [x] Context management: budget-aware tool-result summaries, goal-aware history
      compaction, capped verbatim recent context
- [x] Parallel tool calls: concurrent execution of independent read-only calls in one turn
- [x] Re-planning on failure: detect stalled/failing loops and revise the plan with
      failure evidence instead of burning steps

## Tier 2 - Trust & safety (completed)

- [x] Scoped permissions model (path edit rules, command allowlists, ask/allow scopes)
- [x] Prompt-injection defense (mark tool/web output as untrusted data)
- [x] Sandbox tiering for arbitrary tool execution (network/kernel limits)

## Tier 3 - Autonomy & scale

- [x] Multi-expert dispatch / parallel subagents with structured findings
- [ ] Git branch-per-task + diff review + PR workflow
- [ ] Persistent cross-run agent memory (learned project knowledge, user preferences)
- [ ] MCP tool ecosystem support

## Tier 4 - UX & operations

- [ ] Interactive TUI (keyboard-driven approval, live tool stream, diff review)
- [ ] Observability, tracing (OpenTelemetry), and per-run cost accounting
- [ ] Cost/latency controls: model fallback routing, per-tool token budgets

## Infrastructure

- [ ] Scale infrastructure: GPU, quantized models, autoscaling runners
- [ ] Harden safety, persona, and memory systems


## Jarvis parity gates

- [x] Standalone local agent loop with remote OpenAI-compatible inference
- [x] Repository read/search, guarded patching, constrained commands, verification
- [x] Optional explicit durable Server mode
- [ ] Named, searchable, resumable local sessions with compaction
- [ ] Local text/JSON/stream-JSON automation contract
- [ ] Plan-only mode and layered permissions/configuration
- [ ] Rich terminal editor, attachments, and per-hunk diff review
- [ ] MCP, hooks, skills/plugins, and connector protocol
- [ ] Worktree/branch workflows and safe parallel local agents
- [ ] Keyring, named provider profiles, signed releases, and upgrade path

## Server production gates

- [ ] OIDC/OAuth, tenant isolation, scoped service tokens, and identity linking
- [ ] Idempotent webhook/channel gateway with queued outbound delivery
- [ ] OpenTelemetry traces, SLO dashboards, and per-run usage accounting
- [ ] Quotas, admission control, circuit breakers, and capacity/load tests
- [ ] Tamper-evident audit events, secret rotation, SBOM, and release signing
- [ ] Retention/deletion, malware scanning, backup/restore, and DR drills
- [ ] Stable SDK/webhook schemas and compatibility/deprecation tests

## Channel roadmap

- [ ] Shared channel message envelope and adapter SDK
- [ ] Authenticated web app with event replay and approval UI
- [ ] Telegram webhook adapter with linking, signatures, and idempotency
- [ ] WhatsApp Cloud API adapter with linking, retries, and policy handling
- [ ] Mobile client with OIDC, safe push notifications, and offline resume

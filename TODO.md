# AI Stack TODO

## Agent core (completed)

- [x] Implement streaming responses and responsive CLI UX
- [x] Add local `aistack stream` command with `--simulate` fallback
- [x] Add regression test for CLI stream behavior
- [x] `search_code` tool (regex search with line numbers/context, bounded matches)
- [x] Deterministic post-mutation verification (an edit is not finished until verified)
- [x] Executor reflection pass for unsupported answer drafts
- [x] Deterministic transient-tool retry policy (never retries deterministic failures)
- [x] Full transcript checkpoint/resume for durable runs

## Tier 1 - Agent reliability (completed)

- [x] Offline replay evals harness: golden traces + deterministic replay through the
      executor, sequence-fidelity regression checks (`agents/evals/`)
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

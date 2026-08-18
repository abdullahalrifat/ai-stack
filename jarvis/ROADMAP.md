# Jarvis world-class terminal roadmap

The goal is not to copy another tool's interface line for line. The goal is to
match the qualities users expect from a world-class coding-agent terminal:
fast startup, dependable process ownership, strong repository context, safe
autonomy, resumable sessions, useful automation, rich extensibility, and
predictable behavior under failure.

Claude Code is one useful external benchmark for session workflows,
non-interactive output, layered permissions, project memory, MCP integration,
installation, and diagnostics. Jarvis must implement those outcomes without requiring a commercial model or
the AI Stack Server. Local mode owns its planning and guarded tools; optional
Server mode adds durable history, isolated remote write sandboxes, and shared
policy.

## Current baseline

The CLI already provides:

- an interactive shell and one-shot task execution;
- persistent history, slash-command completion, multiline input, and
  `/status`;
- per-task interactive edit permission and reviewed sandbox writes;
- disposable Git worktrees with explicit approve/discard;
- foreground client leases and explicit detached runs;
- immediate cancellation attempts for `Ctrl-C`, `SIGHUP`, and `SIGTERM`;
- deterministic abandoned-client cancellation through PostgreSQL;
- durable event replay, SSE heartbeats, and bounded reconnection;
- run list/show/resume/approve/discard/cancel commands;
- workspace-scoped continuation of the latest conversation;
- workspace and saved-project selection without a hardcoded repository;
- text, JSON, and streaming JSON automation output;
- stable script-friendly exit codes;
- a server capability handshake and foreground-lease compatibility check;
- a standard-library-only runtime package, wheel build, installer, tests, and
  independent coverage floor.

Those capabilities primarily describe durable Server mode. Standalone Jarvis
also has a bounded model/tool loop, AGENTS.md instructions, workspace-confined
read/search/edit tools, guarded unified patches, constrained command execution,
Git status/diff inspection, verification, and OpenAI-compatible or Anthropic
remote inference. The following work is required before calling the combined
local experience a world-class general-purpose terminal agent.

## Product principles

1. **The terminal owns foreground work.** Closing a foreground client must not
   leave hidden work consuming models, commands, or sandboxes.
2. **Autonomy remains reviewable.** More convenience must not weaken workspace
   boundaries, command constraints, or explicit write approval.
3. **Jarvis is independently useful.** Local mode owns its agent loop and must
   never require Server. Server mode is explicit and owns only remote runs.
4. **Automation is a first-class interface.** Human output and machine output
   must both be stable, documented, and testable.
5. **Failure is a normal state.** Disconnects, restarts, partial output, stale
   worktrees, unavailable dependencies, and incompatible versions need
   deliberate recovery behavior.
6. **Open-model first does not mean one endpoint.** Local models, remote GPUs,
   multiple workspaces, secure credentials, and enterprise policy fit the same
   provider-neutral design.
7. **Claims require evidence.** A capability is complete only when its safety,
   failure, and compatibility behavior is documented and tested.

## P0: correctness and lifecycle — completed

P0 was completed with protocol v1 before work on the heavier terminal UI:

### 1. In-flight interruption

- One context-local cancellation token covers planning, streamed model calls,
  tool invocation, isolated commands, and Git sandbox processes.
- Runner jobs have authenticated stable IDs, bounded output, explicit timeout
  and cancellation states, and owner leases that stop work after API loss.
- Runner and Git commands execute in new process sessions. Cancellation sends
  `SIGTERM` to the process group and escalates to `SIGKILL` after a bounded
  grace period.
- Closeable OpenAI-compatible streams are closed immediately. Providers that
  block before returning stream headers remain bounded by their HTTP timeout.
- Durable events distinguish cancellation, runner/Git timeout, and kill
  failure. Sandbox paths remain durable until cleanup succeeds.
- Process tests cover child-group termination, timeout, owner loss, network
  loss, `Ctrl-C`, `SIGTERM`, and `SIGHUP`. Live validation covers foreground
  `SIGKILL` lease expiry and private-runner cancellation latency.

### 2. Versioned CLI/server protocol

- [`contracts/aistack-protocol-v1.json`](../contracts/aistack-protocol-v1.json)
  is an OpenAPI 3.1 contract for every CLI-used resource. It stays at the
  repository root because the server, CLI, and Runs UI jointly own it.
- Requests, responses, and events carry protocol/schema versions. The server
  advertises minimum/maximum compatible versions and feature flags.
- Server, CLI, and Runs UI contract tests share the checked-in fixture.
- Version-less v1 events and unknown fields are accepted during migration;
  unknown event schemas and incompatible ranges fail with upgrade guidance.
- Protocol changes have a minimum one-version deprecation window.

### 3. Multi-replica lease safety

- Lease creation, renewal, expiry, and age use PostgreSQL time, avoiding host
  clock skew.
- Sweepers claim indexed, bounded batches with `FOR UPDATE SKIP LOCKED`.
- Real PostgreSQL contention tests prove two sweepers cannot duplicate expiry
  and two workers cannot claim one run.
- Authenticated metrics expose renewal age/failures, expired-run backlog, sweep
  failures, and maximum observed cancellation delay.
- Database outages fail open at the sweeper: no run is cancelled from an
  unverified lease. Recovery expires overdue work using database time.
- Startup reconciliation retries terminal sandbox cleanup after repeated
  crashes.

### 4. Failure-safe output

- Broken pipes exit without tracebacks, and human diagnostics stay on stderr.
- JSON and JSONL modes never prompt or mix in human status lines.
- SSE event buffering and rendered text have configurable hard limits.
- Human output neutralizes terminal control characters while preserving
  Unicode; invalid UTF-8 is replaced safely.
- Non-TTY output disables color, terminal sizing has a safe fallback, and
  golden tests cover every durable event plus all output formats.

## P1: excellent daily interaction

### 5. First-class session discovery

The latest workspace conversation can be continued directly, but discovering
or managing older conversations still requires IDs.

Required work:

- `jarvis --resume` with an interactive searchable picker;
- session names, timestamps, workspace, branch, status, and concise summaries;
- rename, fork, archive, and guarded delete operations;
- workspace-scoped recent-session ordering;
- recovery of pending approval and detached work from the picker.

### 6. Rich prompt composition

The current backslash continuation is deliberately small and dependency-free.
A world-class interactive shell needs:

- native multiline editing without sentinel characters;
- bracketed paste and large-paste protection;
- searchable history scoped globally and per workspace;
- reverse history search;
- an external-editor shortcut using `$VISUAL` or `$EDITOR`;
- keyboard shortcuts that work consistently in Bash, Zsh, Fish, WSL, and
  common terminal emulators;
- configurable Vim/Emacs editing modes; and
- clear handling for `Ctrl-C`, `Ctrl-D`, and suspended terminals.

Use a maintained terminal-input library only if its startup, packaging, and
accessibility costs are justified.

### 7. Files, images, and explicit context

The server can ingest documents, but the terminal cannot attach them.

Required work:

- `@path` references with shell-safe completion;
- explicit `--file`, `--image`, and repeated attachment flags;
- clipboard image paste where the platform supports it;
- directory and glob expansion with a preview before upload;
- size/type validation and progress reporting;
- list/remove attachment commands;
- visible document scope and citation metadata;
- context-budget reporting and truncation warnings; and
- protection against instructions embedded in untrusted attachments.

### 8. World-class diff review

Current diffs are printed inline and approved as one unit.

Required work:

- syntax-highlighted, pageable diffs;
- per-file and per-hunk approve/reject;
- open the diff in the user's configured editor;
- show test evidence beside the relevant changes;
- explain stale-base conflicts and offer safe regeneration;
- undo or revert the most recently approved run;
- preserve an auditable approval record; and
- never make non-interactive approval implicit.

This requires server API changes; the CLI must not fake partial approval by
editing the real checkout itself.

### 9. Project instructions and memory

Standalone Jarvis already loads repository-level `AGENTS.md`. Required work:

- load hierarchical repository instructions beyond the repository root;
- support user-level and workspace-local private instructions;
- show exactly which instruction files are active;
- define precedence, imports, size limits, and cycle protection;
- separate durable preferences from conversation history;
- provide commands to inspect and edit memory; and
- treat repository and attachment instructions as untrusted data where
  appropriate.

### 10. Git-aware workflows

Required work:

- show branch, dirty state, and sandbox base in `/status`;
- summarize changes before and after a run;
- optional commit creation only after explicit approval;
- guarded branch creation and worktree selection;
- commit/PR message drafting with evidence;
- GitHub/GitLab integration through a connector rather than embedded secrets;
  and
- clear policy boundaries for rebase, push, force-push, and destructive Git
  operations.

## P2: permissions, configuration, and security

### 11. Layered configuration

Introduce a documented configuration model with deterministic precedence:

1. managed organization policy;
2. command-line flags;
3. workspace-local private configuration;
4. shared repository configuration;
5. user XDG configuration;
6. environment variables;
7. built-in defaults.

Configuration should cover endpoints, workspace mappings, lifecycle defaults,
output preferences, history, editor, notifications, proxy/TLS settings, and
permission rules. `jarvis config list --sources` should show the winning value
without exposing secrets.

### 12. Secure credential management

Required work:

- OS keyring support;
- an external API-key helper with bounded caching;
- named remote profiles;
- short-lived tokens and refresh behavior;
- custom CA bundles and standard proxy support;
- strict redaction in logs and diagnostics;
- logout/revoke workflows; and
- an explicit no-telemetry mode.

Environment variables must remain supported for containers and CI.

### 13. Granular permission modes

Move beyond the current per-task edit approval:

- plan-only, default, accept-edits, and managed modes;
- allow and deny rules by tool and constrained command pattern;
- deny rules always winning over allow rules;
- one-time, session, workspace, and managed approvals;
- `/permissions` showing every effective rule and its source;
- separate network, filesystem, command, Git, and connector permissions;
- policy evaluation on the server, not trusted to the CLI; and
- auditable approval events.

Do not add an unrestricted “skip safety” mode unless execution is inside a
separately hardened environment with an unmistakable warning and policy gate.

### 14. Hooks and notifications

Required work:

- validated pre/post tool, approval, completion, and failure hooks;
- timeouts, output limits, and recursion protection;
- hooks that can deny but never silently broaden server policy;
- desktop/terminal notifications for detached completion or approval;
- optional sound and terminal-title updates; and
- checked-in project hooks separated from private user hooks.

## P3: extensibility and ecosystem

### 15. MCP and connector management

Required work:

- configure local stdio and remote HTTP MCP servers;
- list/test/enable/disable servers by user or project scope;
- surface tool descriptions, health, and permission requirements;
- cap tool output and defend against prompt injection;
- support authenticated remote connectors without storing plaintext secrets;
- expose useful AI Stack resources as an MCP server where appropriate; and
- keep server policy authoritative over connector availability.

### 16. Skills, plugins, and custom commands

Required work:

- discover versioned skills with documented manifests;
- project and user scopes with clear precedence;
- custom slash commands with argument schemas;
- signed or checksummed plugin installation;
- dependency and compatibility checks;
- permission review before enabling a plugin;
- deterministic enable/disable/uninstall; and
- a small stable extension API rather than imports from internal packages.

### 17. Delegation and background work

The server already decomposes routes internally, but users cannot inspect or
control delegated work.

Required work:

- visible subtask tree, dependencies, status, and evidence;
- bounded parallelism and resource budgets;
- per-subtask cancellation and retry;
- background jobs with attach/detach semantics;
- completion notifications;
- clear aggregation of failures; and
- no hidden recursive agent spawning.

### 18. Stable SDK and headless protocol

Required work:

- documented Python and TypeScript clients generated from the versioned API;
- streaming JSON input as well as output;
- JSON Schema for events and final results;
- max-turn, timeout, and budget controls;
- deterministic non-interactive approval behavior;
- CI examples and reusable GitHub/GitLab actions; and
- contract tests that prevent accidental output-format changes.

## P4: product quality and distribution

### 19. Rich, accessible presentation

Required work:

- incremental Markdown rendering with safe terminal escaping;
- compact and verbose views;
- collapsible tool calls and results;
- progress, elapsed time, context use, tokens, and cost where providers expose
  them;
- color themes plus a complete no-color mode;
- screen-reader-friendly output;
- correct narrow-terminal and resize behavior; and
- a full-screen mode only if it remains optional and script output stays
  pristine.

### 20. Diagnostics and observability

Required work:

- `doctor --verbose` with dependency, version, protocol, workspace, database,
  runner, and model checks;
- a redacted diagnostics bundle;
- structured local debug logs with rotation;
- opt-in OpenTelemetry traces and metrics;
- latency breakdown for routing, model calls, tools, queueing, and rendering;
- token/cost budgets and rate-limit reporting;
- correlation IDs across CLI, API, runner, and model gateway; and
- no code, prompt, path, or credential telemetry without explicit policy.

### 21. Installation, updates, and release engineering

Required work:

- signed standalone binaries for Linux, macOS, and Windows/WSL;
- package-manager distribution where maintainable;
- atomic update with rollback and an update-disable policy;
- release channels and compatibility checks;
- reproducible builds, SBOMs, checksums, and provenance;
- migration/rollback tests;
- semantic versioning and changelogs; and
- a supported uninstall that removes only managed files.

### 22. Performance targets

Define and enforce service-level objectives:

- warm CLI startup below 150 ms before network access;
- first status output below 500 ms on a healthy local stack;
- bounded memory use during long streams;
- no event duplication after reconnect;
- resumable sessions with thousands of stored events;
- cancellation-state transition within the configured lease plus sweep window;
- no polling hot loops; and
- benchmarked performance on local models and remote providers.

### 23. Team and enterprise readiness

Required work:

- per-user identity instead of one shared API key;
- SSO/OIDC and short-lived credentials;
- project/workspace authorization;
- managed policies users cannot override;
- per-user quotas and budgets;
- tamper-evident audit records;
- retention and deletion controls;
- centralized policy/version reporting; and
- documented backup, restore, incident, and upgrade procedures.

## Recommended delivery order

### Milestone A: cannot leave work behind — completed

In-flight cancellation, runner ownership, lifecycle metrics, output safety,
and cross-version protocol contracts are implemented and tested.

### Milestone B: excellent every-day coding

Add session discovery, native multiline editing, attachments, rich diff
review, hierarchical instructions, and Git-aware status.

### Milestone C: safely customizable

Add layered configuration, keyring/helper credentials, granular permissions,
hooks, MCP, skills, and stable SDKs.

### Milestone D: broadly distributable

Add signed multi-platform releases, updates, observability, accessibility,
performance SLOs, and enterprise identity/policy.

## Release gates for “world-class”

Do not use that label until:

- abrupt-exit and in-flight cancellation pass real process-level tests;
- supported CLI/server version combinations pass contract tests;
- no interactive feature can corrupt JSON/JSONL automation output;
- session resume, attachments, and diff approval work end to end;
- effective permissions and configuration sources are inspectable;
- credentials are never required in plaintext files;
- installation and rollback are tested on Linux, macOS, and Windows/WSL;
- startup, reconnect, cancellation, and long-session SLOs are measured in CI;
- security review covers terminal escaping, prompt injection, plugins, MCP, and
  update supply chain; and
- documentation includes recovery procedures for every persistent resource.

## Explicit non-goals

- Do not move the planner or model-selected tool execution into the CLI.
- Do not weaken server workspace boundaries to mimic local unrestricted access.
- Do not approve writes automatically in headless mode.
- Do not reintroduce a default workspace tied to one repository.
- If the CLI moves to a separate repository, keep the OpenAPI contract fixture
  and cross-version release matrix as mandatory gates in both repositories.

## Benchmark references

The roadmap uses current first-party Claude Code documentation as a benchmark
for expected terminal-agent workflows, not as an implementation dependency:

- [CLI commands, output formats, resume, permissions, and directory flags](https://docs.anthropic.com/en/docs/claude-code/cli-usage)
- [Installation, doctor, updates, and platform support](https://docs.anthropic.com/en/docs/claude-code/getting-started)
- [Project and user memory](https://docs.anthropic.com/en/docs/claude-code/memory)
- [Model Context Protocol](https://docs.anthropic.com/en/docs/claude-code/mcp)
- [Security and permission principles](https://docs.anthropic.com/en/docs/claude-code/security)

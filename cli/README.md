# ai-stack terminal agent

`aistack` is the terminal interface for the repository's durable server-side
agent. Running it without arguments opens an interactive shell:

```text
$ aistack
ai-stack agent 0.1.0
workspace: /workspace/ai-stack
type /help for commands
aistack>
```

The terminal is a thin client. It does not run model-selected commands or
maintain a second agent loop on the local machine. Planning, tools, recovery,
verification, sandboxing, and durable history remain in the agent service.

## Package boundary

The CLI is an independent Python package under `cli/`. Its runtime uses only
the Python standard library and communicates with the agent service through
the authenticated Runs HTTP API and Server-Sent Events. It does not import
server code from `agents/`, and the agent container does not install the CLI.

Keeping both packages in this repository makes protocol changes atomic, but
they no longer require lockstep deployment. Protocol v1 has a checked-in
OpenAPI 3.1 contract at
[`contracts/aistack-protocol-v1.json`](../contracts/aistack-protocol-v1.json);
the CLI, server, and Runs UI verify its constants in contract tests.
First-party requests advertise `X-AIStack-Protocol-Version`, `/capabilities`
publishes compatible ranges and feature flags, and incompatible versions fail
with an upgrade instruction.

The canonical contract intentionally lives at the repository root, not under
`cli/`: it defines a boundary jointly owned by the agent server, CLI, and Runs
UI. If the CLI later moves to a separate repository, release automation should
publish or copy this versioned artifact rather than creating a second source of
truth.

See [ROADMAP.md](ROADMAP.md) for the remaining gaps between this focused
client and mature coding-agent terminals.

## Current capabilities

### Interactive terminal experience

- Running `aistack` with no arguments opens a conversational shell.
- Prompts in one shell share a conversation until `/new` or `/clear`.
- Command history persists under the XDG state directory.
- Arrow-key history, Tab completion for slash commands, and backslash-based
  multiline prompts work without third-party runtime dependencies.
- `/status` shows the active workspace, edit-approval behavior, lifecycle, and
  conversation ID.

### Repository work and safety

- Interactive tasks ask whether that task may edit in a disposable Git
  worktree. Any resulting diff is then presented for explicit approval or
  discard.
- The terminal never applies a pending diff automatically in non-interactive
  use.
- Workspace selection is validated by the server and can map a host checkout
  to its in-container path without hardcoding a particular project.
- Saved Runs UI projects can be selected by name or ID prefix.

### Process lifecycle and recovery

- Runs are foreground-owned by default. `Ctrl-C`, `SIGHUP`, and `SIGTERM`
  request cancellation through the normal API path.
- A PostgreSQL-backed client lease cancels an abandoned run after the
  disconnect grace period even when the TCP stream does not close cleanly.
- `--detach` and `/detach` explicitly opt into work that should survive the
  terminal.
- Server-Sent Event heartbeats prevent quiet model calls from looking dead.
- Interrupted streams reconnect from the last durable event ID, avoiding
  repeated output.
- A protocol handshake rejects servers that do not support foreground leases.
- Cancellation follows the run into planning, model streams, tool calls, and
  Git sandbox operations.
- Isolated commands have stable job IDs, an authenticated cancel endpoint, and
  an owner lease. Commands run in process groups and receive `SIGTERM` followed
  by bounded `SIGKILL` escalation.
- API or network loss cannot leave a hidden runner job until its full command
  timeout; the runner's owner lease terminates it.

### Sessions and run management

- `list`, `show`, and `resume` expose durable server-side history.
- Pending write runs can be approved or discarded later.
- Queued and running work can be cancelled explicitly.
- Resuming a run restores its conversation, workspace, and project context in
  the interactive shell.

### Automation

- One-shot tasks accept command-line text or standard input.
- Text, final JSON, and newline-delimited streaming JSON output are supported.
- Machine output never opens an approval prompt; diagnostics remain on stderr.
- Text output sanitizes terminal controls, oversized SSE events have a hard
  bound, and broken pipes exit without a traceback.
- Exit codes distinguish success, failure, cancellation, and interruption.
- API endpoint, API key, workspace, project, conversation, edit permission, and
  lifecycle mode can be selected without modifying source code.

### Packaging and compatibility

- The CLI is a standard-library-only runtime package with its own
  `pyproject.toml`, tests, coverage floor, wheel build, launcher, and installer.
- `aistack doctor` validates authentication, workspace mapping, server protocol
  version, and advertised features.
- The package builds as `aistack-cli` and exposes both the `aistack` console
  entry point and `python -m aistack_cli`.

## Prerequisites

Start the stack and confirm that the agent service is healthy:

```bash
docker compose up -d
docker compose ps agents
```

The default endpoint is `http://127.0.0.1:8000`. The client requires the same
`AGENT_API_KEY` configured for the service.

## Install

From the repository root:

```bash
./cli/scripts/install-aistack
```

This creates:

```text
~/.local/bin/aistack -> /path/to/ai-stack/cli/scripts/aistack
```

The installer:

- does not copy credentials;
- does not overwrite an existing command;
- is safe to run repeatedly; and
- prints a `PATH` reminder when `~/.local/bin` is not available in the current
  shell.

Verify the installation:

```bash
aistack --version
aistack doctor
```

`doctor` reports the server protocol version and advertised features as well
as authentication and workspace mapping.

Remove only the managed symlink with:

```bash
./cli/scripts/install-aistack --uninstall
```

The repository-local form remains available without installation:

```bash
./cli/scripts/aistack
```

## Interactive shell

Start the shell from the repository you want the agent to use:

```bash
cd /path/to/repository
aistack
```

Enter an ordinary task at the prompt:

```text
aistack> review the test configuration and explain any gaps
Allow this task to edit files in a reviewable sandbox? [y/N]: n
```

The client creates a foreground-owned run and displays planning, tool calls,
compact tool results, model output, and completion status. Prompts in one
shell reuse a conversation ID until `/new` or `/clear` is entered. Arrow-key
history persists in `${XDG_STATE_HOME:-~/.local/state}/aistack/history`, and
Tab completes slash commands.

### Shell commands

| Command | Purpose |
| --- | --- |
| `/help` | Show the interactive command reference |
| `/runs` | List recent durable runs |
| `/resume RUN_ID` | Replay or continue monitoring a run and adopt its conversation |
| `/workspace` | Display the current agent workspace |
| `/workspace PATH` | Change to another allowed workspace |
| `/detach` | Let subsequent runs survive terminal exit |
| `/foreground` | Cancel subsequent runs when this client exits |
| `/status` | Show workspace, edit approval, lifecycle, and conversation ID |
| `/approve RUN_ID` | Apply a pending sandbox diff |
| `/discard RUN_ID` | Delete a pending sandbox diff |
| `/cancel RUN_ID` | Request cancellation of a queued or running task |
| `/new` | Start a new conversation |
| `/clear` | Alias for `/new` |
| `/exit` | Leave the shell |

Each task asks for edit permission before it starts:

```text
aistack> fix the failing tests
Allow this task to edit files in a reviewable sandbox? [y/N]: y
```

Pressing `Ctrl-C` while a run is active sends an in-flight cancellation
request. Pressing `Ctrl-C` at an idle prompt clears that prompt without exiting.
`Ctrl-D` exits the shell. `SIGHUP` and `SIGTERM` use the same cancellation
path. End an input line with `\` to continue the task on another line.

## Foreground and detached runs

CLI runs are foreground-owned by default. The client sends a unique lease ID
when it creates a run, and the event stream renews that lease while attached.
A clean interrupt requests cancellation immediately. If the terminal or CLI
process disappears without cleanup, the server requests cancellation after a
30-second grace period.

Use detached mode only when the run should intentionally continue without the
terminal:

```bash
aistack run --detach "perform the long repository audit"
```

In the interactive shell, `/detach` changes subsequent tasks and
`/foreground` restores the default. A detached run is still stored and can be
followed with `aistack resume RUN_ID`.

Cancellation closes active OpenAI-compatible streams when the provider exposes
a closeable stream. Isolated commands and Git sandbox commands terminate their
complete process groups, escalating from `SIGTERM` to `SIGKILL` after a bounded
grace period. A provider that blocks before returning stream headers remains
bounded by its configured HTTP timeout.

## Edit approval workflow

In an interactive terminal, enter the task normally and approve sandbox edits
for that task when prompted:

```text
aistack> fix the failing tests and verify the result
Allow this task to edit files in a reviewable sandbox? [y/N]: y
```

Declining runs that task without edit or command tools. After an approved task
creates changes, the CLI shows the diff and separately asks whether to apply it
to the real checkout:

```text
Apply pending changes? [a]pprove/[d]iscard/[l]ater:
```

- `approve` applies the reviewed diff to the original repository;
- `discard` removes the sandbox without applying it; and
- `later` leaves the run in `awaiting_approval`.

Review a pending run later:

```bash
aistack resume RUN_ID
aistack approve RUN_ID
```

Non-interactive processes never approve a diff automatically.
Use `--allow-edits` (or the backward-compatible `--write` alias) when a
headless task needs sandbox edit tools:

```bash
aistack run --allow-edits --no-review "prepare a reviewable patch"
```

## One-shot commands

The shortest form starts and follows one task:

```bash
aistack "explain how requests are routed"
```

The explicit form exposes run options:

```bash
aistack run "run the tests and summarize failures"
aistack run --allow-edits "repair the failing tests"
aistack run --detach "perform a long repository audit"
aistack run --no-review --allow-edits "prepare a reviewable patch"
```

Read a task from standard input:

```bash
printf '%s\n' "review this stack" | aistack run -
```

Continue a previous conversation:

```bash
aistack run --conversation CONVERSATION_ID "check the remaining issue"
```

Global connection and workspace options go before the subcommand:

```bash
aistack --workspace /workspace/ai-stack run "review the repository"
aistack --project ai-stack run --allow-edits "update the documentation"
aistack --url http://127.0.0.1:8000 doctor
```

`--allow-edits` (and its `--write` compatibility alias) and `--detach` are
accepted either before or after `run`.

## Run management

List recent runs:

```bash
aistack list
aistack list --limit 50
```

Inspect the stored run record:

```bash
aistack show RUN_ID
```

Replay durable events or reconnect to a running task:

```bash
aistack resume RUN_ID
```

The client records the last event ID and reconnects an interrupted event stream
without repeating already displayed events. A brief connection interruption
does not cancel a foreground run because its client lease has a grace period.
If the client does not reconnect, cancellation follows; detached runs continue
on the server. The server emits an SSE heartbeat every ten seconds while a
model call is otherwise quiet, and the client converts socket timeouts into
bounded reconnect attempts instead of terminating with a Python traceback.

Run states include:

| Status | Meaning |
| --- | --- |
| `queued` | Waiting for an agent worker |
| `running` | Planning or executing tools |
| `cancelling` | In-flight cancellation requested |
| `awaiting_approval` | A sandbox diff is ready for review |
| `completed` | Finished, or an approved diff was applied |
| `discarded` | A pending sandbox was discarded |
| `cancelled` | Execution stopped after cancellation |
| `failed` | The server recorded an unrecovered error |

## Automation output

Text is the default human-readable output:

```bash
aistack run "inspect the project"
```

Emit only the final run object:

```bash
aistack run --output json "inspect the project"
```

Emit each durable event as one JSON line:

```bash
aistack run --output stream-json "inspect the project"
```

Exit codes are suitable for scripts:

| Code | Meaning |
| --- | --- |
| `0` | Completed, discarded, or awaiting explicit approval |
| `1` | API/configuration error or failed run |
| `130` | Cancelled or interrupted |

`AISTACK_MAX_SSE_EVENT_BYTES` defaults to 1 MiB and bounds one decoded SSE
event. `AISTACK_MAX_TEXT_EVENT_CHARS` defaults to 200,000 characters and bounds
one human-rendered event. `AISTACK_MAX_HTTP_RESPONSE_BYTES` defaults to 4 MiB
for non-streaming API responses. Increase these only for a trusted server. JSON
modes retain complete valid objects or fail; they never emit a truncated JSON
line.

## Authentication and endpoint configuration

Credential precedence is:

1. `AISTACK_API_KEY`
2. `AGENT_API_KEY`
3. `AISTACK_API_KEY` read from `AISTACK_ENV_FILE`
4. `AGENT_API_KEY` read from `AISTACK_ENV_FILE`

The repository launcher sets `AISTACK_ENV_FILE` to the repository `.env` when
that file exists. It parses the required value as data and never sources or
executes the file.

Examples:

```bash
export AISTACK_API_KEY="replace-with-agent-key"
export AISTACK_URL="http://127.0.0.1:8000"
aistack doctor
```

Do not place keys directly in shell history or commit them to the repository.

## Workspaces and projects

The service operates on in-container paths such as `/workspace/ai-stack`.
When the shell starts, the client compares the current host checkout with
allowed server workspaces and selects the unique matching path.

Inspect the result:

```bash
aistack doctor
aistack workspaces
```

If automatic mapping is ambiguous, select an in-container path:

```bash
aistack --workspace /workspace/ai-stack
```

List and select saved Runs UI projects:

```bash
aistack projects
aistack --project ai-stack
aistack --project PROJECT_ID_PREFIX run "review this project"
```

Workspace validation remains server-side. The CLI cannot expand configured
workspace roots.

## Continue and IDE usage

Use one orchestration owner for each task:

- Use `aistack` or the Runs UI for autonomous repository review, debugging,
  testing, edits, and approval.
- Use Continue with the direct `coder` model for completion, questions, and
  small IDE-owned edits.
- Do not point Continue Agent mode at `agent.orchestrator`; that nests the IDE
  agent around the server agent and creates competing tool loops.

The terminal shell is the first-party interface for the server agent. Continue
does not participate in an `aistack` run.

## Troubleshooting

### `aistack: command not found`

Run the installer and verify that `~/.local/bin` is on `PATH`:

```bash
./cli/scripts/install-aistack
command -v aistack
```

Start a new terminal after changing `PATH`.

### API is unreachable

Check the service and configured URL:

```bash
docker compose ps agents
curl -fsS http://127.0.0.1:8000/health
AISTACK_URL=http://127.0.0.1:8000 aistack doctor
```

### Authentication fails

Confirm that the client key matches `AGENT_API_KEY` used by the running
container:

```bash
aistack doctor
docker compose config
```

Do not print the actual key while diagnosing it.

### Workspace cannot be mapped

List the paths visible to the service and pass one explicitly:

```bash
aistack workspaces
aistack --workspace /workspace/repository doctor
```

The host repository must be inside `WORKSPACE_PATH` or another configured
workspace mount.

### A run is still active after closing the terminal

Foreground cancellation has a 30-second disconnect grace period. After expiry,
the worker closes active model streams and cancels active runner/Git process
groups. Inspect or cancel explicitly:

```bash
aistack list
aistack resume RUN_ID
aistack cancel RUN_ID
```

If the run was started with `--detach` or `/detach`, remaining active is
expected. If an ordinary foreground run never moves to `cancelling`, rebuild
and restart the agent service so migrations `002_client_leases.sql` and
`003_client_lease_sweeping.sql` are applied.

Lease sweeping uses bounded `FOR UPDATE SKIP LOCKED` batches, so multiple API
replicas do not publish duplicate disconnect events. `GET /metrics/runs`
reports database-clock renewal age, expired foreground runs, renewal failures,
sweep failures, and maximum observed cancellation delay.

During a PostgreSQL outage lease handling is fail-open at the database
boundary: the monitor logs and counts the failed sweep instead of guessing
that a client disconnected. When PostgreSQL returns, the next sweep uses
database time and expires overdue leases. Terminal sandbox paths remain
recorded until startup reconciliation removes them successfully, so cleanup is
retried after repeated crashes.

### The terminal reports an event-stream timeout

Current clients reconnect automatically, and current agent containers emit
heartbeats during quiet model calls. Rebuild and restart the agent service if
either side predates this behavior:

```bash
docker compose build agents
docker compose up -d --force-recreate agents
aistack resume RUN_ID
```

Older clients do not send a foreground lease ID, so their runs remain durable
after a socket `TimeoutError`.

### A write run made no changes

The server removes an empty sandbox and completes the run without requesting
approval. Review its tool events and final answer with:

```bash
aistack resume RUN_ID
```

### Approval is rejected as stale

The repository HEAD changed after the sandbox was created. This protection
prevents applying a diff onto an unexpected revision. Discard the old run and
start a new write task against the current repository state.

## Security model

- The CLI sends its key only to `AISTACK_URL`.
- Use HTTPS when `AISTACK_URL` is not bound to the local machine.
- Model-selected tools execute in the server's constrained agent environment,
  not in the terminal client.
- Workspace access remains limited by server configuration.
- Write runs use disposable Git worktrees.
- Applying a diff requires an explicit approval request.
- Foreground CLI runs expire when their matching client lease is abandoned.
- Detached mode must be selected explicitly when work should survive exit.
- The installer refuses to overwrite another `aistack` command.
- Uninstall removes only the symlink created for this checkout.

See the repository [security model](../README.md#security-model) for the
server-side controls.

## Development

Run the isolated CLI checks from the repository root:

```bash
PYTHONPATH=cli/src python3 -m pytest -q cli/tests
python3 -m compileall -q cli/src
```

The package can also be installed in a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ./cli
.venv/bin/aistack --version
```

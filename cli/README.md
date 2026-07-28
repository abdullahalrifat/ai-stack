# ai-stack terminal agent

`aistack` is the terminal interface for the repository's durable server-side
agent. Running it without arguments opens an interactive shell:

```text
$ aistack
ai-stack agent 0.1.0
workspace: /workspace/ai-stack
type /help for commands
aistack [read]>
```

The terminal is a thin client. It does not run model-selected commands or
maintain a second agent loop on the local machine. Planning, tools, recovery,
verification, sandboxing, and durable history remain in the agent service.

## Package boundary

The CLI is an independent Python package under `cli/`. Its runtime uses only
the Python standard library and communicates with the agent service through
the authenticated Runs HTTP API and Server-Sent Events. It does not import
server code from `agents/`, and the agent container does not install the CLI.

Keeping both packages in this repository currently makes changes to the
unversioned Runs protocol atomic. The CLI can move to a separate repository
after that protocol has explicit versioning, capability discovery, and a
documented compatibility policy.

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
aistack [read]> review the test configuration and explain any gaps
```

The client creates a durable run and displays planning, tool calls, compact
tool results, model output, and completion status. Prompts in one shell reuse a
conversation ID until `/new` is entered.

### Shell commands

| Command | Purpose |
| --- | --- |
| `/help` | Show the interactive command reference |
| `/runs` | List recent durable runs |
| `/resume RUN_ID` | Replay or continue monitoring a run and adopt its conversation |
| `/workspace` | Display the current agent workspace |
| `/workspace PATH` | Change to another allowed workspace |
| `/write` | Enable reviewed sandbox writes for subsequent prompts |
| `/read-only` | Return to read-only mode |
| `/approve RUN_ID` | Apply a pending sandbox diff |
| `/discard RUN_ID` | Delete a pending sandbox diff |
| `/cancel RUN_ID` | Request cancellation of a queued or running task |
| `/new` | Start a new conversation |
| `/exit` | Leave the shell |

The prompt always shows the current permission mode:

```text
aistack [read]>
aistack [write]>
```

Pressing `Ctrl-C` while a run is active sends a cooperative cancellation
request. Pressing `Ctrl-C` at an idle prompt clears that prompt without exiting.
`Ctrl-D` exits the shell.

## Read-only and write workflows

Read-only is the default:

```bash
aistack "inspect the authentication code and report risks"
```

For edits, enable a reviewable sandbox:

```bash
aistack run --write "fix the failing tests and verify the result"
```

The server creates a disposable Git worktree, runs the task there, and returns
the resulting diff. It does not modify the real checkout automatically. In an
interactive terminal, choose:

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

## One-shot commands

The shortest form starts and follows one task:

```bash
aistack "explain how requests are routed"
```

The explicit form exposes run options:

```bash
aistack run "run the tests and summarize failures"
aistack run --write "repair the failing tests"
aistack run --no-review --write "prepare a reviewable patch"
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
aistack --project ai-stack run --write "update the documentation"
aistack --url http://127.0.0.1:8000 doctor
```

`--write` is accepted either before or after `run`.

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
without repeating already displayed events. The run itself continues on the
server if the terminal closes. The server emits an SSE heartbeat every ten
seconds while a model call is otherwise quiet; the client also converts socket
timeouts into bounded reconnect attempts instead of terminating with a Python
traceback.

Run states include:

| Status | Meaning |
| --- | --- |
| `queued` | Waiting for an agent worker |
| `running` | Planning or executing tools |
| `cancelling` | Cooperative cancellation requested |
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

Runs are server-owned and durable. Reconnect or cancel explicitly:

```bash
aistack list
aistack resume RUN_ID
aistack cancel RUN_ID
```

### The terminal reports an event-stream timeout

Current clients reconnect automatically, and current agent containers emit
heartbeats during quiet model calls. Rebuild and restart the agent service if
either side predates this behavior:

```bash
docker compose build agents
docker compose up -d --force-recreate agents
aistack resume RUN_ID
```

The run is durable and normally remains active even if an older client exits
with a socket `TimeoutError`.

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

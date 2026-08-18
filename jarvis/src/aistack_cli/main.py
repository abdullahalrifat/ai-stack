"""Command-line entry point for the durable ai-stack coding agent."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import shlex
import signal
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import __version__
from .client import AgentClient, APIError
from .render import EventRenderer

TERMINAL_STATUSES = {
    "awaiting_approval",
    "cancelled",
    "completed",
    "discarded",
    "failed",
}
SHELL_COMMANDS = (
    "/approve",
    "/cancel",
    "/clear",
    "/detach",
    "/discard",
    "/exit",
    "/foreground",
    "/help",
    "/new",
    "/quit",
    "/resume",
    "/runs",
    "/status",
    "/workspace",
)
_EDIT_INTENT = re.compile(
    r"\b(?:add|build|change|create|edit|fix|implement|improve|imporove|modify|refactor|"
    r"remove|rename|replace|update|write)\b",
    re.IGNORECASE,
)


def _env_value(path: Path, name: str) -> str | None:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == name:
            return value.strip().strip("\"'")
    return None


def resolve_api_key(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    for name in ("AISTACK_API_KEY", "AGENT_API_KEY"):
        if os.getenv(name):
            return str(os.environ[name])
    env_file = os.getenv("AISTACK_ENV_FILE")
    if env_file:
        for name in ("AISTACK_API_KEY", "AGENT_API_KEY"):
            value = _env_value(Path(env_file), name)
            if value:
                return value
    raise APIError("No API key configured. Set AISTACK_API_KEY or AGENT_API_KEY.")


def _history_path() -> Path:
    configured = os.getenv("AISTACK_HISTORY_FILE")
    if configured:
        return Path(configured).expanduser()
    state_home = os.getenv("XDG_STATE_HOME")
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local/state"
    return base / "aistack/history"


def configure_shell_history() -> None:
    """Enable persistent history and slash-command completion when available."""

    try:
        import readline
    except ImportError:  # pragma: no cover - platform dependent
        return

    history_file = _history_path()
    try:
        history_file.parent.mkdir(parents=True, exist_ok=True)
        readline.read_history_file(history_file)
    except FileNotFoundError:
        pass
    except OSError:
        return

    readline.set_history_length(1_000)

    def complete(text: str, state: int) -> str | None:
        matches = [command for command in SHELL_COMMANDS if command.startswith(text)]
        return matches[state] if state < len(matches) else None

    readline.set_completer(complete)
    readline.parse_and_bind("tab: complete")

    def save_history() -> None:
        try:
            readline.write_history_file(history_file)
        except OSError:
            pass

    atexit.register(save_history)


def read_shell_input(prompt: str) -> str:
    """Read one task, joining lines ending in a backslash."""

    lines = [input(prompt)]
    while lines[-1].endswith("\\"):
        lines[-1] = lines[-1][:-1]
        lines.append(input("... "))
    return "\n".join(lines)


def request_edit_permission() -> bool:
    """Ask whether one interactive task may use the write sandbox."""

    while True:
        try:
            choice = (
                input("Allow this task to edit files in a reviewable sandbox? [y/N]: ")
                .strip()
                .lower()
            )
        except EOFError:
            print()
            return False
        if choice in {"", "n", "no"}:
            return False
        if choice in {"y", "yes"}:
            return True
        print("Please answer yes or no.")


def task_requests_edits(task: str) -> bool:
    """Return whether a task clearly asks to change the workspace."""

    return bool(_EDIT_INTENT.search(task))


def _import_chat_stream_text():
    try:
        from app.llm.client import chat_stream_text

        return chat_stream_text
    except Exception:
        return None


def simulated_stream(prompt: str):
    text = (
        "Quicksort is a divide-and-conquer sorting algorithm. It picks a pivot, "
        "partitions the array, and recursively sorts the resulting subarrays. "
        "This makes it fast on average and easy to understand."
    )
    for i in range(0, len(text), 20):
        yield text[i : i + 20]
        time.sleep(0.05)


def stream_response(prompt: str, simulate: bool = False):
    """Stream a direct model response without ever fabricating a fallback."""

    if simulate:
        yield from simulated_stream(prompt)
        return

    chat_stream_text = _import_chat_stream_text()
    if chat_stream_text is None:
        raise APIError(
            "Direct local streaming is unavailable in the standalone CLI. "
            "Use 'jarvis run' to connect to the configured remote agent service."
        )

    messages = [{"role": "user", "content": prompt}]
    try:
        yield from chat_stream_text(messages)
    except Exception as exc:
        raise APIError(f"Direct model stream failed: {exc}") from exc


def stream_prompt(args: argparse.Namespace) -> int:
    if args.prompt:
        prompt = " ".join(args.prompt).strip()
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        try:
            prompt = input("Prompt: ").strip()
        except EOFError:
            return 1
    if not prompt:
        print("Error: no prompt provided.", file=sys.stderr)
        return 1

    start = time.time()
    try:
        for chunk in stream_response(prompt, simulate=args.simulate):
            print(chunk, end="", flush=True)
        print()
    except KeyboardInterrupt:
        print("\nStreaming cancelled.", file=sys.stderr)
        return 1
    except APIError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    elapsed = time.time() - start
    print(f"\n[done in {elapsed:.2f}s]", file=sys.stderr)
    return 0


@contextmanager
def cancellation_signals():
    """Convert terminal shutdown signals into the normal cancellation path."""

    previous = {}

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    try:
        for name in ("SIGTERM", "SIGHUP"):
            signum = getattr(signal, name, None)
            if signum is None:
                continue
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupt)
    except ValueError:
        previous.clear()
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _common_suffix(left: Path, right: Path) -> int:
    count = 0
    for left_part, right_part in zip(
        reversed(left.parts),
        reversed(right.parts),
    ):
        if left_part != right_part:
            break
        count += 1
    return count


def match_workspace(local_path: Path, choices: list[str]) -> str | None:
    """Map a host checkout to the corresponding in-container workspace."""

    resolved = local_path.resolve()
    exact = [choice for choice in choices if Path(choice) == resolved]
    if exact:
        return exact[0]
    scored = [(_common_suffix(resolved, Path(choice)), choice) for choice in choices]
    best = max((score for score, _ in scored), default=0)
    matches = [choice for score, choice in scored if score == best and score > 0]
    return matches[0] if len(matches) == 1 else None


def resolve_project(
    projects: list[dict[str, Any]],
    selector: str,
) -> dict[str, Any]:
    exact_name = [
        project for project in projects if str(project.get("name")) == selector
    ]
    id_matches = [
        project
        for project in projects
        if str(project.get("id", "")).startswith(selector)
    ]
    matches = exact_name or id_matches
    if len(matches) != 1:
        raise APIError(f"Project selector is missing or ambiguous: {selector}")
    return matches[0]


def resolve_workspace(
    client: AgentClient,
    requested: str | None,
    project: str | None,
) -> tuple[str, str | None]:
    if project:
        selected = resolve_project(client.projects(), project)
        return str(selected["workspace"]), str(selected["id"])

    choices = client.workspaces()
    if requested:
        if requested in choices:
            return requested, None
        matched = match_workspace(Path(requested), choices)
        if matched:
            return matched, None
        raise APIError(
            f"Could not map '{requested}' to an agent workspace. "
            "Run `jarvis workspaces` or pass an in-container path."
        )

    matched = match_workspace(Path.cwd(), choices)
    return (matched or client.default_workspace()), None


def latest_conversation_id(client: AgentClient, workspace: str) -> str:
    """Return the newest resumable conversation for one workspace."""

    for run in client.list_runs(limit=100):
        conversation_id = run.get("conversation_id")
        if (
            run.get("requested_workspace") == workspace
            and conversation_id
            and run.get("status") in TERMINAL_STATUSES
        ):
            return str(conversation_id)
    raise APIError(f"No previous conversation found for workspace: {workspace}")


def follow_run(
    client: AgentClient,
    run_id: str,
    renderer: EventRenderer,
    *,
    client_id: str | None = None,
) -> dict[str, Any]:
    cursor = 0
    retries = 0
    try:
        while True:
            starting_cursor = cursor
            try:
                stream_options: dict[str, Any] = {"after": cursor}
                if client_id:
                    stream_options["client_id"] = client_id
                for event in client.stream_events(run_id, **stream_options):
                    if event.get("id"):
                        cursor = max(cursor, int(event["id"]))
                    renderer.render(event)
                run = client.get_run(run_id)
                if run.get("status") in TERMINAL_STATUSES:
                    renderer.render_run(run)
                    return run
                if cursor > starting_cursor:
                    retries = 0
                retries += 1
                if retries > 5:
                    raise APIError("Run event stream ended before the run completed")
            except APIError:
                if cursor > starting_cursor:
                    retries = 0
                retries += 1
                if retries > 5:
                    raise
            time.sleep(min(2 ** (retries - 1), 5))
    except BaseException:
        if client_id:
            try:
                client.action(run_id, "cancel")
            except APIError:
                pass
        raise


def review_run(
    client: AgentClient,
    run: dict[str, Any],
    *,
    auto_approve: bool,
) -> dict[str, Any]:
    """Auto-apply an awaiting_approval run's pending diff without prompting."""
    if run.get("status") != "awaiting_approval" or not auto_approve:
        return run
    try:
        client.action(str(run["id"]), "approve")
    except APIError as exc:
        print(f"Could not apply pending changes: {exc}", file=sys.stderr)
        print(f"Review later with: jarvis resume {run['id']}")
        return run
    run["status"] = "completed"
    print("Changes approved and applied.")
    return run


def run_task(
    client: AgentClient,
    task: str,
    *,
    workspace: str,
    project_id: str | None,
    conversation_id: str,
    allow_write: bool,
    detached: bool,
    output: str,
    review: bool,
) -> dict[str, Any]:
    ensure_compatible = getattr(client, "ensure_compatible", None)
    if ensure_compatible is not None:
        required = (
            ("durable_events",)
            if detached
            else (
                "durable_events",
                "client_leases",
            )
        )
        ensure_compatible(*required)
    client_id = None if detached else str(uuid.uuid4())
    created = client.create_run(
        task,
        workspace=workspace,
        conversation_id=conversation_id,
        allow_write=allow_write,
        project_id=project_id,
        client_id=client_id,
    )
    run_id = str(created["run_id"])
    if client_id and created.get("client_id") != client_id:
        try:
            client.action(run_id, "cancel")
        except APIError:
            pass
        raise APIError(
            "The agent server does not support foreground client leases. "
            "Update the server, or use --detach intentionally."
        )
    if output == "text":
        # The run id is a lifecycle handle. Make it observable immediately
        # even when stdout is redirected to a pipe or log collector.
        print(f"Run {run_id}", flush=True)
    renderer = EventRenderer(output=output)
    with cancellation_signals():
        run = follow_run(client, run_id, renderer, client_id=client_id)
    return review_run(
        client,
        run,
        auto_approve=review and output == "text",
    )


def _print_runs(runs: list[dict[str, Any]]) -> None:
    if not runs:
        print("No runs found.")
        return
    for run in runs:
        created = str(run.get("created_at", ""))[:19].replace("T", " ")
        task = " ".join(str(run.get("task", "")).split())
        print(
            f"{str(run.get('id', ''))[:8]}  "
            f"{run.get('status', '')!s:<18}  "
            f"{created:<19}  {task[:80]}"
        )


def _shell_help() -> None:
    print("""Commands:
  /help                 Show this help
  /runs                 List recent runs
  /resume RUN_ID        Replay or follow a run and adopt its conversation
  /workspace [PATH]     Show or change the active workspace
  /detach                Let runs survive terminal exit
  /foreground            Cancel runs when this terminal exits
  /status                Show session settings
  /approve RUN_ID       Apply a pending diff
  /discard RUN_ID       Discard a pending diff
  /cancel RUN_ID        Cancel a queued/running task
  /new                   Start a new conversation
  /clear                 Alias for /new
  /exit                  Exit the shell

Any other input starts a foreground agent run.
End a line with \\ to continue a task on the next line.""")


def interactive_shell(
    client: AgentClient,
    *,
    workspace: str,
    project_id: str | None,
    allow_write: bool,
    detached: bool = False,
    conversation_id: str | None = None,
) -> int:
    conversation_id = conversation_id or str(uuid.uuid4())
    active_run: dict[str, Any] | None = None
    print(f"Jarvis {__version__}")
    print(f"workspace: {workspace}")
    print("type /help for commands")

    while True:
        try:
            line = read_shell_input("jarvis> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        if not line:
            continue
        if not line.startswith("/"):
            try:
                task_allow_write = allow_write and task_requests_edits(line)
                active_run = run_task(
                    client,
                    line,
                    workspace=workspace,
                    project_id=project_id,
                    conversation_id=conversation_id,
                    allow_write=task_allow_write,
                    detached=detached,
                    output="text",
                    review=True,
                )
            except KeyboardInterrupt:
                print("\nCancellation requested.")
            except APIError as exc:
                print(f"Error: {exc}", file=sys.stderr)
            continue

        try:
            parts = shlex.split(line)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            continue
        command = parts[0].lower()
        argument = parts[1] if len(parts) > 1 else None
        try:
            if command in {"/exit", "/quit"}:
                return 0
            if command == "/help":
                _shell_help()
            elif command == "/runs":
                _print_runs(client.list_runs())
            elif command == "/detach":
                detached = True
                print("Detached mode enabled; runs survive terminal exit.")
            elif command == "/foreground":
                detached = False
                print("Foreground mode enabled; terminal exit cancels active runs.")
            elif command == "/status":
                print(f"workspace: {workspace}")
                permission = (
                    "automatic for change requests" if allow_write else "read-only"
                )
                print(f"edit permission: {permission}")
                print(f"lifecycle: {'detached' if detached else 'foreground'}")
                print(f"conversation: {conversation_id}")
            elif command in {"/new", "/clear"}:
                conversation_id = str(uuid.uuid4())
                active_run = None
                print("Started a new conversation.")
            elif command == "/workspace":
                if argument:
                    workspace, project_id = resolve_workspace(client, argument, None)
                print(workspace)
            elif command == "/resume":
                if not argument:
                    raise APIError("Usage: /resume RUN_ID")
                run = client.get_run(argument)
                with cancellation_signals():
                    active_run = follow_run(
                        client,
                        str(run["id"]),
                        EventRenderer(),
                        client_id=run.get("client_id"),
                    )
                conversation_id = str(
                    active_run.get("conversation_id") or conversation_id
                )
                workspace = str(active_run.get("requested_workspace") or workspace)
                project_id = active_run.get("project_id")
                active_run = review_run(
                    client,
                    active_run,
                    auto_approve=sys.stdin.isatty(),
                )
            elif command in {"/approve", "/discard", "/cancel"}:
                run_id = argument or (str(active_run["id"]) if active_run else None)
                if not run_id:
                    raise APIError(f"Usage: {command} RUN_ID")
                result = client.action(run_id, command[1:])
                print(f"{run_id}: {result.get('status', command[1:])}")
            else:
                print(f"Unknown command: {command}. Type /help.")
        except (APIError, KeyError) as exc:
            print(f"Error: {exc}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Jarvis: a standalone, open-model coding agent.",
    )
    parser.add_argument(
        "--url",
        default=os.getenv("AISTACK_URL", "http://127.0.0.1:8000"),
        help="Agent API URL (default: %(default)s)",
    )
    parser.add_argument(
        "--workspace",
        help="Agent workspace path, or a local path that maps to one",
    )
    parser.add_argument("--project", help="Project name or ID prefix")
    parser.add_argument(
        "--allow-edits",
        "--write",
        dest="write",
        action="store_true",
        default=True,
        help="Automatically apply edits (default)",
    )
    parser.add_argument(
        "--read-only",
        dest="write",
        action="store_false",
        help="Disable workspace edits",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="Let active runs continue after this client exits",
    )
    parser.add_argument(
        "--continue",
        dest="continue_session",
        action="store_true",
        help="Continue the latest conversation in the selected workspace",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Start a task and stream its progress")
    run.add_argument("task", nargs="+", help="Task text, or - to read stdin")
    run.add_argument(
        "--output",
        choices=("text", "json", "stream-json"),
        default="text",
    )
    run.add_argument("--conversation", help="Conversation ID for a follow-up")
    run.add_argument(
        "--continue",
        dest="continue_session",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Continue the latest conversation in the selected workspace",
    )
    run.add_argument(
        "--no-review",
        action="store_true",
        help="Leave pending changes for later instead of auto-applying them",
    )
    run.add_argument(
        "--allow-edits",
        "--write",
        dest="write",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Automatically apply edits (default)",
    )
    run.add_argument(
        "--read-only",
        dest="write",
        action="store_false",
        default=argparse.SUPPRESS,
        help="Disable workspace edits",
    )
    run.add_argument(
        "--detach",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Let the run continue after this client exits",
    )

    local = subparsers.add_parser(
        "local",
        help="Run the coding agent locally against a remote model API",
    )
    local.add_argument("task", nargs="*", help="Task text; reads stdin when omitted")
    local.add_argument("--provider", choices=("openai", "anthropic"))
    local.add_argument("--base-url", help="Remote model API base URL")
    local.add_argument("--model", help="Remote model identifier")
    local.add_argument(
        "--api-key-env",
        help="Environment variable containing the model API key",
    )
    local.add_argument("--workspace", dest="local_workspace")
    local.add_argument("--max-steps", type=int, default=30)
    local.add_argument("--timeout", type=float, default=180)
    local.add_argument(
        "--accept-edits",
        action="store_true",
        help="Apply model-proposed patches without an interactive prompt",
    )
    local.add_argument(
        "--accept-commands",
        action="store_true",
        help="Run allowlisted commands without an interactive prompt",
    )
    local.add_argument(
        "--read-only",
        dest="write",
        action="store_false",
        default=True,
        help="Disable the local patch tool",
    )

    stream = subparsers.add_parser(
        "stream",
        help="Stream a local prompt with a lightweight local LLM UX",
    )
    stream.add_argument(
        "prompt",
        nargs="*",
        help="Prompt text for the stream. If omitted, reads from stdin or prompts interactively.",
    )
    stream.add_argument(
        "--simulate",
        action="store_true",
        help="Always use simulated streaming output instead of a real local model.",
    )

    listing = subparsers.add_parser("list", help="List recent runs")
    listing.add_argument("--limit", type=int, default=20)
    show = subparsers.add_parser("show", help="Show one run")
    show.add_argument("run_id")
    resume = subparsers.add_parser("resume", help="Replay or follow one run")
    resume.add_argument("run_id")
    for action in ("approve", "discard", "cancel"):
        action_parser = subparsers.add_parser(action, help=f"{action.title()} a run")
        action_parser.add_argument("run_id")
    subparsers.add_parser("projects", help="List configured projects")
    subparsers.add_parser("workspaces", help="List agent workspaces")
    subparsers.add_parser("doctor", help="Check API access and workspace mapping")
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    commands = {
        "approve",
        "cancel",
        "discard",
        "doctor",
        "list",
        "local",
        "projects",
        "resume",
        "run",
        "show",
        "stream",
        "workspaces",
    }
    if argv and not argv[0].startswith("-") and argv[0] not in commands:
        argv = ["run", *argv]
    args = build_parser().parse_args(argv)
    if args.command == "local":
        try:
            from .local_agent import (
                LocalTools,
                interactive_approval,
                resolve_local_config,
                run_local_agent,
            )

            task = " ".join(args.task).strip()
            if not task and not sys.stdin.isatty():
                task = sys.stdin.read().strip()
            if not task:
                task = input("Task: ").strip()
            if not task:
                raise APIError("Task cannot be empty")
            config = resolve_local_config(args)
            tools = LocalTools(config, approval=interactive_approval)
            print(run_local_agent(task, config, tools=tools))
            return 0
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            return 130
        except APIError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    if args.command == "stream":
        return stream_prompt(args)
    try:
        client = AgentClient(args.url, resolve_api_key())
        if args.command == "list":
            _print_runs(client.list_runs(args.limit))
            return 0
        if args.command == "show":
            print(json.dumps(client.get_run(args.run_id), default=str, indent=2))
            return 0
        if args.command == "resume":
            run = client.get_run(args.run_id)
            with cancellation_signals():
                run = follow_run(
                    client,
                    str(run["id"]),
                    EventRenderer(),
                    client_id=run.get("client_id"),
                )
            review_run(client, run, auto_approve=sys.stdin.isatty())
            return run_exit_code(run)
        if args.command in {"approve", "discard", "cancel"}:
            result = client.action(args.run_id, args.command)
            print(json.dumps(result))
            return 0
        if args.command == "projects":
            for project in client.projects():
                print(
                    f"{str(project.get('id', ''))[:8]}  "
                    f"{project.get('name')}  {project.get('workspace')}"
                )
            return 0
        if args.command == "workspaces":
            for workspace in client.workspaces():
                print(workspace)
            return 0

        workspace, project_id = resolve_workspace(
            client,
            args.workspace,
            args.project,
        )
        if args.command == "doctor":
            health = client.health()
            capabilities = client.capabilities()
            print(f"API: {health.get('status', 'unknown')} ({args.url})")
            print(f"Protocol: {capabilities.get('api_version', 'unknown')}")
            print(
                "Features: "
                + ", ".join(str(item) for item in capabilities.get("features", []))
            )
            print(f"Workspace: {workspace}")
            print("Authentication: ok")
            return 0
        if args.command == "run":
            task = (
                sys.stdin.read().strip()
                if args.task == ["-"]
                else " ".join(args.task).strip()
            )
            if not task:
                raise APIError("Task cannot be empty")
            if args.conversation and args.continue_session:
                raise APIError("Use either --conversation or --continue, not both")
            conversation_id = (
                latest_conversation_id(client, workspace)
                if args.continue_session
                else args.conversation or str(uuid.uuid4())
            )
            allow_write = args.write and task_requests_edits(task)
            run = run_task(
                client,
                task,
                workspace=workspace,
                project_id=project_id,
                conversation_id=conversation_id,
                allow_write=allow_write,
                detached=args.detach,
                output=args.output,
                review=not args.no_review,
            )
            return run_exit_code(run)
        if args.command == "stream":
            return stream_prompt(args)
        configure_shell_history()
        conversation_id = (
            latest_conversation_id(client, workspace) if args.continue_session else None
        )
        return interactive_shell(
            client,
            workspace=workspace,
            project_id=project_id,
            allow_write=args.write,
            detached=args.detach,
            conversation_id=conversation_id,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Avoid both a traceback and Python's second broken-pipe warning while
        # flushing stdout during interpreter shutdown.
        try:
            descriptor = sys.stdout.fileno()
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, descriptor)
            os.close(devnull)
        except (AttributeError, OSError):
            pass
        return 0
    except APIError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def run_exit_code(run: dict[str, Any]) -> int:
    if run.get("status") == "cancelled":
        return 130
    if run.get("status") == "failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

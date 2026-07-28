"""Command-line entry point for the durable ai-stack coding agent."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
import uuid
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
            "Run `aistack workspaces` or pass an in-container path."
        )

    matched = match_workspace(Path.cwd(), choices)
    return (matched or client.default_workspace()), None


def follow_run(
    client: AgentClient,
    run_id: str,
    renderer: EventRenderer,
) -> dict[str, Any]:
    cursor = 0
    retries = 0
    try:
        while True:
            try:
                for event in client.stream_events(run_id, after=cursor):
                    if event.get("id"):
                        cursor = max(cursor, int(event["id"]))
                    renderer.render(event)
                run = client.get_run(run_id)
                if run.get("status") in TERMINAL_STATUSES:
                    renderer.render_run(run)
                    return run
                retries += 1
                if retries > 5:
                    raise APIError("Run event stream ended before the run completed")
            except APIError:
                retries += 1
                if retries > 5:
                    raise
            time.sleep(min(2 ** (retries - 1), 5))
    except KeyboardInterrupt:
        try:
            client.action(run_id, "cancel")
        except APIError:
            pass
        raise


def review_run(
    client: AgentClient,
    run: dict[str, Any],
    *,
    interactive: bool,
) -> dict[str, Any]:
    if run.get("status") != "awaiting_approval" or not interactive:
        return run
    while True:
        try:
            choice = (
                input("Apply pending changes? [a]pprove/[d]iscard/[l]ater: ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print(f"\nReview later with: aistack resume {run['id']}")
            return run
        if choice in {"a", "approve"}:
            client.action(str(run["id"]), "approve")
            run["status"] = "completed"
            print("Changes approved and applied.")
            return run
        if choice in {"d", "discard"}:
            client.action(str(run["id"]), "discard")
            run["status"] = "discarded"
            print("Pending changes discarded.")
            return run
        if choice in {"l", "later", ""}:
            print(f"Review later with: aistack resume {run['id']}")
            return run


def run_task(
    client: AgentClient,
    task: str,
    *,
    workspace: str,
    project_id: str | None,
    conversation_id: str,
    allow_write: bool,
    output: str,
    review: bool,
) -> dict[str, Any]:
    created = client.create_run(
        task,
        workspace=workspace,
        conversation_id=conversation_id,
        allow_write=allow_write,
        project_id=project_id,
    )
    run_id = str(created["run_id"])
    if output == "text":
        print(f"Run {run_id}")
    renderer = EventRenderer(output=output)
    run = follow_run(client, run_id, renderer)
    return review_run(client, run, interactive=review and sys.stdin.isatty())


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
  /write                 Enable reviewed sandbox writes
  /read-only             Disable writes
  /approve RUN_ID       Apply a pending diff
  /discard RUN_ID       Discard a pending diff
  /cancel RUN_ID        Cancel a queued/running task
  /new                   Start a new conversation
  /exit                  Exit the shell

Any other input starts a durable agent run.""")


def interactive_shell(
    client: AgentClient,
    *,
    workspace: str,
    project_id: str | None,
    allow_write: bool,
) -> int:
    conversation_id = str(uuid.uuid4())
    active_run: dict[str, Any] | None = None
    print(f"ai-stack agent {__version__}")
    print(f"workspace: {workspace}")
    print("type /help for commands")

    while True:
        mode = "write" if allow_write else "read"
        try:
            line = input(f"aistack [{mode}]> ").strip()
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
                active_run = run_task(
                    client,
                    line,
                    workspace=workspace,
                    project_id=project_id,
                    conversation_id=conversation_id,
                    allow_write=allow_write,
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
            elif command == "/write":
                allow_write = True
                print("Reviewed sandbox writes enabled.")
            elif command in {"/read", "/read-only"}:
                allow_write = False
                print("Read-only mode enabled.")
            elif command == "/new":
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
                active_run = follow_run(
                    client,
                    str(run["id"]),
                    EventRenderer(),
                )
                conversation_id = str(
                    active_run.get("conversation_id") or conversation_id
                )
                workspace = str(active_run.get("requested_workspace") or workspace)
                project_id = active_run.get("project_id")
                active_run = review_run(
                    client,
                    active_run,
                    interactive=sys.stdin.isatty(),
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
        prog="aistack",
        description="Terminal client for the durable ai-stack agent.",
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
        "--write", action="store_true", help="Use a reviewable write sandbox"
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
        "--no-review",
        action="store_true",
        help="Leave pending changes for later instead of prompting",
    )
    run.add_argument(
        "--write",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Use a reviewable write sandbox",
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
        "projects",
        "resume",
        "run",
        "show",
        "workspaces",
    }
    if argv and not argv[0].startswith("-") and argv[0] not in commands:
        argv = ["run", *argv]
    args = build_parser().parse_args(argv)
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
            run = follow_run(client, str(run["id"]), EventRenderer())
            review_run(client, run, interactive=sys.stdin.isatty())
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
            print(f"API: {health.get('status', 'unknown')} ({args.url})")
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
            run = run_task(
                client,
                task,
                workspace=workspace,
                project_id=project_id,
                conversation_id=args.conversation or str(uuid.uuid4()),
                allow_write=args.write,
                output=args.output,
                review=not args.no_review,
            )
            return run_exit_code(run)
        return interactive_shell(
            client,
            workspace=workspace,
            project_id=project_id,
            allow_write=args.write,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
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
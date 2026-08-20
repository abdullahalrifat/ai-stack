"""Per-hunk review, selective application, and transactional undo."""

from __future__ import annotations

import re
from pathlib import Path
import subprocess
import uuid

from jarvis_core import ChangeTransaction, ReviewHunk

from .sandbox import remove_sandbox, sandbox_diff
from .store import get_run_store

_DIFF = re.compile(r"(?m)^diff --git ")
_HUNK = re.compile(r"(?m)^@@")


def parse_review_hunks(diff: str) -> list[ReviewHunk]:
    result = []
    sections = [item for item in _DIFF.split(diff) if item.strip()]
    for body in sections:
        section = "diff --git " + body
        first_hunk = _HUNK.search(section)
        if first_hunk is None:
            continue
        header = section[: first_hunk.start()]
        header_line = section.splitlines()[0]
        path = header_line.split(" b/", 1)[-1]
        positions = [match.start() for match in _HUNK.finditer(section)]
        positions.append(len(section))
        for index in range(len(positions) - 1):
            patch = header + section[positions[index] : positions[index + 1]]
            result.append(ReviewHunk(path, patch))
    return result


def review_run(run_id: str) -> ChangeTransaction:
    store = get_run_store()
    run = store.get_run(run_id)
    if run is None or not run.get("sandbox_path"):
        raise ValueError("Run has no reviewable sandbox")
    return ChangeTransaction(
        str(run["base_commit"]),
        parse_review_hunks(sandbox_diff(str(run["sandbox_path"]))),
    )


def _git(repository: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        shell=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git operation failed")
    return result.stdout.strip()


def apply_hunks(run_id: str, hunk_ids: set[str]) -> dict[str, str]:
    store = get_run_store()
    run = store.get_run(run_id)
    transaction = review_run(run_id)
    transaction.approve(hunk_ids)
    patch = transaction.approved_patch
    if not patch:
        raise ValueError("No pending hunks matched the supplied IDs")
    repository = Path(run["repository_path"]).resolve()
    if _git(repository, "rev-parse", "HEAD") != transaction.base_revision:
        raise RuntimeError("Repository HEAD changed since the run began")
    _git(repository, "apply", "--index", "-", input_text=patch)
    applied_revision = _git(repository, "write-tree")
    transaction.mark_applied(applied_revision)
    transaction_id = str(uuid.uuid4())
    store.record_change_transaction(
        transaction_id,
        run_id,
        transaction.base_revision,
        applied_revision,
        patch,
    )
    remove_sandbox(str(repository), str(run["sandbox_path"]))
    store.update_run(run_id, status="completed", sandbox_path=None)
    store.append_event(
        run_id,
        "hunks_applied",
        {"transaction_id": transaction_id, "hunk_ids": sorted(hunk_ids)},
    )
    return {"run_id": run_id, "status": "completed", "transaction_id": transaction_id}


def undo_transaction(transaction_id: str) -> dict[str, str]:
    store = get_run_store()
    transaction = store.get_change_transaction(transaction_id)
    if transaction is None:
        raise ValueError("Change transaction not found")
    if transaction["status"] != "applied":
        raise ValueError("Change transaction is not applied")
    run = store.get_run(str(transaction["run_id"]))
    repository = Path(run["repository_path"]).resolve()
    _git(repository, "apply", "--reverse", "--index", "-", input_text=transaction["approved_patch"])
    store.revert_change_transaction(transaction_id)
    store.append_event(str(transaction["run_id"]), "transaction_reverted", {"transaction_id": transaction_id})
    return {"transaction_id": transaction_id, "status": "reverted"}

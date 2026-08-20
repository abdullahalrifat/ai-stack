"""Hierarchical trusted instruction loading for mounted workspaces."""

from __future__ import annotations

import os
from pathlib import Path

from jarvis_core import Instruction, InstructionLevel, resolve_instructions


def load_server_instructions(workspace: str | Path, target: str | Path | None = None) -> list[Instruction]:
    root = Path(workspace).resolve()
    destination = Path(target or root).resolve()
    items: list[Instruction] = []
    configured = os.getenv("JARVIS_USER_INSTRUCTIONS")
    if configured:
        user = Path(configured).expanduser()
        if user.is_file():
            items.append(Instruction(user.read_text(), InstructionLevel.USER, str(user)))
    workspace_file = root / "AGENTS.md"
    if workspace_file.is_file():
        items.append(
            Instruction(
                workspace_file.read_text(),
                InstructionLevel.WORKSPACE,
                str(workspace_file),
                str(root),
            )
        )
    try:
        relative = destination.relative_to(root)
    except ValueError:
        relative = Path()
    current = root
    for part in relative.parts:
        current /= part
        candidate = current / "AGENTS.md"
        if candidate.is_file() and candidate != workspace_file:
            items.append(
                Instruction(
                    candidate.read_text(),
                    InstructionLevel.DIRECTORY,
                    str(candidate),
                    str(current),
                )
            )
    return resolve_instructions(items, destination)


def instruction_prompt(workspace: str | Path) -> str:
    instructions = load_server_instructions(workspace)
    if not instructions:
        return ""
    sections = [
        f"[{item.level.name.lower()} instruction: {item.source}]\n{item.content}"
        for item in instructions
    ]
    return (
        "\n\nTrusted instruction precedence (later, more specific levels override "
        "earlier levels):\n" + "\n\n".join(sections)
    )

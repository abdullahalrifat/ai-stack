"""Explicit bounded execution-graph transitions for agent runs."""

from __future__ import annotations

from typing import Callable

TERMINAL_PHASES = {"complete", "partial", "rejected"}

ALLOWED_TRANSITIONS = {
    "pending": {"analyzing"},
    "analyzing": {"ready", "partial", "rejected"},
    "ready": {
        "implementing",
        "verifying",
        "reviewing",
        "complete",
        "partial",
        "rejected",
    },
    "implementing": {"repairing", "verifying", "reviewing", "partial", "rejected"},
    "repairing": {"implementing", "verifying", "partial", "rejected"},
    "verifying": {
        "repairing",
        "implementing",
        "reviewing",
        "complete",
        "partial",
        "rejected",
    },
    "reviewing": {"repairing", "complete", "partial", "rejected"},
    "complete": set(),
    "partial": set(),
    "rejected": set(),
}


def transition_graph(
    state,
    phase: str,
    *,
    reason: str,
    on_event: Callable[[str, dict], None] | None = None,
    force: bool = False,
) -> bool:
    """Move the run to a valid phase and emit a durable transition event."""

    current = str(getattr(state, "graph_phase", "pending") or "pending")
    if current == phase:
        return False
    if not force and phase not in ALLOWED_TRANSITIONS.get(current, set()):
        return False
    state.graph_phase = phase
    history = list(getattr(state, "graph_history", []) or [])
    transition = {"from": current, "to": phase, "reason": reason}
    history.append(transition)
    state.graph_history = history[-40:]
    if on_event is not None:
        on_event("graph_transition", transition)
    return True

"""Context-local cooperative cancellation for one agent run."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .exceptions import RunCancelled

CancelCheck = Callable[[], bool]

_cancel_check: ContextVar[CancelCheck | None] = ContextVar(
    "agent_cancel_check",
    default=None,
)


@contextmanager
def cancellation_context(check: CancelCheck | None) -> Iterator[None]:
    """Make a run's cancellation callback available to nested I/O helpers."""

    token = _cancel_check.set(check)
    try:
        yield
    finally:
        _cancel_check.reset(token)


def has_cancellation_context() -> bool:
    return _cancel_check.get() is not None


def cancellation_requested() -> bool:
    check = _cancel_check.get()
    return bool(check and check())


def raise_if_cancelled() -> None:
    if cancellation_requested():
        raise RunCancelled()

class RunCancelled(Exception):
    """Raised between agent operations after a user requested cancellation."""


class ProcessKillFailed(RuntimeError):
    """Raised when a cancelled process group survives TERM and KILL."""

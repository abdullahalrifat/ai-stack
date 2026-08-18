"""Scoped permissions for agent tool execution.

The executor derives a :class:`PermissionPolicy` from the request and installs
it for the duration of every tool call. Write tools and the command runner
enforce the active policy as a second gate after path validation, so a policy
is never bypassed by calling a tool outside the executor.

The ContextVar default is a full-write policy (minus always-denied sensitive
files) so direct tool callers and unit tests keep working, while every
production run is explicitly scoped through ``permissions_context``.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

# Ask/allow scopes. "read" grants no mutation tools; "scoped-write" restricts
# writes to a configured set of workspace-relative directories; "full-write"
# allows any non-sensitive path inside the workspace.
READ = "read"
SCOPED_WRITE = "scoped-write"
FULL_WRITE = "full-write"

VALID_SCOPES = {READ, SCOPED_WRITE, FULL_WRITE}

# Paths that even a full-write scope must never modify. Mirrors the read-side
# sensitive-file guard in app.tools.filesystem, kept here so write tools can
# enforce it without importing filesystem internals.
DENY_WRITE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".env.staging",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
}
SAFE_ENV_FILE_NAMES = {".env.example", ".env.sample", ".env.template"}
DENY_WRITE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}


def sensitive_write_path(path: Path) -> bool:
    """Return True when a write to *path* should always be refused."""

    name = path.name.lower()
    is_env_secret = name in DENY_WRITE_NAMES or (
        name.startswith(".env.") and name not in SAFE_ENV_FILE_NAMES
    )
    return bool(is_env_secret or path.suffix.lower() in DENY_WRITE_SUFFIXES)


@dataclass(frozen=True)
class PermissionPolicy:
    scope: str = READ
    # Workspace-relative directories a scoped-write request may modify.
    # Empty means no scoped directories are configured.
    edit_roots: tuple = ()
    # Extra restriction on top of the platform ALLOWED_COMMANDS. Empty means
    # no additional restriction.
    command_allowlist: frozenset = frozenset()

    def allows_write(self) -> bool:
        return self.scope in {SCOPED_WRITE, FULL_WRITE}

    def check_write(self, path: Path, workspace: Path) -> None:
        """Raise PermissionError unless this scope may modify *path*."""

        if not self.allows_write():
            raise PermissionError(
                "Write access is not permitted for this request."
            )
        if sensitive_write_path(path):
            raise PermissionError(
                f"Writing sensitive path is not permitted: {path}"
            )
        if self.scope == FULL_WRITE:
            return
        try:
            relative = path.relative_to(workspace)
        except ValueError:
            raise PermissionError(
                "Write path is outside the active workspace."
            )
        if not self.edit_roots or not any(
            relative.is_relative_to(root) for root in self.edit_roots
        ):
            raise PermissionError(
                "Write path is outside the request's allowed edit scope."
            )

    def check_command(self, executable: str) -> None:
        """Raise PermissionError when *executable* is outside this scope."""

        if self.command_allowlist and executable not in self.command_allowlist:
            raise PermissionError(
                f"'{executable}' is outside the request's command allowlist."
            )


_default_policy = PermissionPolicy(scope=FULL_WRITE)

_permission_scope: ContextVar[PermissionPolicy] = ContextVar(
    "permission_scope", default=_default_policy
)


def active_policy() -> PermissionPolicy:
    return _permission_scope.get()


@contextmanager
def permissions_context(policy: PermissionPolicy):
    token = _permission_scope.set(policy)
    try:
        yield
    finally:
        _permission_scope.reset(token)


def policy_for(
    allow_write: bool,
    edit_paths=(),
    command_allowlist=(),
) -> PermissionPolicy:
    """Build the policy for a request from its write flag and config."""

    if not allow_write:
        return PermissionPolicy(scope=READ)
    allowlist = frozenset(command_allowlist)
    if edit_paths:
        return PermissionPolicy(
            scope=SCOPED_WRITE,
            edit_roots=tuple(Path(path) for path in edit_paths),
            command_allowlist=allowlist,
        )
    return PermissionPolicy(scope=FULL_WRITE, command_allowlist=allowlist)

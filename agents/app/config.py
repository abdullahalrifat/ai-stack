"""Runtime configuration for the agent service.

All settings are environment driven so the same image can be used locally and
in a more restricted deployment.  Secrets must never have application defaults.
"""

import os
from pathlib import Path


def env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "coder")
AGENT_MODEL_ID = os.getenv("AGENT_MODEL_ID", "coding-agent")
WEB_SEARCH_ENABLED = env_flag("WEB_SEARCH_ENABLED", True)
WEB_SEARCH_URL = os.getenv("WEB_SEARCH_URL", "http://searxng:8080/search")
WEB_SEARCH_TIMEOUT_SECONDS = int(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "15"))
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_DIR", "/workspace")).resolve()
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "12"))
MAX_TOOL_OUTPUT_CHARS = int(os.getenv("MAX_TOOL_OUTPUT_CHARS", "30000"))
COMMAND_TIMEOUT_SECONDS = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "120"))
POSTGRES_URL = os.getenv("POSTGRES_URL")
SANDBOX_ROOT = Path(os.getenv("SANDBOX_ROOT", "/tmp/agent-sandboxes")).resolve()

# Authentication is mandatory unless a developer explicitly opts into an
# insecure, local-only mode.  This avoids accidentally publishing an agent
# with filesystem write capabilities without authentication.
AGENT_API_KEY = os.getenv("AGENT_API_KEY")
ALLOW_INSECURE_NO_AUTH = env_flag("ALLOW_INSECURE_NO_AUTH")


def validate_settings() -> None:
    if not AGENT_API_KEY and not ALLOW_INSECURE_NO_AUTH:
        raise RuntimeError(
            "AGENT_API_KEY is required. Set ALLOW_INSECURE_NO_AUTH=true only "
            "for an isolated local development environment."
        )
    if not WORKSPACE_ROOT.exists():
        raise RuntimeError(f"WORKSPACE_DIR does not exist: {WORKSPACE_ROOT}")

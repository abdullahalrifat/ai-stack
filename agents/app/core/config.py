"""Runtime configuration for the agent service.

All settings are environment driven so the same image can be used locally and
in a more restricted deployment. Secrets must never have application defaults.
"""

import os
from pathlib import Path


def env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen3-8b")
AGENT_MODEL_ID = os.getenv("AGENT_MODEL_ID", "coding-agent")
FAST_MODEL = os.getenv("FAST_MODEL", "quick")
# Used by the Open WebUI coding-agent for current web/financial research when
# no per-run model was explicitly selected in the Runs UI.
RESEARCH_MODEL = os.getenv("RESEARCH_MODEL", FAST_MODEL)

WEB_SEARCH_ENABLED = env_flag("WEB_SEARCH_ENABLED", True)
WEB_SEARCH_URL = os.getenv("WEB_SEARCH_URL", "http://searxng:8080/search")
WEB_SEARCH_TIMEOUT_SECONDS = int(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "15"))

# Optional Automatic1111/Forge-compatible image generation API. Ollama vision
# models analyze images but do not create them, so generation stays separate.
IMAGE_GENERATION_URL = os.getenv("IMAGE_GENERATION_URL", "").rstrip("/")
IMAGE_GENERATION_TIMEOUT_SECONDS = int(os.getenv("IMAGE_GENERATION_TIMEOUT_SECONDS", "180"))

# Multiple workspace roots can be mounted (e.g. several repositories).
# WORKSPACE_DIR is kept for backward compatibility and is always included as
# the first allowed root. Add more with a comma-separated WORKSPACE_ROOTS.
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_DIR", "/workspace")).resolve()
_extra_roots = [Path(p).resolve() for p in env_list("WORKSPACE_ROOTS")]
WORKSPACE_ROOTS: list[Path] = [WORKSPACE_ROOT] + [
    p for p in _extra_roots if p != WORKSPACE_ROOT
]

MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "12"))
# A local 8B model has a finite context window.  Keep individual tool payloads
# compact so the model sees the task and evidence rather than a truncated tail.
MAX_TOOL_OUTPUT_CHARS = int(os.getenv("MAX_TOOL_OUTPUT_CHARS", "8000"))
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "90"))
LLM_MAX_COMPLETION_TOKENS = int(os.getenv("LLM_MAX_COMPLETION_TOKENS", "384"))
COMMAND_TIMEOUT_SECONDS = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "120"))
POSTGRES_URL = os.getenv("POSTGRES_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
SANDBOX_ROOT = Path(os.getenv("SANDBOX_ROOT", "/tmp/agent-sandboxes")).resolve()
RUNNER_CPU_SECONDS = int(os.getenv("RUNNER_CPU_SECONDS", "90"))
RUNNER_MEMORY_MB = int(os.getenv("RUNNER_MEMORY_MB", "2048"))
RUNNER_MAX_OPEN_FILES = int(os.getenv("RUNNER_MAX_OPEN_FILES", "256"))
RUN_EVENT_BATCH_CHARS = int(os.getenv("RUN_EVENT_BATCH_CHARS", "256"))
RUN_EVENT_BATCH_SECONDS = float(os.getenv("RUN_EVENT_BATCH_SECONDS", "0.10"))

# Commands the run_command tool may execute. Only the first whitespace
# token of a requested command is checked against this list; shell
# chaining/redirection syntax is rejected outright regardless of allowlist.
ALLOWED_COMMANDS = env_list(
    "ALLOWED_COMMANDS",
    "git,ls,cat,pytest,python,python3,npm,node,make,grep,find,"
    "mypy,ruff,black,flake8",
)

# How long the cached model list from the inference gateway is trusted
# before being refreshed, to avoid an extra HTTP round trip on every call.
MODEL_LIST_CACHE_SECONDS = int(os.getenv("MODEL_LIST_CACHE_SECONDS", "300"))

# Executor context management: after this many tool-call steps, older
# transcript entries are summarized down to keep context bounded.
CONTEXT_COMPACT_EVERY_STEPS = int(os.getenv("CONTEXT_COMPACT_EVERY_STEPS", "6"))
CONTEXT_COMPACT_KEEP_RECENT = int(os.getenv("CONTEXT_COMPACT_KEEP_RECENT", "4"))

# Authentication is mandatory unless a developer explicitly opts into an
# insecure, local-only mode. This avoids accidentally publishing an agent
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
    for root in WORKSPACE_ROOTS:
        if not root.exists():
            raise RuntimeError(f"Configured workspace root does not exist: {root}")

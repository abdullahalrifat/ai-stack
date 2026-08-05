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
AGENT_MODEL_ID = os.getenv("AGENT_MODEL_ID", "orchestrator")
FAST_MODEL = os.getenv("FAST_MODEL", "quick")
# Small, low-latency model used only to translate an Auto request into a
# validated workflow contract. It does not answer the user's request.
ROUTER_MODEL = os.getenv("ROUTER_MODEL", FAST_MODEL)
ROUTER_ESCALATION_MODEL = os.getenv("ROUTER_ESCALATION_MODEL", DEFAULT_MODEL)
ROUTER_MAX_COMPLETION_TOKENS = int(os.getenv("ROUTER_MAX_COMPLETION_TOKENS", "1024"))
ROUTER_TIMEOUT_SECONDS = int(os.getenv("ROUTER_TIMEOUT_SECONDS", "120"))
ROUTER_ESCALATION_TIMEOUT_SECONDS = int(
    os.getenv("ROUTER_ESCALATION_TIMEOUT_SECONDS", "240")
)
FINANCE_MODEL = os.getenv("FINANCE_MODEL", DEFAULT_MODEL)
# Used by the Open WebUI orchestrator for current web/financial research when
# no per-run model was explicitly selected in the Runs UI.
RESEARCH_MODEL = os.getenv("RESEARCH_MODEL", FAST_MODEL)

WEB_SEARCH_ENABLED = env_flag("WEB_SEARCH_ENABLED", True)
WEB_SEARCH_URL = os.getenv("WEB_SEARCH_URL", "http://searxng:8080/search")
WEB_SEARCH_TIMEOUT_SECONDS = int(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "15"))
# Maximum size of an externally retrieved HTML/PDF document. This is separate
# from MAX_TOOL_OUTPUT_CHARS, which bounds only the text given to the model.
WEB_FETCH_MAX_BYTES = int(os.getenv("WEB_FETCH_MAX_BYTES", "8000000"))

# Optional Automatic1111/Forge-compatible image generation API. Ollama vision
# models analyze images but do not create them, so generation stays separate.
IMAGE_GENERATION_URL = os.getenv("IMAGE_GENERATION_URL", "").rstrip("/")
IMAGE_GENERATION_TIMEOUT_SECONDS = int(
    os.getenv("IMAGE_GENERATION_TIMEOUT_SECONDS", "180")
)

# Multiple workspace roots can be mounted (e.g. several repositories).
# WORKSPACE_DIR is kept for backward compatibility and is always included as
# the first allowed root. Add more with a comma-separated WORKSPACE_ROOTS.
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_DIR", "/workspace")).resolve()
_extra_roots = [Path(p).resolve() for p in env_list("WORKSPACE_ROOTS")]
WORKSPACE_ROOTS: list[Path] = [WORKSPACE_ROOT] + [
    p for p in _extra_roots if p != WORKSPACE_ROOT
]
# The mounted workspace can contain several repositories. Keep the broad
# mount as an allowed root, while making one repository the safe default for
# requests which do not explicitly select a workspace.
DEFAULT_WORKSPACE = Path(
    os.getenv("DEFAULT_WORKSPACE_DIR", str(WORKSPACE_ROOT))
).resolve()

MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "40"))
# Independent read-only tool calls in one model turn run concurrently instead
# of sequentially. Write tools are never parallelized.
MAX_PARALLEL_TOOL_CALLS = int(os.getenv("MAX_PARALLEL_TOOL_CALLS", "4"))
# Stop a weak tool-calling model from spending the entire run repeatedly
# returning an empty assistant turn. The executor synthesizes its collected
# evidence once this threshold is reached.
MAX_EMPTY_MODEL_TURNS = int(os.getenv("MAX_EMPTY_MODEL_TURNS", "3"))
# A sequence of empty repository content searches is an agent-planning loop,
# not useful new evidence. Synthesize from earlier findings instead.
MAX_EMPTY_SEARCH_RESULTS = int(os.getenv("MAX_EMPTY_SEARCH_RESULTS", "3"))
MAX_UNPRODUCTIVE_TOOL_CALLS = int(os.getenv("MAX_UNPRODUCTIVE_TOOL_CALLS", "3"))
# A local 8B model has a finite context window.  Keep individual tool payloads
# compact so the model sees the task and evidence rather than a truncated tail.
MAX_TOOL_OUTPUT_CHARS = int(os.getenv("MAX_TOOL_OUTPUT_CHARS", "8000"))
# Model-visible budget for a single tool result once structured summarization
# kicks in. Smaller than MAX_TOOL_OUTPUT_CHARS because it is the token budget
# for the transcript, not the raw tool payload. The full result is always
# retained in the run's observations.
TOOL_RESULT_SUMMARY_CHARS = int(os.getenv("TOOL_RESULT_SUMMARY_CHARS", "3000"))
# Head/tail items preserved when a list-shaped tool result exceeds the summary
# budget (search matches, directory listings, file items).
TOOL_RESULT_SUMMARY_ITEMS = int(os.getenv("TOOL_RESULT_SUMMARY_ITEMS", "3"))
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "180"))
LLM_MAX_COMPLETION_TOKENS = int(os.getenv("LLM_MAX_COMPLETION_TOKENS", "768"))
# Evidence-rich cross-domain comparisons use a stronger model for the one
# synthesis turn only. If it is unavailable, the executor falls back to the
# active workflow model without losing the collected evidence.
ANALYSIS_SYNTHESIS_MODEL = os.getenv("ANALYSIS_SYNTHESIS_MODEL", "qwen3-14b")
ANALYSIS_SYNTHESIS_MAX_TOKENS = int(os.getenv("ANALYSIS_SYNTHESIS_MAX_TOKENS", "1200"))
ANALYSIS_SYNTHESIS_TIMEOUT_SECONDS = int(
    os.getenv("ANALYSIS_SYNTHESIS_TIMEOUT_SECONDS", "360")
)
# Planning is advisory; a small bounded response avoids wasting the local
# context window on a plan the executor does not need to execute literally.
PLANNER_MAX_COMPLETION_TOKENS = int(os.getenv("PLANNER_MAX_COMPLETION_TOKENS", "192"))
REPLAN_MAX_COMPLETION_TOKENS = int(os.getenv("REPLAN_MAX_COMPLETION_TOKENS", "256"))
# Re-planning policy: revise the plan when this many consecutive tool-failing
# steps occur, or when this many steps produce no new useful evidence.
REPLAN_FAIL_STREAK = int(os.getenv("REPLAN_FAIL_STREAK", "3"))
REPLAN_STUCK_STEPS = int(os.getenv("REPLAN_STUCK_STEPS", "6"))
# Hard cap so a pathological loop cannot re-plan forever; after this many
# revisions the executor falls back to its normal recovery behavior.
REPLAN_MAX_RETRIES = int(os.getenv("REPLAN_MAX_RETRIES", "2"))
# OpenAI-compatible clients often attach long histories, IDE excerpts, and
# tool instructions. This bounds only their *incoming* text before the agent
# adds its own prompt and tool schemas for an 8K local model context.
OPENAI_INPUT_MAX_CHARS = int(os.getenv("OPENAI_INPUT_MAX_CHARS", "3500"))
CONTEXT_TOKEN_LIMIT = int(os.getenv("CONTEXT_TOKEN_LIMIT", "8192"))
CONTEXT_OUTPUT_RESERVE_TOKENS = int(os.getenv("CONTEXT_OUTPUT_RESERVE_TOKENS", "768"))
# Finance answers need room for a compact evidence summary plus scenarios.
# Kept separate so normal Code/Quick responses remain fast on CPU.
FINANCE_MAX_COMPLETION_TOKENS = int(os.getenv("FINANCE_MAX_COMPLETION_TOKENS", "1024"))
FINANCE_LLM_TIMEOUT_SECONDS = int(os.getenv("FINANCE_LLM_TIMEOUT_SECONDS", "300"))
COMMAND_TIMEOUT_SECONDS = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "120"))
POSTGRES_URL = os.getenv("POSTGRES_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
SANDBOX_ROOT = Path(os.getenv("SANDBOX_ROOT", "/tmp/agent-sandboxes")).resolve()
RUNNER_CPU_SECONDS = int(os.getenv("RUNNER_CPU_SECONDS", "90"))
RUNNER_MEMORY_MB = int(os.getenv("RUNNER_MEMORY_MB", "2048"))
RUNNER_MAX_OPEN_FILES = int(os.getenv("RUNNER_MAX_OPEN_FILES", "256"))
RUN_EVENT_BATCH_CHARS = int(os.getenv("RUN_EVENT_BATCH_CHARS", "2048"))
RUN_EVENT_BATCH_SECONDS = float(os.getenv("RUN_EVENT_BATCH_SECONDS", "0.50"))
MAX_CONCURRENT_AGENT_RUNS = int(os.getenv("MAX_CONCURRENT_AGENT_RUNS", "2"))
MAX_CONCURRENT_LLM_CALLS = int(os.getenv("MAX_CONCURRENT_LLM_CALLS", "1"))
RUN_WORKER_LEASE_SECONDS = int(os.getenv("RUN_WORKER_LEASE_SECONDS", "60"))
RUNNER_URL = os.getenv("RUNNER_URL", "http://agent-runner:8001").rstrip("/")
RUNNER_API_KEY = os.getenv("RUNNER_API_KEY")

# Commands the run_command tool may execute. Only the first whitespace
# token of a requested command is checked against this list; shell
# chaining/redirection syntax is rejected outright regardless of allowlist.
ALLOWED_COMMANDS = env_list(
    "ALLOWED_COMMANDS",
    "git,ls,cat,pytest,python,python3,npm,node,make,grep,find,mypy,ruff,black,flake8",
)

# Tier 2 scoped permissions. EDIT_ALLOWED_PATHS narrows write tools to a
# comma-separated list of workspace-relative directories when a request has
# allow_write set (scope becomes "scoped-write"); empty keeps the legacy
# full-write scope. COMMAND_ALLOWLIST optionally narrows ALLOWED_COMMANDS
# further for every run that carries write permission.
EDIT_ALLOWED_PATHS = env_list("EDIT_ALLOWED_PATHS")
COMMAND_ALLOWLIST = env_list("COMMAND_ALLOWLIST")

# Commands may need outbound network for some workflows (for example npm
# install). Off by default: the compose "runner" network is internal, and the
# runner blocks egress for the "isolated" tier as defense in depth.
RUN_COMMANDS_ALLOW_NETWORK = env_flag("RUN_COMMANDS_ALLOW_NETWORK", False)

# How long the cached model list from the inference gateway is trusted
# before being refreshed, to avoid an extra HTTP round trip on every call.
MODEL_LIST_CACHE_SECONDS = int(os.getenv("MODEL_LIST_CACHE_SECONDS", "300"))

# Executor context management: after this many tool-call steps, older
# transcript entries are summarized down to keep context bounded.
CONTEXT_COMPACT_EVERY_STEPS = int(os.getenv("CONTEXT_COMPACT_EVERY_STEPS", "6"))
CONTEXT_COMPACT_KEEP_RECENT = int(os.getenv("CONTEXT_COMPACT_KEEP_RECENT", "4"))
# Compact only when the complete executor transcript is genuinely near the
# model context budget. The old step-count setting is retained for backwards
# compatible configuration but is no longer the trigger.
CONTEXT_COMPACT_THRESHOLD_TOKENS = int(
    os.getenv("CONTEXT_COMPACT_THRESHOLD_TOKENS", "7000")
)

# Embeddings are useful for explicit RAG workflows but expensive on a host
# that keeps one Ollama model resident. Keep routine Code/Quick turns fast.
MEMORY_ENABLED = env_flag("MEMORY_ENABLED", True)
MEMORY_FOR_CODE_RUNS = env_flag("MEMORY_FOR_CODE_RUNS", False)
# Model-generated answers are not trusted source material by default. Persist
# them only when an operator explicitly accepts the feedback-loop risk.
GENERATED_MEMORY_ENABLED = env_flag("GENERATED_MEMORY_ENABLED", False)
MEMORY_CONTEXT_TOKENS = int(os.getenv("MEMORY_CONTEXT_TOKENS", "1200"))
DOCUMENT_MAX_BYTES = int(os.getenv("DOCUMENT_MAX_BYTES", "10000000"))

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
    if not DEFAULT_WORKSPACE.exists() or not DEFAULT_WORKSPACE.is_dir():
        raise RuntimeError(
            f"DEFAULT_WORKSPACE_DIR does not exist or is not a directory: {DEFAULT_WORKSPACE}"
        )
    if not any(
        DEFAULT_WORKSPACE == root or root in DEFAULT_WORKSPACE.parents
        for root in WORKSPACE_ROOTS
    ):
        raise RuntimeError(
            "DEFAULT_WORKSPACE_DIR must be inside WORKSPACE_DIR or WORKSPACE_ROOTS: "
            f"{DEFAULT_WORKSPACE}"
        )
    if WEB_FETCH_MAX_BYTES <= 0:
        raise RuntimeError("WEB_FETCH_MAX_BYTES must be greater than zero")
    if OPENAI_INPUT_MAX_CHARS < 1000:
        raise RuntimeError("OPENAI_INPUT_MAX_CHARS must be at least 1000")
    if (
        CONTEXT_TOKEN_LIMIT < 1024
        or CONTEXT_OUTPUT_RESERVE_TOKENS >= CONTEXT_TOKEN_LIMIT
    ):
        raise RuntimeError("Invalid model context token budget")
    if MAX_CONCURRENT_AGENT_RUNS < 1:
        raise RuntimeError("MAX_CONCURRENT_AGENT_RUNS must be at least 1")
    if MAX_PARALLEL_TOOL_CALLS < 1:
        raise RuntimeError("MAX_PARALLEL_TOOL_CALLS must be at least 1")
    if MAX_CONCURRENT_LLM_CALLS < 1:
        raise RuntimeError("MAX_CONCURRENT_LLM_CALLS must be at least 1")
    if RUN_EVENT_BATCH_CHARS < 1 or RUN_EVENT_BATCH_SECONDS <= 0:
        raise RuntimeError("Run event batch settings must be greater than zero")
    if MAX_EMPTY_MODEL_TURNS < 1:
        raise RuntimeError("MAX_EMPTY_MODEL_TURNS must be at least 1")
    if MAX_EMPTY_SEARCH_RESULTS < 1:
        raise RuntimeError("MAX_EMPTY_SEARCH_RESULTS must be at least 1")
    if MAX_UNPRODUCTIVE_TOOL_CALLS < 1:
        raise RuntimeError("MAX_UNPRODUCTIVE_TOOL_CALLS must be at least 1")
    if TOOL_RESULT_SUMMARY_CHARS < 256:
        raise RuntimeError("TOOL_RESULT_SUMMARY_CHARS must be at least 256")
    if TOOL_RESULT_SUMMARY_ITEMS < 1:
        raise RuntimeError("TOOL_RESULT_SUMMARY_ITEMS must be at least 1")
    if REPLAN_FAIL_STREAK < 1 or REPLAN_STUCK_STEPS < 1:
        raise RuntimeError("Re-planning thresholds must be at least 1")
    if REPLAN_MAX_RETRIES < 0:
        raise RuntimeError("REPLAN_MAX_RETRIES cannot be negative")
    if MEMORY_CONTEXT_TOKENS < 128 or DOCUMENT_MAX_BYTES < 1:
        raise RuntimeError("Invalid document retrieval settings")
    if not RUNNER_API_KEY:
        raise RuntimeError("RUNNER_API_KEY is required for isolated command execution")
    for root in EDIT_ALLOWED_PATHS:
        if Path(root).is_absolute():
            raise RuntimeError(
                "EDIT_ALLOWED_PATHS entries must be workspace-relative paths"
            )

"""
Central tool registration.

All tools available to the executor must be registered here.

The executor only interacts with:
    tool_registry.registry

Tool schemas for native function calling live in app/tool_schemas.py and
must be kept in sync with the tool names registered below.
"""

from app.tools.code_intelligence import analyze_task_context, inspect_code
from app.tools.filesystem import (
    apply_patch,
    edit_file,
    find_file,
    inspect_files,
    inspect_test_environment,
    list_files,
    project_summary,
    read_file,
    run_command,
    run_tests,
    search_code,
    search_text,
    tree,
    workspace_root,
    write_file,
)
from app.tools.git import git_blame, git_diff, git_log, git_status
from app.tools.registry import registry
from app.tools.web_fetch import web_fetch
from app.tools.web_search import web_search

# =====================================================
# Filesystem Tools
# =====================================================

registry.register(name="workspace_root", function=workspace_root)

registry.register(name="tree", function=tree)

registry.register(name="list_files", function=list_files)

registry.register(name="read_file", function=read_file)

registry.register(name="find_file", function=find_file)

registry.register(name="search_text", function=search_text)

registry.register(name="search_code", function=search_code)

registry.register(name="project_summary", function=project_summary)

registry.register(name="inspect_test_environment", function=inspect_test_environment)

registry.register(name="inspect_files", function=inspect_files)

registry.register(name="analyze_task_context", function=analyze_task_context)

registry.register(name="inspect_code", function=inspect_code)

registry.register(name="write_file", function=write_file)

registry.register(name="edit_file", function=edit_file)

registry.register(name="apply_patch", function=apply_patch)

registry.register(name="run_command", function=run_command)

registry.register(name="run_tests", function=run_tests)

# =====================================================
# Read-only Git Tools
# =====================================================

registry.register(name="git_status", function=git_status)

registry.register(name="git_diff", function=git_diff)

registry.register(name="git_log", function=git_log)

registry.register(name="git_blame", function=git_blame)

registry.register(name="web_search", function=web_search)

registry.register(name="web_fetch", function=web_fetch)

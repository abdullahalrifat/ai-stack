from agents.app.backup.tools.docker import (
    list_docker_containers,
    docker_logs,
    restart_container,
    docker_health,
)

from agents.app.backup.tools.filesystem import (
    workspace_root,
    tree,
    list_files,
    read_file,
    find_file,
    search_text,
    project_summary,
)


# Docker related tools
DOCKER_TOOLS = [
    list_docker_containers,
    docker_logs,
    restart_container,
    docker_health,
]


# Filesystem related tools
FILESYSTEM_TOOLS = [
    workspace_root,
    tree,
    list_files,
    read_file,
    find_file,
    search_text,
    project_summary
]


# All tools available to the agent
TOOLS = (
    DOCKER_TOOLS
    + FILESYSTEM_TOOLS
)
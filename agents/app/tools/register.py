from app.tools.docker import (
    list_docker_containers,
    docker_logs,
    restart_container,
    docker_health,
)

from app.tools.filesystem import (
    list_files,
    read_file,
    search_files,
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
    list_files,
    read_file,
    search_files,
]


# All tools available to the agent
TOOLS = (
    DOCKER_TOOLS
    + FILESYSTEM_TOOLS
)
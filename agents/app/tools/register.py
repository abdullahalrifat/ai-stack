"""
Central tool registration.

All tools available to the executor must be registered here.

The executor only interacts with:
    tool_registry.registry

"""

from app.tool_registry import registry


from app.tools.filesystem import (
    list_files,
    read_file,
    search_text,
    tree,
    workspace_root,
    find_file,
    project_summary,
    inspect_files
)


from app.tools.docker import (
    list_docker_containers,
    docker_logs,
    restart_container,
    docker_health,
)



# =====================================================
# Filesystem Tools
# =====================================================

registry.register(
    name="workspace_root",
    function=workspace_root
)


registry.register(
    name="tree",
    function=tree
)


registry.register(
    name="list_files",
    function=list_files
)


registry.register(
    name="read_file",
    function=read_file
)


registry.register(
    name="find_file",
    function=find_file
)


registry.register(
    name="search_text",
    function=search_text
)


registry.register(
    name="project_summary",
    function=project_summary
)


registry.register(
    name="inspect_files",
    function=inspect_files
)
# =====================================================
# Docker Tools
# =====================================================

registry.register(
    name="list_docker_containers",
    function=list_docker_containers
)


registry.register(
    name="docker_logs",
    function=docker_logs
)


registry.register(
    name="restart_container",
    function=restart_container
)


registry.register(
    name="docker_health",
    function=docker_health
)



# =====================================================
# LangChain compatibility
# =====================================================
#
# Used only if agent.py uses create_agent()
#

TOOLS = [

    workspace_root,

    tree,

    list_files,

    read_file,

    find_file,

    search_text,

    project_summary,

    list_docker_containers,

    docker_logs,

    restart_container,

    docker_health,

]
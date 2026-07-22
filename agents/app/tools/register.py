"""
Central tool registration.

All agent tools must be registered here.

The executor only knows about:
    tool_registry.registry

It does not import tools directly.
"""


from app.tool_registry import registry


from app.tools.filesystem import (
    list_files,
    read_file,
    search_files,
    workspace_tree
)


from app.tools.docker import (
    list_docker_containers,
    docker_logs,
    restart_container,
)



# =====================================================
# Filesystem Tools
# =====================================================


registry.register(

    name="list_files",

    function=list_files

)



registry.register(

    name="read_file",

    function=read_file

)



registry.register(

    name="search_files",

    function=search_files

)

registry.register(
    name="workspace_tree",
    function=workspace_tree
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



# =====================================================
# Export for compatibility
# =====================================================

TOOLS = [

    list_files,

    read_file,

    search_files,

    list_docker_containers,

    docker_logs,

    restart_container,

]
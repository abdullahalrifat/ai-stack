import docker
from langchain.tools import tool


client = docker.from_env()


@tool
def list_docker_containers():
    """
    List all docker containers and their current state.
    """

    containers = client.containers.list(
        all=True
    )

    return [
        {
            "name": c.name,
            "status": c.status
        }
        for c in containers
    ]


@tool
def docker_logs(container_name:str, lines:int=50):
    """
    Get recent logs from a docker container.
    """

    container = client.containers.get(
        container_name
    )

    logs = container.logs(
        tail=lines
    )

    return logs.decode()


@tool
def restart_container(container_name:str):
    """
    Restart an allowed docker container.
    """

    allowed = [
        "nextcloud",
        "redis",
        "cloudflared"
    ]

    if container_name not in allowed:
        return "Container not allowed"

    container = client.containers.get(
        container_name
    )

    container.restart()

    return f"{container_name} restarted"
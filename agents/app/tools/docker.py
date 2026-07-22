import docker
from langchain.tools import tool


_client = None

def get_docker_client():

    global _client

    if _client is None:
        _client = docker.from_env()

    return _client

@tool
def list_docker_containers():
    """
    List all Docker containers and their current state.
    """

    try:
        client = get_docker_client()

        containers = client.containers.list(
            all=True
        )

        return [
            {
                "name": c.name,
                "status": c.status,
                "image": c.image.tags,
            }
            for c in containers
        ]

    except Exception as e:
        return {
            "error": str(e)
        }



@tool
def docker_logs(
    container_name: str,
    lines: int = 50
):
    """
    Get recent logs from a Docker container.
    """

    try:
        client = get_docker_client()


        container = client.containers.get(
            container_name
        )

        logs = container.logs(
            tail=lines
        )

        return logs.decode(
            errors="ignore"
        )


    except docker.errors.NotFound:

        return {
            "error":
                f"Container {container_name} not found"
        }


    except Exception as e:

        return {
            "error": str(e)
        }



@tool
def restart_container(
    container_name: str
):
    """
    Restart an approved Docker container.
    """

    allowed = [
        "nextcloud",
        "redis",
        "cloudflared",
        "ollama",
        "qdrant",
    ]


    if container_name not in allowed:

        return {
            "error":
                "Container not allowed"
        }


    try:
        client = get_docker_client()

        container = client.containers.get(
            container_name
        )

        container.restart()

        return {
            "status":
                f"{container_name} restarted"
        }


    except Exception as e:

        return {
            "error": str(e)
        }



@tool
def docker_health():
    """
    Check Docker container health status.
    """

    try:
        client = get_docker_client()

        containers = client.containers.list(
            all=True
        )

        result=[]


        for c in containers:

            state = c.attrs.get(
                "State",
                {}
            )


            result.append(
                {
                    "name": c.name,
                    "status": c.status,
                    "health":
                        state.get("Health")
                }
            )


        return result


    except Exception as e:

        return {
            "error": str(e)
        }
import docker


client=docker.from_env()


def list_containers():

    containers=client.containers.list()

    return [
        c.name
        for c in containers
    ]
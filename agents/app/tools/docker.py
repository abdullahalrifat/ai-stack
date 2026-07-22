import subprocess



def run_command(
    cmd
):

    result=subprocess.run(

        cmd,

        capture_output=True,

        text=True

    )


    if result.returncode !=0:

        return result.stderr


    return result.stdout




def list_docker_containers():

    return run_command(

        [
            "docker",
            "ps",
            "--format",
            "{{.Names}} {{.Status}}"
        ]

    )





def docker_logs(
    container,
    lines=100
):

    return run_command(

        [
            "docker",
            "logs",
            "--tail",
            str(lines),
            container
        ]

    )





def restart_container(
    container
):

    return run_command(

        [
            "docker",
            "restart",
            container
        ]

    )
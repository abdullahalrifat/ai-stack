from pathlib import Path
from langchain.tools import tool


ALLOWED_PATHS = [
    Path("/data").resolve(),
    Path("/workspace").resolve(),
]



def is_allowed(path: Path):

    path = path.resolve()


    return any(
        allowed == path
        or allowed in path.parents
        for allowed in ALLOWED_PATHS
    )



@tool
def list_files(
    directory: str
):
    """
    List files from allowed directories.
    """

    path = Path(directory)


    if not is_allowed(path):

        return {
            "error":
                "Access denied"
        }


    if not path.exists():

        return {
            "error":
                "Path does not exist"
        }


    return {
        "files":
            [
                str(x)
                for x in path.iterdir()
            ]
    }



@tool
def read_file(
    file_path:str
):
    """
    Read a text file.
    """

    path = Path(file_path)


    if not is_allowed(path):

        return {
            "error":
                "Access denied"
        }


    if not path.exists():

        return {
            "error":
                "File not found"
        }


    try:

        return {
            "content":
                path.read_text(
                    encoding="utf-8"
                )[:10000]
        }


    except Exception as e:

        return {
            "error":
                str(e)
        }



@tool
def search_files(
    directory:str,
    keyword:str
):
    """
    Search text files for keyword.
    """

    path = Path(directory)


    if not is_allowed(path):

        return {
            "error":
                "Access denied"
        }


    results=[]


    for file in path.rglob("*"):

        if file.is_file():

            try:

                content=file.read_text(
                    errors="ignore"
                )


                if keyword.lower() in content.lower():

                    results.append(
                        str(file)
                    )


            except:

                pass



    return {
        "matches":
            results[:50]
    }
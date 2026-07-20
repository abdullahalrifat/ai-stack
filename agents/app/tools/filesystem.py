from pathlib import Path


# Only expose selected directories
ALLOWED_PATHS = [
    Path("/data"),
    Path("/workspace"),
]


def is_allowed(path: Path):
    """
    Prevent agent from accessing sensitive system files.
    """

    path = path.resolve()

    return any(
        str(path).startswith(str(p))
        for p in ALLOWED_PATHS
    )


def list_files(directory: str):
    """
    List files in allowed directory.
    """

    path = Path(directory)

    if not is_allowed(path):
        return {
            "error": "Access denied"
        }

    if not path.exists():
        return {
            "error": "Path does not exist"
        }

    return {
        "files": [
            str(x)
            for x in path.iterdir()
        ]
    }



def read_file(file_path: str):
    """
    Read text file.
    """

    path = Path(file_path)

    if not is_allowed(path):
        return {
            "error": "Access denied"
        }


    if not path.exists():
        return {
            "error": "File not found"
        }


    try:

        content = path.read_text(
            encoding="utf-8"
        )

        return {
            "content": content[:10000]
        }


    except Exception as e:

        return {
            "error": str(e)
        }



def search_files(
    directory: str,
    keyword: str
):

    path = Path(directory)

    if not is_allowed(path):
        return {
            "error":"Access denied"
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
        "matches":results[:50]
    }
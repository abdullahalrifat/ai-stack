import os


WORKSPACE="/workspace"



def normalize_path(path):

    if path.startswith("/workspace"):

        return path


    return os.path.join(
        WORKSPACE,
        path
    )




def list_files(
    directory="/workspace"
):

    directory=normalize_path(
        directory
    )


    result=[]


    for root, dirs, files in os.walk(
        directory
    ):

        for file in files:

            result.append(

                os.path.join(
                    root,
                    file
                )

            )


    return result[:200]





def read_file(
    file_path
):

    file_path=normalize_path(
        file_path
    )


    with open(
        file_path,
        "r",
        errors="ignore"
    ) as f:

        return f.read(
            20000
        )





def search_files(
    query,
    directory="/workspace"
):

    directory=normalize_path(
        directory
    )


    matches=[]


    for root,dirs,files in os.walk(
        directory
    ):

        for file in files:

            path=os.path.join(
                root,
                file
            )


            try:

                content=open(
                    path,
                    errors="ignore"
                ).read()


                if query.lower() in content.lower():

                    matches.append(
                        path
                    )

            except:

                pass



    return matches[:50]
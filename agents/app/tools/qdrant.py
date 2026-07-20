import os

from qdrant_client import QdrantClient



client = QdrantClient(
    url=os.getenv(
        "QDRANT_URL",
        "http://qdrant:6333"
    )
)



COLLECTION="memory"



def search_memory(
    vector:list,
    limit:int=5
):

    """
    Search vector database.
    """

    result = client.search(
        collection_name=COLLECTION,
        query_vector=vector,
        limit=limit
    )


    return [
        {
            "text":item.payload,
            "score":item.score
        }
        for item in result
    ]



def save_memory(
    vector:list,
    payload:dict,
    point_id:str
):

    """
    Store information.
    """

    client.upsert(

        collection_name=COLLECTION,

        points=[
            {
                "id":point_id,

                "vector":vector,

                "payload":payload
            }
        ]
    )


    return {
        "status":"saved"
    }
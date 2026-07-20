import requests


def create_embedding(text):

    response = requests.post(

        "http://ollama:11434/api/embeddings",

        json={
            "model":"nomic-embed-text",
            "prompt":text
        }
    )


    return response.json()["embedding"]
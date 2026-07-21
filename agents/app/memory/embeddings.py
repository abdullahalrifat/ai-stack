import os
import requests



OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://ollama:11434"
)


MODEL = os.getenv(
    "OLLAMA_EMBED_MODEL",
    "nomic-embed-text"
)



def create_embedding(
    text: str
):


    response = requests.post(

        f"{OLLAMA_URL}/api/embeddings",

        json={

            "model":
                MODEL,

            "prompt":
                text

        },

        timeout=30

    )


    response.raise_for_status()


    return response.json()["embedding"]
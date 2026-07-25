from openai import OpenAI
import os
import requests
from .config import DEFAULT_MODEL


_client = None

def get_client():
    global _client

    if _client is None:
        _client = OpenAI(
            base_url=os.getenv("OPENAI_API_BASE"),
            api_key=os.getenv("OPENAI_API_KEY"),
        )

    return _client


def get_available_models():

    
    base_url = os.getenv(
        "OPENAI_API_BASE",
        "http://litellm:4000/v1"
    )

    api_key = os.getenv(
        "OPENAI_API_KEY"
    )

    response = requests.get(
        f"{base_url}/models",
        headers={
            "Authorization": f"Bearer {api_key}"
        },
        timeout=10,
    )

    response.raise_for_status()

    data = response.json()

    return [
        item["id"]
        for item in data["data"]
    ]

def chat(messages, model=DEFAULT_MODEL):

    available = get_available_models()
    client = get_client()

    if model not in available:

        raise ValueError(
            f"Model '{model}' not available. "
            f"Available: {available}"
        )
    response = client.chat.completions.create(

        model=model,

        messages=messages,

        temperature=0,
        timeout=120,

    )


    return (
        response
        .choices[0]
        .message
        .content
    )

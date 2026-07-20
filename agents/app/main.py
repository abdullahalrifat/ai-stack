from fastapi import FastAPI

from app.agent import agent


app = FastAPI()


@app.get("/")
def health():

    return {
        "status":"running"
    }



@app.post("/chat")
async def chat(message:str):

    result = agent.invoke(
        {
            "messages":[
                {
                "role":"user",
                "content":message
                }
            ]
        }
    )


    return {
        "answer":
        result["messages"][-1].content
    }
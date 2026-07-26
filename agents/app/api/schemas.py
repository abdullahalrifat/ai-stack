"""Pydantic contracts for the HTTP and OpenAI-compatible APIs."""

from typing import Any

from pydantic import BaseModel

from app.core.config import DEFAULT_WORKSPACE


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    workspace: str | None = str(DEFAULT_WORKSPACE)
    model: str | None = None
    allow_write: bool = False


class PlanRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    workspace: str | None = str(DEFAULT_WORKSPACE)


class ExecuteRequest(BaseModel):
    task: str
    conversation_id: str | None = None
    workspace: str | None = str(DEFAULT_WORKSPACE)
    model: str | None = None
    allow_write: bool = False


class RunRequest(ExecuteRequest):
    document_scope: str | None = None
    project_id: str | None = None


class ProjectRequest(BaseModel):
    name: str
    workspace: str | None = str(DEFAULT_WORKSPACE)


class IngestRequest(BaseModel):
    documents: list[str]
    metadata: dict[str, Any] | None = None
    scope: str | None = None


class MemoryQuery(BaseModel):
    query: str
    top_k: int = 5
    scope: str | None = None


class OpenAIChatMessage(BaseModel):
    role: str
    content: str


class OpenAIChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[OpenAIChatMessage]
    temperature: float | None = 0
    max_tokens: int | None = None
    stream: bool | None = False
    workspace: str | None = str(DEFAULT_WORKSPACE)
    conversation_id: str | None = None
    allow_write: bool = False


class OpenAIEmbeddingRequest(BaseModel):
    model: str | None = None
    input: list[str] | str


class ImageGenerationRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    steps: int = 28
    seed: int = -1

"""Pydantic contracts for the HTTP and OpenAI-compatible APIs."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    model_config = ConfigDict(extra="ignore")

    protocol_version: Literal[1] | None = None
    document_scope: str | None = None
    project_id: str | None = None
    client_id: str | None = Field(default=None, min_length=1, max_length=128)


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


class HunkApprovalRequest(BaseModel):
    hunk_ids: list[str]


class MemoryUpdateRequest(BaseModel):
    text: str | None = None
    expires_at: str | None = None
    scope: str | None = None


class ClaimVerificationRequest(BaseModel):
    claims: list[dict]

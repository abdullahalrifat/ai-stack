"""Durable control plane for checkpoints and live run steering."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from ..dependencies import require_run_store, verify_api_key
from ..runs.store import get_run_store

router = APIRouter(prefix="/runs", dependencies=[Depends(verify_api_key), Depends(require_run_store)])

class CheckpointRequest(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    workspace_revision: str | None = Field(default=None, max_length=200)
    conversation_revision: str | None = Field(default=None, max_length=200)

class SteeringRequest(BaseModel):
    action: str
    instruction: str | None = Field(default=None, max_length=20_000)
    checkpoint_id: str | None = Field(default=None, max_length=200)

_ALLOWED = {"pause", "resume", "cancel", "redirect", "rewind"}


def _event(run_id: str, event_type: str, payload: dict):
    store = get_run_store()
    if not store.get_run(run_id):
        raise HTTPException(404, "Run not found")
    return store.append_event(run_id, event_type, payload)


@router.post("/{run_id}/checkpoints")
def checkpoint(run_id: str, request: CheckpointRequest):
    return _event(run_id, "checkpoint_created", request.model_dump())


@router.post("/{run_id}/steer")
def steer(run_id: str, request: SteeringRequest):
    action = request.action.strip().lower()
    if action not in _ALLOWED:
        raise HTTPException(400, f"Unsupported steering action: {action}")
    if action == "redirect" and not (request.instruction or "").strip():
        raise HTTPException(400, "redirect requires instruction")
    if action == "rewind" and not request.checkpoint_id:
        raise HTTPException(400, "rewind requires checkpoint_id")
    return _event(run_id, "run_steering", request.model_dump())

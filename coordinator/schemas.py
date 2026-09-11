"""
schemas.py
Pydantic models defining the request/response bodies for the coordinator API.
Keeping these separate from models.py: models.py is our internal domain data,
schemas.py is what's exposed over the wire.
"""

from pydantic import BaseModel
from typing import Optional


class AddTaskRequest(BaseModel):
    task_id: str
    payload: str


class ClaimRequest(BaseModel):
    worker_id: str


class HeartbeatRequest(BaseModel):
    task_id: str
    worker_id: str
    fencing_token: int


class CommitRequest(BaseModel):
    task_id: str
    worker_id: str
    fencing_token: int
    result: str


class TaskResponse(BaseModel):
    task_id: str
    payload: str
    state: str
    fencing_token: int
    current_owner: Optional[str] = None
    lease_expires_at: Optional[float] = None
    result: Optional[str] = None
"""
app.py
The DupliGuard Coordinator API.

Exposes the TaskStore over HTTP so multiple worker processes (running
independently, possibly on different machines) can:
  - submit tasks
  - claim a task to work on
  - send heartbeats to keep their lease alive
  - commit results (protected by fencing tokens)
  - inspect task/system state

Run with:
    uvicorn coordinator.app:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException

from coordinator.store import TaskStore
from coordinator.models import Task
from coordinator.schemas import (
    AddTaskRequest,
    ClaimRequest,
    HeartbeatRequest,
    CommitRequest,
    TaskResponse,
)

app = FastAPI(title="DupliGuard Coordinator")

# Single shared store for this process. In a real production system this
# would be backed by a database; for our mini project, in-memory + a lock
# is enough to demonstrate the mechanism correctly.
store = TaskStore(lease_seconds=10)


def _to_response(task: Task) -> TaskResponse:
    return TaskResponse(
        task_id=task.task_id,
        payload=task.payload,
        state=task.state.value,
        fencing_token=task.fencing_token,
        current_owner=task.current_owner,
        lease_expires_at=task.lease_expires_at,
        result=task.result,
    )


@app.post("/tasks", response_model=TaskResponse)
def add_task(req: AddTaskRequest):
    """Submit a new task into the system."""
    try:
        task = store.add_task(req.task_id, req.payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_response(task)


@app.post("/claim", response_model=TaskResponse)
def claim_task(req: ClaimRequest):
    """A worker asks for a task to work on."""
    task = store.claim_task(req.worker_id)
    if task is None:
        raise HTTPException(status_code=204, detail="No tasks available")
    return _to_response(task)


@app.post("/heartbeat")
def heartbeat(req: HeartbeatRequest):
    """A worker signals it's still alive, extending its lease."""
    ok = store.heartbeat(req.task_id, req.worker_id, req.fencing_token)
    if not ok:
        raise HTTPException(status_code=409, detail="Heartbeat rejected (stale lease/token)")
    return {"ok": True}


@app.post("/commit")
def commit_task(req: CommitRequest):
    """A worker submits its finished result. Rejected if the fencing token is stale."""
    ok = store.commit_task(req.task_id, req.worker_id, req.fencing_token, req.result)
    if not ok:
        raise HTTPException(status_code=409, detail="Commit rejected (stale fencing token or wrong owner)")
    return {"ok": True}


@app.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str):
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _to_response(task)


@app.get("/tasks", response_model=list[TaskResponse])
def list_tasks():
    return [_to_response(t) for t in store.list_tasks()]
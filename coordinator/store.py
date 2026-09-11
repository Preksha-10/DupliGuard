"""
store.py
The TaskStore is the brain of DupliGuard. It holds all tasks in memory and
enforces the core guarantees of the system:

1. A task can only be LEASED to one worker at a time.
2. If a worker's lease expires (it died / took too long), the task becomes
   claimable again by a different worker, and gets a NEW fencing token.
3. When a worker tries to commit a result, it must present the fencing token
   it received at claim time. If that token is stale (a newer claim has since
   happened), the commit is rejected -> this is what prevents duplicate/zombie
   writes from corrupting results.
"""

import threading
import time
from typing import Dict, List, Optional

from coordinator.models import Task, TaskState


# Default lease duration: how long a worker has to finish a task before
# the coordinator assumes it died and reassigns the task.
DEFAULT_LEASE_SECONDS = 10


class TaskStore:
    def __init__(self, lease_seconds: int = DEFAULT_LEASE_SECONDS):
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.Lock()  # protects against race conditions across requests
        self._lease_seconds = lease_seconds

    # ------------------------------------------------------------------
    # Task creation
    # ------------------------------------------------------------------
    def add_task(self, task_id: str, payload: str) -> Task:
        with self._lock:
            if task_id in self._tasks:
                raise ValueError(f"Task '{task_id}' already exists")
            task = Task(task_id=task_id, payload=payload)
            self._tasks[task_id] = task
            return task

    # ------------------------------------------------------------------
    # Claiming a task (worker asks: "give me something to do")
    # ------------------------------------------------------------------
    def claim_task(self, worker_id: str) -> Optional[Task]:
        """
        Finds one available task (PENDING, or LEASED-but-expired) and assigns
        it to worker_id. Issues a NEW fencing token for this claim.
        Returns the claimed Task, or None if nothing is available.
        """
        with self._lock:
            for task in self._tasks.values():
                self._reclaim_if_expired(task)

                if task.state == TaskState.PENDING:
                    task.state = TaskState.LEASED
                    task.current_owner = worker_id
                    task.fencing_token += 1  # bump token: this is a fresh claim
                    task.lease_expires_at = time.time() + self._lease_seconds
                    return task
            return None

    def _reclaim_if_expired(self, task: Task) -> None:
        """If a task's lease has expired, put it back to PENDING so it can be
        claimed by someone else. Does NOT touch the fencing token here -
        the token only increments at the moment of a NEW claim."""
        if task.is_lease_expired():
            task.state = TaskState.PENDING
            task.current_owner = None
            task.lease_expires_at = None

    # ------------------------------------------------------------------
    # Heartbeat (worker says: "I'm still alive, extend my lease")
    # ------------------------------------------------------------------
    def heartbeat(self, task_id: str, worker_id: str, fencing_token: int) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            self._reclaim_if_expired(task)

            if (
                task.state == TaskState.LEASED
                and task.current_owner == worker_id
                and task.fencing_token == fencing_token
            ):
                task.lease_expires_at = time.time() + self._lease_seconds
                return True
            return False

    # ------------------------------------------------------------------
    # Commit (worker says: "I finished, here's the result")
    # ------------------------------------------------------------------
    def commit_task(
        self, task_id: str, worker_id: str, fencing_token: int, result: str
    ) -> bool:
        """
        Accepts the result ONLY if:
        - the task exists
        - it is still LEASED
        - the fencing token matches the current one exactly

        This is the crucial anti-duplicate check. If Worker A's lease expired
        and Worker B claimed the task (bumping the token), Worker A's late
        commit will carry the OLD token and get rejected here.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False

            self._reclaim_if_expired(task)

            if task.state != TaskState.LEASED:
                return False
            if task.fencing_token != fencing_token:
                return False  # stale/zombie worker - reject
            if task.current_owner != worker_id:
                return False

            task.state = TaskState.DONE
            task.result = result
            task.current_owner = None
            task.lease_expires_at = None
            return True

    # ------------------------------------------------------------------
    # Read helpers (for the API layer / tests / demo)
    # ------------------------------------------------------------------
    def get_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                self._reclaim_if_expired(task)
            return task

    def list_tasks(self) -> List[Task]:
        with self._lock:
            for task in self._tasks.values():
                self._reclaim_if_expired(task)
            return list(self._tasks.values())
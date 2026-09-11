"""
models.py
Defines the core data structures used by the DupliGuard coordinator:
- TaskState: lifecycle states a task can be in
- Task: a unit of work tracked by the coordinator
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import time


class TaskState(str, Enum):
    PENDING = "PENDING"     # not claimed by any worker yet
    LEASED = "LEASED"       # claimed by a worker, lease active
    DONE = "DONE"           # successfully committed
    FAILED = "FAILED"       # permanently failed (optional, for future use)


@dataclass
class Task:
    task_id: str
    payload: str                      # placeholder for "the work" (e.g. a job description)
    state: TaskState = TaskState.PENDING

    # Fencing token: increases every time a worker claims this task.
    # Used to reject stale/zombie commits.
    fencing_token: int = 0

    # Which worker currently holds the lease (None if unclaimed)
    current_owner: Optional[str] = None

    # Wall-clock time (epoch seconds) when the current lease expires
    lease_expires_at: Optional[float] = None

    # Final result, once committed
    result: Optional[str] = None

    def is_lease_expired(self) -> bool:
        """A task's lease has expired if it's LEASED but the expiry time has passed."""
        if self.state != TaskState.LEASED or self.lease_expires_at is None:
            return False
        return time.time() > self.lease_expires_at
"""
test_dupliguard.py
Automated tests proving DupliGuard's core guarantees:

1. A task can only be claimed by one worker at a time.
2. If a worker's lease expires, the task becomes claimable again with a
   NEW fencing token.
3. A stale (old fencing token) commit is rejected - this is what prevents
   duplicate/zombie writes.
4. A valid commit (current fencing token) succeeds.
5. Heartbeats correctly extend a lease, preventing premature reclaiming.
"""

import time
import pytest

from coordinator.store import TaskStore
from coordinator.models import TaskState


def test_claim_assigns_pending_task():
    store = TaskStore(lease_seconds=10)
    store.add_task("t1", "payload-1")

    task = store.claim_task("workerA")

    assert task is not None
    assert task.task_id == "t1"
    assert task.state == TaskState.LEASED
    assert task.current_owner == "workerA"
    assert task.fencing_token == 1


def test_second_worker_cannot_claim_active_lease():
    store = TaskStore(lease_seconds=10)
    store.add_task("t1", "payload-1")

    store.claim_task("workerA")
    second_claim = store.claim_task("workerB")

    # No other tasks exist, and t1's lease hasn't expired -> nothing to claim
    assert second_claim is None


def test_expired_lease_is_reclaimed_with_new_fencing_token():
    store = TaskStore(lease_seconds=1)  # short lease for fast test
    store.add_task("t1", "payload-1")

    first = store.claim_task("workerA")
    assert first.fencing_token == 1

    time.sleep(1.2)  # let the lease expire

    second = store.claim_task("workerB")
    assert second is not None
    assert second.current_owner == "workerB"
    assert second.fencing_token == 2  # token must increase on reclaim


def test_stale_commit_is_rejected_after_reclaim():
    """This is THE core guarantee of the whole project: a zombie worker
    that wakes up late must NOT be able to commit a duplicate result."""
    store = TaskStore(lease_seconds=1)
    store.add_task("t1", "payload-1")

    first = store.claim_task("workerA")
    stale_token = first.fencing_token

    time.sleep(1.2)  # workerA "dies" - lease expires

    second = store.claim_task("workerB")
    fresh_token = second.fencing_token

    # workerA wakes up late and tries to commit with its OLD token
    stale_commit_ok = store.commit_task("t1", "workerA", stale_token, "result-from-A")
    assert stale_commit_ok is False

    # workerB commits with the CURRENT token -> must succeed
    fresh_commit_ok = store.commit_task("t1", "workerB", fresh_token, "result-from-B")
    assert fresh_commit_ok is True

    final_task = store.get_task("t1")
    assert final_task.state == TaskState.DONE
    assert final_task.result == "result-from-B"


def test_heartbeat_extends_lease_and_prevents_reclaim():
    store = TaskStore(lease_seconds=2)
    store.add_task("t1", "payload-1")

    task = store.claim_task("workerA")
    token = task.fencing_token

    # Sleep a bit, then heartbeat before the lease expires
    time.sleep(1)
    ok = store.heartbeat("t1", "workerA", token)
    assert ok is True

    # Sleep past the ORIGINAL expiry, but heartbeat should have pushed it out
    time.sleep(1.5)
    second_claim = store.claim_task("workerB")
    assert second_claim is None  # still leased to workerA, not expired yet


def test_heartbeat_with_stale_token_is_rejected():
    store = TaskStore(lease_seconds=1)
    store.add_task("t1", "payload-1")

    first = store.claim_task("workerA")
    stale_token = first.fencing_token

    time.sleep(1.2)  # workerA's lease expires
    store.claim_task("workerB")  # workerB takes over, bumps token

    # workerA's late heartbeat should be rejected (stale token)
    ok = store.heartbeat("t1", "workerA", stale_token)
    assert ok is False


def test_commit_marks_task_done_and_frees_owner():
    store = TaskStore(lease_seconds=10)
    store.add_task("t1", "payload-1")

    task = store.claim_task("workerA")
    ok = store.commit_task("t1", "workerA", task.fencing_token, "final-result")

    assert ok is True
    final = store.get_task("t1")
    assert final.state == TaskState.DONE
    assert final.result == "final-result"
    assert final.current_owner is None
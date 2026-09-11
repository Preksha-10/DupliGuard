"""
demo.py
Automated end-to-end demonstration of DupliGuard's core guarantee:

  A worker crashes mid-task -> its lease expires -> a second worker
  takes over with a NEW fencing token -> the crashed worker's late
  commit attempt (simulating it waking back up) is REJECTED -> the
  second worker's commit SUCCEEDS.

This runs entirely in-process (no need to start uvicorn or juggle
multiple terminals) by talking to the TaskStore directly - the same
logic the HTTP API uses under the hood.

Run with:
    python demo.py
"""

import time
from coordinator import store
from coordinator.store import TaskStore
from coordinator.models import TaskState

LEASE_SECONDS = 3
TASK_ID = "demo-task-1"


def line(msg: str):
    print(f"[demo] {msg}")


def main():
    store = TaskStore(lease_seconds=LEASE_SECONDS)
    store.add_task(TASK_ID, "sum-numbers")
    line(f"Task '{TASK_ID}' submitted. Lease duration = {LEASE_SECONDS}s")

    # --- Worker A claims the task and starts "working" ---
    task_a = store.claim_task("workerA")
    token_a = task_a.fencing_token          # snapshot the token NOW, before it can change
    line(f"workerA claimed '{TASK_ID}' with fencing_token={token_a}")

    # Simulate workerA sending ONE heartbeat, then crashing
    # (e.g. process killed, network partition, GC pause, etc.)
    time.sleep(1)
    hb_ok = store.heartbeat(TASK_ID, "workerA", token_a)
    line(f"workerA heartbeat sent -> accepted={hb_ok}")

    line("workerA now CRASHES (simulated) - no more heartbeats sent")
    line(f"Waiting {LEASE_SECONDS + 1}s for workerA's lease to expire...")
    time.sleep(LEASE_SECONDS + 1)

    # --- Worker B claims the same task after the lease expires ---
    task_b = store.claim_task("workerB")
    assert task_b is not None, "workerB should have been able to reclaim the task"
    token_b = task_b.fencing_token           # snapshot workerB's token too
    line(f"workerB claimed '{TASK_ID}' with NEW fencing_token={token_b}")
    assert token_b > token_a, "fencing token must increase on reclaim"

    # --- workerB does the work and commits successfully ---
    line("workerB working...")
    time.sleep(1)
    commit_b_ok = store.commit_task(TASK_ID, "workerB", token_b, "result-from-workerB")
    line(f"workerB commit -> accepted={commit_b_ok}")
    assert commit_b_ok is True, "workerB's commit with the current fencing token must succeed"


    # --- workerA "wakes up" late and tries to commit its stale result ---
    line("workerA wakes up late (simulated) and tries to commit its old result...")
    commit_a_ok = store.commit_task(TASK_ID, "workerA", token_a, "result-from-workerA")
    line(f"workerA commit -> accepted={commit_a_ok}")
    assert commit_a_ok is False, "workerA's stale commit MUST be rejected"

    # --- Final state check ---
    final_task = store.get_task(TASK_ID)
    line(f"Final task state: {final_task.state.value}, result = '{final_task.result}'")
    assert final_task.state == TaskState.DONE
    assert final_task.result == "result-from-workerB"

    print()
    line("SUCCESS: No duplicate commit landed. Crashed worker's task was")
    line("automatically recovered and completed by another worker.")


if __name__ == "__main__":
    main()
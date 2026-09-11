"""
worker.py
A DupliGuard worker process.

Each worker:
  1. Polls the coordinator for a task to claim.
  2. "Executes" the task (simulated with a sleep).
  3. Sends periodic heartbeats to keep its lease alive while working.
  4. Commits the result using the fencing token it received at claim time.

Run multiple copies of this script (different terminals) to simulate
multiple workers competing for tasks. Kill one mid-task (Ctrl+C or close
the terminal) to simulate a crash and watch another worker pick up the
abandoned task once its lease expires.

Usage:
    python worker/worker.py <worker_id> [--work-seconds N] [--coordinator-url URL]
"""

import argparse
import time
import sys
import requests


def claim_task(base_url: str, worker_id: str):
    resp = requests.post(f"{base_url}/claim", json={"worker_id": worker_id})
    if resp.status_code == 204:
        return None
    resp.raise_for_status()
    return resp.json()


def send_heartbeat(base_url: str, task_id: str, worker_id: str, fencing_token: int) -> bool:
    resp = requests.post(
        f"{base_url}/heartbeat",
        json={"task_id": task_id, "worker_id": worker_id, "fencing_token": fencing_token},
    )
    return resp.status_code == 200


def commit_task(base_url: str, task_id: str, worker_id: str, fencing_token: int, result: str) -> bool:
    resp = requests.post(
        f"{base_url}/commit",
        json={
            "task_id": task_id,
            "worker_id": worker_id,
            "fencing_token": fencing_token,
            "result": result,
        },
    )
    return resp.status_code == 200


def run_worker(worker_id: str, base_url: str, work_seconds: int, poll_interval: float = 2.0):
    print(f"[{worker_id}] starting, polling {base_url} every {poll_interval}s")

    while True:
        task = claim_task(base_url, worker_id)

        if task is None:
            print(f"[{worker_id}] no tasks available, waiting...")
            time.sleep(poll_interval)
            continue

        task_id = task["task_id"]
        fencing_token = task["fencing_token"]
        print(f"[{worker_id}] claimed task '{task_id}' (fencing_token={fencing_token}), "
              f"working for {work_seconds}s")

        # Simulate doing the work in small chunks, sending a heartbeat
        # after each chunk so the coordinator knows we're still alive.
        elapsed = 0
        heartbeat_every = 3  # seconds
        while elapsed < work_seconds:
            chunk = min(heartbeat_every, work_seconds - elapsed)
            time.sleep(chunk)
            elapsed += chunk

            ok = send_heartbeat(base_url, task_id, worker_id, fencing_token)
            if not ok:
                print(f"[{worker_id}] heartbeat REJECTED for '{task_id}' "
                      f"(lease lost / stale token) - abandoning task")
                break
            print(f"[{worker_id}] heartbeat ok for '{task_id}' ({elapsed}/{work_seconds}s)")
        else:
            # Work finished normally (loop didn't break) -> commit result
            result = f"processed:{task['payload']}:by:{worker_id}"
            committed = commit_task(base_url, task_id, worker_id, fencing_token, result)
            if committed:
                print(f"[{worker_id}] COMMIT SUCCESS for '{task_id}' -> {result}")
            else:
                print(f"[{worker_id}] COMMIT REJECTED for '{task_id}' "
                      f"(stale fencing token - another worker already took over)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DupliGuard worker process")
    parser.add_argument("worker_id", help="Unique identifier for this worker, e.g. workerA")
    parser.add_argument("--work-seconds", type=int, default=8,
                         help="Simulated time to 'process' each task")
    parser.add_argument("--coordinator-url", default="http://127.0.0.1:8000",
                         help="Base URL of the coordinator API")
    args = parser.parse_args()

    try:
        run_worker(args.worker_id, args.coordinator_url, args.work_seconds)
    except KeyboardInterrupt:
        print(f"\n[{args.worker_id}] shutting down (Ctrl+C)")
        sys.exit(0)
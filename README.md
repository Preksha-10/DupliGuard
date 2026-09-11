# DupliGuard

A fault-tolerant distributed task coordination engine that **prevents duplicate work** and **automatically recovers failed tasks** using lease-based ownership and fencing tokens.

## Problem

In distributed systems, the same unit of work can end up processed by multiple workers due to concurrent requests, retries, or communication failures — wasting CPU, memory, and time. If a worker crashes mid-task, that task's progress can also be lost entirely unless something detects the failure and reassigns it.

DupliGuard solves both problems with a minimal, understandable mechanism: **leases** (time-bound ownership of a task) and **fencing tokens** (a monotonically increasing number that invalidates late/duplicate commits from workers that have lost ownership).

## How it works

1. A **coordinator** holds all tasks and their state: `PENDING → LEASED → DONE`.
2. A **worker** claims a `PENDING` task. The coordinator grants it a **lease** (an expiry time) and a **fencing token** (an integer that increases every time a task is claimed).
3. While working, the worker sends periodic **heartbeats** to extend its lease. If it stops heartbeating (crash, network failure, etc.), the lease **expires**.
4. Once expired, the task becomes claimable again — a different worker can claim it, receiving a **new, higher fencing token**.
5. When a worker **commits** a result, it must present the fencing token it was given at claim time. If a newer claim has happened since (i.e. the coordinator has since moved on), the commit is **rejected** — this is what stops a "zombie" worker (one that crashed, got reassigned, then woke up and tried to finish anyway) from overwriting or duplicating a result.

This guarantees: **at most one valid commit per task**, and **no task is permanently lost** due to a single worker failure.

## Architecture

```
                     ┌─────────────────┐
   POST /tasks       │                 │
   POST /claim        →  Coordinator   │◄── in-memory TaskStore
   POST /heartbeat    →  (FastAPI)     │    (leases + fencing tokens)
   POST /commit        →                │
                     └─────────────────┘
                        ▲            ▲
                        │            │
                  claim/commit  claim/commit
                        │            │
                  ┌──────────┐  ┌──────────┐
                  │ Worker A │  │ Worker B │   ... more workers
                  └──────────┘  └──────────┘
```

## Project structure

```
DupliGuard/
├── coordinator/
│   ├── models.py     # Task + TaskState data model
│   ├── store.py       # Core logic: claim, heartbeat, commit, fencing
│   ├── schemas.py      # API request/response schemas
│   └── app.py           # FastAPI server exposing the store over HTTP
├── worker/
│   └── worker.py          # Worker process: polls, heartbeats, commits
├── tests/
│   └── test_dupliguard.py    # Automated proofs of correctness
├── requirements.txt
└── README.md
```

## Running it

### 1. Setup

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Start the coordinator

```bash
python -m uvicorn coordinator.app:app --reload --port 8000
```

Swagger UI available at `http://127.0.0.1:8000/docs` for manual testing.

### 3. Submit a task

```bash
curl -X POST http://127.0.0.1:8000/tasks -H "Content-Type: application/json" -d '{"task_id": "t1", "payload": "sum-numbers"}'
```

### 4. Start one or more workers

```bash
python worker/worker.py workerA --work-seconds 8
python worker/worker.py workerB --work-seconds 8    # in another terminal
```

### 5. Simulate a crash (the key demo)

1. Submit a task with a long `--work-seconds`.
2. Start `workerA`. Once you see it claim the task and send its first heartbeat, kill it (Ctrl+C) — simulating a crash mid-task.
3. Start `workerB`. Once `workerA`'s lease expires (default 10s), `workerB` claims the same task with a **new fencing token** and completes it.
4. If `workerA` were to wake up and try to commit late, its commit would be **rejected** by the coordinator due to its stale fencing token.

## Running the automated tests

```bash
python -m pytest tests/ -v
```

Tests cover:
- Only one worker can hold an active lease on a task at a time.
- An expired lease is correctly reclaimed with a new fencing token.
- A stale (old-token) commit is rejected — the core anti-duplicate guarantee.
- A valid (current-token) commit succeeds.
- Heartbeats correctly extend a lease and prevent premature reassignment.
- A stale heartbeat (after reassignment) is rejected.

## Why this matters

This mirrors a real pattern used in production distributed systems (e.g. distributed locks in Chubby/ZooKeeper, DynamoDB-style lease tables, Kafka consumer group rebalancing) to solve a very common class of bug: **split-brain duplicate writes** caused by a worker that appears dead but isn't truly dead (a GC pause, network partition, etc.), and later "wakes up" and tries to act as if nothing happened. Fencing tokens are the standard fix, popularized by Martin Kleppmann's writing on distributed locks.

## Possible extensions

- Persist tasks in a real database (Postgres/Redis) instead of in-memory, so the coordinator itself can restart without losing state.
- Run the coordinator with multiple replicas behind a consensus layer (e.g. Raft) to remove it as a single point of failure.
- Add a retry-limit / dead-letter queue for tasks that fail repeatedly.
- Add authentication so workers must prove identity when claiming tasks.

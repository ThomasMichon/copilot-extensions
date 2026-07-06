"""agent-dispatch -- a portable agent task-queue + coordinator.

This package currently ships the queue **engine** (:mod:`agent_dispatch.queue`):
a single-writer, WAL-mode SQLite leased task queue with a six-state model,
capability-gated atomic claim, and lease recovery. The per-host coordinator
daemon and CLI land in a subsequent slice.
"""

from __future__ import annotations

from agent_dispatch.queue import Status, Task, TaskError, TaskQueue, worker_id_for

__all__ = ["Status", "Task", "TaskError", "TaskQueue", "worker_id_for"]
__version__ = "0.1.0-dev12"

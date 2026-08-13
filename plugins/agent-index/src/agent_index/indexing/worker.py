"""Detached, versioned index worker (model A).

The retrieval service delegates each dequeued indexing task to a short-lived
worker subprocess launched from the ACTIVE versioned install folder
(``versions/<ver>/Scripts/python.exe -m agent_index index-worker --task <id>``).

Because the worker runs from its own immutable version folder, it keeps running
across a retrieval-service cutover (near-ZDD), and the durable task queue makes
an interrupted job resumable. The worker OWNS the task's progress + terminal
status in the ``TaskStore``; the service only spawns and monitors it.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_index.indexing.task_store import TaskStore

log = logging.getLogger(__name__)

# Throttle interval for persisting progress to SQLite (seconds).
_PROGRESS_PERSIST_INTERVAL = 5.0


class WorkerProgress:
    """Progress sink for ``run_reindex`` that persists to the ``TaskStore``.

    Decoupled from the service's asyncio ``ProgressCallback``: it writes progress
    rows (throttled) and checks a cancel flag set by a signal handler. Implements
    exactly the surface ``run_reindex`` calls: ``check_cancelled``,
    ``source_started``, ``source_complete``, ``phase``, ``file_discovered``,
    ``batch_complete``.
    """

    def __init__(
        self, task_id: str, store: TaskStore, cancelled: threading.Event
    ) -> None:
        self._task_id = task_id
        self._store = store
        self._cancelled = cancelled
        self._last_persist = 0.0
        self._last_stage = ""
        self._pct = 0.0

    # ── cancellation ────────────────────────────────────────
    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            from agent_index.indexing.runner import IndexingCancelled

            raise IndexingCancelled(f"Task {self._task_id} cancelled")

    # ── progress ────────────────────────────────────────────
    def phase(self, stage: str, msg: str = "", *, pct: float = 0.0) -> None:
        self._update(stage, pct, msg, force=True)

    def source_started(self, source: str) -> None:
        self._update("discovering", 0.0, f"Discovering {source}", force=True)

    def source_complete(self, source: str, chunks: int) -> None:
        self._update("source_complete", self._pct, f"{source}: {chunks} chunks", force=True)

    def file_discovered(self, count: int, total: int = 0) -> None:
        msg = f"Discovered {count} files" + (f" / {total}" if total else "")
        self._update("discovering", 5.0, msg)

    def batch_complete(self, chunks_done: int, chunks_total: int | None = None) -> None:
        if chunks_total and chunks_total > 0:
            pct = min(95.0, 10.0 + 85.0 * (chunks_done / chunks_total))
        else:
            pct = min(95.0, self._pct + 1.0)
        msg = f"Embedded {chunks_done}"
        if chunks_total:
            msg += f" / {chunks_total}"
        msg += " chunks"
        self._update("embedding", pct, msg)

    def _update(self, stage: str, pct: float, msg: str, *, force: bool = False) -> None:
        self._pct = pct
        now = time.monotonic()
        changed = stage != self._last_stage
        self._last_stage = stage
        if force or changed or (now - self._last_persist) >= _PROGRESS_PERSIST_INTERVAL:
            self._last_persist = now
            try:
                self._store.update_progress(self._task_id, stage, pct, msg)
            except Exception:  # progress is best-effort
                log.debug("progress persist failed", exc_info=True)


def _install_signal_handlers(cancelled: threading.Event) -> None:
    """Set a cancel flag on SIGTERM/SIGINT/SIGBREAK (best-effort; POSIX-reliable).

    On Windows ``Popen.terminate`` uses ``TerminateProcess`` (not a catchable
    signal), so cancellation there is reconciled by the service monitor when it
    sees the worker die; this handler still covers Ctrl-Break and POSIX signals.
    """

    def _handler(_signum: int, _frame: object) -> None:
        cancelled.set()

    for signame in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass


def run_worker(task_id: str) -> int:
    """Entry point for ``agent-index index-worker --task <id>``.

    Claims the task's worker identity, runs the reindex, and writes the terminal
    status + result stats to the queue. Returns a process exit code.
    """
    from agent_index import __version__
    from agent_index import config as _config
    from agent_index.index_config import IndexConfig
    from agent_index.indexing import engine as indexing_engine
    from agent_index.indexing.runner import IndexingCancelled
    from agent_index.indexing.task_store import TaskStatus, TaskStore

    store = TaskStore(IndexConfig().data_dir / "tasks.db")
    task = store.get_task(task_id)
    if task is None:
        print(f"[FAIL] worker: task {task_id} not found", file=sys.stderr)
        return 2
    if task.status in {TaskStatus.COMPLETE.value, TaskStatus.CANCELLED.value}:
        return 0  # already terminal — idempotent no-op

    try:
        host = _config.machine_id()
    except Exception:  # fall back to the node name
        import platform

        host = platform.node()
    store.set_worker(task_id, os.getpid(), host, __version__)

    cancelled = threading.Event()
    _install_signal_handlers(cancelled)
    cb = WorkerProgress(task_id, store, cancelled)

    src = task.source if task.source != "all" else None
    # On a retried task (attempt > 1) resume: skip files already stored at the
    # same content hash within THIS task's window. Scope the window to the task's
    # FIRST processing time (first_started_at) so files written by OTHER tasks
    # while this one waited in the queue aren't wrongly skipped; fall back to
    # created_at for rows migrated before that column existed. A fresh task
    # (attempt 1) re-embeds everything the crawl selected (full-rebuild preserved).
    resume_since: float | None = None
    if task.attempt_count > 1:
        resume_since = task.first_started_at or task.created_at
        log.info("worker: task %s resuming (attempt %d)", task_id, task.attempt_count)
    try:
        result = indexing_engine.run_reindex(
            full=task.full, source=src, progress_cb=cb, resume_since=resume_since
        )
        if isinstance(result, dict):
            store.set_result_stats(task_id, result)
        store.update_progress(task_id, "complete", 100.0, "Indexing complete")
        store.update_status(task_id, TaskStatus.COMPLETE.value)
        log.info("worker: task %s complete", task_id)
        return 0
    except IndexingCancelled:
        store.update_status(task_id, TaskStatus.CANCELLED.value)
        log.info("worker: task %s cancelled", task_id)
        return 0
    except Exception as exc:  # record the failure durably
        err = f"{type(exc).__name__}: {exc}"
        store.update_status(task_id, TaskStatus.FAILED.value, err)
        log.error("worker: task %s failed: %s", task_id, err, exc_info=True)
        return 1

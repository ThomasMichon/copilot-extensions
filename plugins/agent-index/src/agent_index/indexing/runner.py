"""Task runner — async worker loop that drains the task queue.

Modelled after analysis-feed's ``EngineService`` worker loop and
permanent-record's ``TaskRunner``.  Runs in a persistent asyncio task,
executing one indexing job at a time.  Progress is throttled for SQLite
writes but streamed immediately via the EventBus.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_procutil import (
    detached_kwargs,
    no_window_kwargs,
    windowless_python,
    windowless_python_env,
)

from agent_index.indexing.task_store import TERMINAL, TaskStatus

if TYPE_CHECKING:
    from agent_index.indexing.task_store import TaskRecord, TaskStore
    from agent_index.server.event_bus import EventBus

log = logging.getLogger(__name__)

# Throttle interval for persisting progress to SQLite (seconds).
# SSE events are always emitted immediately.
_PROGRESS_PERSIST_INTERVAL = 5.0

_TERMINAL_VALUES = {s.value for s in TERMINAL}
_GOVERNANCE_BACKOFF_SECONDS = 10.0


def _machine_id() -> str:
    """This host's identity (matches the worker's recorded ``worker_host``)."""
    try:
        from agent_index import config as _config

        return _config.machine_id()
    except Exception:  # fall back to node name
        import platform

        return platform.node()


def _pid_alive(pid: int | None) -> bool:
    """Best-effort liveness check for a local PID (cross-platform)."""
    if not pid:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            k32 = ctypes.windll.kernel32
            handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok) and code.value == STILL_ACTIVE
            finally:
                k32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # The PID exists but is owned by another user — treat as alive.
        return True
    except OSError:
        return False


def _terminate_pid(pid: int) -> None:
    """Request a local worker PID to stop (SIGTERM / TerminateProcess)."""
    try:
        if sys.platform == "win32":
            import ctypes

            PROCESS_TERMINATE = 0x0001
            k32 = ctypes.windll.kernel32
            handle = k32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
            if handle:
                try:
                    k32.TerminateProcess(handle, 1)
                finally:
                    k32.CloseHandle(handle)
        else:
            os.kill(int(pid), signal.SIGTERM)
    except Exception:  # best effort
        log.debug("terminate pid %s failed", pid, exc_info=True)


def _load_governance_module():
    context_path = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()
    installation_id = os.environ.get("AGENT_INDEX_INSTALLATION_ID", "").strip()
    plugin_root_value = os.environ.get("AGENT_INDEX_HOME", "").strip()
    if not context_path or not installation_id or not plugin_root_value:
        return None
    plugin_root = Path(plugin_root_value).expanduser()
    manifest_path = plugin_root / "deploy-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source = manifest.get("source")
        payload_root_value = source.get("path") if isinstance(source, dict) else None
        if not isinstance(payload_root_value, str) or not payload_root_value.strip():
            raise ValueError("deploy manifest source.path is missing")
        payload_root = Path(payload_root_value).expanduser().resolve(strict=True)
        script = payload_root / "scripts" / "installation-context" / "installation_context.py"
        if not script.is_file():
            raise FileNotFoundError(script)
        module_name = (
            "agent_index_installation_context_"
            + hashlib.sha256(os.fsencode(script)).hexdigest()[:16]
        )
        spec = importlib.util.spec_from_file_location(module_name, script)
        if spec is None or spec.loader is None:
            raise ImportError("installation-context module cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        prior = sys.modules.get(module_name)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            if prior is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = prior
            raise
        marketplace_id, separator, plugin_id = installation_id.partition("/")
        if not separator or plugin_id != "agent-index":
            raise ValueError("installation id is invalid")
        return {
            "module": module,
            "context": context_path,
            "marketplace_id": marketplace_id,
            "plugin_id": plugin_id,
            "legacy_root": str(Path.home() / ".agent-index"),
        }
    except Exception as exc:
        return {
            "error": {
                "status": "backoff",
                "reason": "governance-unavailable",
                "detail": str(exc),
            }
        }


class IndexingCancelled(Exception):
    """Raised when a running task is cancelled via the API."""


class ProgressCallback:
    """Bridge between the indexing engine and the task runner.

    Called from a worker thread.  Throttles SQLite writes to every
    ``_PROGRESS_PERSIST_INTERVAL`` seconds (or on stage change), while
    streaming SSE events immediately via ``run_coroutine_threadsafe``.
    """

    def __init__(
        self,
        task_id: str,
        store: TaskStore,
        event_bus: EventBus,
        loop: asyncio.AbstractEventLoop,
        cancelled: threading.Event,
    ) -> None:
        self._task_id = task_id
        self._store = store
        self._event_bus = event_bus
        self._loop = loop
        self._cancelled = cancelled
        self._last_persist: float = 0.0
        self._last_stage: str = ""
        # Running counters (updated in-thread)
        self._stage: str = "queued"
        self._pct: float = 0.0
        self._msg: str = ""

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check_cancelled(self) -> None:
        """Raise IndexingCancelled if the task was cancelled."""
        if self._cancelled.is_set():
            raise IndexingCancelled(f"Task {self._task_id} cancelled")

    def phase(self, stage: str, msg: str = "", *, pct: float = 0.0) -> None:
        """Report a phase transition (discovering, chunking, embedding)."""
        self._update(stage, pct, msg, force_persist=True)

    def source_started(self, source: str) -> None:
        self._update("discovering", 0.0, f"Discovering {source}", force_persist=True)

    def source_complete(self, source: str, chunks: int) -> None:
        self._update(
            "source_complete", self._pct,
            f"{source}: {chunks} chunks",
            force_persist=True,
        )

    def file_discovered(self, count: int, total: int = 0) -> None:
        pct = 5.0  # discovery is early
        msg = f"Discovered {count} files" + (f" / {total}" if total else "")
        self._update("discovering", pct, msg)

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

    def _update(
        self,
        stage: str,
        pct: float,
        msg: str,
        *,
        force_persist: bool = False,
    ) -> None:
        self._stage = stage
        self._pct = pct
        self._msg = msg

        now = time.monotonic()
        stage_changed = stage != self._last_stage
        self._last_stage = stage

        # Always persist on stage change or forced; otherwise throttle
        if (
            force_persist
            or stage_changed
            or (now - self._last_persist) >= _PROGRESS_PERSIST_INTERVAL
        ):
            self._last_persist = now
            self._store.update_progress(self._task_id, stage, pct, msg)

        # SSE is always immediate
        self._event_bus.publish("task_progress", {
            "task_id": self._task_id,
            "stage": stage,
            "percent": round(pct, 1),
            "message": msg,
        })


class TaskRunner:
    """Async worker loop that drains the task queue sequentially.

    Runs as a persistent asyncio task.  When the queue is empty, waits
    on an ``asyncio.Event`` until awakened by ``notify()``.
    """

    def __init__(
        self,
        store: TaskStore,
        event_bus: EventBus,
    ) -> None:
        self.store = store
        self.event_bus = event_bus
        self._index_fn: Callable[..., None] | None = None
        self._post_index_fn: Callable[[], None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._shutdown = False
        self._paused = False
        self._wake = asyncio.Event()
        # Cancellation per running task
        self._cancel_events: dict[str, threading.Event] = {}
        self._cancel_lock = threading.Lock()
        # Per-source write lock (set by server at startup)
        self._source_lock_acquire: Callable[..., Any] | None = None
        # Worker delegation (model A): each active indexing task runs in a
        # detached versioned subprocess. We track the live workers, their monitor
        # coroutines, held source locks, and cancellation requests.
        self._active_workers: set[str] = set()
        self._worker_procs: dict[str, subprocess.Popen[bytes]] = {}
        self._monitors: dict[str, asyncio.Task[None]] = {}
        self._worker_locks: dict[str, Any] = {}
        self._cancel_requested: set[str] = set()
        self._governance_helper = _load_governance_module()
        self._governance_baseline: dict[str, Any] | None = None
        self._governance_state: dict[str, Any] | None = None
        # Observable state
        self.running = False
        self.active_task_id: str | None = None
        self.tasks_completed: int = 0
        self.tasks_failed: int = 0

    def set_index_fn(self, fn: Callable[..., None]) -> None:
        """Register the indexing function (called by the server at startup)."""
        self._index_fn = fn

    def set_post_index_fn(self, fn: Callable[[], None]) -> None:
        """Register a callback run after each successful indexing task."""
        self._post_index_fn = fn

    def set_source_lock_fn(self, fn: Callable[..., Any]) -> None:
        """Register the per-source lock acquire function.

        ``fn(source) → asyncio.Lock`` — called before entering the
        executor so the lock is held in the async context.
        """
        self._source_lock_acquire = fn

    def set_governance_recheck_fn(
        self,
        fn: Callable[[str], dict[str, Any]] | None,
    ) -> None:
        """Override the loop-governance recheck callback (tests)."""
        self._governance_helper = fn
        self._governance_baseline = None
        self._governance_state = None

    # ── Lifecycle ────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize DB, reap orphaned workers, adopt live ones, start the loop."""
        host = _machine_id()
        reaped = await asyncio.to_thread(self.store.reap_orphaned, host, _pid_alive)
        if reaped:
            self.event_bus.publish("queue_update", {"reason": "recovery"})

        # Re-adopt any task whose detached worker is still alive: it survived a
        # service cutover, running from its pinned version folder (near-ZDD).
        await self._adopt_running_workers(host)

        pending = await asyncio.to_thread(self.store.get_pending_count)
        if pending > 0:
            log.info("Startup recovery: %d queued task(s)", pending)

        self._shutdown = False
        self._task = asyncio.create_task(self._worker_loop())
        log.info("Task runner started")

    async def stop(self) -> None:
        """Stop the loop + monitors. Detached workers are LEFT running — they
        survive a cutover and are re-adopted by the next service."""
        self._shutdown = True
        self._wake.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        for mon in list(self._monitors.values()):
            mon.cancel()
        for mon in list(self._monitors.values()):
            with suppress(asyncio.CancelledError, Exception):
                await mon
        self._monitors.clear()
        for lock in list(self._worker_locks.values()):
            if lock is not None:
                with suppress(Exception):
                    lock.release()
        self._worker_locks.clear()
        log.info("Task runner stopped")

    async def drain(self, *, timeout: float = 300.0, poll: float = 0.5) -> bool:
        """Pause dequeueing for a cutover; always drains clean.

        In the worker-delegation model indexing runs in a DETACHED versioned
        subprocess that survives this service's exit (it runs from its pinned
        version folder) and is re-adopted by the next service — so a running
        index job never blocks a cutover. We only stop dequeueing new work;
        in-flight searches are drained separately by the DrainGate. ``timeout``
        and ``poll`` are accepted for signature compatibility but not needed
        (there is no in-process indexing work to wait out), so this never returns
        ``False``.
        """
        self._paused = True
        self._wake.set()
        return True

    async def resume(self) -> None:
        """Resume dequeueing queued tasks after a drain rollback."""
        self._paused = False
        self._wake.set()

    def notify(self) -> None:
        """Wake the worker loop (call after enqueueing)."""
        self._wake.set()

    # ── Cancel ───────────────────────────────────────────────

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a queued or running task. Returns True if cancelled."""
        # Queued tasks: cancel in the store directly.
        if self.store.cancel(task_id):
            self.event_bus.publish("task_cancelled", {"task_id": task_id})
            return True
        # Running task: terminate its detached worker subprocess. The monitor
        # reconciles the terminal status (cancelled) when the worker exits.
        pid: int | None = None
        proc = self._worker_procs.get(task_id)
        if proc is not None:
            pid = proc.pid
        else:
            rec = self.store.get_task(task_id)
            pid = rec.worker_pid if rec else None
        if pid:
            self._cancel_requested.add(task_id)
            _terminate_pid(int(pid))
            return True
        return False

    # ── Status ───────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "paused": self._paused,
            "active_task_id": self.active_task_id,
            "governance": self._governance_state,
            "stats": {
                "completed": self.tasks_completed,
                "failed": self.tasks_failed,
            },
        }

    async def _wait_for_wake(self, timeout: float) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            pass

    def _recheck_governance(self, checkpoint: str) -> dict[str, Any] | None:
        helper = self._governance_helper
        if helper is None:
            self._governance_state = None
            return None
        if callable(helper):
            result = helper(checkpoint)
            if "checkpoint" not in result:
                result = dict(result)
                result["checkpoint"] = checkpoint
        elif "error" in helper:
            result = {
                "checkpoint": checkpoint,
                **helper["error"],
            }
        else:
            module = helper["module"]
            result = module.recheck_loop_governance(
                context=helper["context"],
                expected_marketplace_id=helper["marketplace_id"],
                expected_plugin_id=helper["plugin_id"],
                legacy_root=helper["legacy_root"],
                baseline=self._governance_baseline,
                environment=os.environ,
            )
            result["checkpoint"] = checkpoint
        self._governance_state = result
        if result.get("status") == "ready":
            baseline = result.get("baseline")
            self._governance_baseline = baseline if isinstance(baseline, dict) else None
            self._governance_state = None
        return result

    # ── Worker loop ──────────────────────────────────────────

    async def _worker_loop(self) -> None:
        log.info("Worker loop started")
        # Small initial delay to let the service fully start
        await asyncio.sleep(2)

        while not self._shutdown:
            boundary = self._recheck_governance("iteration-boundary")
            if boundary is not None and boundary.get("status") != "ready":
                log.warning(
                    "Worker loop backing off at %s: %s (%s)",
                    boundary.get("checkpoint"),
                    boundary.get("reason"),
                    boundary.get("status"),
                )
                await self._wait_for_wake(_GOVERNANCE_BACKOFF_SECONDS)
                continue
            # Pause (drain) or a worker already running => wait. At most one
            # indexing worker runs at a time (serialized via _active_workers).
            if self._paused or self._active_workers:
                await self._wait_for_wake(5.0)
                continue

            pending = await asyncio.to_thread(self.store.get_pending_count)
            if pending <= 0:
                await self._wait_for_wake(10.0)
                continue
            before_dequeue = self._recheck_governance("pre-mutation:dequeue")
            if before_dequeue is not None and before_dequeue.get("status") != "ready":
                log.warning(
                    "Worker loop refused dequeue at %s: %s (%s)",
                    before_dequeue.get("checkpoint"),
                    before_dequeue.get("reason"),
                    before_dequeue.get("status"),
                )
                await self._wait_for_wake(_GOVERNANCE_BACKOFF_SECONDS)
                continue

            task = await asyncio.to_thread(self.store.dequeue_next)
            if task is None:
                await self._wait_for_wake(10.0)
                continue
            before_launch = self._recheck_governance("pre-mutation:launch")
            if before_launch is not None and before_launch.get("status") != "ready":
                await asyncio.to_thread(self.store.release_processing, task.id)
                log.warning(
                    "Worker loop released task %s after %s: %s (%s)",
                    task.id,
                    before_launch.get("checkpoint"),
                    before_launch.get("reason"),
                    before_launch.get("status"),
                )
                await self._wait_for_wake(_GOVERNANCE_BACKOFF_SECONDS)
                continue

            await self._launch_worker(task)

        log.info("Worker loop exited")

    # ── Worker delegation (model A) ──────────────────────────

    def _spawn_worker(self, task_id: str) -> subprocess.Popen[bytes]:
        """Spawn a detached versioned worker subprocess for ``task_id``.

        Launched with THIS service's ``sys.executable`` — the active versioned
        slot's python — so the worker runs from its own immutable version folder
        and survives a legacy cutover. Managed workers stay in the supervisor's
        containment boundary so their runtime generation cannot outlive its lease.
        """
        log_path = self.store.data_dir / "worker.log"
        # Workers run one-at-a-time (no interleaving), but bound the shared log so
        # it can't grow without limit over many runs: truncate once it is large.
        try:
            if log_path.exists() and log_path.stat().st_size > 2_000_000:
                log_path.write_bytes(b"")
        except OSError:
            pass
        logf = open(log_path, "ab", buffering=0)  # child inherits this handle
        try:
            python = sys.executable
            cmd = [
                windowless_python(python),
                "-I",
                "-B",
                "-X",
                "utf8",
                "-m",
                "agent_index",
                "index-worker",
                "--task",
                task_id,
            ]
            kwargs: dict[str, Any] = {
                "cwd": os.path.dirname(os.path.dirname(sys.executable)),
                "env": {
                    key: value
                    for key, value in os.environ.items()
                    if key not in {"PYTHONPATH", "PYTHONHOME"}
                },
                "stdout": logf,
                "stderr": logf,
                "stdin": subprocess.DEVNULL,
            }
            kwargs["env"].update(windowless_python_env(python))
            kwargs.update(
                no_window_kwargs()
                if os.environ.get("AGENT_INDEX_MANAGED_PYTHON")
                else detached_kwargs()
            )
            return subprocess.Popen(cmd, **kwargs)  # noqa: S603
        finally:
            with suppress(Exception):
                logf.close()  # the child holds its own inherited handle

    async def _launch_worker(self, task: TaskRecord) -> None:
        """Spawn + register a worker for a freshly dequeued (processing) task."""
        # NOTE: this per-source lock is IN-PROCESS — it serializes the worker
        # against THIS service's own ingest endpoints for the worker's lifetime.
        # It does NOT coordinate across processes: after a cutover the new service
        # does not hold it while the detached worker keeps writing. That narrow
        # cross-process write window is left to the store's own file locking; a
        # durable cross-process source lock is a follow-up.
        source_lock = None
        if self._source_lock_acquire is not None:
            source_key = task.source if task.source != "all" else "__all__"
            source_lock = await self._source_lock_acquire(source_key)
        self._worker_locks[task.id] = source_lock
        try:
            proc = await asyncio.to_thread(self._spawn_worker, task.id)
        except Exception:
            log.exception("Failed to spawn worker for task %s", task.id)
            if source_lock is not None:
                with suppress(Exception):
                    source_lock.release()
            self._worker_locks.pop(task.id, None)
            await asyncio.to_thread(
                self.store.update_status, task.id, TaskStatus.FAILED.value,
                "worker spawn failed",
            )
            return

        from agent_index import __version__

        await asyncio.to_thread(
            self.store.set_worker, task.id, proc.pid, _machine_id(), __version__,
        )
        self._worker_procs[task.id] = proc
        self._active_workers.add(task.id)
        self.running = True
        self.active_task_id = task.id
        self.event_bus.publish("task_started", {
            "task_id": task.id,
            "source": task.source,
            "full": task.full,
            "trigger_source": task.trigger_source,
        })
        self._monitors[task.id] = asyncio.create_task(self._monitor_worker(task, proc))

    async def _adopt_running_workers(self, host: str) -> None:
        """Adopt tasks whose detached worker survived a cutover (monitor only)."""
        try:
            running = await asyncio.to_thread(self.store.get_running_with_worker, host)
        except Exception:
            log.debug("adopt scan failed", exc_info=True)
            return
        for task in running:
            if task.id in self._active_workers or not _pid_alive(task.worker_pid):
                continue
            log.info(
                "Adopting in-flight worker for task %s (pid %s)", task.id, task.worker_pid
            )
            self._active_workers.add(task.id)
            self.running = True
            self.active_task_id = task.id
            self._monitors[task.id] = asyncio.create_task(
                self._monitor_worker(task, None)
            )

    async def _monitor_worker(
        self, task: TaskRecord, proc: subprocess.Popen[bytes] | None
    ) -> None:
        """Poll a worker to completion, then reconcile status + counters.

        The WORKER owns the terminal status; the monitor only writes one if the
        worker died without recording it (crash or hard-kill cancellation).
        """
        try:
            while True:
                rec = await asyncio.to_thread(self.store.get_task, task.id)
                if rec is not None and rec.status in _TERMINAL_VALUES:
                    break
                if proc is not None:
                    alive = proc.poll() is None
                else:
                    alive = _pid_alive(rec.worker_pid if rec else task.worker_pid)
                if not alive:
                    # Worker gone without a terminal status: reconcile it.
                    if task.id in self._cancel_requested:
                        final, err = TaskStatus.CANCELLED.value, None
                    else:
                        final, err = (
                            TaskStatus.INTERRUPTED.value,
                            "Worker exited without completing",
                        )
                    await asyncio.to_thread(self.store.update_status, task.id, final, err)
                    break
                await asyncio.sleep(1.0)

            rec = await asyncio.to_thread(self.store.get_task, task.id)
            status = rec.status if rec is not None else TaskStatus.FAILED.value
            if status == TaskStatus.COMPLETE.value:
                self.tasks_completed += 1
                if self._post_index_fn is not None:
                    try:
                        await asyncio.to_thread(self._post_index_fn)
                    except Exception:
                        log.warning("Post-index hook failed", exc_info=True)
            elif status in (TaskStatus.FAILED.value, TaskStatus.INTERRUPTED.value):
                self.tasks_failed += 1
            log.info("Task %s finished: %s", task.id, status)
            self.event_bus.publish("task_complete", {
                "task_id": task.id,
                "success": status == TaskStatus.COMPLETE.value,
                "status": status,
                "result_stats": rec.result_stats if rec else None,
            })
        except asyncio.CancelledError:
            # Service is stopping (e.g. cutover): LEAVE the detached worker
            # running — the next service adopts it. Do not mark the task.
            raise
        finally:
            lock = self._worker_locks.pop(task.id, None)
            if lock is not None:
                with suppress(Exception):
                    lock.release()
            self._active_workers.discard(task.id)
            self._worker_procs.pop(task.id, None)
            self._cancel_requested.discard(task.id)
            self._monitors.pop(task.id, None)
            self.running = bool(self._active_workers)
            self.active_task_id = next(iter(self._active_workers), None)
            self._wake.set()

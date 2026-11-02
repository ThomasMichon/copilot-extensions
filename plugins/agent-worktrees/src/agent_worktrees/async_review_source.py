"""Optional execution/drain adapter under an already selected source lifetime."""

import asyncio
import copy
import os
import threading

from agent_procutil.owned_process import OwnedCleanupError

from .pr_review_source import LocalReviewSource


class AsyncReviewSource:
    """The embedding owner must drain this adapter before releasing its source."""

    def __init__(self, source):
        if not isinstance(source, LocalReviewSource):
            raise ValueError("selected local source required")
        self.source = source
        self._pid = os.getpid()
        self._loop = None
        self._closing = threading.Event()
        self._tasks = {}
        self._cleanup_errors = []
        self._lost_workers = set()

    def contract(self):
        return self.source.contract()

    def read_contract(self):
        return self.source.read_contract()

    def acquisition_contract(self):
        return self.source.acquisition_contract()

    @staticmethod
    def execution_contract():
        return {
            "schema": "agent-pull-requests.source-execution", "version": 1,
            "execution": "owned_async", "release": "terminal_drain",
        }

    def _owner(self):
        loop = asyncio.get_running_loop()
        if os.getpid() != self._pid or self._loop is not None and loop is not self._loop:
            raise RuntimeError("source belongs to another execution owner")
        self._loop = loop

    async def execute(self, operation, *args, **kwargs):
        self._owner()
        if self._closing.is_set() or self._cleanup_errors or self._lost_workers:
            raise RuntimeError("source admission stopped")
        if operation not in ("inspect_and_retain", "prepare_and_retain", "read_retained"):
            raise ValueError("unsupported source operation")
        self.source.checkpoint()
        cancelled = threading.Event()
        source = copy.copy(self.source)

        def checkpoint():
            self.source.checkpoint()
            if self._closing.is_set() or cancelled.is_set():
                raise PermissionError("source operation withdrawn")

        source.checkpoint = checkpoint
        task = asyncio.create_task(asyncio.to_thread(
            getattr(source, operation), *copy.deepcopy(args), **copy.deepcopy(kwargs),
        ))
        self._tasks[task] = cancelled
        task.add_done_callback(self._completed)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    def _completed(self, task):
        self._tasks.pop(task, None)
        if task.cancelled():
            self._lost_workers.add(task)
            self.stop_admission()
        else:
            error = task.exception()
            if isinstance(error, OwnedCleanupError):
                self._cleanup_errors.append(error)
                self.stop_admission()

    @property
    def cleanup_errors(self):
        """Original retained process/Job custody for the existing cleanup owner."""
        return tuple(self._cleanup_errors)

    def stop_admission(self):
        self._closing.set()

    async def drain(self, *, timeout):
        import math
        self._owner()
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("finite drain timeout required")
        self.stop_admission()
        tasks = set(self._tasks)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            if pending:
                raise TimeoutError("source operations not drained; custody retained")
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._cleanup_errors:
            raise self._cleanup_errors[0]
        if self._lost_workers:
            raise RuntimeError("source execution completion unknown; custody retained")

    def status(self):
        return {"admitting": not self._closing.is_set(), "pending": len(self._tasks),
                "cleanup_unconfirmed": bool(self._cleanup_errors or self._lost_workers),
                "drained": self._closing.is_set() and not (
                    self._tasks or self._cleanup_errors or self._lost_workers)}

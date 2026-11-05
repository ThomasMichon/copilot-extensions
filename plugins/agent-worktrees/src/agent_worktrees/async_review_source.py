"""Optional execution/drain adapter under an already selected source lifetime."""

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
import math
import inspect
import os
import threading
import time
import weakref

from agent_procutil.owned_process import OwnedCleanupError

from .pr_review_source import LocalReviewSource, SourceIncomplete
from ._review_source_operation import SourceOperation


class AsyncReviewSource:
    """The embedding owner must drain this adapter before releasing its source."""

    def __init__(self, source, *, executor):
        if not isinstance(source, LocalReviewSource) or not isinstance(executor, ThreadPoolExecutor):
            raise ValueError("selected source and existing caller-owned thread executor required")
        with source._async_binding_lock:
            if source._async_bound:
                raise ValueError("selected source already has an async owner")
            if source._sync_active:
                raise ValueError("selected source has active synchronous work")
            source._async_bound = True
        self.source = source
        self._scope_keys = tuple(source.scope)
        self._executor = executor
        self._pid = os.getpid()
        self._loop = None
        self._closing = threading.Event()
        self._lock = threading.RLock()
        self._work = set()
        self._bridges = {}
        self._operations = {}
        self._cleanup_errors = []
        self._terminal_drain = False
        self._closed_coroutines = weakref.WeakSet()

    def contract(self):
        return self.source.contract()

    def read_contract(self):
        return self.source.read_contract()

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
        if operation == "inspect_and_retain":
            raise SourceIncomplete("async_inspection_requires_immutable_generation")
        async with self.operation(kwargs["budget"]) as admitted:
            return await admitted.execute(operation, *args, **kwargs)

    async def attach(self, generation, *, budget, build, timeout_result, **limits):
        if type(generation) is not bytes:
            raise SourceIncomplete("async_inspection_requires_immutable_generation")
        if not callable(build) or not isinstance(timeout_result, dict):
            raise ValueError("existing attachment preparation required")
        async with self.operation(budget) as admitted:
            return await admitted.attach(generation, build=build, timeout_result=timeout_result, **limits)

    @asynccontextmanager
    async def operation(self, budget):
        self._owner()
        if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
            raise ValueError("finite source operation budget required")
        admitted = SourceOperation(self, time.monotonic() + budget)
        completion = admitted.completion
        with self._lock:
            self._settle_completed_locked()
            if self._closing.is_set() or self._cleanup_errors:
                raise RuntimeError("source admission stopped")
            if self._work:
                raise SourceIncomplete("source_execution_busy")
            self._work.add(completion)
            self._operations[completion] = admitted
        completion.add_done_callback(self._completed)
        try:
            yield admitted
        except BaseException as error:
            admitted.withdraw()
            admitted.end(error)
            raise
        else:
            admitted.end()

    def _completed(self, _completion):
        with self._lock:
            self._settle_completed_locked()

    def _retain_rejected_task(self, task):
        """An already-started rejected client must settle before custody releases."""
        if not isinstance(task, asyncio.Task):
            self._unconfirmed_client_future(task)
            return
        completion = Future()
        completion.set_running_or_notify_cancel()
        completion.producer = task
        with self._lock:
            self._work.add(completion)
            if self._terminal_drain:
                self._cleanup_errors.append(SourceIncomplete("client_work_after_terminal_drain"))
        completion.add_done_callback(self._completed)
        def settled(future):
            error = None if future.cancelled() else future.exception()
            if error is None and not future.cancelled():
                self._retain_unconfirmed_result(future.result())
            if error is None:
                completion.set_result(None)
            else:
                completion.set_exception(error)
            del completion.producer
        task.add_done_callback(settled)
        cancelling = getattr(task, "cancelling", None)
        if not task.done() and callable(cancelling) and not cancelling():
            task.cancel()

    def _retain_native_future(self, future):
        with self._lock:
            if future in self._work:
                return
            if self._terminal_drain:
                error = SourceIncomplete("native_work_after_terminal_drain")
                error.future, error.observed = future, False
                self._cleanup_errors.append(error)
            self._work.add(future)
        future.add_done_callback(self._completed)

    def _unconfirmed_client_future(self, future):
        self.stop_admission()
        with self._lock:
            existing = next((item for item in self._cleanup_errors
                             if getattr(item, "future", None) is future), None)
            if existing is not None:
                return existing
            error = SourceIncomplete("client_future_native_settlement_unconfirmed")
            error.future, error.observed = future, False
            self._cleanup_errors.append(error)
        if not isinstance(future, (Future, asyncio.Future)):
            return error
        if future.done():
            self._observe_unconfirmed(error)
            return error
        def register():
            try:
                future.add_done_callback(lambda item: self._observe_unconfirmed(error))
            except RuntimeError:
                self._observe_unconfirmed(error)
        if isinstance(future, Future):
            register()
        elif future.get_loop() is self._loop and not self._loop.is_closed():
            try:
                if asyncio.get_running_loop() is self._loop:
                    register()
                else:
                    self._loop.call_soon_threadsafe(register)
            except RuntimeError:
                if not self._loop.is_closed() and self._loop.is_running():
                    try:
                        self._loop.call_soon_threadsafe(register)
                    except RuntimeError:
                        self._observe_unconfirmed(error)
        return error

    def _observe_unconfirmed(self, error):
        future = error.future
        with self._lock:
            if error.observed or not isinstance(future, (Future, asyncio.Future)) or not future.done():
                return
            error.observed = True
        failure = None if future.cancelled() else future.exception()
        if isinstance(failure, OwnedCleanupError):
            with self._lock:
                if not any(item is failure for item in self._cleanup_errors):
                    self._cleanup_errors.append(failure)
        elif failure is None and not future.cancelled():
            value = future.result()
            self._retain_unconfirmed_result(value)

    def _retain_unconfirmed_result(self, value):
        seen = {}
        while isinstance(value, Future):
            if id(value) in seen:
                self._unconfirmed_client_future(value)
                return
            seen[id(value)] = value
            with self._lock:
                if value in self._work:
                    return
            if value.done():
                error = None if value.cancelled() else value.exception()
                if isinstance(error, OwnedCleanupError):
                    with self._lock:
                        self._cleanup_errors.append(error)
                    self.stop_admission()
                elif error is None and not value.cancelled():
                    value = value.result()
                    continue
                return
            self._retain_native_future(value)
            return
        if inspect.iscoroutine(value):
            if value in self._closed_coroutines:
                return
            unstarted = inspect.getcoroutinestate(value) == inspect.CORO_CREATED
            if unstarted:
                self._closed_coroutines.add(value)
                value.close()
                return
        elif not (inspect.isawaitable(value) or isinstance(value, Future)):
            return
        self._unconfirmed_client_future(value)

    def _settle_completed_locked(self):
        for completion in tuple(self._work):
            if completion.done():
                error = None if completion.cancelled() else completion.exception()
                if error is None and not completion.cancelled():
                    self._retain_unconfirmed_result(completion.result())
                self._work.discard(completion)
                self._bridges.pop(completion, None)
                self._operations.pop(completion, None)
                if isinstance(error, OwnedCleanupError):
                    if not any(item is error for item in self._cleanup_errors):
                        self._cleanup_errors.append(error)
                    self.stop_admission()

    @property
    def cleanup_errors(self):
        """Original cleanup handles and unsupported producers retained for the owner."""
        with self._lock:
            self._settle_completed_locked()
            for error in tuple(self._cleanup_errors):
                if hasattr(error, "future") and hasattr(error, "observed"):
                    self._observe_unconfirmed(error)
            return tuple(self._cleanup_errors)

    def stop_admission(self):
        self._closing.set()

    def _bridge(self, completion):
        with self._lock:
            bridge = self._bridges.get(completion)
            if bridge is None:
                bridge = asyncio.wrap_future(completion)
                bridge.add_done_callback(lambda future: None if future.cancelled() else future.exception())
                if not completion.done():
                    self._bridges[completion] = bridge
            return bridge

    async def drain(self, *, timeout):
        self._owner()
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("finite drain timeout required")
        self.stop_admission()
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._settle_completed_locked()
                work = tuple(self._work)
                operations = tuple(self._operations.values())
                if not work:
                    if not self._cleanup_errors:
                        self._terminal_drain = True
                    break
            for operation in operations:
                operation.withdraw()
            for completion in work:
                completion.cancel()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("source operations not drained; custody retained")
            bridges = {self._bridge(completion) for completion in work}
            _, pending = await asyncio.wait(bridges, timeout=remaining)
            if pending:
                raise TimeoutError("source operations not drained; custody retained")
            await asyncio.gather(*bridges, return_exceptions=True)
        errors = self.cleanup_errors
        if errors:
            raise errors[0]

    def status(self):
        with self._lock:
            self._settle_completed_locked()
            return {"admitting": not self._closing.is_set(), "pending": len(self._work),
                    "cleanup_unconfirmed": bool(self._cleanup_errors),
                    "drained": self._terminal_drain and not (self._work or self._cleanup_errors)}

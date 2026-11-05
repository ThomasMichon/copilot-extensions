"""One operation within the existing selected-source lifetime."""

import asyncio
from concurrent.futures import Future
import contextvars
import copy
import inspect
import threading
import time

from agent_procutil.owned_process import OwnedCleanupError

from .pr_review_source import SourceIncomplete


_REVISION_KEYS = ("head_sha", "common_sha", "iteration", "iteration_head_sha", "iteration_target_sha", "target_sha")


def _pin(mapping, keys):
    if type(mapping) is not dict or len(mapping) > len(keys):
        raise SourceIncomplete("invalid_source_selection")
    if any(key not in keys or type(value) not in (str, int, float, bool, type(None))
           for key, value in mapping.items()):
        raise SourceIncomplete("invalid_source_selection")
    return tuple((key, mapping[key]) for key in keys if key in mapping)


class _ClientTask(asyncio.Task):
    """Track our own cancellation requests on every supported Python runtime."""
    def __init__(self, coroutine, *, loop):
        self._cancellation_requested = False
        super().__init__(coroutine, loop=loop)

    def cancel(self, *args, **kwargs):
        accepted = super().cancel(*args, **kwargs)
        self._cancellation_requested |= accepted
        return accepted

    def cancelling(self):
        native = getattr(super(), "cancelling", None)
        return native() if callable(native) else int(self._cancellation_requested)


class SourceOperation:
    def __init__(self, owner, deadline):
        self.owner, self.deadline = owner, deadline
        self.completion = Future()
        self.completion.set_running_or_notify_cancel()
        self.cancelled = threading.Event()
        self._lock = threading.RLock()
        self._native = {}
        self._tasks = set()
        self._withdrawn_tasks = set()
        self._ended = False
        self._error = None

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise SourceIncomplete("deadline_exhausted")
        if self.owner._closing.is_set() or self.cancelled.is_set():
            errors = self.owner.cleanup_errors
            if errors:
                raise errors[0]
            raise PermissionError("source operation withdrawn")
        return remaining

    def checkpoint(self):
        self.remaining()
        self.owner.source.checkpoint()
        self.remaining()

    def _record(self, error):
        if isinstance(error, OwnedCleanupError) or self._error is None:
            self._error = error
        if isinstance(error, OwnedCleanupError):
            with self.owner._lock:
                if not any(item is error for item in self.owner._cleanup_errors):
                    self.owner._cleanup_errors.append(error)
            self.owner.stop_admission()

    def _settle(self):
        if self._ended and not self._native and not self._tasks and not self.completion.done():
            if self._error is None:
                self.completion.set_result(None)
            else:
                self.completion.set_exception(self._error)

    def withdraw(self):
        self.cancelled.set()
        with self._lock:
            native = tuple(self._native.values())
            tasks = tuple(self._tasks)
        for future in native:
            if future is not None:
                future.cancel()
                self._settle_returned_native(future)
        for task in tasks:
            self._cancel_task(task)

    def _cancel_task(self, task):
        with self._lock:
            if task in self._withdrawn_tasks or task.done():
                return
            self._withdrawn_tasks.add(task)
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        cancelling = getattr(task, "cancelling", None)
        if task is not current and callable(cancelling) and not cancelling():
            task.cancel()

    def end(self, error=None):
        with self._lock:
            if error is not None:
                self._record(error)
            self._ended = True
            for future in tuple(self._native):
                self._settle_returned_native(future)
            self._settle()

    def _retain_result_future(self, future):
        with self._lock:
            if future in self._native:
                return
            self._native[future] = future
        future.add_done_callback(self._settle_returned_native)

    def _settle_returned_native(self, future):
        with self._lock:
            if self._native.get(future) is not future or not future.done():
                return
            error = None if future.cancelled() else future.exception()
            value = None if future.cancelled() or error is not None else future.result()
            producer = inspect.isawaitable(value) or isinstance(value, Future)
            if producer and not (self.cancelled.is_set() or self._ended or self.owner._closing.is_set()):
                return
            self._native.pop(future)
            if not future.cancelled():
                if error is not None:
                    self._record(error)
                elif producer:
                    self.owner._retain_unconfirmed_result(value)
            self._settle()

    async def _wait(self, observed):
        observed.add_done_callback(lambda future: None if future.cancelled() else future.exception())
        try:
            if observed.done():
                result = observed.result()
                self._accept(result)
                return result
            try:
                remaining = self.remaining()
            except BaseException:
                self.withdraw()
                raise
            done, _ = await asyncio.wait({observed}, timeout=remaining)
            if not done:
                self.withdraw()
                raise SourceIncomplete("deadline_exhausted")
            result = observed.result()
            self._accept(result)
            return result
        except asyncio.CancelledError:
            self.withdraw()
            if observed.done() and not observed.cancelled() and observed.exception() is None:
                self.owner._retain_unconfirmed_result(observed.result())
            raise

    def _accept(self, result):
        try:
            self.remaining()
            if isinstance(result, asyncio.Task) and result.get_loop() is self.owner._loop:
                self._track_task(result)
            elif isinstance(result, Future):
                self._retain_result_future(result)
        except BaseException:
            if inspect.iscoroutine(result):
                self.owner._retain_unconfirmed_result(result)
            elif isinstance(result, Future):
                self.owner._retain_native_future(result)
                result.cancel()
            elif asyncio.isfuture(result):
                self.owner._retain_rejected_task(result)
            self.withdraw()
            raise

    async def wait(self, awaitable):
        """Client IO stays on the caller's original loop and remains accounted."""
        if isinstance(awaitable, Future):
            if self._ended or self.completion.done():
                self.owner._retain_native_future(awaitable)
                self.remaining()
                raise SourceIncomplete("source_operation_ended")
            self._retain_result_future(awaitable)
            try:
                self.remaining()
                if self._ended:
                    raise SourceIncomplete("source_operation_ended")
                result = await self._wait(asyncio.wrap_future(awaitable))
                with self._lock:
                    self._native.pop(awaitable, None)
                    self._settle()
                return result
            except BaseException:
                awaitable.cancel()
                self._settle_returned_native(awaitable)
                raise
        if asyncio.isfuture(awaitable) and not isinstance(awaitable, asyncio.Task):
            raise self.owner._unconfirmed_client_future(awaitable)
        if isinstance(awaitable, asyncio.Task) and awaitable.get_loop() is not self.owner._loop:
            raise self.owner._unconfirmed_client_future(awaitable)
        if not (inspect.iscoroutine(awaitable) or isinstance(awaitable, asyncio.Task)):
            raise self.owner._unconfirmed_client_future(awaitable)
        try:
            self.remaining()
        except BaseException:
            if inspect.iscoroutine(awaitable):
                self.owner._retain_unconfirmed_result(awaitable)
            elif asyncio.isfuture(awaitable):
                self.owner._retain_rejected_task(awaitable)
            raise
        with self._lock:
            if self._ended or self.completion.done():
                if inspect.iscoroutine(awaitable):
                    self.owner._retain_unconfirmed_result(awaitable)
                elif asyncio.isfuture(awaitable):
                    self.owner._retain_rejected_task(awaitable)
                raise SourceIncomplete("source_operation_ended")
            if inspect.iscoroutine(awaitable):
                async def guarded():
                    try:
                        self.remaining()
                    except BaseException:
                        self.owner._retain_unconfirmed_result(awaitable)
                        raise
                    return await awaitable
                task = _ClientTask(guarded(), loop=self.owner._loop)
                task.add_done_callback(lambda _: self.owner._retain_unconfirmed_result(
                    awaitable) if inspect.getcoroutinestate(awaitable) == inspect.CORO_CREATED else None)
            else:
                task = asyncio.ensure_future(awaitable)
            self._track_task(task)
        return await self._wait(task)

    def _track_task(self, task):
        with self._lock:
            if task in self._tasks:
                return
            self._tasks.add(task)
        def completed(future):
            with self._lock:
                self._tasks.discard(future)
                self._withdrawn_tasks.discard(future)
                if not future.cancelled():
                    error = future.exception()
                    if error is not None:
                        self._record(error)
                    else:
                        value = future.result()
                        if self.cancelled.is_set() or self._ended or self.owner._closing.is_set():
                            self.owner._retain_unconfirmed_result(value)
                        elif inspect.iscoroutine(value):
                            pass  # The active waiter accepts it before guarded execution.
                        elif isinstance(value, asyncio.Task) and value.get_loop() is self.owner._loop:
                            if value is future:
                                self.owner._unconfirmed_client_future(value)
                            else:
                                self._track_task(value)
                        elif isinstance(value, Future):
                            self._retain_result_future(value)
                        elif inspect.isawaitable(value):
                            self.owner._unconfirmed_client_future(value)
                self._settle()

        task.add_done_callback(completed)

    async def call(self, function, *args, **kwargs):
        """Register client custody before invoking a possibly task-starting client."""
        async def invoke():
            self.remaining()
            result = function(*args, **kwargs)
            seen = {}
            while inspect.isawaitable(result) or isinstance(result, Future):
                if id(result) in seen:
                    self.withdraw()
                    raise SourceIncomplete("cyclic_client_result")
                seen[id(result)] = result
                result = await self.wait(result)
            return result
        return await self.wait(invoke())

    async def prepare(self, function, *args, **kwargs):
        """CPU/storage preparation uses the same admitted native executor."""
        return await self._run(lambda source, *a, **k: function(*a, **k), args, kwargs)

    def _submit_native(self, function, *args):
        self.remaining()
        with self._lock:
            if self._ended:
                raise SourceIncomplete("source_operation_ended")
            context = contextvars.copy_context()
            def work():
                self.checkpoint()
                value = function(*args)
                self.checkpoint()
                return value
            future = self.owner._executor.submit(context.run, work)
            self.owner._retain_native_future(future)
            return future

    async def execute(self, operation, *args, **kwargs):
        if operation == "inspect_and_retain":
            raise SourceIncomplete("async_inspection_requires_immutable_generation")
        if operation != "read_retained":
            raise ValueError("unsupported source operation")
        pin = None
        if operation == "read_retained":
            manifest = args[0] if args else kwargs.get("manifest")
            if (
                not isinstance(manifest, dict) or not isinstance(manifest.get("scope"), dict)
                or not isinstance(manifest.get("revision"), dict)
            ):
                raise SourceIncomplete("invalid_source_read_selection")
            if type(manifest.get("id")) is not str or len(manifest["id"]) != 64:
                raise SourceIncomplete("invalid_source_read_selection")
            pin = (manifest["id"], _pin(manifest["scope"], self.owner._scope_keys),
                   _pin(manifest["revision"], _REVISION_KEYS))

        def invoke(source, *selected_args, **selected_kwargs):
            selection = selected_args[0] if selected_args else selected_kwargs["manifest"]
            snapshot = (_pin(selection.get("scope"), self.owner._scope_keys),
                        _pin(selection.get("revision"), _REVISION_KEYS))
            if operation == "read_retained":
                snapshot = (selection.get("id"), *snapshot)
            if snapshot != pin:
                raise SourceIncomplete("source_selection_changed")
            selected_kwargs["budget"] = self.remaining()
            return getattr(source, operation)(*selected_args, **selected_kwargs)

        return await self._run(invoke, args, kwargs)

    async def attach(self, generation, *, build, timeout_result, **limits):
        if type(generation) is not bytes:
            raise SourceIncomplete("async_inspection_requires_immutable_generation")
        def invoke(source, snapshot):
            return build(snapshot, source, budget=self.remaining(), **limits)
        try:
            return await self._run(invoke, (generation,), {})
        except SourceIncomplete as error:
            if str(error) == "deadline_exhausted":
                return dict(timeout_result)
            raise

    async def _run(self, function, args, kwargs):
        self.remaining()
        step = Future()
        step.set_running_or_notify_cancel()
        with self._lock:
            if self._ended or self.completion.done():
                raise SourceIncomplete("source_operation_ended")
            if self._native:
                raise SourceIncomplete("source_worker_busy")
            self._native[step] = None
        payload = [args, kwargs]
        context = contextvars.copy_context()

        def work():
            self.checkpoint()
            source = copy.copy(self.owner.source)
            source.checkpoint = self.checkpoint
            source._execution_deadline = self.deadline
            source.inspect_and_retain = source._inspect_and_retain_owned
            source.read_retained = source._read_retained_owned
            arguments, options = copy.deepcopy(payload[0]), copy.deepcopy(payload[1])
            self.checkpoint()
            return function(source, *arguments, **options)
        try:
            native = self.owner._executor.submit(context.run, work)
            with self._lock:
                if step in self._native:
                    self._native[step] = native

            def settle_native(future, result=None):
                with self._lock:
                    self._native.pop(step, None)
                    if future.cancelled():
                        payload.clear()
                        if not self.cancelled.is_set():
                            self.owner.stop_admission()
                        error = SourceIncomplete("source_cancelled_before_start")
                    else:
                        error = future.exception()
                    if error is None:
                        if isinstance(result, Future):
                            self._retain_result_future(result)
                            if self.cancelled.is_set() or self.owner._closing.is_set() or time.monotonic() >= self.deadline:
                                result.cancel()
                        elif inspect.iscoroutine(result):
                            if inspect.getcoroutinestate(result) == inspect.CORO_CREATED:
                                self.owner._retain_unconfirmed_result(result)
                            else:
                                error = self.owner._unconfirmed_client_future(result)
                            result = None
                            if error is None and not (
                                self.cancelled.is_set() or self._ended or self.owner._closing.is_set()
                            ):
                                error = SourceIncomplete("worker_coroutine_not_supported")
                        elif inspect.isawaitable(result):
                            error = self.owner._unconfirmed_client_future(result)
                    if error is not None:
                        self._record(error)
                        step.set_exception(error)
                    else:
                        step.set_result(result)
                    self._settle()

            def native_done(future):
                result = None if future.cancelled() or future.exception() is not None else future.result()
                settle_native(future, result)

            native.add_done_callback(native_done)
        except BaseException as error:
            with self._lock:
                self._native.pop(step, None)
                self._record(error)
            step.set_exception(error)
            raise
        return await self._wait(asyncio.wrap_future(step))

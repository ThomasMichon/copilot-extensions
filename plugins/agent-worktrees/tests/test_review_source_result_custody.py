"""Actual owner completion boundaries without backend or runtime effects."""

import asyncio
import threading
import time
import gc
import inspect
import weakref

import pytest

from agent_procutil.owned_process import OwnedCleanupError
from agent_worktrees.async_review_source import AsyncReviewSource
from agent_worktrees.pr_review_source import SourceIncomplete
from test_pr_review_source import prepared as prepared
from test_review_source_owner import execution_pool as execution_pool


@pytest.mark.parametrize("coroutine", [False, True])
def test_active_nested_result_handoff_remains_owned_until_real_completion(prepared, execution_pool, coroutine):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def child():
            entered.set()
            await release.wait()
            return {"nested": True}
        async def parent():
            return child() if coroutine else asyncio.create_task(child())
        async with owner.operation(60) as operation:
            caller = asyncio.create_task(operation.call(parent))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                assert not caller.done() and owner.status()["pending"]
            finally:
                release.set()
            assert await caller == {"nested": True}
        await owner.drain(timeout=2)
        assert owner.status()["drained"] and not owner.cleanup_errors
    asyncio.run(scenario())


def test_unbound_async_inspection_refuses_before_native_admission(prepared, execution_pool, monkeypatch):
    import copy
    source, generation, git, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    request = {"scope": copy.deepcopy(generation["scope"]), "revision": copy.deepcopy(generation["revision"]),
               "changes": copy.deepcopy(generation["sections"]["changes"]["data"])}
    monkeypatch.setattr(execution_pool, "submit", lambda *a, **k: pytest.fail("unbound source queued work"))
    async def scenario():
        with pytest.raises(SourceIncomplete, match="async_inspection_requires_immutable_generation"):
            await owner.execute("inspect_and_retain", request, budget=60)
        assert owner.status()["pending"] == 0
        await owner.drain(timeout=2)
    asyncio.run(scenario())
    assert git("for-each-ref", "--format=%(refname)", "refs/agent-pull-requests/source") == ""


def test_completed_native_result_transfers_to_active_waiter(prepared, execution_pool):
    from concurrent.futures import Future
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    async def scenario():
        async def child():
            await asyncio.sleep(0)
            return {"native_nested": True}
        native = Future()
        native.set_result(child())
        async with owner.operation(60) as operation:
            assert await operation.call(lambda: native) == {"native_nested": True}
        await owner.drain(timeout=2)
        assert owner.status()["drained"] and not owner.cleanup_errors
    asyncio.run(scenario())


def test_native_result_stays_owned_when_cancel_precedes_loop_bridge_delivery(prepared, execution_pool):
    from concurrent.futures import Future
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    release = threading.Event()
    def work():
        assert release.wait(10)
        return {"child": True}
    async def scenario():
        parent = Future()
        parent.set_running_or_notify_cancel()
        child = execution_pool.submit(work)
        try:
            async with owner.operation(60) as operation:
                caller = asyncio.create_task(operation.wait(parent))
                await asyncio.sleep(0)
                caller.cancel()
                parent.set_result(child)
                with pytest.raises(asyncio.CancelledError):
                    await caller
            with pytest.raises(TimeoutError):
                await owner.drain(timeout=.001)
            assert not child.done() and not owner.status()["drained"]
        finally:
            release.set()
        await owner.drain(timeout=2)
        assert child.done() and owner.status()["drained"]
    asyncio.run(scenario())


@pytest.mark.parametrize("scheduled", [False, True])
def test_completed_worker_native_result_is_not_reclaimed_before_active_waiter(prepared, execution_pool, scheduled):
    from concurrent.futures import Future
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def child():
            entered.set()
            await release.wait()
            return {"worker_nested": True}
        async with owner.operation(60) as operation:
            returned = Future()
            returned.set_result(asyncio.create_task(child()) if scheduled else child())
            caller = asyncio.create_task(operation.call(operation.prepare, lambda: returned))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                assert not caller.done() and not owner.cleanup_errors
            finally:
                release.set()
            assert await caller == {"worker_nested": True}
        await owner.drain(timeout=2)
        assert owner.status()["drained"]
    asyncio.run(scenario())


def test_nested_traversal_retains_prior_awaitables_until_consumed(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    async def scenario():
        anchor = None
        async def nested(depth):
            if depth:
                return nested(depth - 1)
            gc.collect()
            assert anchor() is not None
            return {"chain": True}
        initial = [nested(64)]
        anchor = weakref.ref(initial[0])
        async with owner.operation(60) as operation:
            assert await operation.call(initial.pop) == {"chain": True}
        await owner.drain(timeout=2)
    asyncio.run(scenario())


def test_started_worker_coroutine_is_retained_without_driving_its_cleanup(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    async def scenario():
        cleanup_entered, release, cleaned = (asyncio.Event() for _ in range(3))
        async def producer():
            try:
                await asyncio.sleep(0)
            finally:
                cleanup_entered.set()
                await release.wait()
                cleaned.set()
        coroutine = producer()
        coroutine.send(None)
        task = None
        try:
            with pytest.raises(SourceIncomplete, match="native_settlement_unconfirmed"):
                async with owner.operation(60) as operation:
                    await operation.prepare(lambda: coroutine)
            assert inspect.getcoroutinestate(coroutine) == inspect.CORO_SUSPENDED
            assert not cleanup_entered.is_set()
            task = asyncio.create_task(coroutine)
            await asyncio.wait_for(cleanup_entered.wait(), 2)
        finally:
            release.set()
            if task is None:
                task = asyncio.create_task(coroutine)
            await task
        assert cleaned.is_set()
        with pytest.raises(SourceIncomplete, match="native_settlement_unconfirmed"):
            await owner.drain(timeout=2)
    asyncio.run(scenario())


@pytest.mark.parametrize("ended", [False, True])
def test_rejected_suspended_coroutine_retains_cleanup_without_driving_it(prepared, execution_pool, ended):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    async def scenario():
        cleanup_entered, release, cleaned = (asyncio.Event() for _ in range(3))
        async def producer():
            try:
                await asyncio.sleep(0)
            finally:
                cleanup_entered.set()
                await release.wait()
                cleaned.set()
        coroutine = producer()
        coroutine.send(None)
        try:
            async with owner.operation(60) as operation:
                operation.end() if ended else operation.withdraw()
                with pytest.raises(SourceIncomplete if ended else PermissionError):
                    await operation.wait(coroutine)
                assert inspect.getcoroutinestate(coroutine) == inspect.CORO_SUSPENDED
                assert not cleanup_entered.is_set()
            with pytest.raises(SourceIncomplete, match="native_settlement_unconfirmed"):
                await owner.drain(timeout=2)
            assert not owner.status()["drained"]
        finally:
            release.set()
            await asyncio.create_task(coroutine)
        assert cleaned.is_set()
    asyncio.run(scenario())


def test_reentrant_native_completion_at_callback_registration_settles_without_key_error(prepared, execution_pool):
    from concurrent.futures import Future
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    class CompletingFuture(Future):
        def add_done_callback(self, callback):
            self.set_result({"complete": True})
            return super().add_done_callback(callback)
    child, parent = CompletingFuture(), Future()
    parent.set_result(child)
    owner._retain_native_future(parent)
    assert owner.status()["pending"] == 0
    asyncio.run(owner.drain(timeout=2))
    assert owner.status()["drained"]


def test_completed_native_result_cycle_records_custody_without_callback_recursion(prepared, execution_pool):
    from concurrent.futures import Future
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    first, second, returned = Future(), Future(), Future()
    first.set_result(second)
    second.set_result(first)
    returned.set_result(first)
    async def scenario():
        with pytest.raises(SourceIncomplete, match="cyclic_client_result"):
            async with owner.operation(5) as operation:
                await operation.call(operation.prepare, lambda: returned)
        with pytest.raises(SourceIncomplete, match="native_settlement_unconfirmed"):
            await owner.drain(timeout=2)
        assert first.done() and second.done() and not owner.status()["drained"]
    asyncio.run(scenario())


def test_withdrawn_unstarted_coroutine_has_no_unconfirmed_producer(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    async def scenario():
        entered, child_entered = asyncio.Event(), asyncio.Event()
        async def child():
            child_entered.set()
        async def parent():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return child()
        async with owner.operation(60) as operation:
            caller = asyncio.create_task(operation.call(parent))
            await entered.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
        await owner.drain(timeout=2)
        assert not child_entered.is_set() and owner.status()["drained"]
        assert not owner.cleanup_errors
    asyncio.run(scenario())


def test_stopped_admission_does_not_release_before_terminal_drain(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    def work():
        entered.set()
        assert release.wait(10)
    async def scenario():
        async with owner.operation(60) as operation:
            pass
        owner.stop_admission()
        assert not owner.status()["drained"]
        native = execution_pool.submit(work)
        try:
            with pytest.raises(PermissionError):
                await operation.wait(native)
            with pytest.raises(TimeoutError):
                await owner.drain(timeout=.001)
            assert not owner.status()["drained"]
        finally:
            release.set()
        await owner.drain(timeout=2)
        assert owner.status()["drained"]
    asyncio.run(scenario())


def test_late_parent_cannot_start_nested_io_after_deadline(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    async def scenario():
        entered = asyncio.Event()
        async def child():
            entered.set()
        async def parent():
            time.sleep(.03)
            return child()
        with pytest.raises(SourceIncomplete, match="deadline_exhausted"):
            async with owner.operation(.01) as operation:
                await operation.call(parent)
        await owner.drain(timeout=2)
        assert not entered.is_set() and owner.status()["drained"]
    asyncio.run(scenario())


def test_native_metadata_keeps_original_git_limit_meanings(prepared, execution_pool):
    import copy
    source, generation, _, _ = prepared
    request = {"scope": copy.deepcopy(generation["scope"]), "revision": copy.deepcopy(generation["revision"]),
               "changes": copy.deepcopy(generation["sections"]["changes"]["data"]) * 4}
    request["changes"][0]["synthetic_metadata"] = "m" * 16384
    expected = source.inspect_and_retain(request, budget=60, max_paths=16, max_output_bytes=2048)
    assert expected["complete"]
    owner = AsyncReviewSource(source, executor=execution_pool)
    async def scenario():
        with pytest.raises(SourceIncomplete, match="async_inspection_requires_immutable_generation"):
            await owner.execute("inspect_and_retain", request, budget=60)
        assert not owner.status()["pending"]
        await owner.drain(timeout=2)
    asyncio.run(scenario())


def test_immutable_generation_is_not_changed_by_later_raw_metadata_mutation(prepared, execution_pool):
    import copy
    from agent_pull_requests.workspace_operations import encoded
    from agent_pull_requests.source_attachment import attach_source_async
    source, generation, git, _ = prepared
    data = encoded(generation)
    expected = copy.deepcopy(generation["sections"]["changes"]["data"])
    owner = AsyncReviewSource(source, executor=execution_pool)
    release = threading.Event()
    def occupied():
        assert release.wait(10)
    async def scenario():
        busy = asyncio.get_running_loop().run_in_executor(execution_pool, occupied)
        caller = asyncio.create_task(attach_source_async(data, owner, selection=generation, budget=60))
        try:
            await asyncio.sleep(0)
            generation["sections"]["changes"]["data"][-1]["changeTrackingId"] = 99999
            release.set()
            attachment = await caller
            assert attachment["state"] == "ready"
            assert attachment["source"]["native_changes"] == expected
        finally:
            release.set()
            await busy
        await owner.drain(timeout=2)
    asyncio.run(scenario())
    assert git("for-each-ref", "--format=%(refname)", "refs/agent-pull-requests/source")


def test_late_native_registration_cannot_erase_terminal_drain_violation(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        assert release.wait(10)
        return {"late": True}
    async def scenario():
        async with owner.operation(60) as operation:
            pass
        await owner.drain(timeout=2)
        assert owner.status()["drained"]
        native = execution_pool.submit(blocked)
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            with pytest.raises(SourceIncomplete, match="native_work_after_terminal_drain"):
                await operation.wait(native)
        finally:
            release.set()
            native.result(timeout=2)
        with pytest.raises(SourceIncomplete, match="native_work_after_terminal_drain"):
            await owner.drain(timeout=2)
        assert not owner.status()["drained"]
    asyncio.run(scenario())


def test_repeated_drain_reuses_bridge_without_accumulating_native_callbacks(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        assert release.wait(10)
    async def scenario():
        async def calling():
            async with owner.operation(60) as operation:
                await operation.prepare(blocked)
        caller = asyncio.create_task(calling())
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            completion = next(iter(owner._work))
            bridge, callbacks = None, None
            for _ in range(3):
                with pytest.raises(TimeoutError):
                    await owner.drain(timeout=.001)
                if bridge is None:
                    bridge = owner._bridges[completion]
                    callbacks = len(completion._done_callbacks)
                assert owner._bridges[completion] is bridge
                assert len(completion._done_callbacks) == callbacks
        finally:
            release.set()
        await owner.drain(timeout=2)
        assert not owner._bridges and owner.status()["drained"]
    asyncio.run(scenario())


@pytest.mark.parametrize("completed", [False, True])
def test_unconfirmed_completion_observes_nested_original_cleanup(prepared, execution_pool, completed):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    parent_release = threading.Event()
    failure = OwnedCleanupError(object())
    async def scenario():
        child_release = asyncio.Event()
        async def child():
            await child_release.wait()
            raise failure
        task = asyncio.create_task(child())
        def parent():
            assert parent_release.wait(10)
            return task
        native = execution_pool.submit(parent)
        try:
            if completed:
                parent_release.set()
                native.result(timeout=2)
                owner._retain_unconfirmed_result(native)
            else:
                async with owner.operation(60) as operation:
                    with pytest.raises(SourceIncomplete, match="native_settlement_unconfirmed"):
                        await operation.wait(asyncio.wrap_future(native))
                parent_release.set()
            for _ in range(1000):
                if any(getattr(item, "future", None) is task for item in owner.cleanup_errors):
                    break
                await asyncio.sleep(.001)
            assert any(getattr(item, "future", None) is task for item in owner.cleanup_errors)
        finally:
            parent_release.set()
            child_release.set()
            native.result(timeout=2)
            await asyncio.gather(task, return_exceptions=True)
        assert any(item is failure for item in owner.cleanup_errors)
        assert not owner.status()["drained"]
    asyncio.run(scenario())


def test_unconfirmed_observation_is_cycle_safe(prepared, execution_pool):
    from concurrent.futures import Future
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    first, second = Future(), Future()
    first.set_result(second)
    second.set_result(first)
    owner._unconfirmed_client_future(first)
    assert len(owner.cleanup_errors) == 2
    assert {id(item.future) for item in owner.cleanup_errors} == {id(first), id(second)}


def test_closed_loop_failed_task_preserves_original_cleanup_without_callback_delivery(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    failure = OwnedCleanupError(object())
    loop = asyncio.new_event_loop()
    loop.set_debug(True)
    async def scenario():
        async with owner.operation(60):
            pass
        async def failed():
            raise failure
        task = asyncio.create_task(failed())
        with pytest.raises(OwnedCleanupError):
            await task
        return task
    try:
        task = loop.run_until_complete(scenario())
        loop.close()
        execution_pool.submit(owner._unconfirmed_client_future, task).result(timeout=2)
        assert any(item is failure for item in owner.cleanup_errors)
        assert not owner.status()["drained"]
    finally:
        loop.close()


def test_custom_awaitable_cannot_hide_a_running_native_future(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        assert release.wait(10)

    async def scenario():
        native = execution_pool.submit(blocked)
        invoked = []
        class Opaque:
            def __await__(self):
                invoked.append(True)
                return asyncio.wrap_future(native).__await__()
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            async with owner.operation(60) as operation:
                with pytest.raises(SourceIncomplete, match="native_settlement_unconfirmed"):
                    await operation.wait(Opaque())
            with pytest.raises(SourceIncomplete, match="native_settlement_unconfirmed"):
                await owner.drain(timeout=2)
            assert not native.done() and not invoked and not owner.status()["drained"]
        finally:
            release.set()
            native.result(timeout=2)
    asyncio.run(scenario())


@pytest.mark.parametrize("closed_before", [False, True])
def test_worker_coroutine_settles_without_stopped_loop_callback_delivery(prepared, execution_pool, closed_before):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    loop = asyncio.new_event_loop()
    async def unused():
        pytest.fail("withdrawn worker coroutine started")
    result = unused()
    def delayed():
        entered.set()
        assert release.wait(10)
        return result
    async def scenario():
        async def calling():
            async with owner.operation(60) as operation:
                await operation.prepare(delayed)
        caller = asyncio.create_task(calling())
        for _ in range(1000):
            if entered.is_set():
                break
            await asyncio.sleep(.001)
        assert entered.is_set()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
    try:
        loop.run_until_complete(scenario())
        if closed_before:
            loop.close()
        release.set()
        execution_pool.submit(lambda: None).result(timeout=2)
        loop.close()
        assert inspect.getcoroutinestate(result) == inspect.CORO_CLOSED
        assert owner.status()["pending"] == 0
    finally:
        release.set()
        loop.close()
        result.close()


def test_rejected_task_has_strong_custody_until_its_callback_settles(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    async def scenario():
        entered, cleanup_entered, cleaned = (asyncio.Event() for _ in range(3))
        cleanup = []
        async def client():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                pending = asyncio.get_running_loop().create_future()
                cleanup.append(weakref.ref(pending))
                cleanup_entered.set()
                await pending
                cleaned.set()
        task = asyncio.create_task(client())
        await entered.wait()
        task.cancel()
        await cleanup_entered.wait()
        reference = weakref.ref(task)
        async with owner.operation(60) as operation:
            operation.deadline = time.monotonic() - 1
            with pytest.raises(SourceIncomplete, match="deadline_exhausted"):
                await operation.wait(task)
        del task
        gc.collect()
        try:
            assert reference() is not None
            with pytest.raises(TimeoutError):
                await owner.drain(timeout=.001)
        finally:
            if cleanup[0]() is not None:
                cleanup[0]().set_result(None)
        await owner.drain(timeout=2)
        assert cleaned.is_set() and owner.status()["drained"]
    asyncio.run(scenario())


@pytest.mark.parametrize("ready", [False, True])
def test_preparation_native_future_handoff_preserves_known_tracking(prepared, execution_pool, ready):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    def produce():
        entered.set()
        assert release.wait(10)
        return {"prepared": True}
    existing = execution_pool.submit(lambda: {"prepared": True}) if ready else None
    if existing is not None:
        existing.result(timeout=2)
    async def scenario():
        async with owner.operation(60) as operation:
            task = asyncio.create_task(operation.call(operation.prepare, lambda: (
                existing if ready else execution_pool.submit(produce))))
            try:
                if not ready:
                    for _ in range(1000):
                        if entered.is_set():
                            break
                        await asyncio.sleep(.001)
                    assert entered.is_set() and not owner.cleanup_errors
                release.set()
                assert await task == {"prepared": True}
            finally:
                release.set()
        await owner.drain(timeout=2)
        assert not owner.cleanup_errors and owner.status()["drained"]
    asyncio.run(scenario())


def test_owned_client_deadline_cancels_once_and_retains_async_cleanup(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)

    async def scenario():
        entered, release, cleaned = (asyncio.Event() for _ in range(3))
        async def materialize():
            try:
                await asyncio.Event().wait()
            finally:
                entered.set()
                await release.wait()
                cleaned.set()
        try:
            with pytest.raises(SourceIncomplete, match="deadline_exhausted"):
                async with owner.operation(.02) as operation:
                    await operation.call(materialize)
            await asyncio.wait_for(entered.wait(), 2)
            with pytest.raises(TimeoutError):
                await owner.drain(timeout=.001)
            assert not cleaned.is_set()
        finally:
            release.set()
        await owner.drain(timeout=2)
        assert cleaned.is_set() and owner.status()["drained"]

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["client", "rejected", "native"])
def test_cancelled_parent_returned_producer_never_proves_drain(prepared, execution_pool, boundary):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    thread_entered, thread_release = threading.Event(), threading.Event()
    failure = OwnedCleanupError(object())

    async def scenario():
        parent_entered, parent_cleanup, parent_release, child_release = (
            asyncio.Event() for _ in range(4)
        )
        async def child():
            await child_release.wait()
            raise failure
        child_task = asyncio.create_task(child())
        async def parent():
            try:
                parent_entered.set()
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                parent_cleanup.set()
                await parent_release.wait()
                return child_task
        def native_parent():
            thread_entered.set()
            assert thread_release.wait(10)
            return child_task

        original = None
        try:
            if boundary == "rejected":
                original = asyncio.create_task(parent())
                await parent_entered.wait()
                original.cancel()
                await parent_cleanup.wait()
                async with owner.operation(60) as operation:
                    operation.deadline = time.monotonic() - 1
                    with pytest.raises(SourceIncomplete, match="deadline_exhausted"):
                        await operation.wait(original)
                parent_release.set()
                await original
            else:
                async with owner.operation(60) as operation:
                    if boundary == "client":
                        caller = asyncio.create_task(operation.wait(parent()))
                        await parent_entered.wait()
                    else:
                        original = execution_pool.submit(native_parent)
                        caller = asyncio.create_task(operation.call(lambda: original))
                        for _ in range(1000):
                            if thread_entered.is_set() and original in operation._native:
                                break
                            await asyncio.sleep(.001)
                        assert thread_entered.is_set()
                    caller.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await caller
                parent_release.set()
                thread_release.set()
                if boundary == "native":
                    original.result(timeout=2)
            for _ in range(1000):
                if owner.cleanup_errors:
                    break
                await asyncio.sleep(.001)
            with pytest.raises(SourceIncomplete, match="native_settlement_unconfirmed"):
                await owner.drain(timeout=2)
            assert not child_task.done() and not owner.status()["drained"]
        finally:
            parent_release.set()
            thread_release.set()
            child_release.set()
            await asyncio.gather(child_task, return_exceptions=True)
            if original is not None and boundary == "rejected":
                await asyncio.gather(original, return_exceptions=True)
        await asyncio.sleep(0)
        assert any(item is failure for item in owner.cleanup_errors)
        assert any(getattr(item, "future", None) is child_task for item in owner.cleanup_errors)
        assert not owner.status()["drained"]

    asyncio.run(scenario())

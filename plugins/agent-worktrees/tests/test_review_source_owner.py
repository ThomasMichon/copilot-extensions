"""Synthetic selected repositories through the actual Git/source/lifetime owners."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from agent_worktrees.async_review_source import AsyncReviewSource
from agent_worktrees.pr_review_source import LocalReviewSource, SourceIncomplete
from test_pr_review_source import prepared as prepared


@pytest.fixture
def execution_pool():
    with ThreadPoolExecutor(max_workers=1) as pool:
        yield pool


async def inspect_generation(owner, generation, **limits):
    from agent_pull_requests.source_attachment import attach_source_async
    from agent_pull_requests.workspace_operations import encoded
    value = await attach_source_async(encoded(generation), owner, selection=generation, **limits)
    if value["state"] == "incomplete" and value["errors"] == ["deadline_exhausted"]:
        raise SourceIncomplete("deadline_exhausted")
    return value["source"]


def test_async_real_inspection_and_retained_read_under_existing_owner(prepared, monkeypatch, execution_pool):
    from agent_worktrees import pr_review_source
    source, generation, _, _ = prepared
    from agent_pull_requests.source_attachment import attach_source_async
    owner = AsyncReviewSource(source, executor=execution_pool)
    budget = threading.local()
    original_git = pr_review_source._owned_git

    def observe_git(root, checkpoint, deadline, *args, **kwargs):
        assert deadline <= budget.deadline
        return original_git(root, checkpoint, deadline, *args, **kwargs)

    def observe_operation(method):
        def execute(selected, *args, **kwargs):
            budget.deadline = selected._execution_deadline
            time.sleep(.01)  # Existing owner setup must not renew the child deadline.
            return method(selected, *args, **kwargs)
        return execute

    monkeypatch.setattr(pr_review_source, "_owned_git", observe_git)
    for name in ("_inspect_and_retain_owned", "_read_retained_owned"):
        monkeypatch.setattr(LocalReviewSource, name, observe_operation(getattr(LocalReviewSource, name)))

    async def scenario():
        from agent_pull_requests.workspace_operations import encoded
        attachment = await attach_source_async(encoded(generation), owner, selection=generation, budget=60)
        assert attachment["state"] == "ready"
        value = await owner.execute("read_retained", attachment["source"], "/edit.txt", "after", budget=60)
        assert value["content"] == "after\r\n"
        await owner.drain(timeout=2)
        assert owner.status() == {"admitting": False, "pending": 0, "drained": True, "cleanup_unconfirmed": False}
        with pytest.raises(RuntimeError, match="admission stopped"):
            await owner.execute("read_retained", attachment["source"], "/edit.txt", "after", budget=60)

    asyncio.run(scenario())


def test_original_process_cleanup_failure_blocks_drain_and_retains_native_custody(prepared, monkeypatch, execution_pool):
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_worktrees import pr_review_source
    source, generation, _, _ = prepared
    native_custody = object()
    failure = OwnedCleanupError(native_custody)

    def incomplete(*args, **kwargs):
        raise failure

    monkeypatch.setattr(pr_review_source, "_owned_git", incomplete)
    owner = AsyncReviewSource(source, executor=execution_pool)
    {"scope": generation["scope"], "revision": generation["revision"],
               "changes": generation["sections"]["changes"]["data"]}

    async def scenario():
        with pytest.raises(OwnedCleanupError) as observed:
            await inspect_generation(owner, generation, budget=60)
        assert observed.value is failure
        with pytest.raises(OwnedCleanupError):
            await owner.drain(timeout=2)
        assert owner.cleanup_errors == (failure,)
        assert owner.cleanup_errors[0].process is native_custody
        assert owner.status()["cleanup_unconfirmed"] is True
        assert owner.status()["drained"] is False

    asyncio.run(scenario())


def test_async_admission_deadline_includes_existing_executor_wait_and_busy_has_no_queue(prepared, monkeypatch, execution_pool):
    from agent_worktrees import pr_review_source
    source, generation, _, _ = prepared
    {"scope": generation["scope"], "revision": generation["revision"],
               "changes": generation["sections"]["changes"]["data"]}
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(pr_review_source, "_owned_git", lambda *a, **k: pytest.fail("expired request launched Git"))
    owner = AsyncReviewSource(source, executor=execution_pool)

    def occupied():
        entered.set()
        assert release.wait(10)

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(execution_pool)
        existing = loop.run_in_executor(None, occupied)
        task = None
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            task = asyncio.create_task(inspect_generation(owner, generation, budget=.02))
            await asyncio.sleep(0)
            assert owner.status()["pending"] == 1
            with pytest.raises(SourceIncomplete, match="source_execution_busy"):
                await inspect_generation(owner, generation, budget=60)
            with pytest.raises(SourceIncomplete, match="deadline_exhausted"):
                await asyncio.wait_for(asyncio.shield(task), timeout=.1)
            assert owner.status()["pending"] == 0
            assert owner.status()["admitting"] is True
            await owner.drain(timeout=2)
            assert owner.status()["drained"] is True
        finally:
            release.set()
            await existing
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_second_adapter_cannot_escape_selected_source_admission_or_drain(prepared, execution_pool):
    source, generation, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    original = source.checkpoint

    def blocked():
        entered.set()
        assert release.wait(10)
        original()

    source.checkpoint = blocked
    {"scope": generation["scope"], "revision": generation["revision"],
               "changes": generation["sections"]["changes"]["data"]}

    async def scenario():
        task = asyncio.create_task(inspect_generation(owner, generation, budget=60))
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            with pytest.raises(ValueError, match="already has an async owner"):
                AsyncReviewSource(source, executor=execution_pool)
            with pytest.raises(TimeoutError):
                await owner.drain(timeout=.001)
            assert owner.status()["pending"] == 1
        finally:
            release.set()
            with pytest.raises(PermissionError):
                await task
            await owner.drain(timeout=2)

    asyncio.run(scenario())


def test_absolute_source_budget_refuses_expiry_during_native_preparation(prepared, monkeypatch):
    from agent_procutil import _caller_owned, owned_process
    from agent_worktrees import pr_review_source
    from agent_worktrees.git_repository_environment import repository_identity_env
    source, _, _, _ = prepared
    original = _caller_owned._validate

    def delayed(request):
        time.sleep(.04)
        return original(request)

    monkeypatch.setattr(_caller_owned, "_validate", delayed)
    monkeypatch.setattr(owned_process, "spawn_sync_in_kill_on_close_job",
                        lambda *a, **k: pytest.fail("native launch attempted after expired preparation"))
    with pytest.raises(SourceIncomplete, match="git_io_budget_exhausted"):
        pr_review_source._owned_git(source.root, source.checkpoint, time.monotonic() + .02,
                                   repository_identity_env(), 128, ("status",))


def test_async_binding_refuses_active_sync_work_then_fences_public_sync_access(prepared, monkeypatch, execution_pool):
    from agent_worktrees import pr_review_source
    source, generation, _, _ = prepared
    entered, release = threading.Event(), threading.Event()
    original = pr_review_source._owned_git
    request = {"scope": generation["scope"], "revision": generation["revision"],
               "changes": generation["sections"]["changes"]["data"]}

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(pr_review_source, "_owned_git", blocked)
    native = execution_pool.submit(source.inspect_and_retain, request, budget=60)
    try:
        assert entered.wait(2)
        with pytest.raises(ValueError, match="active synchronous work"):
            AsyncReviewSource(source, executor=execution_pool)
    finally:
        release.set()
    manifest = native.result(timeout=30)
    assert manifest["complete"] is True
    owner = AsyncReviewSource(source, executor=execution_pool)
    with pytest.raises(SourceIncomplete, match="async_lifetime_owned"):
        source.read_retained(manifest, "/edit.txt", "after", budget=60)
    with pytest.raises(SourceIncomplete, match="async_lifetime_owned"):
        source.inspect_and_retain(request, budget=60)

    async def scenario():
        result = await owner.execute("read_retained", manifest, "/edit.txt", "after", budget=60)
        assert result["content"] == "after\r\n"
        await owner.drain(timeout=2)

    asyncio.run(scenario())


def test_worker_cleanup_custody_survives_cancelled_wrapper_and_loop_shutdown(prepared, monkeypatch, execution_pool):
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_worktrees import pr_review_source
    source, generation, _, _ = prepared
    {"scope": generation["scope"], "revision": generation["revision"],
               "changes": generation["sections"]["changes"]["data"]}
    entered, release, terminal = threading.Event(), threading.Event(), threading.Event()
    custody = object()
    failure = OwnedCleanupError(custody)

    def native_work(*args, **kwargs):
        entered.set()
        try:
            if not release.wait(10):
                raise RuntimeError("fixture failed to release native work")
            raise failure
        finally:
            terminal.set()

    monkeypatch.setattr(pr_review_source, "_owned_git", native_work)
    owner = AsyncReviewSource(source, executor=execution_pool)

    async def scenario():
        asyncio.get_running_loop().set_default_executor(execution_pool)
        task = asyncio.create_task(inspect_generation(owner, generation, budget=60))
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert owner.status()["pending"] == 1
        finally:
            # asyncio.run shuts down its EXISTING executor after cancelling tasks.
            asyncio.get_running_loop().call_later(.01, release.set)

    asyncio.run(scenario())
    assert terminal.is_set()
    assert owner.cleanup_errors == (failure,)
    assert owner.cleanup_errors[0].process is custody
    assert owner.status()["cleanup_unconfirmed"] is True
    assert owner.status()["drained"] is False


def test_native_executor_cancellation_before_launch_settles_without_source_io(prepared, execution_pool):
    source, generation, _, _ = prepared
    entered, release = threading.Event(), threading.Event()
    owner = AsyncReviewSource(source, executor=execution_pool)
    source.checkpoint = lambda: pytest.fail("unstarted owner checkpoint executed")
    {"scope": generation["scope"], "revision": generation["revision"],
               "changes": generation["sections"]["changes"]["data"]}

    def occupied():
        entered.set()
        assert release.wait(10)

    async def scenario():
        existing = asyncio.get_running_loop().run_in_executor(execution_pool, occupied)
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            task = asyncio.create_task(inspect_generation(owner, generation, budget=60))
            await asyncio.sleep(0)
            execution_pool.shutdown(wait=False, cancel_futures=True)
            with pytest.raises(SourceIncomplete, match="cancelled_before_start"):
                await asyncio.wait_for(task, timeout=2)
            await owner.drain(timeout=2)
            assert owner.status()["drained"] is True
        finally:
            release.set()
            await existing

    asyncio.run(scenario())


def test_blocking_owner_checkpoint_does_not_block_loop_or_cancellation(prepared, execution_pool):
    source, generation, _, _ = prepared
    entered, release = threading.Event(), threading.Event()
    owner = AsyncReviewSource(source, executor=execution_pool)
    original = source.checkpoint

    def checkpoint():
        entered.set()
        assert release.wait(10)
        original()

    source.checkpoint = checkpoint
    {"scope": generation["scope"], "revision": generation["revision"],
               "changes": generation["sections"]["changes"]["data"]}

    async def scenario():
        task = asyncio.create_task(inspect_generation(owner, generation, budget=60))
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            await asyncio.wait_for(asyncio.sleep(.001), timeout=.1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=.1)
            with pytest.raises(TimeoutError):
                await owner.drain(timeout=.001)
            assert owner.status()["pending"] == 1
        finally:
            release.set()
            await owner.drain(timeout=2)
        assert owner.status()["drained"] is True

    asyncio.run(scenario())


def test_drain_observes_native_cleanup_failure_before_completion_callback_runs(prepared, monkeypatch, execution_pool):
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_worktrees import pr_review_source
    source, generation, _, _ = prepared
    failure = OwnedCleanupError(object())
    entered, release = threading.Event(), threading.Event()
    native_entered, native_release = threading.Event(), threading.Event()
    owner = AsyncReviewSource(source, executor=execution_pool)
    original_completed = owner._completed

    def delayed_completed(completion):
        entered.set()
        assert release.wait(10)
        original_completed(completion)

    def incomplete(*args, **kwargs):
        native_entered.set()
        assert native_release.wait(10)
        raise failure

    monkeypatch.setattr(owner, "_completed", delayed_completed)
    monkeypatch.setattr(pr_review_source, "_owned_git", incomplete)
    {"scope": generation["scope"], "revision": generation["revision"],
               "changes": generation["sections"]["changes"]["data"]}

    async def scenario():
        task = asyncio.create_task(inspect_generation(owner, generation, budget=60))
        try:
            for _ in range(1000):
                if native_entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert native_entered.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            native_release.set()
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            with pytest.raises(OwnedCleanupError):
                await owner.drain(timeout=2)
            assert owner.cleanup_errors == (failure,)
            assert owner.status()["drained"] is False
        finally:
            native_release.set()
            release.set()

    asyncio.run(scenario())


def test_git_bounded_capture_preserves_binary_stdin_at_actual_caller_boundary(prepared):
    import hashlib
    from agent_procutil.owned_process import run_caller_owned
    from agent_worktrees.git_repository_environment import repository_identity_env
    source, _, _, _ = prepared
    body = b"\0\xff\r\n\x80\rbyte-exact"
    expected = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest().encode()
    result = run_caller_owned(
        ["git", "-c", "core.fsmonitor=false", "hash-object", "--stdin"],
        cwd=source.root, env=repository_identity_env(), timeout=15,
        input_bytes=body, binary=True, max_stdout_bytes=128, max_stderr_bytes=8192,
    )
    assert result.stdout.strip() == expected


def test_native_executor_tail_is_retained_and_late_cleanup_failure_is_not_lost(prepared, monkeypatch):
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_worktrees import pr_review_source
    source, generation, _, _ = prepared
    entered, release = threading.Event(), threading.Event()
    failure = OwnedCleanupError(object())

    class Executor(ThreadPoolExecutor):
        def submit(self, function, *args, **kwargs):
            def wrapped():
                function(*args, **kwargs)
                entered.set()
                assert release.wait(10)
                raise failure
            return super().submit(wrapped)

    monkeypatch.setattr(pr_review_source, "_owned_git", lambda *a, **k: (_ for _ in ()).throw(
        SourceIncomplete("synthetic incomplete source")))
    {"scope": generation["scope"], "revision": generation["revision"],
               "changes": generation["sections"]["changes"]["data"]}
    with Executor(max_workers=1) as pool:
        owner = AsyncReviewSource(source, executor=pool)

        async def scenario():
            task = asyncio.create_task(inspect_generation(owner, generation, budget=60))
            try:
                for _ in range(1000):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.001)
                assert entered.is_set()
                with pytest.raises(TimeoutError):
                    await owner.drain(timeout=.001)
                assert owner.status()["pending"] == 1
                assert owner.status()["drained"] is False
            finally:
                release.set()
                with pytest.raises(OwnedCleanupError):
                    await task
                with pytest.raises(OwnedCleanupError):
                    await owner.drain(timeout=2)
            assert owner.cleanup_errors == (failure,)

        asyncio.run(scenario())


def test_expiry_between_submit_and_wait_cancels_the_actual_queued_job(prepared, execution_pool, monkeypatch):
    source, _, _, _ = prepared
    entered, release = threading.Event(), threading.Event()
    owner = AsyncReviewSource(source, executor=execution_pool)
    original_submit = execution_pool.submit

    def occupied():
        entered.set()
        assert release.wait(10)

    async def scenario():
        existing = asyncio.get_running_loop().run_in_executor(execution_pool, occupied)
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            async with owner.operation(60) as operation:
                def submit(*args, **kwargs):
                    future = original_submit(*args, **kwargs)
                    operation.deadline = time.monotonic() - 1
                    return future
                monkeypatch.setattr(execution_pool, "submit", submit)
                with pytest.raises(SourceIncomplete, match="deadline_exhausted"):
                    await operation.prepare(lambda: pytest.fail("expired queued work ran"))
            await owner.drain(timeout=2)
            assert owner.status()["drained"] is True
        finally:
            release.set()
            await existing

    asyncio.run(scenario())


def test_ended_operation_cannot_register_late_native_or_client_work(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    calls = []

    async def client():
        calls.append("client")

    async def scenario():
        async with owner.operation(60) as operation:
            pass
        with pytest.raises(SourceIncomplete, match="operation_ended"):
            await operation.prepare(lambda: calls.append("native"))
        with pytest.raises(SourceIncomplete, match="operation_ended"):
            await operation.wait(client())
        await owner.drain(timeout=2)
        assert calls == [] and owner.status()["drained"] is True

    asyncio.run(scenario())


def test_nested_client_cancellation_does_not_interrupt_async_cleanup(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()

    def blocked():
        entered.set()
        assert release.wait(10)

    async def scenario():
        cleanup_entered, cleanup_release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async with owner.operation(60) as operation:
            async def client():
                try:
                    await operation.prepare(blocked)
                finally:
                    cleanup_entered.set()
                    await cleanup_release.wait()
                    cleaned.set()
            task = asyncio.create_task(operation.call(client))
            try:
                for _ in range(1000):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.001)
                assert entered.is_set()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await asyncio.wait_for(cleanup_entered.wait(), 2)
                with pytest.raises(TimeoutError):
                    await owner.drain(timeout=.001)
                assert not cleaned.is_set()
            finally:
                release.set()
                cleanup_release.set()
        await owner.drain(timeout=2)
        assert cleaned.is_set() and owner.status()["drained"]

    asyncio.run(scenario())


@pytest.mark.parametrize("legacy", [False, True])
def test_scheduled_cancelling_client_cleanup_survives_nested_invocation(prepared, execution_pool, legacy):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)

    async def scenario():
        entered, cleanup_entered, release, cleaned, invoked = (asyncio.Event() for _ in range(5))
        async def client():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                cleanup_entered.set()
                await release.wait()
                cleaned.set()
        class LegacyTask(asyncio.Task):
            cancelling = None
        client_task = LegacyTask(client()) if legacy else asyncio.create_task(client())
        await entered.wait()
        client_task.cancel()
        await cleanup_entered.wait()
        async with owner.operation(60) as operation:
            def invoke():
                invoked.set()
                return client_task
            caller = asyncio.create_task(operation.call(invoke))
            await invoked.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            try:
                with pytest.raises(TimeoutError):
                    await owner.drain(timeout=.001)
                assert not cleaned.is_set()
            finally:
                release.set()
        await owner.drain(timeout=2)
        assert cleaned.is_set() and owner.status()["drained"]

    asyncio.run(scenario())


@pytest.mark.parametrize("producer", ["worker", "client"])
def test_native_future_returned_by_worker_keeps_actual_producer_and_cleanup_custody(prepared, execution_pool, producer):
    from agent_procutil.owned_process import OwnedCleanupError
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    failure = OwnedCleanupError(object())
    def blocked():
        entered.set()
        assert release.wait(10)
        raise failure

    async def scenario():
        async with owner.operation(60) as operation:
            client_entered = asyncio.Event()
            if producer == "worker":
                native = await operation.prepare(lambda: execution_pool.submit(blocked))
            else:
                native = execution_pool.submit(blocked)
                def client():
                    client_entered.set()
                    return native
                caller = asyncio.create_task(operation.call(client))
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            if producer == "client":
                await asyncio.wait_for(client_entered.wait(), 2)
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller
        try:
            with pytest.raises(TimeoutError):
                await owner.drain(timeout=.001)
            assert not native.done() and not owner.status()["drained"]
        finally:
            release.set()
        with pytest.raises(OwnedCleanupError) as observed:
            await owner.drain(timeout=2)
        assert observed.value is failure and owner.cleanup_errors == (failure,)

    asyncio.run(scenario())


@pytest.mark.parametrize("scheduled", [False, True])
def test_worker_late_awaitable_is_closed_or_retained_before_terminal_drain(prepared, execution_pool, scheduled):
    import inspect
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()

    async def scenario():
        started, cleanup_entered, cleanup_release, cleaned = (asyncio.Event() for _ in range(4))
        async def client():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                cleanup_entered.set()
                await cleanup_release.wait()
                cleaned.set()
        result = asyncio.create_task(client()) if scheduled else client()
        if scheduled:
            await started.wait()
        def delayed():
            entered.set()
            assert release.wait(10)
            return result
        async with owner.operation(60) as operation:
            caller = asyncio.create_task(operation.prepare(delayed))
            try:
                for _ in range(1000):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.001)
                assert entered.is_set()
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller
            finally:
                release.set()
        try:
            if scheduled:
                with pytest.raises(SourceIncomplete, match="native_settlement_unconfirmed"):
                    await owner.drain(timeout=2)
                result.cancel()
                await asyncio.wait_for(cleanup_entered.wait(), 2)
                assert not cleaned.is_set()
        finally:
            cleanup_release.set()
        if scheduled:
            await asyncio.gather(result, return_exceptions=True)
            with pytest.raises(SourceIncomplete, match="native_settlement_unconfirmed"):
                await owner.drain(timeout=2)
            assert cleaned.is_set()
        else:
            await owner.drain(timeout=2)
            assert inspect.getcoroutinestate(result) == inspect.CORO_CLOSED and not started.is_set()
        assert owner.status()["drained"] is not scheduled

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["scope", "revision"])
def test_selection_header_refuses_unbounded_mapping_before_executor_submission(
    prepared, execution_pool, monkeypatch, field,
):
    from agent_pull_requests.workspace_operations import encoded
    from agent_pull_requests.source_attachment import attach_source_async
    source, generation, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    request = dict(generation)
    request[field] = {str(index): "unselected" for index in range(1000)}
    monkeypatch.setattr(execution_pool, "submit", lambda *a, **k: pytest.fail("unbounded header submitted"))
    async def scenario():
        with pytest.raises(ValueError, match="invalid source selection header"):
            await attach_source_async(encoded(generation), owner, selection=request, budget=60)
        await owner.drain(timeout=2)
    asyncio.run(scenario())


def test_untrackable_executor_wrapper_refuses_and_never_proves_native_drain(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        assert release.wait(10)

    async def scenario():
        native = execution_pool.submit(blocked)
        wrapper = asyncio.wrap_future(native)
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            async with owner.operation(60) as operation:
                with pytest.raises(SourceIncomplete, match="native_settlement_unconfirmed"):
                    await operation.wait(wrapper)
            assert not wrapper.cancelled()
            wrapper.cancel()
            with pytest.raises(SourceIncomplete, match="native_settlement_unconfirmed"):
                await owner.drain(timeout=2)
            assert not native.done() and not owner.status()["drained"]
        finally:
            release.set()
            native.result(timeout=2)
        with pytest.raises(SourceIncomplete, match="native_settlement_unconfirmed"):
            await owner.drain(timeout=2)
        assert owner.status()["cleanup_unconfirmed"]

    asyncio.run(scenario())


def test_drain_rechecks_client_cleanup_registered_after_its_initial_snapshot(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)

    async def scenario():
        entered, cleanup_entered, release, cleaned = (asyncio.Event() for _ in range(4))
        async def cleanup():
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_entered.set()
                await release.wait()
                cleaned.set()
        async def calling():
            async with owner.operation(60) as operation:
                async def client():
                    try:
                        entered.set()
                        await asyncio.Event().wait()
                    finally:
                        task = asyncio.create_task(cleanup())
                        await asyncio.sleep(0)
                        with pytest.raises(PermissionError, match="withdrawn"):
                            await operation.wait(task)
                await operation.call(client)
        caller = asyncio.create_task(calling())
        await entered.wait()
        draining = asyncio.create_task(owner.drain(timeout=2))
        try:
            await cleanup_entered.wait()
            with pytest.raises(asyncio.CancelledError):
                await caller
            await asyncio.sleep(0)
            assert not draining.done() and not cleaned.is_set()
        finally:
            release.set()
        await draining
        assert cleaned.is_set() and owner.status()["drained"]

    asyncio.run(scenario())


@pytest.mark.parametrize("ended", [False, True])
def test_rejected_started_client_remains_owned_through_actual_cleanup(prepared, execution_pool, ended):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)

    async def scenario():
        entered, cleanup_entered, cleanup_release, cleaned = (asyncio.Event() for _ in range(4))
        async def client():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                cleanup_entered.set()
                await cleanup_release.wait()
                cleaned.set()
        async with owner.operation(60) as operation:
            task = asyncio.create_task(client())
            await entered.wait()
            if not ended:
                operation.deadline = time.monotonic() - 1
                with pytest.raises(SourceIncomplete, match="deadline_exhausted"):
                    await operation.wait(task)
        if ended:
            with pytest.raises(SourceIncomplete, match="operation_ended"):
                await operation.wait(task)
        await asyncio.wait_for(cleanup_entered.wait(), 2)
        try:
            with pytest.raises(TimeoutError):
                await owner.drain(timeout=.001)
            assert not cleaned.is_set() and not owner.status()["drained"]
        finally:
            cleanup_release.set()
        await owner.drain(timeout=2)
        assert cleaned.is_set() and owner.status()["drained"]

    asyncio.run(scenario())


@pytest.mark.parametrize("completed", [False, True])
def test_successful_result_after_withdrawal_or_expiry_is_not_accepted(prepared, execution_pool, completed):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)

    async def scenario():
        async with owner.operation(60) as operation:
            result = asyncio.get_running_loop().create_future()
            if completed:
                result.set_result({"state": "ready"})
                operation.cancelled.set()
                with pytest.raises(PermissionError, match="withdrawn"):
                    await operation._wait(result)
            else:
                waiting = asyncio.create_task(operation._wait(result))
                await asyncio.sleep(0)
                operation.deadline = time.monotonic() - 1
                result.set_result({"state": "ready"})
                with pytest.raises(SourceIncomplete, match="deadline_exhausted"):
                    await waiting
        await owner.drain(timeout=2)
        assert owner.status()["drained"]

    asyncio.run(scenario())


def test_queued_immutable_inspection_preserves_original_revision(prepared, execution_pool):
    import copy
    source, generation, git, _ = prepared
    original_revision = copy.deepcopy(generation["revision"])
    entered, release = threading.Event(), threading.Event()
    owner = AsyncReviewSource(source, executor=execution_pool)
    def occupied():
        entered.set()
        assert release.wait(10)

    async def scenario():
        existing = asyncio.get_running_loop().run_in_executor(execution_pool, occupied)
        task = None
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            task = asyncio.create_task(inspect_generation(owner, generation, budget=60))
            await asyncio.sleep(0)
            generation["revision"]["head_sha"] = generation["revision"]["common_sha"]
            generation["sections"]["changes"]["data"] = []
            release.set()
            await existing
            result = await task
            assert result["complete"] and result["revision"] == original_revision
            for role in ("head", "common"):
                retained = result["retained_refs"][role]
                assert git("rev-parse", retained["ref"]) == original_revision[role + "_sha"]
            await owner.drain(timeout=2)
        finally:
            release.set()
            await existing
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_async_read_accepts_keywords_and_refuses_malformed_manifest(prepared, execution_pool):
    source, generation, _, _ = prepared
    from agent_pull_requests.source_attachment import attach_source
    manifest = attach_source(generation, source, budget=60)["source"]
    owner = AsyncReviewSource(source, executor=execution_pool)

    async def scenario():
        result = await owner.execute("read_retained", manifest=manifest, path="/edit.txt",
                                     side="after", budget=60)
        assert result["content"] == "after\r\n"
        with pytest.raises(SourceIncomplete, match="invalid_source_read_selection"):
            await owner.execute("read_retained", manifest=None, path="/edit.txt", side="after", budget=60)
        await owner.drain(timeout=2)

    asyncio.run(scenario())


def test_withdrawal_during_argument_snapshot_prevents_preparation_callback(prepared, execution_pool):
    source, _, _, _ = prepared
    owner = AsyncReviewSource(source, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Snapshot:
        def __deepcopy__(self, memo):
            entered.set()
            assert release.wait(10)
            return self

    async def scenario():
        async with owner.operation(60) as operation:
            task = asyncio.create_task(operation.prepare(lambda value: calls.append("called"), Snapshot()))
            try:
                for _ in range(1000):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.001)
                assert entered.is_set()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                with pytest.raises(TimeoutError):
                    await owner.drain(timeout=.001)
            finally:
                release.set()
        await owner.drain(timeout=2)
        assert calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [True, False])
def test_async_cancellation_or_owner_loss_retains_worker_until_terminal_drain(prepared, monkeypatch, cancel, execution_pool):
    import gc
    from agent_worktrees import pr_review_source
    source, generation, _, _ = prepared
    {"scope": generation["scope"], "revision": generation["revision"],
               "changes": generation["sections"]["changes"]["data"]}
    entered, release, denied = threading.Event(), threading.Event(), threading.Event()
    original = pr_review_source._owned_git

    def delayed(*args, **kwargs):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("fixture failed to release owned Git")
        return original(*args, **kwargs)

    def checkpoint():
        if denied.is_set():
            raise PermissionError("selected owner lost")

    source.checkpoint = checkpoint
    monkeypatch.setattr(pr_review_source, "_owned_git", delayed)
    owner = AsyncReviewSource(source, executor=execution_pool)

    async def scenario():
        unhandled = []
        asyncio.get_running_loop().set_exception_handler(lambda loop, context: unhandled.append(context))
        task = asyncio.create_task(inspect_generation(owner, generation, budget=60))
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                denied.set()
            with pytest.raises(TimeoutError, match="custody retained"):
                await owner.drain(timeout=.001)
            assert owner.status()["pending"] == 1
            assert owner.status()["drained"] is False
            joining = asyncio.create_task(owner.drain(timeout=5))
            await asyncio.sleep(0)
            joining.cancel()
            with pytest.raises(asyncio.CancelledError):
                await joining
            assert owner.status()["pending"] == 1
        finally:
            release.set()
            if not cancel:
                with pytest.raises(PermissionError):
                    await task
            await owner.drain(timeout=5)
        assert owner.status()["drained"] is True
        await asyncio.sleep(0)
        gc.collect()
        assert not unhandled

    asyncio.run(scenario())

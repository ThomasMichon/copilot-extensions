"""Cooperative source deadlines through the actual existing client frontdoor."""

import asyncio
import time
import threading

import pytest

from agent_pull_requests.workspace_tools import WorkspaceTools
from test_workspace_tools import environment as environment
from test_workspace_tools import integration as integration
from test_workspace_tools import tools as tools
from test_workspace_tools import execution_pool as execution_pool

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


def test_native_lookup_forwards_deadline_through_blocked_owner_checkpoint(
    environment, integration, tools, execution_pool, monkeypatch,
):
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_worktrees.pr_review_source import SourceIncomplete
    native_host, _, _ = tools
    _, _, initial, _, _, _ = environment
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    entered, release = threading.Event(), threading.Event()
    reads = []
    original_checkpoint = native_host.client.checkpoint
    original_read = native_host.client.operations._materialized
    def blocked():
        entered.set()
        assert release.wait(10)
        original_checkpoint()
    def materialized(*args, **kwargs):
        reads.append(True)
        return original_read(*args, **kwargs)
    async def scenario():
        loaded = await native_host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        monkeypatch.setattr(native_host.client, "checkpoint", blocked)
        monkeypatch.setattr(native_host.client.operations, "_materialized", materialized)
        async with owner.operation(60) as operation:
            task = asyncio.create_task(operation.prepare(lambda: native_host.client.materialize(
                42, loaded["result"]["descriptor"]["id"], checkpoint=operation.remaining)))
            try:
                for _ in range(1000):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.001)
                assert entered.is_set()
                operation.deadline = time.monotonic() - 1
                release.set()
                with pytest.raises(SourceIncomplete, match="deadline_exhausted"):
                    await task
                assert reads == []
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        await owner.drain(timeout=2)
    asyncio.run(scenario())


def test_unqualified_scheduled_delegate_refuses_before_read_admission(
    environment, integration, tools, execution_pool, monkeypatch,
):
    from agent_worktrees.async_review_source import AsyncReviewSource
    from agent_pull_requests.workspace_tools import WorkspaceToolUnavailable
    _, client, _, _, _, _ = integration
    native_host, _, _ = tools
    _, _, initial, _, _, _ = environment
    owner = AsyncReviewSource(native_host.source_owner, executor=execution_pool)
    class Delegate:
        preparation_contract = staticmethod(client.preparation_contract)
        def ensure_capabilities(self, **kwargs):
            return asyncio.create_task(client.ensure_capabilities(**kwargs))
        def exchange(self, *args, **kwargs):
            return asyncio.create_task(client.exchange(*args, **kwargs))
        def materialize(self, *args, **kwargs):
            return asyncio.create_task(client.materialize(*args, **kwargs))
    host = WorkspaceTools(Delegate(), source_owner=owner)
    chunks = []
    original = client._caller.request
    async def request(method, path, **kwargs):
        if path.endswith("/chunk"):
            chunks.append(path)
        return await original(method, path, **kwargs)
    monkeypatch.setattr(client._caller, "request", request)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def admission():
            entered.set()
            await release.wait()
        client._read_admission = admission
        loaded = await native_host.invoke("load", {
            "number": 42, "expected_current": initial["id"], "budget_seconds": 60,
        })
        with pytest.raises(WorkspaceToolUnavailable, match="preparation_binding_unsupported"):
            await host.invoke("source", {
                "number": 42, "generation_id": loaded["result"]["descriptor"]["id"], "budget_seconds": 60,
            })
        assert chunks == [] and not entered.is_set() and owner.status()["pending"] == 0
        release.set()
        await owner.drain(timeout=2)
        assert owner.status()["drained"]
    asyncio.run(scenario())

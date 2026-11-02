"""Far-side startup admission outlives provider enumeration and native tails."""

import asyncio
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest
from agent_procutil._daemon_instance_identity import identity_state, stop_identity
from agent_procutil.daemon_instances import inspect_instances

from agent_bridge.agent_registry import AgentConfig, AgentResolver, CliNamespaceResolver
from agent_bridge.session_host.agent_runner import run_agent_session_host


async def wait_for(predicate, *, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["cancel", "repeated-cancel", "sample-cancel", "success"])
async def test_provider_native_work_retires_before_host_admission(
    tmp_path, monkeypatch, ending,
):
    from agent_procutil import _daemon_instance_store as store

    registry = tmp_path / "registry"
    monkeypatch.setenv(store.REGISTRY_ENV, str(registry))
    monkeypatch.delenv(store.INSTANCE_ENV, raising=False)
    monkeypatch.delenv(store.TEST_CONTEXT_ENV, raising=False)
    ready, release = tmp_path / "ready.json", tmp_path / "release"
    command = """
import json,os,sys,time
from pathlib import Path
from agent_procutil._daemon_instance_identity import birth_identity
root=Path(sys.argv[1])
(root/'ready.json').write_text(json.dumps({'pid':os.getpid(),'birth':birth_identity(os.getpid())}))
while not (root/'release').exists(): time.sleep(.01)
print('[]',flush=True)
"""
    sample_entered, sample_release, sample_retired = (
        threading.Event(), threading.Event(), threading.Event()
    )

    def sample():
        sample_entered.set()
        try:
            assert sample_release.wait(10)
        finally:
            sample_retired.set()

    executor = ThreadPoolExecutor(max_workers=1)
    native_sample = executor.submit(sample)
    assert sample_entered.wait(5)

    class Namespace(CliNamespaceResolver):
        @property
        def pending_cpu_sample_future(self):
            return native_sample

    namespace = Namespace("fixture", "fixture-provider", command=[
        sys.executable, "-B", "-c", command, str(tmp_path),
    ])
    resolver = AgentResolver({"fixture": AgentConfig(name="fixture", cwd=str(tmp_path))}, {})
    monkeypatch.setattr(resolver, "refresh_provider_resolvers", lambda: None)
    resolver.register_namespace_resolver(namespace)
    hosting = AsyncMock()
    monkeypatch.setattr("agent_bridge.session_host.launcher.run_host", hosting)
    monkeypatch.setattr(
        "agent_bridge.hosting_policy.require_target_hosting", AsyncMock(),
    )
    monkeypatch.setattr(
        "agent_bridge.transport.resolve_local_launch",
        AsyncMock(return_value=(["synthetic-host"], str(tmp_path), {})),
    )
    task = asyncio.create_task(run_agent_session_host("fixture", resolver=resolver))
    child = None

    def record_state():
        rows = inspect_instances(registry)
        assert len(rows) == 1
        return rows[0]["state"]

    def child_ready():
        nonlocal child
        try:
            child = json.loads(ready.read_text())
            return True
        except (OSError, ValueError):
            return False

    try:
        await wait_for(child_ready)
        owner = next((
            pending for pending in asyncio.all_tasks()
            if pending.get_name() == "session-host-agent-startup"
        ), task)
        enumeration = namespace.pending_list_task
        assert enumeration is not None and not enumeration.done()
        assert record_state() == "running"
        if ending in {"cancel", "repeated-cancel"}:
            task.cancel("original cancellation")
            await asyncio.wait({task}, timeout=0.05)
            assert (task.done(), enumeration.done(), record_state()) == (
                False, False, "running",
            )
            assert identity_state(child["pid"], child["birth"]) == "matching"
        release.write_text("complete")
        await wait_for(lambda: enumeration.done())
        await wait_for(owner.done)
        assert identity_state(child["pid"], child["birth"]) == "dead"
        # The native sample remains owned after enumeration drops its task.
        resolver.unregister_namespace_resolver("fixture")
        await asyncio.wait({task}, timeout=0.05)
        assert (task.done(), native_sample.done(), record_state()) == (
            False, False, "running",
        )
        if ending in {"repeated-cancel", "sample-cancel"}:
            for _ in range(3):
                task.cancel("later cancellation")
                await asyncio.sleep(0)
            assert not task.done() and record_state() == "running"
        sample_release.set()
        if ending == "success":
            await asyncio.wait_for(asyncio.shield(task), 5)
            hosting.assert_awaited_once()
        else:
            done, _ = await asyncio.wait({task}, timeout=5)
            assert task in done
            with pytest.raises(asyncio.CancelledError) as error:
                task.result()
            if ending in {"cancel", "repeated-cancel"}:
                assert error.value.args == ("original cancellation",)
        assert native_sample.done() and sample_retired.is_set()
        assert record_state() == "finished"
        if ending in {"cancel", "repeated-cancel"}:
            hosting.assert_not_awaited()
    finally:
        release.write_text("complete")
        sample_release.set()
        await asyncio.gather(task, return_exceptions=True)
        if namespace.pending_list_task is not None:
            await asyncio.gather(namespace.pending_list_task, return_exceptions=True)
        executor.shutdown(wait=True)
        if child is not None and identity_state(child["pid"], child["birth"]) != "dead":
            assert stop_identity(child["pid"], child["birth"])


@pytest.mark.asyncio
@pytest.mark.parametrize("resolver_failure", [False, True])
async def test_cancelled_startup_joins_original_native_resolution_without_launch(
    tmp_path, monkeypatch, resolver_failure,
):
    from agent_procutil import _daemon_instance_store as store

    from agent_bridge.transport import SpawnTarget

    registry = tmp_path / "registry"
    monkeypatch.setenv(store.REGISTRY_ENV, str(registry))
    monkeypatch.delenv(store.INSTANCE_ENV, raising=False)
    monkeypatch.delenv(store.TEST_CONTEXT_ENV, raising=False)
    entered, release, retired = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def resolve():
        calls.append("resolve")
        entered.set()
        try:
            assert release.wait(10)
        finally:
            retired.set()

    class Resolver:
        async def resolve_async(self, name):
            await asyncio.to_thread(resolve)
            if resolver_failure:
                raise ValueError("late resolution error")
            return SpawnTarget(type="local", cwd=str(tmp_path))

    hosting = AsyncMock()
    monkeypatch.setattr("agent_bridge.session_host.launcher.run_host", hosting)
    monkeypatch.setattr(
        "agent_bridge.hosting_policy.require_target_hosting", AsyncMock(),
    )
    monkeypatch.setattr(
        "agent_bridge.transport.resolve_local_launch",
        AsyncMock(return_value=(["synthetic-host"], str(tmp_path), {})),
    )
    task = asyncio.create_task(run_agent_session_host("fixture", resolver=Resolver()))
    try:
        await wait_for(entered.is_set)
        task.cancel("original cancellation")
        for _ in range(3):
            await asyncio.sleep(0.02)
            task.cancel("later cancellation")
        assert not task.done() and not retired.is_set()
        assert [row["state"] for row in inspect_instances(registry)] == ["running"]
        release.set()
        done, _ = await asyncio.wait({task}, timeout=5)
        assert task in done
        with pytest.raises(asyncio.CancelledError) as error:
            task.result()
        assert error.value.args == ("original cancellation",)
        assert retired.is_set() and calls == ["resolve"]
        assert [row["state"] for row in inspect_instances(registry)] == ["finished"]
        hosting.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


def test_cancelled_startup_owner_retains_unknown_admission(tmp_path):
    from pathlib import Path

    from agent_procutil.owned_process import run_caller_owned

    script = """
import asyncio,json,os,sys,threading
from pathlib import Path
repo,root=map(Path,sys.argv[1:])
sys.path[:0]=[str(repo/'plugins/agent-bridge/src'),str(repo/'libs/agent-procutil/src')]
from agent_bridge.session_host.agent_runner import run_agent_session_host
from agent_procutil.daemon_instances import inspect_instances
os.environ['COPILOT_EXTENSIONS_DAEMON_REGISTRY']=str(root/'registry')
os.environ.pop('COPILOT_EXTENSIONS_DAEMON_INSTANCE',None)
os.environ.pop('COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT',None)
entered,release,retired=threading.Event(),threading.Event(),threading.Event()
def native():
    entered.set()
    assert release.wait(5)
    retired.set()
class Resolver:
    async def resolve_async(self,name):
        await asyncio.to_thread(native)
async def scenario():
    outer=asyncio.create_task(run_agent_session_host('fixture',resolver=Resolver()))
    while not entered.is_set():await asyncio.sleep(.01)
    owner=next(task for task in asyncio.all_tasks()
               if task.get_name()=='session-host-agent-startup')
    owner.cancel()
    await asyncio.sleep(.05)
    assert owner.cancelled() and not outer.done()
    assert [row['state'] for row in inspect_instances(root/'registry')]==['running']
    release.set()
    while not retired.is_set():await asyncio.sleep(.01)
    asyncio.get_running_loop()._default_executor.shutdown(wait=True)
    await asyncio.sleep(.05)
    assert not outer.done()
    assert [row['state'] for row in inspect_instances(root/'registry')]==['running']
    print(json.dumps({'unknown_held':True,'native_retired':True}),flush=True)
    os._exit(0)
asyncio.run(scenario())
"""
    repo = Path(__file__).resolve().parents[3]
    result = run_caller_owned(
        [sys.executable, "-I", "-B", "-c", script, str(repo), str(tmp_path)],
        timeout=15, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"unknown_held": True, "native_retired": True}
    assert "startup completion unknown; retaining admission" in result.stderr

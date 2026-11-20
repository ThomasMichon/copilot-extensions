"""Actor-free binding/admission controls; native read and cancellation are opt-in."""

from __future__ import annotations

import importlib.util
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-index/src/agent_index"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    for package in ("agent_index", "agent_index.indexing"):
        parent = ModuleType(package)
        parent.__path__ = []
        monkeypatch.setitem(sys.modules, package, parent)

    def stub(name: str, **attributes: Any) -> None:
        module = ModuleType("agent_index.indexing." + name)
        for key, value in attributes.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, module.__name__, module)

    events: list[str] = []
    state = SimpleNamespace(blocked=False, pending=None, consumer_failure=False)

    class Consumer:
        paused = True
        tasks = object()

        def __init__(self, **_: Any) -> None:
            events.append("consumer")
            if state.consumer_failure:
                raise ValueError("consumer setup")

        async def __aenter__(self) -> Consumer:
            return self

        async def __aexit__(self, *args: Any) -> None:
            events.append("consumer:close")

        def pause(self) -> None:
            events.append("pause")

    async def settle(operation: Any) -> Any:
        return await operation

    stub("point_hints", AcquisitionHints=object, PointChangeHints=object)
    stub("point_queue_consumer", PointQueueConsumer=Consumer)
    stub("task_store", TaskRecord=object, TaskStore=object)
    stub("runner", _settle=settle)
    name = "agent_index.indexing.point_service"
    spec = importlib.util.spec_from_file_location(name, SOURCE / "indexing/point_service.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "require_point_isolation", lambda *_: events.append("isolation"))

    class Future:
        def __init__(self, call: Any, args: Any) -> None:
            self.call, self.args = call, args
            self.ready = not state.blocked
            self.done = False
            self.result = None
            self.callbacks: list[Any] = []
            state.pending = self

        def add_done_callback(self, callback: Any) -> None:
            self.callbacks.append(callback)

        def __await__(self) -> Any:
            while not self.ready:
                yield None
            if not self.done:
                self.result = self.call(*self.args)
                self.done = True
                for callback in self.callbacks:
                    callback(self)
            return self.result

    async def offloop(call: Any, *args: Any, **kwargs: Any) -> Any:
        return call(*args, **kwargs)

    async def gather(*operations: Any, **_: Any) -> None:
        for operation in operations:
            await operation

    monkeypatch.setattr(module, "asyncio", SimpleNamespace(
        to_thread=offloop, create_task=lambda operation: operation, gather=gather,
        get_running_loop=lambda: SimpleNamespace(
            run_in_executor=lambda _, call, *args: Future(call, args),
        ),
    ))
    monkeypatch.setattr(module, "_CleanupTask", lambda operation: operation)
    config = object()
    scope = ("selected", "family", "account", "binding")
    result = object()

    def read(path: str) -> object:
        assert path == "named"
        events.append("read")
        return result

    runtime = module.PointRuntime(config, scope, lambda _: None, read)

    @asynccontextmanager
    async def context(selected: Any = runtime) -> Any:
        events.append("open")
        try:
            yield selected
        finally:
            events.append("close")

    def service(**kwargs: Any) -> Any:
        return module.PointService(config=config, point_scope=scope, **kwargs)

    def finish(operation: Any) -> Any:
        with pytest.raises(StopIteration) as outcome:
            operation.send(None)
        return outcome.value.value

    return SimpleNamespace(
        module=module, state=state, events=events, runtime=runtime, result=result,
        context=context, service=service, finish=finish,
    )


@pytest.mark.parametrize("both", [False, True])
def test_service_requires_one_explicit_context(api: SimpleNamespace, both: bool) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        api.service(**(
            {"executor_context": api.context, "runtime_context": api.context} if both else {}
        ))
    assert api.events == []


@pytest.mark.parametrize("invalid", ["config", "scope", "reader", "shape", "consumer"])
def test_service_partial_binding_never_exposes_reader_and_closes_context(
    api: SimpleNamespace, invalid: str,
) -> None:
    runtime = api.runtime
    if invalid == "config":
        runtime = replace(runtime, config=object())
    elif invalid == "scope":
        runtime = replace(runtime, point_scope=("other",))
    elif invalid == "reader":
        runtime = replace(runtime, read_component=None)
    elif invalid == "shape":
        runtime = object()
    else:
        api.state.consumer_failure = True
    service = api.service(runtime_context=lambda: api.context(runtime))
    with pytest.raises(ValueError):
        api.finish(service.start(legacy_data_dir=Path("legacy")))
    with pytest.raises(RuntimeError, match="not accepting"):
        api.finish(service.read_component("named"))
    assert api.events == ["isolation", "open"] + (
        ["consumer"] if invalid == "consumer" else []
    ) + ["close"]


def test_executor_only_context_never_exposes_qualified_reads(api: SimpleNamespace) -> None:
    service = api.service(executor_context=lambda: api.context(api.runtime.execute))
    api.finish(service.start(legacy_data_dir=Path("legacy")))
    with pytest.raises(RuntimeError, match="not accepting"):
        api.finish(service.read_component("named"))
    api.finish(service.stop())
    assert api.events[-3:] == ["pause", "consumer:close", "close"]


def test_paired_service_reads_paused_and_joins_admitted_read_before_context_close(
    api: SimpleNamespace,
) -> None:
    service = api.service(runtime_context=api.context)
    with pytest.raises(RuntimeError, match="not accepting"):
        api.finish(service.read_component("named"))
    api.finish(service.start(legacy_data_dir=Path("legacy")))
    assert service.status()["paused"] and not service.status()["admitted"]
    assert api.finish(service.read_component("named")) is api.result
    api.state.blocked = True
    reader = service.read_component("named")
    assert reader.send(None) is None
    pending = api.state.pending
    stopping = service.stop()
    assert stopping.send(None) is None
    assert "close" not in api.events and "consumer:close" not in api.events
    with pytest.raises(RuntimeError, match="not accepting"):
        api.finish(service.read_component("named"))
    pending.ready = True
    api.finish(stopping)
    api.finish(reader)
    assert api.events[-3:] == ["read", "consumer:close", "close"]

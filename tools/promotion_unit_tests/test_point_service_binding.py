"""Actor-free binding/admission controls; native read and cancellation are opt-in."""

from __future__ import annotations

import importlib.util
import ast
import hashlib
import json
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
    for package in ("agent_index", "agent_index.indexing", "agent_index.store"):
        parent = ModuleType(package)
        parent.__path__ = []
        monkeypatch.setitem(sys.modules, package, parent)

    def stub(name: str, **attributes: Any) -> None:
        module = ModuleType("agent_index.indexing." + name)
        for key, value in attributes.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, module.__name__, module)

    events: list[str] = []
    state = SimpleNamespace(blocked=False, pending=None, consumer_failure=False, dispatch_failure=False)

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
            self.paused = True
            events.append("pause")

        async def resume(self) -> None:
            self.paused = False

        async def run_pending(self, **_: Any) -> int:
            if state.dispatch_failure:
                self.paused = True
                raise RuntimeError("dispatch failed")
            return 0

        async def drain(self, **_: Any) -> bool:
            self.pause()
            return True

    async def settle(operation: Any) -> Any:
        return await operation

    def load(name: str) -> ModuleType:
        spec = importlib.util.spec_from_file_location(
            "agent_index." + name, SOURCE / (name.replace(".", "/") + ".py"),
        )
        assert spec is not None and spec.loader is not None
        loaded = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, loaded)
        spec.loader.exec_module(loaded)
        return loaded

    load("indexing.change_hints")
    hints = load("indexing.point_hints")
    layout = load("store.source_layout")
    contract = load("indexing.point_service_contract")
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
    scope = ("selected", "family", "account", hints.PointBinding(
        "https://example.test/project", "query", "context-\u00e9",
    ))
    result = object()

    def read(path: str) -> object:
        assert path == "named"
        events.append("read")
        return result

    runtime = module.PointRuntime(config, scope, lambda _: None, read)
    qualified = module._ConfiguredPointRuntime(config, scope, runtime.execute, read)

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
        module=module, state=state, events=events, runtime=runtime, qualified=qualified,
        result=result, layout=layout, contract=contract, load=load,
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
    assert service.contract()["paired"] is False
    assert service.contract()["reason"] == "startup-incomplete"
    assert api.events == ["isolation", "open"] + (
        ["consumer"] if invalid == "consumer" else []
    ) + ["close"]


def test_executor_only_context_never_exposes_qualified_reads(api: SimpleNamespace) -> None:
    service = api.service(executor_context=lambda: api.context(api.runtime.execute))
    api.finish(service.start(legacy_data_dir=Path("legacy")))
    assert service.contract()["reason"] == "executor-only"
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
    assert service.contract()["reason"] == "unqualified-runtime"
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


def test_qualified_service_declaration_tracks_admission_failure_and_drain_rollback(
    api: SimpleNamespace,
) -> None:
    service = api.service(runtime_context=lambda: api.context(api.qualified))
    assert service.contract()["reason"] == "not-started"
    api.finish(service.start(legacy_data_dir=Path("legacy")))
    before = list(api.events)
    paused = service.contract()
    assert paused["paired"] and paused["state"] == "paused"
    assert not paused["execution_admitted"] and paused["read_admitted"]
    api.contract.require_point_service(
        {"point_service_contract": paused}, api.runtime.point_scope, operation="read",
    )
    with pytest.raises(RuntimeError, match="point-service v1"):
        api.contract.require_point_service(
            {"point_service_contract": paused}, api.runtime.point_scope, operation="execute",
        )
    assert api.events == before
    api.finish(service.activate())
    active = service.contract()
    assert active["state"] == "active" and active["execution_admitted"]
    api.contract.require_point_service(
        {"point_service_contract": active}, api.runtime.point_scope, operation="execute",
    )
    assert service.contract(draining=True)["state"] == "draining"
    assert not service.contract(draining=True)["read_admitted"]
    service.pause()
    assert not service.contract()["execution_admitted"]
    assert not service.contract()["read_admitted"]
    # A cached positive observation cannot reopen the effect-side admission.
    with pytest.raises(RuntimeError, match="not accepting"):
        api.finish(service.read_component("named"))
    assert api.finish(service.run_pending()) == 0
    api.finish(service.resume())
    assert service.contract()["execution_admitted"]
    api.state.dispatch_failure = True
    assert api.finish(service.run_pending()) == 0
    failed = service.contract()
    assert failed["state"] == "failed" and failed["failed"]
    assert not failed["execution_admitted"] and failed["read_admitted"]
    service.pause()
    assert service.contract()["state"] == "draining" and service.contract()["failed"]
    api.finish(service.resume())
    assert service.contract()["state"] == "failed"
    assert not service.contract()["execution_admitted"]
    api.finish(service.stop())
    stopped = service.contract()
    assert not stopped["paired"] and stopped["state"] == "stopped" and stopped["failed"]
    assert "scope_fingerprint" not in stopped and not stopped["read_admitted"]


def test_point_scope_fingerprint_uses_every_existing_receipt_field(api: SimpleNamespace) -> None:
    scope = api.runtime.point_scope
    expected = hashlib.sha256(api.layout._point_scope_json(scope).encode("utf-8")).hexdigest()
    assert api.contract.point_scope_fingerprint(scope) == expected
    variants = [
        ("other", *scope[1:]), (scope[0], "other", *scope[2:]),
        (*scope[:2], "other", scope[3]),
    ] + [
        (*scope[:3], replace(scope[3], **{field: "other"}))
        for field in ("endpoint", "query_scope", "query_context")
    ]
    assert all(api.contract.point_scope_fingerprint(other) != expected for other in variants)


def test_point_requirement_rejects_missing_malformed_rollback_and_changed_scope(
    api: SimpleNamespace,
) -> None:
    service = api.service(runtime_context=lambda: api.context(api.qualified))
    api.finish(service.start(legacy_data_dir=Path("legacy")))
    api.finish(service.activate())
    selected = service.contract()
    api.contract.require_point_service(
        {"point_service_contract": selected | {"future": "additive"}},
        api.runtime.point_scope, operation="execute",
    )
    malformed = [
        None, [], {}, selected | {"id": "other"}, selected | {"version": True},
        selected | {"version": 2}, selected | {"paired": 1},
        selected | {"representation_version": True}, selected | {"coverage": "complete"},
        selected | {"representation": "combined"}, selected | {"read_boundary": "http"},
        selected | {"hints_versions": [True]}, selected | {"hints_versions": "2"},
        selected | {"scope_fingerprint": "different"}, selected | {"failed": True},
        selected | {"state": "paused", "execution_admitted": True},
        selected | {"state": "failed", "failed": False, "execution_admitted": False},
        selected | {"execution_admitted": 1}, selected | {"read_admitted": None},
        selected | {"state": "draining"}, selected | {"state": ["active"]},
    ]
    before = list(api.events)
    for contract in malformed:
        with pytest.raises(RuntimeError, match="point-service v1"):
            api.contract.require_point_service(
                {"point_service_contract": contract}, api.runtime.point_scope, operation="execute",
            )
    with pytest.raises(RuntimeError, match="point-service v1"):
        api.contract.require_point_service({}, api.runtime.point_scope, operation="execute")
    with pytest.raises(RuntimeError, match="point-service v1"):
        api.contract.require_point_service(
            {"point_service_contract": selected},
            ("other", *api.runtime.point_scope[1:]), operation="execute",
        )
    assert api.events == before
    api.finish(service.stop())


def test_health_route_serializes_current_selection_without_factory_or_version_inference(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Execute the actual route body with inert app/identity owners; no ASGI thread actors.
    tree = ast.parse((SOURCE / "server.py").read_bytes())
    build = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "build_app")
    route = next(node for node in build.body if isinstance(node, ast.FunctionDef)
                 and node.name == "health")
    route.decorator_list = []
    sources = ModuleType("agent_index.sources")
    sources.__path__ = []
    credentials = ModuleType("agent_index.sources.credentials")
    setattr(credentials, "source_credentials_contract", lambda: {"id": "inert"})
    monkeypatch.setitem(sys.modules, sources.__name__, sources)
    monkeypatch.setitem(sys.modules, credentials.__name__, credentials)
    legacy = api.load("indexing.change_hints_contract")
    engine = ModuleType("agent_index.indexing.engine")

    def execute(**_: Any) -> None:
        pytest.fail("health cannot execute legacy work")

    setattr(execute, "change_hints_versions", (1,))
    setattr(engine, "run_reindex", execute)
    monkeypatch.setitem(sys.modules, engine.__name__, engine)
    namespace: dict[str, Any] = {
        "__package__": "agent_index", "Request": object, "Any": Any,
        "intake_contract": legacy.intake_contract,
        "service_identity": lambda request: {
            "instanceToken": request.app.state.instance_token, "installationId": "fixture",
        },
    }
    exec(compile(ast.Module(body=[route], type_ignores=[]), str(SOURCE / "server.py"), "exec"),
         namespace)
    service = api.service(runtime_context=lambda: api.context(api.qualified))
    state = SimpleNamespace(
        drain_gate=SimpleNamespace(draining=False), promoted=True, passive=False,
        runtime_version="unchanged", instance_token="first",
        task_runner=SimpleNamespace(point_service=service),
        point_service_factory=lambda: pytest.fail("health must not enter the factory"),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    def health() -> dict[str, Any]:
        payload: dict[str, Any] = json.loads(json.dumps(namespace["health"](request)))
        return payload

    assert health()["point_service_contract"]["reason"] == "not-started"
    api.finish(service.start(legacy_data_dir=Path("legacy")))
    before = list(api.events)
    observed = health()
    declaration = observed["point_service_contract"]
    assert declaration["paired"] and declaration["state"] == "paused"
    assert declaration["representation"] == "point-components"
    assert declaration["representation_version"] == 1
    assert declaration["coverage"] == "named-target-only"
    assert declaration["hints_versions"] == [2]
    assert declaration["required_admission_versions"] == [1]
    assert declaration["read_boundary"] == "in-process-only"
    assert observed["indexing_contract"] == legacy.intake_contract()
    assert observed["indexing_contract"]["execution_versions"] == [1]
    assert observed["instanceToken"] == "first"
    assert api.events == before
    state.passive = True
    assert health()["point_service_contract"]["reason"] == "passive"
    state.passive = False
    state.drain_gate.draining = True
    assert not health()["point_service_contract"]["read_admitted"]
    state.drain_gate.draining = False
    # Replacement/rollback discards the old selection, despite equal package versions.
    state.instance_token = "replacement"
    state.task_runner = SimpleNamespace()
    replacement = health()
    assert replacement["instanceToken"] != observed["instanceToken"]
    assert replacement["point_service_contract"]["reason"] == "unselected"
    assert replacement["indexing_contract"] == observed["indexing_contract"]
    with pytest.raises(RuntimeError, match="point-service v1"):
        api.contract.require_point_service(replacement, api.runtime.point_scope, operation="read")
    api.finish(service.stop())

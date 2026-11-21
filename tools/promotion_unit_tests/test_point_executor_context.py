"""Actor-free configured executor ownership; native composition remains opt-in."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-index/src/agent_index"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    for package in ("agent_index", "agent_index.indexing", "agent_index.sources", "agent_index.store"):
        parent = ModuleType(package)
        parent.__path__ = []
        monkeypatch.setitem(sys.modules, package, parent)

    def stub(name: str, **attributes: Any) -> None:
        module = ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)

    def load(name: str) -> ModuleType:
        spec = importlib.util.spec_from_file_location(
            "agent_index." + name, SOURCE / (name.replace(".", "/") + ".py"),
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        return module

    async def settle(operation: Any) -> Any:
        return await operation

    stub("agent_index.indexing.runner", _settle=settle)
    load("indexing.change_hints")
    hints = load("indexing.point_hints")
    runtime = load("indexing.embedding_runtime")
    events: list[str] = []
    binding = hints.PointBinding("https://example.test", "query", "context")
    requested = hints.PointChangeHints(
        source="selected", canonical_family="components", account_id="account",
        binding=binding, targets=(hints.WorkItemRevisionTarget("7", 1, ("event",)),),
    )
    profiles = {key: SimpleNamespace(model_id=key) for key in ("first", "second")}
    state = SimpleNamespace(
        fail_model=None, fail_ensure=False, borrowed=False, wrong_binding=False,
    )

    def require_binding(connector: object, scope: object) -> None:
        assert scope == requested.scope
        if state.wrong_binding:
            raise ValueError("connector binding")

    def execute(connector: object, task_id: str, **kwargs: Any) -> str:
        assert task_id == "task" and kwargs["expected_hints"] is requested
        assert kwargs["point_scope"] == requested.scope
        events.append("execute")
        for model in profiles:
            kwargs["before_model_embed"](model)
            kwargs["before_model_embed"](model)
        return "complete"

    def read(path: str, **kwargs: Any) -> str:
        assert path == "named"
        assert kwargs["point_scope"] == requested.scope
        assert kwargs["config"] is options["config"]
        assert (kwargs["scope"], kwargs["max_rows"], kwargs["legacy_data_dir"]) == (
            options["scope"], options["max_rows"], options["legacy_data_dir"],
        )
        events.append("read")
        return "qualified"

    def paired(config: Any, point_scope: Any, execute: Any, reader: Any) -> SimpleNamespace:
        return SimpleNamespace(
            config=config, point_scope=point_scope, execute=execute, read_component=reader,
        )

    stub("agent_index.indexing.point_service",
         require_point_isolation=lambda *args: events.append("isolation"),
         PointRuntime=object, _ConfiguredPointRuntime=paired)
    stub("agent_index.store.source_layout", _point_scope_json=lambda scope: "scope")
    stub("agent_index.sources.azure_devops_point_runtime",
         _require_connector_binding=require_binding, run_configured_azure_devops_points=execute,
         read_configured_azure_devops_component=read)
    context = load("sources.azure_devops_point_executor")

    async def offloop(call: Any) -> Any:
        return call()

    monkeypatch.setattr(context, "asyncio", SimpleNamespace(
        get_running_loop=lambda: SimpleNamespace(
            run_in_executor=lambda executor, context_run, call: offloop(lambda: context_run(call)),
        ),
    ))

    def create_connector() -> SimpleNamespace:
        events.append("connector")
        def close() -> None:
            events.append("connector:close")

        return SimpleNamespace(_http=SimpleNamespace(
            owns_client=not state.borrowed, client=SimpleNamespace(close=close),
        ))

    def create_client(profile: Any) -> SimpleNamespace:
        model = profile.model_id
        events.append(model + ":open")
        if state.fail_model == model:
            raise RuntimeError("client construction")
        return SimpleNamespace(close=lambda: events.append(model + ":close"))

    def ensure(profile: Any, client: Any) -> bool:
        events.append(profile.model_id + ":ensure")
        if state.fail_ensure and profile.model_id == "second":
            raise RuntimeError("engine readiness")
        return bool(profile.model_id == "first")

    policy = runtime.EmbeddingRuntime(
        create_client, ensure, lambda profile: events.append(profile.model_id + ":stop"),
    )
    options = dict(
        config=SimpleNamespace(model_profiles=profiles), legacy_data_dir=Path("legacy"),
        point_scope=requested.scope, scope="recovery", max_rows=32,
        create_connector=create_connector, embedding_runtime=policy,
    )
    return SimpleNamespace(
        context=context.configured_azure_devops_point_executor,
        paired_context=context.configured_azure_devops_point_runtime,
        options=options, state=state, events=events,
        task=SimpleNamespace(id="task", change_hints=requested),
    )


@pytest.mark.parametrize("work", [False, True])
def test_executor_opens_inertly_and_stops_only_started_engines(
    api: SimpleNamespace, work: bool,
) -> None:
    async def exercise() -> None:
        async with api.context(**api.options) as execute:
            assert api.events == ["isolation", "connector", "first:open", "second:open"]
            with pytest.raises(ValueError, match="exact configured task"):
                execute(SimpleNamespace(change_hints=None))
            if work:
                assert execute(api.task) == "complete"
                assert execute(api.task) == "complete"
                assert api.events.count("first:ensure") == api.events.count("second:ensure") == 2
            else:
                assert not any(event.endswith(":ensure") for event in api.events)
        expected = ["second:close", "first:close", "connector:close"]
        if work:
            expected.insert(0, "first:stop")
        assert api.events[-len(expected):] == expected

    with pytest.raises(StopIteration):
        exercise().send(None)


def test_paired_context_binds_both_owners_without_readiness_on_read(api: SimpleNamespace) -> None:
    async def exercise() -> None:
        async with api.paired_context(**api.options) as runtime:
            assert runtime.config is api.options["config"]
            assert runtime.point_scope == api.options["point_scope"]
            assert runtime.read_component("named") == "qualified"
            assert api.events == ["isolation", "connector", "first:open", "second:open", "read"]
            assert runtime.execute(api.task) == "complete"
        assert api.events[-4:] == [
            "first:stop", "second:close", "first:close", "connector:close",
        ]

    with pytest.raises(StopIteration):
        exercise().send(None)


def test_executor_readiness_failure_preserves_owned_engine_cleanup(api: SimpleNamespace) -> None:
    api.state.fail_ensure = True

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="engine readiness"):
            async with api.context(**api.options) as execute:
                execute(api.task)
        assert api.events[-4:] == [
            "first:stop", "second:close", "first:close", "connector:close",
        ]

    with pytest.raises(StopIteration):
        exercise().send(None)


@pytest.mark.parametrize("failure", ["budget", "binding", "borrowed", "second-client"])
def test_executor_partial_construction_releases_only_owned_resources(
    api: SimpleNamespace, failure: str,
) -> None:
    if failure == "budget":
        api.options["max_rows"] = 0
    elif failure == "binding":
        api.state.wrong_binding = True
    elif failure == "borrowed":
        api.state.borrowed = True
    else:
        api.state.fail_model = "second"

    async def exercise() -> None:
        with pytest.raises((ValueError, RuntimeError)):
            async with api.context(**api.options):
                pytest.fail("invalid construction must not expose an executor")
        assert api.events == {
            "budget": [],
            "binding": ["isolation", "connector", "connector:close"],
            "borrowed": ["isolation", "connector"],
            "second-client": [
                "isolation", "connector", "first:open", "second:open",
                "first:close", "connector:close",
            ],
        }[failure]

    with pytest.raises(StopIteration):
        exercise().send(None)

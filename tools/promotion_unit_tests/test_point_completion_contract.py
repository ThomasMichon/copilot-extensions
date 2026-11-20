"""Actor-free whole-claim composition; SQLite, native effects and restore are opt-in."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
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
        module = ModuleType("agent_index." + name)
        for key, value in attributes.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, module.__name__, module)

    def load(name: str) -> ModuleType:
        spec = importlib.util.spec_from_file_location(
            "agent_index.indexing." + name, SOURCE / "indexing" / (name + ".py"),
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        return module

    load("change_hints")
    hints = load("point_hints")
    components = load("component_manifest")
    claims = load("point_claim")
    events: list[str] = []
    binding = hints.PointBinding("https://example.test", "project", "query")
    requested = hints.PointChangeHints(
        source="selected", canonical_family="components", account_id="account",
        binding=binding,
        targets=(hints.WorkItemRevisionTarget("one", 1, ("old",)),
         hints.WorkItemRevisionTarget("one", 2, ("event-a",)),
         hints.WorkItemRevisionTarget("one", 2, ("event-b",)),
         hints.CommentVersionTarget("one", "two", 3, ("comment",))),
    )
    claim = claims.PendingPointClaim("task", "a" * 32, requested)
    content = tuple(
        components.ComponentContent(components.ComponentVersion(
            "components", path, version, "render/v1",
            components.ComponentIdentity(kind, "one", comment),
        ), "fingerprint", False)
        for path, version, kind, comment in (
            ("body", 2, "work_item_revision", None), ("comment", 3, "comment_version", "two"),
        )
    )

    class Connection:
        def __init__(self, owner: str) -> None:
            self.owner = owner

        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def execute(self, sql: str, args: object = None) -> Any:
            events.append(self.owner + (":begin" if sql.startswith("BEGIN") else ":read"))
            return SimpleNamespace(fetchone=lambda: state.parent)

        def commit(self) -> None:
            events.append(self.owner + ":commit")

        def close(self) -> None:
            pass

    @contextmanager
    def operation(_db: object, *, point_scope: object) -> Iterator[None]:
        assert point_scope == requested.scope
        events.append("native:enter")
        yield
        events.append("native:exit")

    state = SimpleNamespace(
        parent={"scope": "scope", "payload": '{"version":9}'},
        task=SimpleNamespace(status="queued_hints_v2", result_stats=None),
        refuse=False, applied=content,
    )
    config = SimpleNamespace(data_dir=Path("owned"))
    domain = SimpleNamespace(writing=False, _local=SimpleNamespace(), operation=operation,
                             receipt=lambda: {"domain": "domain"})
    store = SimpleNamespace(content_store=SimpleNamespace(db=object()))

    class Ledger:
        def __init__(self, actual_config: object, actual_store: object) -> None:
            assert actual_config is config and actual_store is store
            self._config, self._store, self._source_domain = config, store, domain

        def get(self, source: str, path: str, *, point_scope: object) -> Any:
            return SimpleNamespace(content=next(c for c in state.applied if c.component.path == path))

    def qualify(*args: Any, **kwargs: Any) -> None:
        events.append("qualify")
        if state.refuse:
            raise ValueError("fixture incomplete coverage")

    def finish(_conn: object, actual_claim: object, stats: object) -> Any:
        assert actual_claim == claim
        events.append("queue:complete")
        state.task = SimpleNamespace(status="complete", result_stats=stats)
        return state.task

    def retire(*args: Any, **kwargs: Any) -> None:
        events.append("pending:retire")
        state.parent = None

    class Journal:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            assert kwargs["require_existing"] is True
            self.payload, self.generation = '{"version":9}', "generation"

        def _require_parent(self, _conn: object) -> None:
            events.append("parent:qualify")

    pending = SimpleNamespace(
        decode_point_pending=lambda *a: (object(), claim), _retire_point_claim=retire,
    )
    stub("indexing.component_application", _component_contents=lambda *a: {
        (c.component.source, c.component.path): c for c in content
    }, _policy=lambda config: "policy", qualify_applied_component=qualify)
    stub("indexing.path_index", PathIndex=object)
    stub("indexing.pending_crawl", PendingCrawlConflict=ValueError, PendingCrawls=pending)
    stub("indexing.prepared_journal", PreparedJournal=Journal)
    stub("indexing.prepared_points",
         PreparedPoints=lambda j: SimpleNamespace(read_application=lambda: object()),
         _json=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")))
    stub("indexing.task_store", TaskRecord=object, TaskStore=object,
         TaskStatus=SimpleNamespace(COMPLETE="complete"))
    stub("store.component_ledger", ComponentLedger=Ledger)
    stub("store.source_layout", SourceLayoutError=ValueError)
    module = load("point_completion")
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(Path, "resolve", lambda self: self)
    paths = SimpleNamespace(_db_path="owned/path_index.db", _connect=lambda: Connection("pending"))
    tasks = SimpleNamespace(
        _db_path="owned/tasks.db", _connect=lambda: Connection("queue"),
        _require_point_claim=lambda *a: state.task, _complete_point_claim=finish,
    )
    return SimpleNamespace(
        module=module, claim=claim, content=content, state=state, events=events,
        load=load, paths=paths, tasks=tasks, ledger=Ledger(config, store),
        complete=lambda: module.complete_point_claim(
            claim, scope="scope", paths=paths, tasks=tasks, ledger=Ledger(config, store), max_rows=8,
        ),
    )


def test_completion_qualifies_all_points_before_success_and_retirement(api: SimpleNamespace) -> None:
    completed = api.complete()
    events = api.events
    assert events.count("qualify") == 2
    assert events.index("queue:complete") > max(i for i, event in enumerate(events) if event == "qualify")
    assert events.index("queue:complete") < events.index("queue:commit") < events.index("pending:retire")
    assert events.index("pending:retire") < events.index("pending:commit") < events.index("native:exit")
    events.clear()
    assert api.complete() == completed
    assert events.count("qualify") == 2
    assert "queue:complete" not in events and "pending:retire" not in events
    api.state.refuse = True
    events.clear()
    with pytest.raises(ValueError, match="incomplete coverage"):
        api.complete()
    assert not any(event in events for event in ("queue:complete", "queue:commit", "pending:retire"))
    api.state.task = SimpleNamespace(status="queued_hints_v2", result_stats=None)
    api.state.refuse = False
    requested = tuple(content.component for content in api.content)

    def higher() -> Any:
        return api.module.complete_higher_point_claim(
            api.claim, components=requested, scope="scope", paths=api.paths,
            tasks=api.tasks, ledger=api.ledger, max_rows=8,
        )

    events.clear()
    assert higher() is None
    assert "queue:complete" not in events and "qualify" not in events
    api.state.applied = tuple(replace(
        content, component=replace(content.component, version=content.component.version + 1),
    ) for content in api.content)
    completed = higher()
    assert completed.status == "complete" and "pending:retire" not in events
    assert completed.result_stats["point_completion"]["mode"] == "higher-covered"
    events.clear()
    assert api.complete() == completed and events.count("qualify") == 2
    assert "queue:complete" not in events and "pending:retire" not in events
    api.state.refuse = True
    events.clear()
    with pytest.raises(ValueError, match="incomplete coverage"):
        api.complete()
    assert not any(event in events for event in ("queue:complete", "queue:commit", "pending:retire"))


def test_completion_outcome_cannot_drop_or_rebind_a_claimed_logical_target(api: SimpleNamespace) -> None:
    cls = api.module._Completion
    valid = cls("claim", "pending", "generation", "scope", "policy", "domain", api.content)
    valid.require_targets(api.claim)
    assert cls.from_stats(json.loads(json.dumps(valid.to_stats()))) == valid
    for content in (
        api.content[:1], api.content + api.content[:1],
        (replace(api.content[0], component=replace(api.content[0].component, identity=None)), api.content[1]),
        (replace(api.content[0], component=replace(api.content[0].component, version=1)), api.content[1]),
    ):
        with pytest.raises(ValueError, match="entire typed point claim"):
            replace(valid, components=content).require_targets(api.claim)
    for field, value in (("version", True), ("components", None), ("unknown", "field")):
        raw = valid.to_stats()
        raw["point_completion"][field] = value
        with pytest.raises(ValueError):
            cls.from_stats(raw)
    higher = replace(valid, pending=None, generation=None, higher_covered=True, components=tuple(
        replace(content, component=replace(content.component, version=content.component.version + 1))
        for content in valid.components
    ))
    higher.require_targets(api.claim)
    with pytest.raises(ValueError, match="entire typed point claim"):
        replace(higher, components=valid.components).require_targets(api.claim)
    assert cls.from_stats(json.loads(json.dumps(higher.to_stats()))) == higher
    for field, value in (("mode", "acquired"), ("pending", "forged"), ("version", True)):
        raw = higher.to_stats()
        raw["point_completion"][field] = value
        with pytest.raises(ValueError):
            cls.from_stats(raw)


def _worker(api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> Any:
    events = api.events

    @contextmanager
    def writer(*args: Any, **kwargs: Any) -> Iterator[None]:
        events.append("writer:enter")
        try:
            yield
        finally:
            events.append("writer:exit")

    ownership = ModuleType("agent_index.indexing.source_ownership")
    setattr(ownership, "source_writer", writer)
    monkeypatch.setitem(sys.modules, ownership.__name__, ownership)

    class Application:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            assert kwargs["point_claim"] == api.claim
            events.append("application:prepare")

        def publish(self) -> None:
            events.append("application:publish")
            if api.state.refuse:
                raise ValueError("fixture uncertain native state")

    class Pending:
        def __init__(self, paths: object) -> None:
            assert paths is api.paths

        def read_payload(self, source: str, scope: str) -> str:
            payload: str = api.state.parent["payload"]
            return payload

    monkeypatch.setattr(
        sys.modules["agent_index.indexing.component_application"],
        "PreparedComponentApplication", Application, raising=False,
    )
    monkeypatch.setattr(sys.modules["agent_index.indexing.pending_crawl"], "PendingCrawls", Pending)
    monkeypatch.setattr(
        sys.modules["agent_index.indexing.prepared_points"], "PreparedPoints",
        lambda journal: SimpleNamespace(compose=lambda: events.append("acquisition:compose")),
    )
    worker = api.load("point_worker")
    return lambda **kwargs: worker.run_retained_point_worker(
        api.claim, scope="scope", paths=api.paths, tasks=api.tasks,
        ledger=api.ledger, clients={}, max_rows=8, **kwargs,
    )


def test_retained_worker_requalifies_outside_native_effects_and_skips_completed_retry(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _worker(api, monkeypatch)
    def qualify() -> None:
        api.events.append("provider:qualify")
    completed = run(qualify_claim=qualify)
    events = api.events
    checks = [i for i, event in enumerate(events) if event == "provider:qualify"]
    assert len(checks) == 3
    assert checks[0] < events.index("application:prepare") < checks[1]
    assert checks[1] < events.index("application:publish") < checks[2]
    assert checks[2] < events.index("native:enter") < events.index("queue:complete")
    events.clear()
    assert run(qualify_claim=qualify) == completed
    assert "provider:qualify" not in events and "application:prepare" not in events


@pytest.mark.parametrize("boundary", [1, 2, 3])
def test_retained_worker_qualification_failure_never_acknowledges(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, boundary: int,
) -> None:
    run = _worker(api, monkeypatch)
    calls = 0
    def qualify() -> None:
        nonlocal calls
        calls += 1
        if calls == boundary:
            raise ValueError("fixture eligibility changed")
    with pytest.raises(ValueError, match="eligibility changed"):
        run(qualify_claim=qualify)
    assert "queue:complete" not in api.events and "pending:retire" not in api.events
    assert ("application:prepare" in api.events) == (boundary > 1)
    assert ("application:publish" in api.events) == (boundary > 2)
    assert api.events[-1] == "writer:exit"


def test_retained_worker_publishes_before_whole_completion_and_never_reapplies_success(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _worker(api, monkeypatch)
    completed = run()
    events = api.events
    assert events.index("writer:enter") < events.index("acquisition:compose")
    assert events.index("application:prepare") < events.index("application:publish")
    assert events.index("application:publish") < events.index("queue:complete")
    assert events.index("pending:commit") < events.index("writer:exit")
    events.clear()
    assert run() == completed
    assert events.count("qualify") == 2
    assert not any(event.startswith(("acquisition:", "application:")) for event in events)


def test_retained_worker_propagates_uncertain_application_without_false_completion(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _worker(api, monkeypatch)
    api.state.refuse = True
    with pytest.raises(ValueError, match="uncertain native state"):
        run()
    assert api.state.task.status == "queued_hints_v2" and api.state.parent is not None
    assert "queue:complete" not in api.events and "pending:retire" not in api.events
    assert api.events[-1] == "writer:exit"

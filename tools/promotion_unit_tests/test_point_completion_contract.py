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
        "selected", "components", "account", binding,
        (hints.WorkItemRevisionTarget("one", 1, ("old",)),
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
        refuse=False,
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
            return SimpleNamespace(content=next(c for c in content if c.component.path == path))

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

"""Actor-free composition control; real native qualification remains opt-in."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-index/src/agent_index/store"


def test_cleanup_preflights_every_model_and_deletes_vectors_before_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = "_promotion_point_cleanup"
    parent = ModuleType(package)
    parent.__path__ = []
    monkeypatch.setitem(sys.modules, package, parent)
    events: list[str] = []
    scope = ("components", "points", "account", object())

    @contextmanager
    def writer(_db: object, *, point_scope: object) -> Iterator[None]:
        assert point_scope is scope
        yield

    @contextmanager
    def mutation(name: str, operation: str, sources: list[str]) -> Iterator[None]:
        assert sources == ["points"] and operation.endswith(":" + name)
        events.append("effect:" + name)
        yield

    domain = SimpleNamespace(
        writing=True, path=Path("selected"), _write=writer, bucket_mutation=mutation,
    )
    content = SimpleNamespace(_source_domain=domain, db=object())
    store = SimpleNamespace(_source_domain=domain, _db_path=domain.path, content_store=content)

    def native(name: str) -> SimpleNamespace:
        def delete(predicate: str) -> None:
            assert "'keep'" not in predicate
            assert "'stale'" in predicate
            assert ("'ghost'" in predicate) == (name == "first")
            events.append("delete:" + name)

        return SimpleNamespace(name=name, delete=delete)

    canonical = SimpleNamespace(_table_name="chunks", _table=native("chunks"))
    first = SimpleNamespace(_model_id="a", _table_name="first", _table=native("first"))
    second = SimpleNamespace(_model_id="b", _table_name="second", _table=native("second"))

    class SelectedStore:
        def __init__(self, path: Path, *, content_table: str, point_scope: object) -> None:
            assert path == domain.path and content_table == "chunks" and point_scope is scope
            self._content: Any = None
            self._vector_tables: dict[str, Any] = {}

    def capture(_config: object, selected: SelectedStore, source: str, path: str, **kwargs: Any) -> Any:
        assert selected._content is canonical
        assert selected._vector_tables == {"a": first, "b": second}
        assert (source, path) == ("points", "one.md")
        assert kwargs == {"max_rows": 3, "retained_chunk_ids": ("ghost",), "point_scope": scope}
        events.append("capture")
        return SimpleNamespace(
            content_rows=json.dumps([{"chunk_id": "keep"}, {"chunk_id": "stale"}]),
            models=[
                SimpleNamespace(table_name="first", rows=json.dumps([
                    {"chunk_id": "keep"}, {"chunk_id": "stale"}, {"chunk_id": "ghost"},
                ])),
                SimpleNamespace(table_name="second", rows=json.dumps([{"chunk_id": "stale"}])),
            ],
        )

    class LayoutError(RuntimeError):
        pass

    for suffix, attributes in (
        ("file_projection", {"capture_file_projection": capture}),
        ("multi_model_store", {"MultiModelStore": SelectedStore}),
        ("point_content", {"_prepared_tables": lambda *_args, **_kwargs: (canonical, [first, second])}),
        ("repo_filter", {"_sql_str": lambda value: value}),
        ("source_layout", {"SourceLayoutError": LayoutError}),
    ):
        stub = ModuleType(package + "." + suffix)
        for key, value in attributes.items():
            setattr(stub, key, value)
        monkeypatch.setitem(sys.modules, stub.__name__, stub)
    modules = []
    for suffix in ("scalar_index", "point_reconciliation"):
        name = package + "." + suffix
        spec = importlib.util.spec_from_file_location(name, SOURCE / f"{suffix}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules.append(module)
    scalar, module = modules
    exhausted = True

    def inspect(table: SimpleNamespace, *, column: str) -> Any:
        events.append("inspect:" + table.name + ":" + column)
        return scalar.ScalarIndexState(
            1, True, 0, scalar.MAX_SEGMENTS if exhausted and table is second._table else 1,
        ), None

    monkeypatch.setattr(module, "inspect_index", inspect)

    def reconcile() -> int:
        return cast(int, module.reconcile_point_file(
            object(), store, "points", "one.md", ("keep",), max_rows=3,
            retained_chunk_ids=("ghost",), point_scope=scope,
        ))

    with pytest.raises(scalar.ScalarIndexPreparationRequired, match="reserved"):
        reconcile()
    preflight = [
        "capture", "inspect:chunks:chunk_id", "inspect:chunks:file_path", "inspect:chunks:source",
        "inspect:first:chunk_id", "inspect:second:chunk_id",
    ]
    assert events == preflight
    events.clear()
    exhausted = False
    assert reconcile() == 2
    assert events == preflight + [
        "effect:first", "delete:first", "effect:second", "delete:second", "effect:chunks", "delete:chunks",
    ]

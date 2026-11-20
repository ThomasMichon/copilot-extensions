"""Actor-free selected capacity composition; native index behavior is opt-in."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-index/src/agent_index/store"


def test_point_capacity_qualifies_canonical_and_counts_unselected_path_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = "_promotion_point_capacity"
    parent = ModuleType(package)
    parent.__path__ = []
    monkeypatch.setitem(sys.modules, package, parent)
    scope = ("components", "points", "account", object())
    events: list[str] = []

    @contextmanager
    def operation(_db: object, *, point_scope: object) -> Iterator[None]:
        assert point_scope is scope
        events.append("operation")
        yield

    domain = SimpleNamespace(writing=True, path=Path("selected"), operation=operation)
    cached = SimpleNamespace(_source_domain=domain, db=object())
    store = SimpleNamespace(_source_domain=domain, _db_path=domain.path, content_store=cached)
    native = object()
    canonical = SimpleNamespace(_get_or_create_table=lambda: native)

    def prepared(content: object, *, point_scope: object) -> Any:
        assert content is cached and point_scope is scope
        events.append("qualified")
        return canonical, []

    class LayoutError(RuntimeError):
        pass

    for suffix, attributes in (
        ("chunk_id_index", {"indexed_chunk_rows": None}),
        ("point_content", {"_prepared_tables": prepared}),
        ("repo_filter", {"_sql_str": None}),
        ("source_layout", {"SourceLayoutError": LayoutError, "PointScope": tuple}),
    ):
        stub = ModuleType(package + "." + suffix)
        for key, value in attributes.items():
            setattr(stub, key, value)
        monkeypatch.setitem(sys.modules, stub.__name__, stub)
    modules = []
    for suffix in ("scalar_index", "file_projection"):
        name = package + "." + suffix
        spec = importlib.util.spec_from_file_location(name, SOURCE / f"{suffix}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules.append(module)
    scalar, module = modules

    def inspect(table: object, *, column: str) -> Any:
        assert table is native and column == "file_path"
        events.append("index")
        return scalar.ScalarIndexState(3, True, 0, scalar.MAX_SEGMENTS), None

    def rows(content: object, path: str, max_rows: int) -> list[dict[str, str]]:
        assert content is canonical and max_rows == 3
        events.append("path:" + path)
        return [{"source": "points"}, {"source": "points"}, {"source": "neighbor"}]

    monkeypatch.setattr(module, "inspect_index", inspect)
    monkeypatch.setattr(module, "_path_rows", rows)
    with pytest.raises(scalar.ScalarIndexPreparationRequired, match="Projected"):
        module.require_file_projection_capacity(
            store, {("points", "one.md"): 3}, max_rows=3, point_scope=scope,
        )
    expected = ["operation", "qualified", "index", "path:one.md"]
    assert events == expected
    events.clear()
    module.require_file_projection_capacity(
        store, {("points", "one.md"): 2}, max_rows=3, point_scope=scope,
    )
    assert events == expected
    for replacements, writing in (({("neighbor", "one.md"): 0}, True), ({}, False)):
        events.clear()
        domain.writing = writing
        with pytest.raises(LayoutError, match="owning source writer"):
            module.require_file_projection_capacity(
                store, replacements, max_rows=3, point_scope=scope,
            )
        assert events == []

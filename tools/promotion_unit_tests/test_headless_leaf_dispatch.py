"""Actor-free regressions for fieldless launch-visitor dispatch."""
from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def launch_guard() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "check-headless-launch.py"
    spec = importlib.util.spec_from_file_location("headless_leaf_dispatch_guard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _visitor(guard: ModuleType, methods: dict[str, object]) -> ast.NodeVisitor:
    base = getattr(guard, "_LaunchVisitor")
    assert isinstance(base, type) and issubclass(base, ast.NodeVisitor)
    visitor = type("LeafDispatchWitness", (base,), methods)()
    assert isinstance(visitor, ast.NodeVisitor)
    return visitor


def test_default_fieldless_nodes_avoid_generic_field_walks(
    launch_guard: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "value = other"
    tree = ast.parse(source)
    empty_walks: list[ast.AST] = []
    original = ast.iter_fields

    def fields(node: ast.AST) -> Iterator[tuple[str, object]]:
        if isinstance(node, (ast.Load, ast.Store)):
            empty_walks.append(node)
        yield from original(node)

    monkeypatch.setattr(ast, "iter_fields", fields)
    visitor = getattr(launch_guard, "_SpawnFinder")(source)
    visitor.visit(tree)
    assert getattr(visitor, "hits") == []
    assert empty_walks == []


def test_custom_generic_handler_still_receives_fieldless_nodes(
    launch_guard: ModuleType,
) -> None:
    tree = ast.parse("value = other + third")
    seen: list[ast.AST] = []

    def generic(self: ast.NodeVisitor, node: ast.AST) -> None:
        seen.append(node)
        ast.NodeVisitor.generic_visit(self, node)

    _visitor(launch_guard, {"generic_visit": generic}).visit(tree)
    assert Counter(map(id, seen)) == Counter(map(id, ast.walk(tree)))


def test_explicit_leaf_handler_keeps_dispatch_priority(
    launch_guard: ModuleType,
) -> None:
    tree = ast.parse("value = other")
    seen: list[ast.Load] = []

    def load(self: ast.NodeVisitor, node: ast.Load) -> None:
        seen.append(node)

    _visitor(launch_guard, {"visit_Load": load}).visit(tree)
    assert seen == [node for node in ast.walk(tree) if isinstance(node, ast.Load)]


def test_cached_fieldless_binding_preserves_instance_children(
    launch_guard: ModuleType,
) -> None:
    visitor = getattr(launch_guard, "_FlagFinder")()
    visitor.visit(ast.Load())
    leaf = ast.Load()
    setattr(leaf, "_fields", ("edge",))
    setattr(
        leaf, "edge",
        ast.Name(id="CREATE_NEW_CONSOLE", ctx=ast.Load(), lineno=1, col_offset=0),
    )
    visitor.visit(leaf)
    assert (1, "CREATE_NEW_CONSOLE") in getattr(visitor, "hits")
    setattr(leaf, "_fields", None)
    with pytest.raises(TypeError):
        visitor.visit(leaf)


def test_replaced_standard_generic_handler_is_not_bypassed(
    launch_guard: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = ast.parse("value = other")
    visitor = _visitor(launch_guard, {})
    seen: list[ast.AST] = []
    original = ast.NodeVisitor.generic_visit

    def generic(self: ast.NodeVisitor, node: ast.AST) -> None:
        if self is visitor:
            seen.append(node)
        original(self, node)

    monkeypatch.setattr(ast.NodeVisitor, "generic_visit", generic)
    visitor.visit(tree)
    assert Counter(map(id, seen)) == Counter(map(id, ast.walk(tree)))

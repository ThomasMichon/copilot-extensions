"""Python 3.10 syntax and import-surface controls for hint transport additions."""

from __future__ import annotations

import ast
import builtins
import importlib.util
import sys
import types
import typing
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "src" / "agent_index"
NEW_MODULES = (
    "indexing/change_hints.py",
    "indexing/task_hints_schema.py",
    "indexing/change_hints_contract.py",
    "index_command.py",
    "reindex_intake.py",
)
CHANGED_MODULES = NEW_MODULES + (
    "__main__.py", "client.py", "server.py",
    "indexing/task_store.py", "indexing/runner.py", "indexing/worker.py",
)
POST_310_TYPING = {
    "Self", "LiteralString", "Never", "Required", "NotRequired",
    "TypeVarTuple", "Unpack", "TypeAliasType", "ReadOnly",
}


@pytest.mark.parametrize("relative", CHANGED_MODULES)
def test_hint_module_source_matches_python_310_syntax_and_typing_surface(relative):
    source = (SOURCE / relative).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=relative, feature_version=(3, 10))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "typing":
            assert not POST_310_TYPING.intersection(alias.name for alias in node.names)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "typing":
                assert node.attr not in POST_310_TYPING


def test_intake_import_and_validation_without_post_310_typing_exports(monkeypatch):
    class Typing310(types.ModuleType):
        def __getattr__(self, name):
            if name in POST_310_TYPING:
                raise AttributeError(name)
            return getattr(typing, name)

    floor_typing = Typing310("typing")
    real_import = builtins.__import__

    def source_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "typing" and level == 0:
            return floor_typing
        return real_import(name, globals, locals, fromlist, level)

    name = "agent_index._python_floor_intake"
    spec = importlib.util.spec_from_file_location(name, SOURCE / "reindex_intake.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    module.__dict__["__builtins__"] = {**vars(builtins), "__import__": source_import}
    source = (SOURCE / "reindex_intake.py").read_text(encoding="utf-8")
    exec(compile(source, str(SOURCE / "reindex_intake.py"), "exec"), module.__dict__)

    from agent_index.indexing.change_hints import ChangeHints

    hints = ChangeHints(source="git:sample", canonical_family="git", account_id="account-a")
    request = module.HintedReindexRequest.model_validate({
        "source": hints.source, "change_hints": hints.to_dict(),
    })
    assert request.change_hints == hints
    assert request.check_scope() is request

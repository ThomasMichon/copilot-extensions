"""Actor-free working-directory contract for the Codespaces owner launch."""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins" / "agent-codespaces" / "src" / "agent_codespaces" / "owner_startup.py"


def _mutate_keyword(source: Path, destination: Path, keyword: str, expression: str) -> Path:
    data = source.read_bytes()
    tree = ast.parse(data, filename=str(source))
    owners = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "spawn_owner_process"
    ]
    assert len(owners) == 1
    calls = [
        node for node in ast.walk(owners[0])
        if isinstance(node, ast.Call) and {"cwd", "log_path"} <= {item.arg for item in node.keywords}
    ]
    assert len(calls) == 1
    values = [item.value for item in calls[0].keywords if item.arg == keyword]
    assert len(values) == 1
    value = values[0]
    lines = data.splitlines(keepends=True)
    start = sum(map(len, lines[:value.lineno - 1])) + value.col_offset
    end = sum(map(len, lines[:value.end_lineno - 1])) + value.end_col_offset
    destination.write_bytes(data[:start] + expression.encode("utf-8") + data[end:])
    return destination


def _replaceable(path: Path, root: Path) -> bool:
    lexical = Path(os.path.abspath(path))
    resolved = path.resolve()
    for name in ("snapshots", "versions"):
        subtree = root / name
        if lexical.is_relative_to(subtree) or resolved.is_relative_to(subtree.resolve()):
            return True
    return False


def _exercise(source: Path, temporary: Path, *, installed: bool = False, existing=()) -> None:
    spec = importlib.util.spec_from_file_location("_owner_cwd_contract", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    home, root, transient = (temporary / name for name in ("home", "runtime", "worktree"))
    home.mkdir(parents=True)
    transient.mkdir()
    for parts in existing:
        root.joinpath(*parts).mkdir(parents=True, exist_ok=True)
    expanduser = os.path.expanduser
    child = object()
    calls = []

    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        return child

    procutil = ModuleType("agent_procutil")
    procutil.__path__ = []
    procutil.spawn_daemon = spawn
    procutil.python_cache_args = lambda _root: []
    instances = ModuleType("agent_procutil.daemon_instances")
    instances.DaemonInstanceSpec = lambda *args, **kwargs: SimpleNamespace(args=args, kwargs=kwargs)

    with (
        patch.dict(sys.modules, {"agent_procutil": procutil, instances.__name__: instances}),
        patch.object(os, "getcwd", return_value=str(transient)),
        patch.object(os.path, "expanduser", side_effect=lambda value: str(home) if value == "~" else expanduser(value)),
        patch.object(Path, "home", return_value=home),
    ):
        returned, log_path = module.spawn_owner_process(root, source=str(root / "source"), installed=installed)
    assert returned is child and len(calls) == 1
    assert root.is_dir(), "owner did not prepare its runtime root"
    options = calls[0][1]
    cwd_path = Path(options["cwd"])
    log = Path(options["log_path"])
    assert not _replaceable(cwd_path, root), "owner cwd is in a replaceable runtime subtree"
    assert not _replaceable(log, root), "owner log is in a replaceable runtime subtree"
    cwd = cwd_path.resolve()
    assert cwd.is_dir() and (cwd == home.resolve() or cwd.is_relative_to(root.resolve())), (
        "owner cwd is not a durable home or runtime root"
    )
    assert log.resolve().is_relative_to(root.resolve())
    assert Path(log_path).resolve() == log.resolve()


@pytest.mark.parametrize("installed", [False, True], ids=["source", "installed"])
def test_codespaces_owner_uses_durable_cwd_and_log(tmp_path: Path, installed: bool) -> None:
    _exercise(SOURCE, tmp_path, installed=installed)


def test_inherited_working_directory_is_rejected(tmp_path: Path) -> None:
    _exercise(SOURCE, tmp_path / "baseline")
    mutant = _mutate_keyword(SOURCE, tmp_path / "owner_startup.py", "cwd", "os.getcwd()")
    with pytest.raises(AssertionError, match="owner cwd is not a durable"):
        _exercise(mutant, tmp_path / "mutant")


@pytest.mark.parametrize(
    "expression", ["Path.home()", "root", 'root / "nested"'], ids=["path-home", "runtime-root", "runtime-child"],
)
def test_equivalent_durable_working_directory_is_accepted(tmp_path: Path, expression: str) -> None:
    _exercise(SOURCE, tmp_path / "baseline")
    mutant = _mutate_keyword(SOURCE, tmp_path / "owner_startup.py", "cwd", expression)
    existing = (("nested",),) if expression == 'root / "nested"' else ()
    _exercise(mutant, tmp_path / "mutant", existing=existing)


def test_mutations_accept_equivalent_source_spelling(tmp_path: Path) -> None:
    _exercise(SOURCE, tmp_path / "baseline")
    equivalent = _mutate_keyword(SOURCE, tmp_path / "equivalent.py", "cwd", "Path.home()")
    _exercise(equivalent, tmp_path / "equivalent")
    mutant = _mutate_keyword(equivalent, tmp_path / "mutant.py", "cwd", "os.getcwd()")
    with pytest.raises(AssertionError, match="owner cwd is not a durable"):
        _exercise(mutant, tmp_path / "mutant")


def test_equivalent_string_log_argument_is_accepted(tmp_path: Path) -> None:
    _exercise(SOURCE, tmp_path / "baseline")
    mutant = _mutate_keyword(SOURCE, tmp_path / "owner_startup.py", "log_path", "str(log_path)")
    _exercise(mutant, tmp_path / "mutant")


@pytest.mark.parametrize("field", ["cwd", "log"])
def test_symlink_parent_cannot_escape_replaceable_tree(tmp_path: Path, field: str) -> None:
    _exercise(SOURCE, tmp_path / "baseline")
    keyword = "cwd" if field == "cwd" else "log_path"
    expression = 'root / "link" / ".."' + (' / "owner.log"' if field == "log" else "")
    mutant = _mutate_keyword(SOURCE, tmp_path / "owner_startup.py", keyword, expression)
    run = tmp_path / "mutant"
    root = run / "runtime"
    target = root / "versions" / "v1" / "deep"
    target.mkdir(parents=True)
    link = root / "link"
    context = nullcontext()
    if os.name == "nt":
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            if path == link / "..":
                return target.parent
            if path == link / ".." / "owner.log":
                return target.parent / "owner.log"
            return original_resolve(path, *args, **kwargs)

        context = patch.object(Path, "resolve", autospec=True, side_effect=resolve)
    else:
        link.symlink_to(target, target_is_directory=True)
    with context, pytest.raises(AssertionError, match="replaceable"):
        _exercise(mutant, run)


@pytest.mark.parametrize(("field", "subtree"), [
    ("cwd", "snapshots"), ("cwd", "versions"),
    ("log", "snapshots"), ("log", "versions"),
], ids=["cwd-snapshots", "cwd-versions", "log-snapshots", "log-versions"])
def test_replaceable_runtime_subtrees_are_rejected(
    tmp_path: Path, field: str, subtree: str,
) -> None:
    _exercise(SOURCE, tmp_path / "baseline")
    if field == "cwd":
        keyword, expression = "cwd", f'root / "{subtree}" / "v1"'
    else:
        keyword, expression = "log_path", f'root / "{subtree}" / "v1" / "owner.log"'
    mutant = _mutate_keyword(SOURCE, tmp_path / "owner_startup.py", keyword, expression)
    with pytest.raises(AssertionError, match="replaceable"):
        _exercise(mutant, tmp_path / "mutant", existing=((subtree, "v1"),))

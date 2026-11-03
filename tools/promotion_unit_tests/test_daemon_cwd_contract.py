"""Actor-free working-directory contract for the Codespaces owner launch."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins" / "agent-codespaces" / "src" / "agent_codespaces" / "owner_startup.py"


def _exercise(source: Path, temporary: Path, *, installed: bool = False) -> None:
    spec = importlib.util.spec_from_file_location("_owner_cwd_contract", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    home, root, transient = (temporary / name for name in ("home", "runtime", "worktree"))
    home.mkdir(parents=True)
    transient.mkdir()
    (root / "nested").mkdir(parents=True)
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
    options = calls[0][1]
    cwd = Path(options["cwd"]).resolve()
    assert cwd.is_dir() and (cwd == home.resolve() or cwd.is_relative_to(root.resolve())), (
        "owner cwd is not a durable home or runtime root"
    )
    assert Path(options["log_path"]).resolve().is_relative_to(root.resolve())
    assert log_path == options["log_path"]


@pytest.mark.parametrize("installed", [False, True], ids=["source", "installed"])
def test_codespaces_owner_uses_durable_cwd_and_log(tmp_path: Path, installed: bool) -> None:
    _exercise(SOURCE, tmp_path, installed=installed)


def test_inherited_working_directory_is_rejected(tmp_path: Path) -> None:
    _exercise(SOURCE, tmp_path / "baseline")
    text = SOURCE.read_text(encoding="utf-8")
    before = 'cwd=os.path.expanduser("~"),'
    assert text.count(before) == 1
    mutant = tmp_path / "owner_startup.py"
    mutant.write_text(text.replace(before, "cwd=os.getcwd(),", 1), encoding="utf-8")
    with pytest.raises(AssertionError, match="owner cwd is not a durable"):
        _exercise(mutant, tmp_path / "mutant")


@pytest.mark.parametrize(
    "expression", ["Path.home()", "root", 'root / "nested"'], ids=["path-home", "runtime-root", "runtime-child"],
)
def test_equivalent_durable_working_directory_is_accepted(tmp_path: Path, expression: str) -> None:
    _exercise(SOURCE, tmp_path / "baseline")
    text = SOURCE.read_text(encoding="utf-8")
    before = 'cwd=os.path.expanduser("~"),'
    assert text.count(before) == 1
    mutant = tmp_path / "owner_startup.py"
    mutant.write_text(text.replace(before, f"cwd={expression},", 1), encoding="utf-8")
    _exercise(mutant, tmp_path / "mutant")

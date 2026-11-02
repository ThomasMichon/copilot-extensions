"""Actor-free Dispatch slot selection; native launch witnesses stay opt-in."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def resolver(monkeypatch, tmp_path):
    def unused(*args, **kwargs):
        raise AssertionError("runtime selection invoked unrelated process machinery")

    def module(name, **attributes):
        result = ModuleType(name)
        result.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, result)
        return result

    module("agent_procutil", __path__=[], **dict.fromkeys((
        "detached_kwargs", "no_window_flags", "no_window_kwargs",
        "windowless_daemon_kwargs", "windowless_python", "windowless_python_env",
    ), unused))
    module("agent_procutil.daemon_instances", DaemonInstanceSpec=unused, spawn_finite_worker=unused)
    package = "promotion_dispatch_runtime"
    module(package, __path__=[])
    module(f"{package}.install_paths", install_dir=lambda: tmp_path)
    specification = importlib.util.spec_from_file_location(
        f"{package}.procutil", ROOT / "plugins/agent-dispatch/src/agent_dispatch/procutil.py",
    )
    assert specification and specification.loader
    result = importlib.util.module_from_spec(specification)
    monkeypatch.setitem(sys.modules, specification.name, result)
    specification.loader.exec_module(result)
    return result


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("tier", ["current", "last-good", "complete", "newest"])
def test_physical_layout_preserves_selection_tiers(resolver, tmp_path, nested, tier):
    def slot(version, *, nested=False, complete=False):
        directory = tmp_path / "versions" / version
        prefix = directory / ".venv" if nested else directory
        python = prefix / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.touch()
        if complete:
            (directory / ".install-complete.json").write_text("{}", encoding="utf-8")
        return python

    wanted = slot("1.0.0-dev9", nested=nested, complete=tier == "complete")
    if tier != "newest":
        slot("1.0.0-dev50")
    if tier in ("current", "last-good"):
        marker = "current-version" if tier == "current" else "last-known-good"
        (tmp_path / marker).write_text("1.0.0-dev9", encoding="utf-8-sig")
    assert resolver.resolve_runtime_python(tmp_path) == wanted


def test_nested_precedence_and_reparse_refusal(resolver, tmp_path, monkeypatch):
    directory = tmp_path / "versions" / "1.0.0"
    for prefix in (directory, directory / ".venv"):
        (prefix / "bin").mkdir(parents=True)
        (prefix / "bin" / "python").touch()
    (tmp_path / "current-version").write_text("1.0.0", encoding="utf-8")
    assert resolver.resolve_runtime_python(tmp_path) == directory / ".venv" / "bin" / "python"
    original = Path.lstat

    def redirected(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == directory / ".venv":
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", redirected)
    assert resolver.resolve_runtime_python(tmp_path) is None

"""The installed helper reaches its real entry-module routing without assembly."""

from __future__ import annotations

from collections.abc import Callable
import importlib.util
from importlib.machinery import ModuleSpec
from pathlib import Path
import shutil
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, NoReturn, cast

import pytest

from _manager_constructor_fixture import apply_constructor_fixture
import worktree_manager
import worktree_manager.self_install as installer


def test_installed_application_helper_routes_through_its_loaded_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cast(Callable[[pytest.MonkeyPatch], None], apply_constructor_fixture)(monkeypatch)
    root = tmp_path / "manager"
    slot = root / "versions/1.2.3"
    slot.mkdir(parents=True)
    (slot / "application.json").write_text('{"name":"worktree-manager"}', encoding="utf-8")
    source = Path(__file__).resolve().parents[2] / "libs/installer-engine/runtime_application_install.py"
    shutil.copyfile(source, slot / "install.py")
    package = slot / ".venv/Lib/site-packages/worktree_manager/__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(slot / ".venv"))
    monkeypatch.setattr(worktree_manager, "__file__", str(package))
    monkeypatch.setattr(worktree_manager, "__version__", "1.2.3")
    name = "_manager_application_install"
    monkeypatch.setitem(sys.modules, name, ModuleType(name))
    monkeypatch.delitem(sys.modules, name)
    original = importlib.util.spec_from_file_location
    observed: list[ModuleType] = []

    def application(entry: ModuleType, candidate: Path) -> dict[str, str]:
        assert entry.__file__ is not None
        assert Path(entry.__file__).samefile(slot / "install.py")
        assert candidate.samefile(slot)
        observed.append(entry)
        return {"name": "worktree-manager"}

    def stop_after_routing(*_args: Any, **_options: Any) -> NoReturn:
        raise RuntimeError("fixture qualification reached")

    def specification(module_name: str, location: Any, **options: Any) -> ModuleSpec | None:
        spec = original(module_name, location, **options)
        if module_name == name:
            assert spec is not None and spec.loader is not None
            execute = spec.loader.exec_module

            def load(module: ModuleType) -> None:
                execute(module)
                monkeypatch.setattr(module, "_qualification", lambda: SimpleNamespace(_application=application))
                monkeypatch.setattr(module, "_qualified", stop_after_routing)

            monkeypatch.setattr(spec.loader, "exec_module", load)
        return spec

    monkeypatch.setattr(importlib.util, "spec_from_file_location", specification)
    result = installer.self_install(root=root, dry_run=False, _reconcile_instances=False)
    assert result.action == "error" and result.reason == "fixture qualification reached"
    assert len(observed) == 1
    assert not (root / "current-version").exists()

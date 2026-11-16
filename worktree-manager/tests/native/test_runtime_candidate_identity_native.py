"""Replay native SDK metadata health with real packaged Manager inputs."""

from __future__ import annotations

import importlib
from importlib.metadata import distribution
import os
from collections.abc import Callable
from pathlib import Path
import shutil
import subprocess
from types import ModuleType
from typing import cast
import venv

import pytest

from worktree_manager import self_install


@pytest.mark.skipif(os.name != "nt", reason="Native Windows namespace and interpreter")
def test_sdk_metadata_health_accepts_the_actual_manager_through_native_paths(tmp_path: Path) -> None:
    adapter = cast(Callable[[], ModuleType], self_install._runtime_installer)()
    health = importlib.import_module("installer_engine_health")
    native = adapter.engine._native_retained_path
    slot = tmp_path / "s"
    native_slot = Path(native(slot))
    venv.EnvBuilder(with_pip=False).create(native_slot)
    site = native_slot / "Lib" / "site-packages"
    installed = distribution("copilot-extensions-worktree-manager")
    package = Path(native(Path(str(installed.locate_file("worktree_manager")))))
    shutil.copytree(package, site / "worktree_manager")
    files = installed.files
    assert files is not None
    metadata = next(
        member.parent for member in files
        if len(member.parts) == 2 and member.name == "METADATA"
        and member.parent.name.endswith(".dist-info")
    )
    shutil.copytree(
        Path(native(Path(str(installed.locate_file(metadata))))), site / metadata.name,
    )
    # Dependencies are borrowed; this is not closed-wheel or fresh-origin evidence.
    (site / "fixture-dependencies.pth").write_text(
        str(installed.locate_file("")) + "\n", encoding="utf-8",
    )
    shutil.copytree(package / "_runtime_assets" / "scripts", native_slot / ".runtime-bootstrap")
    (native_slot / "payload").mkdir()
    shutil.copyfile(
        Path(__file__).resolve().parents[2] / "pyproject.toml",
        native_slot / "payload" / "pyproject.toml",
    )
    retained_package = native_slot / "payload" / "src" / "worktree_manager"
    retained_package.mkdir(parents=True)
    shutil.copyfile(package / "__init__.py", retained_package / "__init__.py")
    assert slot.samefile(native_slot) and slot != native_slot
    context = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
        context.pop(name, None)
    executions: list[subprocess.CompletedProcess[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            argv, env=context, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        executions.append(result)
        return result

    health.verify_installed_inputs(
        slot, slot / "Scripts" / "python.exe", "worktree_manager.runtime_candidate",
        origins={}, extras=(), local_packages=None, excluded_packages=[],
        wheel_inputs=None, health_callable="verify_runtime_candidate",
        require_frozen_origin=False, run=run,
    )
    assert len(executions) == 1

"""Published Main's vendored app closure must not replace the toolkit's process owner."""

from __future__ import annotations

import json
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "worktree-manager" / "scripts"))
import installer_engine  # noqa: E402

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("process"),
    pytest.mark.contract("manager.source_recovery_repository_imports"),
]


@pytest.fixture(scope="module")
def published_toolkit(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("published-recovery")
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "tests")
    scripts = root / "worktree-manager" / "scripts"
    shutil.copytree(REPOSITORY / "worktree-manager" / "scripts", scripts, ignore=ignore)
    packages = installer_engine._package_roots(REPOSITORY / "libs" / "update-driver")
    for package in packages:
        shutil.copytree(package, root / "libs" / package.name, ignore=ignore)
    for directory, module in (
        (root / "worktree-manager", "worktree_manager"),
        (root / "plugins" / "agent-worktrees", "agent_worktrees"),
    ):
        for package in packages:
            shutil.copytree(package, directory / "libs" / package.name, ignore=ignore)
        source = directory / "src" / module
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("", encoding="utf-8")
        (directory / "pyproject.toml").write_text(
            '[project]\nname = "' + module.replace("_", "-") + '"\nversion = "1.0.0"\n'
            '[tool.uv.sources]\n'
            'agent-update-driver = { path = "libs/update-driver" }\n',
            encoding="utf-8",
        )
    tools = root / "tools"
    tools.mkdir()
    for name in ("recover_source.py", "source_preparation_process.py"):
        shutil.copyfile(REPOSITORY / "tools" / name, tools / name)
    shutil.copyfile(Path(__file__).with_name("recovery_import_probe.py"), tools / "import_probe.py")
    return root


@pytest.mark.timeout(120)
@pytest.mark.parametrize("child_exit", [0, 17])
def test_published_recovery_child_uses_repository_owner_and_settles(
    published_toolkit: Path, tmp_path: Path, child_exit: int,
) -> None:
    queue = tmp_path / "queue"
    queue.mkdir(mode=0o700)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("UPDATE_DRIVER_")}
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
         str(published_toolkit / "tools" / "import_probe.py"),
         "parent", str(published_toolkit), str(queue), str(child_exit)],
        capture_output=True, text=True, timeout=100, env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["state"] == "completed" and report["exit_code"] == child_exit, report
    assert not report["origin_failure"], report
    expected = published_toolkit / "libs" / "agent-procutil" / "src" / "agent_procutil"
    assert all(Path(path).parent == expected for path in report["child"]["origins"].values())


@pytest.mark.timeout(60)
def test_recovery_refuses_an_already_loaded_vendored_process_owner(
    published_toolkit: Path, tmp_path: Path,
) -> None:
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B",
         str(published_toolkit / "tools" / "import_probe.py"),
         "preloaded-vendor", str(published_toolkit), str(tmp_path), "0"],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 1
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["error_type"] == "ValueError"
    assert "repository process utilities" in report["error"]


def test_recovery_keeps_prepared_engine_snapshot_in_its_named_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(REPOSITORY / "tools"))
    monkeypatch.syspath_prepend(str(REPOSITORY / "plugins" / "agent-worktrees" / "src"))
    recovery: ModuleType = importlib.import_module("recover_source")
    repair = importlib.import_module("worktree_manager.runtime_source_repair")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    payload = tmp_path / "payload"
    payload.mkdir()
    identity = {
        "runtime_source": {
            "plugin": "agent-worktrees", "package_version": "1.0.0",
            "source_commit": "a" * 40, "source_tree": "b" * 40,
        },
        "runtime_version": "1.0.0+source.fixture",
    }
    native = {
        "identity": identity, "runtime_version": "1.0.0+source.fixture.profile." + "d" * 16,
        "payload_hash": "e" * 64,
    }
    record = {
        "payload": str(payload), "identity": identity,
        "install_source": str(snapshot), "native": native,
    }
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    monkeypatch.setattr(recovery.shutil, "which", lambda *_args: "uv")
    monkeypatch.setattr(recovery, "entry", lambda *_args: {"argument": "--install-source"})
    monkeypatch.setattr(recovery, "prepare_new", lambda *_args: {
        "worktree-manager": record, "agent-worktrees": record,
    })
    monkeypatch.setattr(repair, "qualify_engine", lambda *_args, **_kwargs: None)

    def consume(_repository: Path, selected: Any) -> None:
        assert selected.application is None
        assert selected.install_source == snapshot
        assert json.loads(selected.native) == native

    monkeypatch.setattr(repair, "fresh_engine_plan", consume)
    monkeypatch.setattr(recovery, "native_engine", lambda *_args: 0)
    monkeypatch.setattr(recovery, "install_manager", lambda *_args: 19)
    report: dict[str, Any] = {
        "manager_root": str(tmp_path / "manager"), "effects": [], "repository": "fixture",
    }
    assert recovery.apply(
        {"project": None}, SimpleNamespace(), tmp_path, tmp_path, {}, report,
    ) == 19
    assert report["effects"][-1] == {"stage": "manager-installation", "exit_code": 19}

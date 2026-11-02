"""A new source bootstrap can release an old queue without installing Manager."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from single_instance_lease.operation import OperationLock
from update_driver import RequestSpec, TrustedRunner, enqueue, status

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "runtime_install.py"


@pytest.fixture
def legacy_repair(tmp_path: Path) -> tuple[Path, Path, str, str]:
    home = tmp_path / "home"
    root = home / ".copilot-extensions" / "update-driver"
    root.mkdir(parents=True)
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute(
            """CREATE TABLE requests (
            id TEXT PRIMARY KEY, identity TEXT NOT NULL, spec TEXT NOT NULL,
            runner TEXT NOT NULL, state TEXT NOT NULL
            CHECK(state IN ('pending','running','completed','interrupted','blocked')),
            exit_code INTEGER, error TEXT, created_at REAL NOT NULL,
            started_at REAL, finished_at REAL, boot_id TEXT,
            settled_boot_id TEXT, settled_at REAL)"""
        )
        db.execute("CREATE UNIQUE INDEX pending_identity ON requests(identity) WHERE state='pending'")
        db.execute("PRAGMA user_version=2")
    runner = TrustedRunner((sys.executable,), str(home))
    failed = enqueue(RequestSpec("fixture", argv=("failed",)), runner=runner, root=root)
    pending = enqueue(RequestSpec("fixture", argv=("pending",)), runner=runner, root=root)
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute(
            """UPDATE requests SET state='interrupted',error='legacy preparation failure',
            started_at=1,finished_at=2,boot_id=NULL WHERE id=?""", (failed.id,),
        )
    return home, root, failed.id, pending.id


def invoke(
    home: Path, *arguments: str, installer: Path = INSTALLER,
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "COPILOT_", "WORKTREE_MANAGER_", "PYTHON", "UV_"))
    }
    environment.update(HOME=str(home), USERPROFILE=str(home), PATH="")
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-X", "utf8", str(installer), *arguments],
        cwd=home, env=environment, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def test_source_bootstrap_releases_old_queue_without_installed_application(
    legacy_repair: tuple[Path, Path, str, str],
) -> None:
    home, root, failed, pending = legacy_repair
    before = status(failed, root=root)
    result = invoke(home, "--recover-for-repair", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert document["schema"] == "copilot-extensions.repair-recovery"
    assert document["version"] == 1
    assert document["cleanup_confirmed"] is False
    assert document["released"] == [{
        "request_id": failed, "state": "interrupted",
        "error": "legacy preparation failure", "exit_code": None,
        "recovery_authority": "operator_repair", "settlement_kind": None,
    }]
    released = status(failed, root=root)
    assert released.spec == before.spec and released.runner == before.runner
    assert released.started_at == 1 and released.finished_at == 2
    assert released.settled_at is None and released.settled_boot_id is None
    assert status(pending, root=root).state == "pending"
    again = invoke(home, "--recover-for-repair", "--json")
    assert again.returncode == 0, again.stdout + again.stderr
    assert json.loads(again.stdout)["released"] == []
    assert not (home / ".worktree-manager").exists()
    assert not (home / ".agent-worktrees").exists()
    assert not (home / ".local").exists()
    assert sorted(home.iterdir()) == [home / ".copilot-extensions"]


def test_materialized_bootstrap_releases_old_queue_from_its_own_complete_closure(
    legacy_repair: tuple[Path, Path, str, str], tmp_path: Path,
) -> None:
    from worktree_manager.self_install import _copy_payload

    home, root, failed, pending = legacy_repair
    payload = tmp_path / "materialized"
    _copy_payload(INSTALLER.parents[1], payload, mark_complete=False)
    result = invoke(
        home, "--recover-for-repair", "--json",
        installer=payload / "scripts" / "runtime_install.py",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["released"][0]["request_id"] == failed
    assert status(pending, root=root).state == "pending"
    assert not (home / ".worktree-manager").exists()


def test_source_bootstrap_refuses_current_installer_without_mutation(
    legacy_repair: tuple[Path, Path, str, str],
) -> None:
    home, root, _failed, _pending = legacy_repair
    before = (root / "queue.sqlite3").read_bytes()
    with OperationLock(root / "execution.lock"):
        result = invoke(home, "--recover-for-repair", "--json")
    assert result.returncode != 0
    assert "execution is active" in result.stderr
    assert not result.stdout
    assert (root / "queue.sqlite3").read_bytes() == before


@pytest.mark.parametrize("arguments", [
    ["--json"],
    ["--recover-for-repair", "--repair-update"],
    ["--recover-for-repair", "--describe-recovery"],
    ["--recover-for-repair", "--", "update"],
])
def test_source_bootstrap_rejects_mixed_recovery_before_mutation(
    legacy_repair: tuple[Path, Path, str, str], arguments: list[str],
) -> None:
    home, root, _failed, _pending = legacy_repair
    before = (root / "queue.sqlite3").read_bytes()
    result = invoke(home, *arguments)
    assert result.returncode == 2, result.stdout + result.stderr
    assert (root / "queue.sqlite3").read_bytes() == before
    assert not (home / ".worktree-manager").exists()


@pytest.mark.parametrize("exit_code", [0, 23])
def test_native_bootstrap_routes_recovery_without_touching_installer_staging(
    tmp_path: Path, exit_code: int,
) -> None:
    from test_bootstrap_ps1 import _fake_tools, _source_repo

    shell = shutil.which("pwsh") if os.name == "nt" else shutil.which("bash")
    if not shell or not shutil.which("git"):
        pytest.skip("matching native bootstrap shell and Git required")
    source = _source_repo(tmp_path)
    home = tmp_path / "home"
    root = home / ".worktree-manager"
    staging = root / "staging"
    staging.mkdir(parents=True)
    sentinel = staging / "existing-installer-source"
    sentinel.write_bytes(b"do not replace this installer's staging")
    (root / "config.toml").write_text(
        f'[source]\nrepo = "{source.as_posix()}"\nref = "main"\n', encoding="utf-8",
    )
    tools = tmp_path / "tools"
    _fake_tools(tools, uv_exit=0, python_exit=exit_code)
    observer = tools / "observe.py"
    observer.write_text(
        "import json,os,sys\n"
        "print(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd()}))\n"
        f"raise SystemExit({exit_code})\n", encoding="utf-8",
    )
    if os.name == "nt":
        (tools / "fake-python.cmd").write_text(
            f'@echo off\r\n"{sys.executable}" -I -S -B "{observer}" %*\r\n'
            "exit /b %errorlevel%\r\n", encoding="utf-8",
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-File",
                   str(INSTALLER.parents[1] / "bootstrap.ps1")]
    else:
        (tools / "fake-python").write_text(
            f'#!/bin/sh\nexec "{sys.executable}" -I -S -B "{observer}" "$@"\n',
            encoding="utf-8",
        )
        command = [shell, str(INSTALLER.parents[1] / "bootstrap.sh")]
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    environment = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "WORKTREE_MANAGER_ROOT": str(root),
        "TMPDIR": str(temporary), "TEMP": str(temporary), "TMP": str(temporary),
        "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
    }
    result = subprocess.run(
        [*command, "--recover-for-repair", "--json"],
        cwd=home, env=environment, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert observed["argv"][-2:] == ["--recover-for-repair", "--json"]
    assert "--" not in observed["argv"]
    assert Path(observed["cwd"]).is_relative_to(temporary)
    assert not Path(observed["cwd"]).exists()
    assert not list(temporary.glob("manager-repair.*"))
    assert sentinel.read_bytes() == b"do not replace this installer's staging"
    assert sorted(staging.iterdir()) == [sentinel]


@pytest.mark.parametrize("relative", [True, False])
def test_native_bootstrap_preserves_hold_path_before_staging(tmp_path, monkeypatch, relative):
    from test_bootstrap_ps1 import _fake_tools, _source_repo
    from worktree_manager import self_install

    shell = shutil.which("pwsh") if os.name == "nt" else shutil.which("bash")
    if not shell or not shutil.which("git"):
        pytest.skip("matching native bootstrap shell and Git required")
    source = _source_repo(tmp_path)
    home = tmp_path / "home"
    root = home / ".worktree-manager"
    root.mkdir(parents=True)
    (root / "config.toml").write_text(
        f'[source]\nrepo = "{source.as_posix()}"\nref = "main"\n', encoding="utf-8",
    )
    hold = home / "hold file.json"
    hold.write_text(json.dumps({"active": True}))
    tools = tmp_path / "tools"
    _fake_tools(tools, uv_exit=0, python_exit=0)
    observer = tools / "observe.py"
    observer.write_text(
        "import json,os,sys\n"
        "print(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd(),"
        "'hold':os.environ['BOOTSTRAP_KILLSWITCH_STATE_FILE']}))\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        (tools / "fake-python.cmd").write_text(
            f'@echo off\r\n"{sys.executable}" -I -S -B "{observer}" %*\r\n'
            "exit /b %errorlevel%\r\n", encoding="utf-8",
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-File",
                   str(INSTALLER.parents[1] / "bootstrap.ps1")]
    else:
        (tools / "fake-python").write_text(
            f'#!/bin/sh\nexec "{sys.executable}" -I -S -B "{observer}" "$@"\n',
            encoding="utf-8",
        )
        command = [shell, str(INSTALLER.parents[1] / "bootstrap.sh")]
    result = subprocess.run(
        [*command, "--bootstrap-update", "update", "--auto"], cwd=home,
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home),
             "WORKTREE_MANAGER_ROOT": str(root),
             "BOOTSTRAP_KILLSWITCH_STATE_FILE": "./hold file.json" if relative else str(hold),
             "PATH": str(tools) + os.pathsep + os.environ.get("PATH", "")},
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert Path(observed["cwd"]).is_relative_to(root / "staging")
    assert observed["argv"][-3:] == ["--", "update", "--auto"]
    assert Path(observed["hold"]).is_absolute()
    assert Path(observed["hold"]).resolve() == hold
    monkeypatch.chdir(observed["cwd"])
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", observed["hold"])
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    adapter = self_install._runtime_installer()
    assert adapter._automatic_bootstrap_inhibited(SimpleNamespace(
        payload=INSTALLER.parents[1], command=["update", "--auto"],
    ))
    assert json.loads(hold.read_text()) == {"active": True}

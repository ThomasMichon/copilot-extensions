"""Worktrees native installation retains the existing execution owner."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from update_driver import core
from update_driver.participant import ENVIRONMENT_KEY, borrowed_custody, participant

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("_worktrees_install_driver_test", SCRIPTS / "runtime_install_driver.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(core, "default_root", lambda: tmp_path / "driver")
    monkeypatch.delenv(ENVIRONMENT_KEY, raising=False)
    return module


def _arguments(tmp_path, **values):
    return SimpleNamespace(**{
        "payload": SCRIPTS.parent, "root": tmp_path / "runtime",
        "shell": tmp_path / "powershell.exe", "action": "install",
        "project_name": None, "bootstrap_python": None, "owner": None,
        "force": False, "zero_downtime": False, "provision_missing_runtime": False,
        "remove_config": False, "check_native": False,
        "check_removal": False, "remove_runtime": False, **values,
    })


@pytest.mark.parametrize("exit_code", [0, 7])
def test_post_child_custody_failure_preserves_native_exit_status(
    adapter, tmp_path, monkeypatch, exit_code,
):
    from contextlib import nullcontext
    from update_driver import ParticipantError

    failure = ParticipantError("participant owner refused custody")
    owner = SimpleNamespace(
        request_id="request", root=tmp_path,
        assert_owned=Mock(side_effect=[None, failure]),
    )
    monkeypatch.setattr(adapter, "custody", lambda **kwargs: nullcontext(owner))
    monkeypatch.setattr(core, "status", lambda *args, **kwargs: SimpleNamespace(
        state="running", spec=SimpleNamespace(backend="engine"),
    ))
    child = SimpleNamespace(wait=Mock(return_value=exit_code))
    monkeypatch.setattr(adapter.subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(adapter.subprocess, "CREATE_NO_WINDOW", 0, raising=False)

    with pytest.raises(ParticipantError) as caught:
        adapter._execute(_arguments(tmp_path))

    assert caught.value is failure
    child.wait.assert_called_once_with()
    assert owner.assert_owned.call_count == 2
    assert f"installer child exited {exit_code}; transaction custody is unconfirmed" in "\n".join(caught.value.__notes__)


@pytest.mark.parametrize("legacy_dev", [False, True])
def test_explicit_native_installation_uses_the_existing_finite_driver(adapter, tmp_path, monkeypatch, legacy_dev):
    monkeypatch.setattr(adapter, "_source_digest", lambda payload: "a" * 64)
    args = _arguments(
        tmp_path, project_name="example", bootstrap_python=Path(sys.executable),
        owner=tmp_path / "source-owner", force=True,
        action="dev" if legacy_dev else "install",
        installation_payload=tmp_path / "canonical-payload" if legacy_dev else None,
    )
    if legacy_dev:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    else:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "context.json"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    queued = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=lambda spec, **kwargs: queued.append((spec, kwargs["runner"])) or SimpleNamespace(id="request"),
        drain=Mock(), wait=Mock(return_value=SimpleNamespace(state="completed", exit_code=7)),
    )
    assert adapter._foreground(args, driver) == 7
    spec, runner = queued[0]
    assert spec.backend == "engine-native-install"
    assert spec.project == str(args.root)
    assert spec.source == "a" * 64
    assert spec.argv == tuple(adapter._native_arguments(args))
    if legacy_dev:
        assert "COPILOT_EXTENSIONS_CONTEXT" not in dict(spec.context)
    else:
        assert dict(spec.context)["COPILOT_EXTENSIONS_CONTEXT"] == str(tmp_path / "context.json")
    assert "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE" not in dict(spec.context)
    assert adapter.NATIVE_ENV in runner.context_keys
    assert "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE" in runner.context_keys
    assert runner.argv[:6] == (
        sys.executable, "-I", "-S", "-B", "-X", "utf8",
    )
    assert runner.argv[6] == str(SCRIPTS / "runtime_install_driver.py")
    assert "--participant" in runner.argv
    if legacy_dev:
        assert runner.argv[runner.argv.index("--installation-payload") + 1] == str(args.installation_payload)
        assert spec.argv[spec.argv.index("-InstallationPayload") + 1] == str(args.installation_payload)
    driver.drain.assert_called_once_with()
    driver.wait.assert_called_once_with("request", output=sys.stderr)


def test_first_use_busy_driver_does_not_accept_a_request(adapter, tmp_path, monkeypatch, capsys):
    root = tmp_path / "driver"
    root.mkdir()
    monkeypatch.setattr(adapter, "_admission_digest", lambda _args: "a" * 64)
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_RUNTIME_PUBLICATION", raising=False)
    with core.OperationLock(root / "execution.lock"):
        result = adapter._foreground(_arguments(tmp_path, action="stamp", fail_if_busy=True), core)
    assert result == 75
    assert "::agent-worktrees-driver-busy::" in capsys.readouterr().err
    assert core.status(root=root) == ()


def test_first_use_does_not_drain_an_existing_pending_request(adapter, tmp_path, monkeypatch, capsys):
    root = tmp_path / "driver"
    request = core.enqueue(
        core.RequestSpec("fixture", cwd=str(tmp_path)),
        runner=core.TrustedRunner((sys.executable, "-c", "raise SystemExit(4)"), str(tmp_path)),
        root=root,
    )
    monkeypatch.setattr(adapter, "_admission_digest", lambda _args: "a" * 64)
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_RUNTIME_PUBLICATION", raising=False)
    result = adapter._foreground(_arguments(tmp_path, action="stamp", fail_if_busy=True), core)
    assert result == 75
    assert "::agent-worktrees-driver-busy::" in capsys.readouterr().err
    assert [(item.id, item.state) for item in core.status(root=root)] == [(request.id, "pending")]


def test_first_use_retries_a_concurrent_enqueue_without_replaying_it(adapter, tmp_path, monkeypatch, capsys):
    root = tmp_path / "driver"
    original_enqueue = core.enqueue
    peer = []

    def enqueue(spec, *, runner, root=None, **options):
        if options.get("_exclusive_custody") is not None:
            peer.append(original_enqueue(
                core.RequestSpec("fixture", cwd=str(tmp_path)),
                runner=core.TrustedRunner((sys.executable, "-c", "raise SystemExit(4)"), str(tmp_path)),
                root=root,
            ))
        return original_enqueue(spec, runner=runner, root=root, **options)

    monkeypatch.setattr(core, "enqueue", enqueue)
    monkeypatch.setattr(adapter, "_admission_digest", lambda _args: "a" * 64)
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_RUNTIME_PUBLICATION", raising=False)
    result = adapter._foreground(_arguments(tmp_path, action="stamp", fail_if_busy=True), core)
    assert result == 75
    assert "::agent-worktrees-driver-busy::" in capsys.readouterr().err
    assert len(peer) == 1
    assert [(item.id, item.state) for item in core.status(root=root)] == [(peer[0].id, "pending")]


def test_first_use_reports_an_unsettled_queue_without_retrying(adapter, tmp_path, monkeypatch):
    import sqlite3
    from contextlib import closing

    root = tmp_path / "driver"
    request = core.enqueue(
        core.RequestSpec("fixture", cwd=str(tmp_path)),
        runner=core.TrustedRunner((sys.executable, "-c", "raise SystemExit(4)"), str(tmp_path)),
        root=root,
    )
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute("UPDATE requests SET started_at=1 WHERE id=?", (request.id,))
    monkeypatch.setattr(adapter, "_admission_digest", lambda _args: "a" * 64)
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_RUNTIME_PUBLICATION", raising=False)
    with pytest.raises(core.InterruptedError, match="unsettled"):
        adapter._foreground(_arguments(tmp_path, action="stamp", fail_if_busy=True), core)
    assert [(item.id, item.state) for item in core.status(root=root)] == [(request.id, "pending")]


def test_first_use_reports_an_abandoned_running_request_without_retrying(
    adapter, tmp_path, monkeypatch, capsys,
):
    import sqlite3
    from contextlib import closing

    root = tmp_path / "driver"
    request = core.enqueue(
        core.RequestSpec("fixture", cwd=str(tmp_path)),
        runner=core.TrustedRunner((sys.executable, "-c", "raise SystemExit(4)"), str(tmp_path)),
        root=root,
    )
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute("UPDATE requests SET state='running',started_at=1 WHERE id=?", (request.id,))
    before = core.status(root=root)
    monkeypatch.setattr(adapter, "_admission_digest", lambda _args: "a" * 64)
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_RUNTIME_PUBLICATION", raising=False)

    with pytest.raises(core.InterruptedError, match="unsettled"):
        adapter._foreground(_arguments(tmp_path, action="stamp", fail_if_busy=True), core)
    assert "::agent-worktrees-driver-busy::" not in capsys.readouterr().err
    assert core.status(root=root) == before


def test_first_use_reselects_a_snapshot_changed_before_custody(adapter, tmp_path, monkeypatch, capsys):
    from contextlib import nullcontext

    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    root = tmp_path / "runtime"
    for path in (original, replacement, root):
        path.mkdir()
    (root / "payload-dir").write_text(str(replacement), encoding="utf-8")
    owner = SimpleNamespace(request_id="request", root=tmp_path, assert_owned=Mock())
    monkeypatch.setattr(adapter, "custody", lambda **_kwargs: nullcontext(owner))
    monkeypatch.setattr(core, "status", lambda *_args, **_kwargs: SimpleNamespace(
        state="running", spec=SimpleNamespace(backend="engine"),
    ))
    monkeypatch.setattr(adapter.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("stale snapshot ran"))
    args = _arguments(tmp_path, action="provision", root=root, payload=original, selected_snapshot=original)
    assert adapter._execute(args) == 75
    assert "::agent-worktrees-driver-reselect::" in capsys.readouterr().err


def test_first_use_reselects_a_snapshot_created_before_custody(adapter, tmp_path, monkeypatch, capsys):
    from contextlib import nullcontext

    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    root = tmp_path / "runtime"
    for path in (original, replacement, root):
        path.mkdir()
    args = _arguments(tmp_path, action="provision", root=root, payload=original, expect_no_snapshot=True)
    assert adapter._selected_snapshot_current(args)
    (root / "payload-dir").write_text(str(replacement), encoding="utf-8")
    owner = SimpleNamespace(request_id="request", root=tmp_path, assert_owned=Mock())
    monkeypatch.setattr(adapter, "custody", lambda **_kwargs: nullcontext(owner))
    monkeypatch.setattr(core, "status", lambda *_args, **_kwargs: SimpleNamespace(
        state="running", spec=SimpleNamespace(backend="engine"),
    ))
    monkeypatch.setattr(adapter.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("stale source ran"))
    assert adapter._execute(args) == 75
    assert "::agent-worktrees-driver-reselect::" in capsys.readouterr().err


@pytest.mark.parametrize("absent", [False, True])
def test_first_use_retains_snapshot_selection_for_the_driver(adapter, tmp_path, monkeypatch, absent):
    monkeypatch.setattr(adapter, "_source_digest", lambda _payload: "a" * 64)
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_RUNTIME_PUBLICATION", raising=False)
    selected = tmp_path / "selected"
    queued = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        run_explicit=lambda spec, **kwargs: queued.append((spec, kwargs["runner"])) or SimpleNamespace(id="request"),
        wait=Mock(return_value=SimpleNamespace(state="completed", exit_code=0)),
    )
    args = _arguments(
        tmp_path, action="provision", fail_if_busy=True,
        selected_snapshot=None if absent else selected, expect_no_snapshot=absent,
    )
    assert adapter._foreground(args, driver) == 0
    spec, runner = queued[0]
    if absent:
        assert "--expect-no-snapshot" in runner.argv and "--selected-snapshot" not in runner.argv
    else:
        assert runner.argv[runner.argv.index("--selected-snapshot") + 1] == str(selected)
        assert "--expect-no-snapshot" not in runner.argv
    assert "AGENT_WORKTREES_FIRST_USE_EXPECTED_SNAPSHOT" in runner.context_keys
    assert "AGENT_WORKTREES_FIRST_USE_EXPECTED_SNAPSHOT" not in dict(spec.context)


@pytest.mark.skipif(os.name != "nt", reason="Windows native installer")
@pytest.mark.parametrize("mode", ["project", "standalone"])
@pytest.mark.parametrize("interpreter", ["current", "python310"])
def test_real_stamp_obeys_selected_authority(tmp_path, mode, interpreter):
    from update_driver import change_authority

    python = Path(sys.executable)
    if interpreter == "python310":
        value = os.environ.get("COPILOT_EXTENSIONS_TEST_PYTHON_310")
        if not value:
            pytest.skip("Python 3.10 test interpreter not supplied")
        python = Path(value)
    shell = shutil.which("powershell.exe")
    if not shell:
        pytest.skip("Windows PowerShell is unavailable")
    area = tmp_path.parent.resolve() / ("stamp-" + uuid.uuid4().hex[:8])
    area.mkdir()
    home = area / "h"
    home.mkdir()
    project = area / "p"
    project.mkdir()
    temporary = area / "t"
    temporary.mkdir()
    root = home / ".copilot-extensions" / "update-driver"
    change_authority(mode, project="example" if mode == "project" else "", root=root, timeout=5,
                     verify_capabilities=lambda _deadline: True)
    environment = {
        key: os.environ[key] for key in (
            "PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "ProgramFiles", "ProgramFiles(x86)",
        ) if key in os.environ
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "appdata"),
        LOCALAPPDATA=str(home / "localappdata"), TEMP=str(temporary), TMP=str(temporary),
        COPILOT_PLUGIN_ROOT=str(SCRIPTS.parent), COPILOT_EXTENSIONS_TEST_CONTAINED="0",
        COPILOT_EXTENSIONS_DAEMON_REGISTRY=str(area / "instances"),
        PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
    )
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(SCRIPTS / "install.ps1"), "-Action", "stamp",
         "-InstallDir", str(home / ".agent-worktrees"), "-BootstrapPython", str(python)],
        cwd=project, env=environment, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    marker = home / ".agent-worktrees" / "payload-dir"
    if mode == "project":
        assert result.returncode != 0
        assert "authority" in (result.stdout + result.stderr).lower()
        assert not marker.exists() and not (home / ".agent-worktrees").exists()
        assert not (home / ".local" / "bin" / "agent-worktrees.cmd").exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert marker.is_file() and (home / ".local" / "bin" / "agent-worktrees.cmd").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows native first-use driver")
@pytest.mark.parametrize("interpreter", ["current", "python310"])
def test_real_first_use_reports_abandoned_running_work(tmp_path, interpreter):
    import sqlite3
    from contextlib import closing

    python = Path(sys.executable)
    if interpreter == "python310":
        value = os.environ.get("COPILOT_EXTENSIONS_TEST_PYTHON_310")
        if not value:
            pytest.skip("Python 3.10 test interpreter not supplied")
        python = Path(value)
    shell = shutil.which("powershell.exe")
    if not shell:
        pytest.skip("Windows PowerShell is unavailable")
    area = tmp_path.parent.resolve() / ("abandoned-" + uuid.uuid4().hex[:8])
    area.mkdir()
    home, project, temporary = (area / name for name in ("h", "p", "t"))
    for path in (home, project, temporary):
        path.mkdir()
    root = home / ".copilot-extensions" / "update-driver"
    request = core.enqueue(
        core.RequestSpec("fixture", cwd=str(project)),
        runner=core.TrustedRunner((str(python), "-c", "raise SystemExit(4)"), str(project)),
        root=root,
    )
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute("UPDATE requests SET state='running',started_at=1 WHERE id=?", (request.id,))
    before = core.status(root=root)
    environment = {
        key: os.environ[key] for key in (
            "PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "ProgramFiles", "ProgramFiles(x86)",
        ) if key in os.environ
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), APPDATA=str(area / "appdata"),
        LOCALAPPDATA=str(area / "localappdata"), TEMP=str(temporary), TMP=str(temporary),
        PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
    )
    result = subprocess.run(
        [str(python), "-I", "-S", "-B", str(SCRIPTS / "runtime_install_driver.py"),
         "--payload", str(SCRIPTS.parent), "--root", str(home / ".agent-worktrees"),
         "--shell", shell, "--action", "stamp", "--fail-if-busy"],
        cwd=project, env=environment, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=90, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert "unsettled running work; recovery required" in result.stderr
    assert "::agent-worktrees-driver-busy::" not in result.stderr
    assert core.status(root=root) == before
    assert not (home / ".agent-worktrees").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native first-use locking")
@pytest.mark.parametrize("entry", ["payload", "global", "global-retarget"])
def test_first_use_does_not_hold_provision_lock_while_waiting_for_driver(tmp_path, entry):
    import ctypes

    from agent_procutil import spawn_sync_in_kill_on_close_job
    from update_driver import change_authority

    shell = shutil.which("powershell.exe")
    if not shell:
        pytest.skip("Windows PowerShell is unavailable")
    area = tmp_path.parent.resolve() / ("first-use-" + uuid.uuid4().hex[:8])
    area.mkdir()
    home, project, temporary = (area / name for name in ("h", "p", "t"))
    for path in (home, project, temporary):
        path.mkdir()
    installed = home / ".copilot" / "installed-plugins" / "fixture" / "agent-worktrees"
    shutil.copytree(
        SCRIPTS.parent, installed,
        ignore=shutil.ignore_patterns(
            "tests", "docs", "skills", "__pycache__", ".pytest_cache", "*.pyc",
        ),
    )
    installer = installed / "scripts" / "install.ps1"
    installer.write_text(
        "param([Parameter(Position=0)][string]$Action,[string]$InstallDir)\n"
        "# agent-worktrees:first-use-driver-nonblocking-v1\n"
        "if ($env:AGENT_WORKTREES_NATIVE_INSTALLATION -eq '1') { exit 19 }\n"
        "if (-not $InstallDir) { $InstallDir = Join-Path $env:USERPROFILE '.agent-worktrees' }\n"
        "$driverAction = if ($Action -eq 'provision') { 'stamp' } else { $Action }\n"
        "$arguments = @('-I','-S','-B','-X','utf8',"
        "(Join-Path $PSScriptRoot 'runtime_install_driver.py'),"
        "'--payload',(Split-Path -Parent $PSScriptRoot),'--root',$InstallDir,"
        "'--shell',(Get-Process -Id $PID).Path,'--action',$driverAction)\n"
        "if ($env:AGENT_WORKTREES_FIRST_USE_NO_WAIT -eq '1') { $arguments += '--fail-if-busy' }\n"
        "& $env:TEST_PYTHON @arguments\nexit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    root = home / ".copilot-extensions" / "update-driver"
    change_authority("standalone", root=root, timeout=5,
                     verify_capabilities=lambda _deadline: True)
    environment = {
        key: os.environ[key] for key in (
            "PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "ProgramFiles", "ProgramFiles(x86)",
        ) if key in os.environ
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), APPDATA=str(area / "appdata"),
        LOCALAPPDATA=str(area / "localappdata"), TEMP=str(temporary), TMP=str(temporary),
        COPILOT_PLUGIN_ROOT=str(installed), COPILOT_EXTENSIONS_TEST_CONTAINED="0",
        COPILOT_EXTENSIONS_DAEMON_REGISTRY=str(area / "instances"),
        PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", TEST_PYTHON=sys.executable,
    )
    command = installed / "bin" / "payload" / "agent-worktrees.ps1"
    mutex_name = "Local\\Copilot.AgentWorktrees.Provision.Test." + uuid.uuid4().hex
    selected = area / "selected.txt"
    if entry.startswith("global"):
        runtime = home / ".agent-worktrees"
        runtime.mkdir()
        (runtime / "payload-dir").write_text(str(installed), encoding="utf-8")
        command = installed / "bin" / "agent-worktrees.ps1"
        stub = command.read_text(encoding="utf-8")
        original_mutex = "Local\\Copilot.AgentWorktrees.Provision"
        assert stub.count(original_mutex) == 1
        command.write_text(stub.replace(original_mutex, mutex_name), encoding="utf-8")
        if entry == "global-retarget":
            replacement = area / "replacement"
            (replacement / "scripts").mkdir(parents=True)
            (replacement / "scripts" / "install.ps1").write_text(
                "param([Parameter(Position=0)][string]$Action,[string]$InstallDir)\n"
                "# agent-worktrees:first-use-driver-nonblocking-v1\n"
                "[IO.File]::WriteAllText($env:TEST_SELECTED, 'replacement')\nexit 19\n",
                encoding="utf-8",
            )
            environment["TEST_SELECTED"] = str(selected)
    execution = core.OperationLock(root / "execution.lock")
    execution.acquire()
    log = area / "dispatcher.log"
    process = job = None
    attempted = available = False
    try:
        with log.open("wb") as output:
            process, job = spawn_sync_in_kill_on_close_job(
                [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(command), "--version"],
                cwd=project, env=environment, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
                require_job=True, allow_breakaway=False,
            )
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and process.poll() is None:
                requests = core.status(root=root)
                text = log.read_text(encoding="utf-8", errors="replace")
                if any(request.state == "pending" for request in requests) or "driver is busy" in text:
                    attempted = True
                    break
                time.sleep(.1)
            deadline = time.monotonic() + 3
            provision_lock = home / ".agent-worktrees" / ".provision.lock"
            while attempted and time.monotonic() < deadline:
                if entry.startswith("global"):
                    kernel = ctypes.windll.kernel32
                    kernel.CreateMutexW.restype = ctypes.c_void_p
                    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
                    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
                    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                    handle = kernel.CreateMutexW(None, False, mutex_name)
                    assert handle
                    try:
                        if kernel.WaitForSingleObject(handle, 0) in (0, 0x80):
                            available = True
                            if entry == "global-retarget":
                                (runtime / "payload-dir").write_text(str(replacement), encoding="utf-8")
                            assert kernel.ReleaseMutex(handle)
                    finally:
                        kernel.CloseHandle(handle)
                else:
                    try:
                        with provision_lock.open("a+b"):
                            available = True
                    except OSError:
                        pass
                if available:
                    break
                time.sleep(.05)
    finally:
        execution.release()
        if process is not None:
            try:
                process.wait(timeout=180)
            finally:
                job.close()
                if process.poll() is None:
                    process.wait(timeout=10)
    details = log.read_text(encoding="utf-8", errors="replace")
    assert attempted, details
    assert available, "first use waited for the driver while holding the provision lock\n" + details
    assert process.returncode == (19 if entry == "payload" else 1), details
    if entry == "global-retarget":
        assert selected.read_text(encoding="utf-8") == "replacement"


def test_stamp_source_change_is_refused_before_execution(adapter, tmp_path, monkeypatch):
    from contextlib import nullcontext

    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text('{"name":"agent-worktrees"}', encoding="utf-8")
    args = _arguments(tmp_path, action="stamp", payload=payload)
    original = adapter._admission_digest(args)
    metadata = payload / "runtime-source.json"
    metadata.write_text('{"version":1}', encoding="utf-8")
    with_metadata = adapter._admission_digest(args)
    assert with_metadata != original
    metadata.write_text('{"version":2}', encoding="utf-8")
    changed_metadata = adapter._admission_digest(args)
    assert changed_metadata != with_metadata
    (payload / "snapshot-provenance.json").write_text("changed", encoding="utf-8")
    assert adapter._admission_digest(args) != changed_metadata
    owner = SimpleNamespace(request_id="request", root=tmp_path, assert_owned=Mock())
    monkeypatch.setattr(adapter, "custody", lambda **_kwargs: nullcontext(owner))
    monkeypatch.setattr(core, "status", lambda *_args, **_kwargs: SimpleNamespace(
        state="running", spec=SimpleNamespace(
            backend="engine-native-install", argv=tuple(adapter._native_arguments(args)),
            source=original, project=str(args.root.resolve()),
        ),
    ))
    monkeypatch.setattr(adapter.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("changed source ran"))
    with pytest.raises(RuntimeError, match="changed after driver admission"):
        adapter._execute(args)


@pytest.mark.parametrize("relative", [
    ("nested", ".pytest_cache", "payload.ps1"),
    ("nested", "module.pyc"),
])
def test_stamp_source_identity_includes_files_copied_from_generated_paths(adapter, tmp_path, relative):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text('{"name":"agent-worktrees"}', encoding="utf-8")
    copied = payload.joinpath(*relative)
    copied.parent.mkdir(parents=True, exist_ok=True)
    copied.write_bytes(b"before")
    args = _arguments(tmp_path, action="stamp", payload=payload)
    original = adapter._admission_digest(args)
    copied.write_bytes(b"after")
    assert adapter._admission_digest(args) != original


def test_stamp_source_identity_tracks_generated_files_in_source_snapshots(adapter, tmp_path):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text('{"name":"agent-worktrees"}', encoding="utf-8")
    (payload / "runtime-source.json").write_text("{}", encoding="utf-8")
    copied = payload / "__pycache__" / "module.pyc"
    copied.parent.mkdir()
    copied.write_bytes(b"before")
    args = _arguments(tmp_path, action="stamp", payload=payload)
    original = adapter._admission_digest(args)
    copied.write_bytes(b"after")
    assert adapter._admission_digest(args) != original


def test_stamp_bootstrap_rejects_redirected_admission_sources(adapter, tmp_path):
    payload = tmp_path / "payload"
    base = payload / "scripts" / "_runtime_admission" / "libs"
    for name in ("update-driver", "single-instance-lease", "agent-procutil"):
        (base / name / "src").mkdir(parents=True)
    target = tmp_path / "outside"
    target.mkdir()
    (base / "zdd").mkdir()
    try:
        (base / "zdd" / "src").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    with pytest.raises(RuntimeError, match="ordinary admission sources"):
        adapter.bootstrap(payload, stamp=True)


def test_stamp_bootstrap_refuses_missing_admission_sources(adapter, tmp_path):
    with pytest.raises(RuntimeError, match="ordinary admission sources"):
        adapter.bootstrap(tmp_path / "payload", stamp=True)


def test_stamp_source_refuses_nonfile_metadata(adapter, tmp_path):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text('{"name":"agent-worktrees"}', encoding="utf-8")
    (payload / "runtime-source.json").mkdir()
    with pytest.raises(RuntimeError, match="source metadata"):
        adapter._admission_digest(_arguments(tmp_path, action="stamp", payload=payload))


@pytest.mark.parametrize("action", ["install", "repair"])
@pytest.mark.parametrize("input_timeout", [None, 1234.5])
def test_native_source_installation_retains_selection_through_the_driver(
    adapter, tmp_path, monkeypatch, action, input_timeout,
):
    monkeypatch.setattr(adapter, "_source_digest", lambda payload: "a" * 64)
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_RUNTIME_PUBLICATION", raising=False)
    snapshot = tmp_path / "source inputs"
    descriptor = {"kind": "github", "canonical": "github:example/suite", "ref": ""}
    args = _arguments(
        tmp_path, action=action, install_source=snapshot, source_descriptor=descriptor,
        input_timeout=input_timeout,
    )
    queued = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=lambda spec, **kwargs: queued.append((spec, kwargs["runner"])) or SimpleNamespace(id="request"),
        drain=Mock(), wait=Mock(return_value=SimpleNamespace(state="completed", exit_code=0)),
    )
    assert adapter._foreground(args, driver) == 0
    spec, runner = queued[0]
    index = spec.argv.index("-InstallSource")
    assert spec.argv[index + 1] == str(snapshot)
    index = runner.argv.index("--install-source")
    assert runner.argv[index + 1] == str(snapshot)
    index = spec.argv.index("-RuntimeSourceDescriptor")
    assert json.loads(spec.argv[index + 1]) == descriptor
    index = runner.argv.index("--source-descriptor")
    assert json.loads(runner.argv[index + 1]) == descriptor
    if input_timeout is None:
        assert "-InputTimeout" not in spec.argv and "--input-timeout" not in runner.argv
    else:
        assert spec.argv[spec.argv.index("-InputTimeout") + 1] == str(input_timeout)
        assert runner.argv[runner.argv.index("--input-timeout") + 1] == str(input_timeout)


@pytest.mark.parametrize("action", ["install", "update"])
def test_native_runtime_only_survives_driver_admission(adapter, tmp_path, monkeypatch, action):
    monkeypatch.setattr(adapter, "_source_digest", lambda payload: "a" * 64)
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_RUNTIME_PUBLICATION", raising=False)
    queued = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=lambda spec, **kwargs: queued.append((spec, kwargs["runner"])) or SimpleNamespace(id="request"),
        drain=Mock(), wait=Mock(return_value=SimpleNamespace(state="completed", exit_code=0)),
    )
    assert adapter._foreground(_arguments(tmp_path, action=action, runtime_only=True), driver) == 0
    spec, runner = queued[0]
    assert "-RuntimeOnly" in spec.argv
    assert "-ProjectName" not in spec.argv
    assert "--runtime-only" in runner.argv
    assert "--project-name" not in runner.argv


@pytest.mark.parametrize("action,project", [
    ("install", "example"), ("update", ""), ("repair", None), ("uninstall", None),
])
def test_native_runtime_only_refuses_contradictions_before_admission(
    adapter, tmp_path, monkeypatch, capsys, action, project,
):
    bootstrap = Mock()
    monkeypatch.setattr(adapter, "bootstrap", bootstrap)
    argv = [
        "--payload", str(SCRIPTS.parent), "--root", str(tmp_path / "runtime"),
        "--shell", str(tmp_path / "powershell.exe"), "--action", action, "--runtime-only",
    ]
    if project is not None:
        argv.extend(["--project-name", project])
    with pytest.raises(SystemExit) as failure:
        adapter.main(argv)
    assert failure.value.code == 2
    assert "requires install/update without --project-name" in capsys.readouterr().err
    bootstrap.assert_not_called()
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_native_input_timeout_refuses_invalid_budget_before_admission(
    adapter, tmp_path, monkeypatch, value, capsys,
):
    bootstrap = Mock()
    monkeypatch.setattr(adapter, "bootstrap", bootstrap)
    with pytest.raises(SystemExit) as failure:
        adapter.main([
            "--payload", str(SCRIPTS.parent), "--root", str(tmp_path / "runtime"),
            "--shell", str(tmp_path / "powershell.exe"), "--action", "repair",
            "--install-source", str(tmp_path / "inputs"), "--input-timeout=" + value,
        ])
    assert failure.value.code == 2
    assert "positive and finite" in capsys.readouterr().err
    bootstrap.assert_not_called()
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("action", ["uninstall", "dev", "dev-release"])
def test_native_driver_refuses_source_selection_for_other_actions(
    adapter, tmp_path, monkeypatch, action, capsys,
):
    bootstrap = Mock()
    monkeypatch.setattr(adapter, "bootstrap", bootstrap)
    with pytest.raises(SystemExit) as failure:
        adapter.main([
            "--payload", str(SCRIPTS.parent), "--root", str(tmp_path / "runtime"),
            "--shell", str(tmp_path / "powershell.exe"), "--action", action,
            "--install-source", str(tmp_path / "inputs"),
        ])
    assert failure.value.code == 2
    assert "--install-source selects only" in capsys.readouterr().err
    bootstrap.assert_not_called()


@pytest.mark.parametrize("ticket", [
    "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION",
])
def test_cold_native_entry_never_waits_for_the_driver_under_publication_custody(
    adapter, tmp_path, monkeypatch, ticket,
):
    monkeypatch.setenv(ticket, "live-only-not-journaled")
    driver = SimpleNamespace(enqueue=Mock())
    with pytest.raises(RuntimeError, match="precede publication custody"):
        adapter._foreground(_arguments(tmp_path), driver)
    driver.enqueue.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows installed dev-release entry")
@pytest.mark.parametrize("as_json", [False, True])
def test_public_dev_release_uses_the_existing_json_execution_boundary(
    tmp_path, monkeypatch, capsys, as_json,
):
    import argparse
    from contextlib import contextmanager
    from agent_worktrees import runtime_dev_cli, update_driver_json_cli

    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    owner = tmp_path / "absent-owner"
    root = tmp_path / "runtime"
    slot = root / "versions" / "dev"
    monkeypatch.setattr(runtime_dev_cli.sys, "prefix", str(slot))
    monkeypatch.setattr(runtime_dev_cli.cfg, "install_dir", lambda: root)
    seen = []

    @contextmanager
    def retain(requested, version):
        assert requested == root and version == "dev"
        seen.append("retained")
        try:
            yield slot
        finally:
            seen.append("released")

    def run(verb, flags, args, execute, *, retained_dev):
        assert verb == "dev-release" and flags == ["--json", "--owner", str(owner)]
        assert args.json is True and args.owner == owner
        assert retained_dev and seen == ["retained"]
        seen.append(verb)
        return execute(args)

    def release(args):
        runtime_dev_cli.output._json_output({"runtime_version": "1.2.3"})
        return 0

    monkeypatch.setattr(update_driver_json_cli, "_run_request", run)
    monkeypatch.setattr(runtime_dev_cli, "_lifecycle", lambda: SimpleNamespace(
        runtime=SimpleNamespace(retention_lease=retain),
    ))
    monkeypatch.setattr(runtime_dev_cli, "_cmd_dev_release", release)
    with runtime_dev_cli.output.capture_json_output() as captured:
        assert runtime_dev_cli.cmd_dev_release(argparse.Namespace(owner=owner, json=as_json)) == 0
    output = captured.getvalue() if as_json else capsys.readouterr().out
    if as_json:
        assert json.loads(output)["runtime_version"] == "1.2.3"
    else:
        assert "current-version -> 1.2.3" in output
    assert seen == ["retained", "dev-release", "released"]


@pytest.mark.skipif(os.name != "nt", reason="Windows installed dev-release entry")
def test_public_dev_release_reports_retention_failure_as_json(tmp_path, monkeypatch):
    import argparse
    from contextlib import contextmanager
    from agent_worktrees import runtime_dev_cli, update_driver_json_cli

    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    root = tmp_path / "runtime"
    monkeypatch.setattr(runtime_dev_cli.sys, "prefix", str(root / "versions" / "dev"))
    monkeypatch.setattr(runtime_dev_cli.cfg, "install_dir", lambda: root)

    @contextmanager
    def retain(*args):
        raise RuntimeError("retention unavailable")
        yield

    monkeypatch.setattr(runtime_dev_cli, "_lifecycle", lambda: SimpleNamespace(
        runtime=SimpleNamespace(retention_lease=retain),
    ))
    monkeypatch.setattr(update_driver_json_cli, "_run_request", lambda *a, **k: pytest.fail("unretained execution"))
    with runtime_dev_cli.output.capture_json_output() as captured:
        assert runtime_dev_cli.cmd_dev_release(argparse.Namespace(json=True)) == 1
    assert json.loads(captured.getvalue())["error"] == "retention unavailable"


@pytest.mark.skipif(os.name != "nt", reason="Windows native installation")
@pytest.mark.parametrize("backend", ["engine", "engine-launch-update"])
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("check", [False, True])
def test_native_handoff_reexports_live_custody_without_another_queue(
    adapter, tmp_path, monkeypatch, scoped, check, backend,
):
    args = _arguments(tmp_path, check_native=check)
    request = core.enqueue(
        core.RequestSpec(backend),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )
    calls = []

    def spawn(command, **options):
        assert command[:1] == [str(args.shell)]
        assert command[-4:] == ["-Action", "install", "-InstallDir", str(args.root)]
        assert options["creationflags"] == subprocess.CREATE_NO_WINDOW
        assert options["stdout"] is sys.stdout and options["stderr"] is sys.stderr
        assert ENVIRONMENT_KEY not in os.environ
        assert options["env"][adapter.NATIVE_ENV] == "1"
        with participant(root=core.default_root(), witness=options["env"][ENVIRONMENT_KEY]) as child:
            child.assert_owned()
        calls.append(command)
        return SimpleNamespace(wait=lambda: 0)

    monkeypatch.setattr(adapter.subprocess, "Popen", spawn)
    monkeypatch.setattr(adapter, "_source_digest", lambda payload: pytest.fail("engine owner must not enqueue"))

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        if scoped:
            with borrowed_custody(root=core.default_root()):
                return adapter._execute(args)
        return adapter._execute(args)

    assert core.drain(executor=execute) == 0
    assert core.status(request.id).state == "completed"
    assert len(calls) == (0 if check else 1)


@pytest.mark.parametrize("changed", ["source", "root", "arguments", "backend", None])
def test_native_participant_checks_exact_admitted_installation(adapter, tmp_path, monkeypatch, changed):
    args = _arguments(tmp_path, check_native=True)
    monkeypatch.setattr(adapter, "_source_digest", lambda payload: "a" * 64)
    request = core.enqueue(
        core.RequestSpec(
            "unrelated" if changed == "backend" else "engine-native-install",
            ("different",) if changed == "arguments" else tuple(adapter._native_arguments(args)),
            project="different" if changed == "root" else str(args.root),
            source="different" if changed == "source" else "a" * 64,
        ),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        return adapter._execute(args)

    if changed is None:
        assert core.drain(executor=execute) == 0
    else:
        with pytest.raises(RuntimeError, match="unclosed participants"):
            core.drain(executor=execute)
    assert core.status(request.id).state == ("completed" if changed is None else "interrupted")


@pytest.mark.skipif(os.name != "nt", reason="Windows native installation")
def test_missing_custody_refuses_before_native_execution(adapter, tmp_path, monkeypatch):
    spawn = Mock()
    monkeypatch.setattr(adapter.subprocess, "Popen", spawn)
    with pytest.raises(RuntimeError, match="live driver custody"):
        adapter._execute(_arguments(tmp_path))
    spawn.assert_not_called()
    assert not core.default_root().exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native removal custody")
@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize("staged", [False, True])
def test_native_uninstall_admits_then_removes_broker_before_finite_code(
    adapter, tmp_path, monkeypatch, check, staged,
):
    from update_driver import windows_broker

    args = _arguments(
        tmp_path, action="uninstall", check_removal=check, remove_runtime=not check,
    )
    source = tmp_path / "original-payload" if staged else args.payload
    project = tmp_path / "original-project"
    monkeypatch.setenv("COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT", str(project))
    if staged:
        monkeypatch.setenv("COPILOT_PLUGIN_STAGED_FROM", str(source))
    else:
        monkeypatch.delenv("COPILOT_PLUGIN_STAGED_FROM", raising=False)
    phases = []

    def cleanup(**arguments):
        assert arguments["root"] == args.root
        assert arguments["profile"] == SCRIPTS / "uninstall-profile.json"
        assert arguments["profile"].is_file()
        assert arguments["project_name"] == args.project_name
        assert arguments["purge"] == args.remove_config
        assert os.environ["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] == str(project)
        assert Path(os.environ.get("COPILOT_PLUGIN_STAGED_FROM") or args.payload) == source
        arguments["controller_custody"].assert_owned()
        phases.append("admit" if arguments.get("check_consumers") else "finite")
        return {"status": "ready" if arguments.get("check_consumers") else "removed"}

    monkeypatch.setattr(adapter, "_uninstall_policy", lambda: SimpleNamespace(cleanup=cleanup))
    monkeypatch.setattr(windows_broker, "remove", lambda root: phases.append("broker"))
    monkeypatch.setattr(adapter.subprocess, "Popen", lambda *a, **k: pytest.fail("another installer spawned"))
    request = core.enqueue(
        core.RequestSpec("engine"),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        return adapter._execute(args)

    assert core.drain(executor=execute) == 0
    assert core.status(request.id).state == "completed"
    assert phases == (["admit"] if check else ["admit", "broker", "finite"])


@pytest.mark.skipif(os.name != "nt", reason="Windows native removal custody")
@pytest.mark.parametrize("failure", ["admit", "broker"])
def test_failed_native_uninstall_admission_or_broker_removal_retains_runtime(
    adapter, tmp_path, monkeypatch, failure,
):
    from update_driver import windows_broker

    phases = []
    args = _arguments(tmp_path, action="uninstall", remove_runtime=True)

    def phase(name):
        phases.append(name)
        if name == failure:
            raise RuntimeError("retained removal obligation")
        return {"status": "ready"}

    monkeypatch.setattr(adapter, "_uninstall_policy", lambda: SimpleNamespace(
        cleanup=lambda **arguments: phase("admit" if arguments.get("check_consumers") else "finite"),
    ))
    monkeypatch.setattr(windows_broker, "remove", lambda root: phase("broker"))
    request = core.enqueue(
        core.RequestSpec("engine"),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        return adapter._execute(args)

    with pytest.raises(RuntimeError, match="unclosed participants"):
        core.drain(executor=execute)
    assert core.status(request.id).state == "interrupted"
    assert phases == (["admit"] if failure == "admit" else ["admit", "broker"])
    assert not args.root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows adoption adapter")
@pytest.mark.parametrize("scoped", [False, True])
def test_broker_adoption_uses_the_retained_installed_entry(adapter, tmp_path, monkeypatch, scoped):
    import versioned_runtime as runtime

    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    root, slot = tmp_path / "runtime", tmp_path / "runtime" / "versions" / "1.2.3"
    interpreter = slot / "Scripts" / "python.exe"
    monkeypatch.setattr(runtime, "slot_python", lambda *args: interpreter)
    request = core.enqueue(
        core.RequestSpec("engine"),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )

    def spawn(command, **options):
        assert command == [
            str(interpreter), "-I", "-m", "update_driver.windows_broker",
            "--runtime-root", str(root), "--slot", str(slot),
            "--execution-root", str(core.default_root()),
        ]
        assert ENVIRONMENT_KEY not in os.environ
        with participant(root=core.default_root(), witness=options["env"][ENVIRONMENT_KEY]) as child:
            child.assert_owned()
        return SimpleNamespace(returncode=0, communicate=lambda: ('{"adopted":true}', ""))

    monkeypatch.setattr(adapter.subprocess, "Popen", spawn)

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        if scoped:
            with borrowed_custody(root=core.default_root()):
                adapter.adopt_broker(root, slot)
        else:
            adapter.adopt_broker(root, slot)
        return 0

    assert core.drain(executor=execute) == 0
    assert core.status(request.id).state == "completed"


def test_contained_installation_does_not_register_a_host_task(adapter, tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    spawn = Mock()
    monkeypatch.setattr(adapter.subprocess, "Popen", spawn)
    adapter.adopt_broker(tmp_path, tmp_path / "versions" / "1.2.3")
    spawn.assert_not_called()
    assert not core.default_root().exists()


def test_unowned_bootstrap_environment_never_requires_installed_driver_dependencies(adapter, monkeypatch):
    monkeypatch.delitem(sys.modules, "update_driver")
    monkeypatch.setenv("BOOTSTRAP_CONTEXT", "preserved")
    assert adapter.child_environment()["BOOTSTRAP_CONTEXT"] == "preserved"
    assert "update_driver" not in sys.modules


def test_installer_runner_reexports_only_the_scoped_witness(adapter, tmp_path, monkeypatch):
    from agent_worktrees import installer_run

    request = core.enqueue(
        core.RequestSpec("engine"),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )
    seen = []

    def spawn(argv, cwd, env, capture, **kwargs):
        assert ENVIRONMENT_KEY not in os.environ
        assert env["TARGET_CONTEXT"] == "preserved"
        with participant(root=core.default_root(), witness=env[ENVIRONMENT_KEY]) as child:
            child.assert_owned()
        seen.append(argv)
        return SimpleNamespace(returncode=0, communicate=lambda **kwargs: ("", "")), None

    monkeypatch.setattr(installer_run, "_spawn", spawn)

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        with borrowed_custody(root=core.default_root()):
            result = installer_run.run_installer(
                ["installer"], cwd=tmp_path, env={"TARGET_CONTEXT": "preserved"}, deadline_s=5,
            )
        assert result.status == "OK"
        return 0

    assert core.drain(executor=execute) == 0
    assert core.status(request.id).state == "completed"
    assert seen == [["installer"]]


@pytest.mark.skipif(os.name != "nt", reason="actual Windows native driver entry")
@pytest.mark.parametrize("abandoned,runtime_only", [(False, False), (False, True), (True, False)])
def test_real_cold_driver_and_native_reentry_use_the_same_owner(
    adapter, tmp_path, monkeypatch, abandoned, runtime_only,
):
    """Use a copied fixed no-install shell, never the product installer."""
    from agent_procutil.owned_process import run_owned

    shell = shutil.which("powershell.exe")
    if shell is None:
        pytest.skip("native PowerShell is unavailable")
    monkeypatch.setenv("AGENT_WORKTREES_INSTALL_DEADLINE_SEC", "7")
    monkeypatch.setenv("COPILOT_PLUGIN_INSTALL_DEADLINE_SEC", "500")
    engine = importlib.import_module("installer_engine")
    minimal = tmp_path / "minimal"
    scripts = minimal / "scripts"
    scripts.mkdir(parents=True)
    for source in SCRIPTS.glob("*.py"):
        if source.name.startswith(("installer_", "versioned_runtime", "runtime_install_driver")):
            shutil.copyfile(source, scripts / source.name)
    for name in ("update-driver", "agent-procutil", "single-instance-lease", "zdd"):
        shutil.copytree(
            SCRIPTS.parents[2] / "libs" / name, tmp_path / "libs" / name,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache", "tests"),
        )
    (minimal / "pyproject.toml").write_text(
        '[project]\nname="agent-worktrees"\nversion="1.0.0"\n'
        '[tool.uv.sources]\n'
        'agent-update-driver = { path = "../libs/update-driver" }\n',
        encoding="utf-8",
    )
    payload = engine.freeze_inputs(minimal, tmp_path / "source").plugin
    receipt = tmp_path / "receipt.json"
    entry = payload / "scripts" / "runtime_install_driver.py"
    script = payload / "scripts" / "install.ps1"
    helper = payload / "scripts" / "versioned_runtime_native.ps1"
    shutil.copyfile(SCRIPTS / helper.name, helper)
    script.write_text(
        "param([string]$Action,[string]$InstallDir,[switch]$RuntimeOnly)\n"
        "$ErrorActionPreference='Stop'\n"
        f". '{helper}'\n"
        f"Invoke-Hidden -FilePath '{sys.executable}' -StreamOutput -ArgList (@("
        f"'-I','-S','-B','{entry}','--payload','{payload}',"
        f"'--root',$InstallDir,'--shell','{shell}','--action',$Action,'--check-native') "
        "+ $(if ($RuntimeOnly) { @('--runtime-only') } else { @() }))\n"
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        "[Console]::Out.WriteLine('fixed-native-out')\n"
        "[Console]::Error.WriteLine('fixed-native-error')\n"
        f"@{{root=$InstallDir;driver=$env:AGENT_WORKTREES_NATIVE_INSTALLATION;"
        f"witness=[bool]$env:UPDATE_DRIVER_WITNESS;deadline=$env:AGENT_WORKTREES_INSTALL_DEADLINE_SEC;"
        f"globalDeadline=$env:COPILOT_PLUGIN_INSTALL_DEADLINE_SEC;"
        f"runtimeOnly=[bool]$RuntimeOnly}} | ConvertTo-Json | Set-Content -LiteralPath '{receipt}'\n",
        encoding="utf-8",
    )
    if abandoned:
        child = payload / "abandon.py"
        child.write_text(
            "import importlib.util,os\nfrom pathlib import Path\n"
            f"spec=importlib.util.spec_from_file_location('native_fixture_driver', {str(entry)!r})\n"
            "driver=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(driver)\n"
            f"driver.bootstrap(Path({str(payload)!r}))\n"
            "from update_driver import borrowed_custody\n"
            "with borrowed_custody() as custody, custody.execution_scope():\n"
            "    os._exit(7)\n",
            encoding="utf-8",
        )
        script.write_text(
            "param([string]$Action,[string]$InstallDir)\n"
            "$ErrorActionPreference='Stop'\n"
            f". '{helper}'\n"
            f"Invoke-Hidden -FilePath '{sys.executable}' -StreamOutput "
            f"-ArgList @('-I','-S','-B','{child}')\n"
            "exit $LASTEXITCODE\n",
            encoding="utf-8",
        )
    def report_progress():
        rows = core.status(root=Path.home() / ".copilot-extensions" / "update-driver")
        print(f"native-driver-progress requests={len(rows)}", flush=True)
        for row in rows:
            print(f"native-driver-progress state={row.state} error={row.error}", flush=True)
            if row.log_path.is_file():
                print(row.log_path.read_text(encoding="utf-8")[-8192:], flush=True)

    diagnostic = threading.Timer(10, report_progress)
    diagnostic.daemon = True
    diagnostic.start()
    try:
        result = run_owned([
            sys.executable, "-I", "-S", "-B", str(entry),
            "--payload", str(payload), "--root", str(tmp_path / "runtime"),
            "--shell", shell, "--action", "install",
            *(["--runtime-only"] if runtime_only else []),
        ], timeout=30)
    finally:
        diagnostic.cancel()
        diagnostic.join(timeout=1)
    if abandoned:
        assert result.returncode != 0
        assert not receipt.exists()
        rows = core.status(root=Path.home() / ".copilot-extensions" / "update-driver")
        assert len(rows) == 1 and rows[0].state == "interrupted"
        assert rows[0].exit_code is None and rows[0].settled_at is None
        log = rows[0].log_path.read_text(encoding="utf-8")
        assert "participant owner refused custody" in log
        assert "installer child exited 7; transaction custody is unconfirmed" in log
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(receipt.read_text(encoding="utf-8-sig")) == {
        "root": str(tmp_path / "runtime"), "driver": "1", "witness": True,
        "deadline": "7", "globalDeadline": "500",
        "runtimeOnly": runtime_only,
    }
    rows = core.status(root=Path.home() / ".copilot-extensions" / "update-driver")
    assert len(rows) == 1 and rows[0].state == "completed"
    assert rows[0].exit_code == 0 and rows[0].error is None
    log = rows[0].log_path.read_text(encoding="utf-8")
    assert log.count("fixed-native-out") == log.count("fixed-native-error") == 1

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_dispatch import supervisor_native_lifecycle as native
from agent_dispatch.supervisor_daemon import SELF_UPDATE_EXIT_CODE
from tests.test_supervisor_daemon import FakeClient, FakeLauncher, FakeLock, _daemon, _reg

PLUGIN = Path(__file__).resolve().parents[1]
UNIT = "agent-dispatch-supervisor.service"


def _native_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISOR_SYSTEMD_UNIT", UNIT)
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISOR_MAIN_PID", str(os.getpid()))
    read_text = Path.read_text

    def read(path, *args, **kwargs):
        if path.as_posix() == "/proc/self/cgroup":
            return f"0::/user.slice/app.slice/{UNIT}\n"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)


def test_native_identity_requires_main_pid_and_exact_unit_cgroup(monkeypatch):
    _native_identity(monkeypatch)
    assert native.native_restart_unit() == UNIT
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISOR_MAIN_PID", str(os.getpid() + 1))
    with pytest.raises(native.NativeOwnershipUnconfirmed, match="main-PID"):
        native.native_restart_unit()
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISOR_MAIN_PID", str(os.getpid()))
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISOR_SYSTEMD_UNIT", "agent-dispatch-supervisor-other.service")
    with pytest.raises(native.NativeOwnershipUnconfirmed, match="cgroup"):
        native.native_restart_unit()
    monkeypatch.setattr(native.sys, "platform", "darwin")
    assert native.native_restart_unit() is None
    monkeypatch.setattr(native.sys, "platform", "win32")
    assert native.native_restart_unit() is None


def test_native_handoff_drains_releases_returns_restart_code_without_successor(monkeypatch, tmp_path):
    _native_identity(monkeypatch)
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    spawned = []
    daemon = _daemon(
        FakeClient([_reg("example")]), launcher, lock=lock,
        self_update_enabled=True, self_update_argv=["supervise", "serve"],
        self_update_install_dir=lambda: tmp_path, self_update_running_version="1.0.0",
        self_update_stale_target=lambda *_: tmp_path / "new/bin/python",
        self_update_spawn=lambda *args: spawned.append(args),
    )
    assert daemon.serve() == SELF_UPDATE_EXIT_CODE == 42
    assert spawned == []
    assert lock.released
    assert launcher.proc_for("example").terminated
    events = [json.loads(line) for line in (tmp_path / "lifecycle.log").read_text().splitlines()]
    assert events[-1]["detail"]["reason"] == "native-restart-requested"
    assert events[-1]["detail"]["unit"] == UNIT
    assert events[-1]["outcome"] == "begin"
    assert "spawn-request-returned" not in {e["detail"]["reason"] for e in events}


@pytest.mark.parametrize("failure", ["unreadable", "pid-mismatch", "cgroup-mismatch"])
def test_unconfirmed_native_identity_defers_before_draining_or_releasing(monkeypatch, tmp_path, caplog, failure):
    _native_identity(monkeypatch)
    read_text = Path.read_text

    def read(path, *args, **kwargs):
        if path.as_posix() == "/proc/self/cgroup":
            raise PermissionError("fixture identity unavailable")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    if failure == "pid-mismatch":
        monkeypatch.setenv("AGENT_DISPATCH_SUPERVISOR_MAIN_PID", str(os.getpid() + 1))
    elif failure == "cgroup-mismatch":
        monkeypatch.setattr(Path, "read_text", read_text)
        monkeypatch.setenv("AGENT_DISPATCH_SUPERVISOR_SYSTEMD_UNIT", "agent-dispatch-supervisor-other.service")
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    spawned = []
    daemon = _daemon(
        FakeClient([_reg("example")]), launcher, lock=lock,
        self_update_enabled=True, self_update_argv=["supervise", "serve"],
        self_update_install_dir=lambda: tmp_path, self_update_running_version="1.0.0",
        self_update_stale_target=lambda *_: tmp_path / "new/bin/python",
        self_update_spawn=lambda *args: spawned.append(args),
    )
    assert daemon.acquire_singleton()
    daemon.reconcile_once()
    assert not daemon._maybe_self_update()
    assert not lock.released
    assert not launcher.proc_for("example").terminated
    assert not spawned
    assert daemon._self_update_last_spawn is None
    assert "self-update deferred" in caplog.text


@pytest.mark.skipif(os.name != "posix", reason="POSIX native launcher contract")
def test_stable_supervisor_launcher_resolves_active_slot_instead_of_installer_pin(tmp_path):
    install = tmp_path / "runtime"
    (install / "bin").mkdir(parents=True)
    shutil.copy(PLUGIN / "scripts/resolve-runtime.sh", install / "bin/resolve-runtime.sh")
    for version in ("1.0.0", "2.0.0"):
        slot = install / "versions" / version
        (slot / "bin").mkdir(parents=True)
        (slot / ".install-complete.json").write_text(json.dumps({
            "version": version, "completed_at": "1970-01-01T00:00:00Z", "pid": 100,
        }))
        python = slot / "bin/python"
        python.write_text(
            f'#!/bin/bash\n[[ "$AGENT_DISPATCH_SUPERVISOR_MAIN_PID" == "$$" ]] || exit 99\n'
            '[[ "$AGENT_DISPATCH_SUPERVISOR_SYSTEMD_UNIT" == agent-dispatch-supervisor.service ]] || exit 98\n'
            f'printf "{version} %s\\n" "$*"\n'
        )
        python.chmod(0o700)
    (install / "current-version").write_text("2.0.0")
    text = (PLUGIN / "scripts/install.sh").read_text()
    start = text.index("_write_supervisor_launcher() {")
    function = text[start:text.index("\n}\n", start) + 2]
    launcher = install / "supervise-service.sh"
    script = "\n".join([
        "set -euo pipefail", function,
        f"INSTALL_DIR={shlex.quote(str(install))}",
        f"SUPERVISOR_LAUNCHER={shlex.quote(str(launcher))}",
        f"VENV_PYTHON={shlex.quote(str(install / 'versions/1.0.0/bin/python'))}",
        "_write_supervisor_launcher",
    ])
    subprocess.run(["/bin/bash"], input=script, text=True, check=True, timeout=10)
    result = subprocess.run(
        [str(launcher), "--systemd-unit", UNIT], env=dict(os.environ, AGENT_DISPATCH_SUPERVISE_MODE="serve",
                                  AGENT_DISPATCH_SUPERVISE_MACHINE="host-a",
                                  AGENT_DISPATCH_SUPERVISOR_SYSTEMD_UNIT="malformed-override"),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "2.0.0 -m agent_dispatch supervise serve --legacy-env --interval 30 --machine host-a\n"
    unit_text = text[text.index("_install_supervisor_unit() {"):]
    assert "Restart=on-failure" in unit_text
    assert "SuccessExitStatus=42" not in unit_text
    assert "ExecStart=$SUPERVISOR_LAUNCHER --systemd-unit $unit" in unit_text

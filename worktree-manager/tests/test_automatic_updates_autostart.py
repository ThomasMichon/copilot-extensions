from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from worktree_manager import automatic_updates_autostart as autostart
from worktree_manager import automatic_updates_startup as startup


@pytest.fixture
def installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "Manager space % $"
    root.mkdir()
    (root / "current-version").write_text("0.1.0-dev97\n", encoding="utf-8")
    slot = root / "versions/0.1.0-dev97"
    for name in startup._KEY_FILES:
        target = slot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    (slot / ".install-complete.json").write_text(
        json.dumps({"version": slot.name, "payload_hash": "a" * 64}), encoding="utf-8",
    )
    venv = slot
    (venv / "bin").mkdir(parents=True)
    if os.name == "nt":
        (venv / "bin/python").write_bytes(b"POSIX interpreter fixture")
    else:
        (venv / "bin/python").symlink_to(sys.executable)
    (venv / "Scripts").mkdir()
    (venv / "Scripts/python.exe").write_bytes(b"test interpreter")
    (venv / "pyvenv.cfg").write_text("home = test\n", encoding="utf-8")
    return root


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[tuple[str, ...]]:
    monkeypatch.setattr(autostart, "_platform", lambda: "linux")
    monkeypatch.setattr(autostart, "_unit_path", lambda name: tmp_path / "units" / f"{name}.service")
    calls: list[tuple[str, ...]] = []

    def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 0, "ActiveState=inactive\nUnitFileState=enabled\n" if args[0] == "show" else "", "",
        )

    monkeypatch.setattr(autostart, "_systemctl", systemctl)
    return calls


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    monkeypatch.setattr(autostart, "_platform", lambda: "windows")
    base = tmp_path_factory.mktemp("gui") / "pythonw.exe"
    base.write_bytes(b"GUI subsystem fixture")
    monkeypatch.setattr(autostart, "windowless_python", lambda *a, **kw: str(base))
    entries: dict[str, str] = {}

    class Key:
        def __enter__(self) -> Key:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    def query(key: Key, name: str) -> tuple[str, int]:
        if name not in entries:
            raise FileNotFoundError(name)
        return entries[name], 1

    def write(key: Key, name: str, reserved: int, kind: int, value: str) -> None:
        assert kind == 1
        entries[name] = value

    registry = SimpleNamespace(
        HKEY_CURRENT_USER="HKCU", KEY_READ=1, KEY_SET_VALUE=2, REG_SZ=1,
        OpenKey=lambda *a: Key(), CreateKeyEx=lambda *a: Key(),
        QueryValueEx=query, SetValueEx=write, DeleteValue=lambda key, name: entries.pop(name),
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    return entries


def test_inspect_never_creates_artifacts(installation: Path, linux: list) -> None:
    before = sorted(installation.rglob("*"))
    result = autostart.inspect_autostart(installation)
    assert result["registered"] is False
    assert sorted(installation.rglob("*")) == before
    assert not (installation / "automatic-updates-autostart.lock").exists()


def test_refresh_preview_refuses_changed_native_interpreter_without_writes(
    installation: Path, linux: list, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered = autostart.register_autostart(installation)
    paths = [
        installation / "automatic-updates/startup.py",
        installation / "automatic-updates/autostart.json",
        autostart._unit_path(str(registered["name"])),
    ]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    replacement = tmp_path / "other-base-python"
    replacement.write_bytes(b"fixture bootstrap interpreter")
    replacement.chmod(0o700)
    monkeypatch.setattr(sys, "_base_executable", str(replacement))
    monkeypatch.setattr(autostart, "_unit_fields", lambda *a: {
        "ActiveState": "active", "UnitFileState": "enabled",
    })
    publication = Mock(side_effect=AssertionError("preview must not publish"))
    monkeypatch.setattr(autostart, "_publish", publication)
    result = autostart.inspect_autostart_refresh(installation)
    assert result["registered"] is True and result["activation"] == "blocked"
    assert "separate stop/handover approval" in str(result["blockers"])
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before
    publication.assert_not_called()


def test_linux_register_enables_without_start_and_is_idempotent(
    installation: Path, linux: list,
) -> None:
    first = autostart.register_autostart(installation)
    assert first["registered"] is True
    assert first["activation"] == "pending"
    assert "worker-readiness-not-checked" in first["blockers"]
    assert ("daemon-reload",) in linux
    assert ("enable", first["unit"]) in linux
    assert all(args[0] not in {"start", "restart", "stop"} for args in linux)
    assert autostart.register_autostart(installation)["registered"] is True
    unit = autostart._unit_path(str(first["name"])).read_text(encoding="utf-8")
    assert "WantedBy=default.target" in unit
    assert autostart.UNIT_ENV in unit
    assert "%%" in unit and "$$" in unit
    assert 'WorkingDirectory=/' in unit
    assert "linger" not in unit.lower()
    assert str(installation / "versions") not in unit
    assert "worktrees" not in unit


def test_windows_registry_uses_real_gui_and_durable_bootstrap(
    installation: Path, windows: dict,
) -> None:
    result = autostart.register_autostart(installation)
    assert result["registered"] is True
    assert result["activation"] == "pending"
    command = windows[result["name"]]
    assert "pythonw.exe" in command and "startup.py" in command
    assert "-I -S" in command
    assert "--root" not in command and command.count(str(installation)) == 1
    assert "versions" not in command and "TaskScheduler" not in command
    assert "logon" in result["lifetime"]
    assert autostart.register_autostart(installation)["registered"] is True
    assert autostart.remove_autostart(installation)["registered"] is False
    assert not windows
    assert autostart.remove_autostart(installation)["registered"] is False
    assert autostart.register_autostart(installation)["registered"] is True


@pytest.mark.parametrize("platform_fixture", ["linux", "windows"])
def test_foreign_entry_refused_without_writes(
    installation: Path, platform_fixture: str, request: pytest.FixtureRequest,
) -> None:
    backend = request.getfixturevalue(platform_fixture)
    _, _, name = autostart._paths(installation)
    if platform_fixture == "linux":
        path = autostart._unit_path(name)
        path.parent.mkdir()
        path.write_text("foreign service", encoding="utf-8")
    else:
        backend[name] = "foreign startup"
    result = autostart.register_autostart(installation)
    assert result["activation"] == "blocked"
    assert "unowned" in str(result["blockers"])
    assert not (installation / "automatic-updates").exists()


@pytest.mark.parametrize("artifact", ["entry", "bootstrap", "receipt"])
def test_edited_registration_preserved_on_register_and_remove(
    installation: Path, linux: list, artifact: str,
) -> None:
    result = autostart.register_autostart(installation)
    directory = installation / "automatic-updates"
    path = {
        "entry": autostart._unit_path(str(result["name"])),
        "bootstrap": directory / "startup.py", "receipt": directory / "autostart.json",
    }[artifact]
    path.write_text("foreign contents", encoding="utf-8")
    for action in (autostart.register_autostart, autostart.remove_autostart):
        assert action(installation)["activation"] == "blocked"
        assert path.read_text(encoding="utf-8") == "foreign contents"


def test_missing_user_manager_preserves_registration_but_reports_blocked(
    installation: Path, linux: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        autostart, "_systemctl",
        lambda *a: subprocess.CompletedProcess(a, 1, "", "Failed to connect to bus"),
    )
    result = autostart.register_autostart(installation)
    assert result["registered"] is True
    assert result["activation"] == "blocked"
    assert "Failed to connect to bus" in str(result["blockers"])


def test_active_unit_removal_requires_explicit_stop_approval(
    installation: Path, linux: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered = autostart.register_autostart(installation)
    unit = autostart._unit_path(str(registered["name"]))
    before = unit.read_bytes()
    monkeypatch.setattr(
        autostart, "_systemctl",
        lambda *a: subprocess.CompletedProcess(
            a, 0, "ActiveState=active\nUnitFileState=enabled\n", "",
        ),
    )
    result = autostart.remove_autostart(installation)
    assert result["registered"] is True
    assert result["activation"] == "blocked"
    assert "stop-requires-separate-approval" in str(result["blockers"])
    assert unit.read_bytes() == before


def test_linux_remove_disables_but_does_not_stop(installation: Path, linux: list) -> None:
    result = autostart.register_autostart(installation)
    assert autostart.remove_autostart(installation)["registered"] is False
    assert ("disable", result["unit"]) in linux
    assert all(args[0] != "stop" for args in linux)
    assert not autostart._unit_path(str(result["name"])).exists()
    assert autostart.remove_autostart(installation)["registered"] is False


@pytest.mark.parametrize("version", ["../escape", "/outside", "a/b", "0.1\\escape", "..", ""])
def test_selector_traversal_refused(
    installation: Path, linux: list, version: str,
) -> None:
    (installation / "current-version").write_text(version, encoding="utf-8")
    result = autostart.register_autostart(installation)
    assert result["activation"] == "blocked"
    assert not (installation / "automatic-updates").exists()


def test_selected_slot_is_resolved_fresh(installation: Path) -> None:
    old_slot = startup.completed_slot(installation)
    (installation / "current-version").write_text("0.2.0", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        startup.completed_slot(installation)
    assert old_slot.is_dir()


def test_constructed_completion_and_interpreter_are_required_without_rebuilding(
    installation: Path, linux: list,
) -> None:
    slot = startup.completed_slot(installation)
    (slot / ".install-complete.json").unlink()
    assert autostart.register_autostart(installation)["activation"] == "blocked"
    (slot / ".install-complete.json").write_text(
        json.dumps({"version": slot.name, "payload_hash": "a" * 64}), encoding="utf-8",
    )
    (slot / "pyvenv.cfg").unlink()
    assert autostart.register_autostart(installation)["activation"] == "blocked"


@pytest.mark.parametrize("artifact", ["current-version", "automatic-updates"])
def test_linked_artifacts_refused(
    installation: Path, linux: list, tmp_path: Path, artifact: str,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = installation / artifact
    if target.is_file():
        target.unlink()
    try:
        target.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"fixture requires symlink creation: {exc}")
    assert autostart.register_autostart(installation)["activation"] == "blocked"
    assert not list(outside.iterdir())


def test_windows_missing_real_gui_refuses_registration(
    installation: Path, windows: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object, **kwargs: object) -> str:
        raise RuntimeError("real base GUI interpreter unavailable")

    monkeypatch.setattr(autostart, "windowless_python", refuse)
    result = autostart.register_autostart(installation)
    assert result["activation"] == "blocked" and not windows


def test_windows_bootstrap_invokes_hidden_ensure_without_installation(
    installation: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (installation / "automatic-updates").mkdir()
    monkeypatch.setattr(startup.sys, "platform", "win32")
    monkeypatch.setattr(startup.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    runner = Mock(return_value=subprocess.CompletedProcess([], 7))
    monkeypatch.setattr(startup.subprocess, "run", runner)
    monkeypatch.setenv("PYTHONPATH", "foreign")
    monkeypatch.setenv("GH_TOKEN", "test-only")
    assert startup.startup(installation) == 7
    args, kwargs = runner.call_args
    assert args[0] == startup.selected_command(installation, windows=True)
    assert args[0][1:6] == ["-I", "-S", "-B", "-X", "utf8"]
    assert args[0][0].endswith("Scripts/python.exe")
    assert kwargs["creationflags"] == 0x08000000
    assert kwargs["timeout"] == 90
    assert "PYTHONPATH" not in kwargs["env"] and "GH_TOKEN" not in kwargs["env"]
    assert "__PYVENV_LAUNCHER__" not in kwargs["env"]


def test_systemd_command_quoting_never_uses_a_shell() -> None:
    unit = autostart._unit(Path('/home/user/a % $ "quote"'), "owner", ["/usr/bin/python3", "$x%y"])
    assert '"$$x%%y"' in unit
    assert 'WorkingDirectory=/home/user/a %% $ "quote"\n' in unit
    assert "/bin/sh" not in unit
    with pytest.raises(ValueError, match="newlines"):
        autostart._quote("line\ninjection")


def test_damaged_runtime_remains_registered_but_activation_is_blocked(
    installation: Path, linux: list,
) -> None:
    assert autostart.register_autostart(installation)["registered"] is True
    (installation / "current-version").write_text("../escape", encoding="utf-8")
    result = autostart.inspect_autostart(installation)
    assert result["registered"] is True
    assert result["activation"] == "blocked"


def test_owned_bootstrap_can_be_updated_without_changing_startup_command(
    installation: Path, linux: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert autostart.register_autostart(installation)["registered"] is True
    monkeypatch.setattr(autostart, "_bootstrap_body", lambda: "changed implementation\n")
    result = autostart.register_autostart(installation)
    assert result["registered"] is True
    assert result["activation"] == "pending"
    assert (installation / "automatic-updates/startup.py").read_text() == "changed implementation\n"


def test_missing_root_inspection_is_read_only(tmp_path: Path, linux: list) -> None:
    absent = tmp_path / "absent"
    assert autostart.inspect_autostart(absent)["activation"] == "blocked"
    assert not absent.exists()


def test_receipt_records_exact_body_and_not_only_a_marker(installation: Path, windows: dict) -> None:
    autostart.register_autostart(installation)
    directory = installation / "automatic-updates"
    receipt = json.loads((directory / "autostart.json").read_text(encoding="utf-8"))
    assert receipt["bootstrap"] == autostart._digest(
        (directory / "startup.py").read_text(encoding="utf-8"),
    )
    assert receipt["entry"] == windows[receipt["name"]]


def test_failed_bootstrap_update_restores_owned_bytes(
    installation: Path, linux: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert autostart.register_autostart(installation)["registered"] is True
    directory = installation / "automatic-updates"
    originals = {name: (directory / name).read_bytes() for name in ("startup.py", "autostart.json")}
    monkeypatch.setattr(autostart, "_bootstrap_body", lambda: "new bootstrap\n")
    real_write = autostart._write

    def write(path: Path, body: str) -> None:
        if path.name == "autostart.json" and body.encode() != originals["autostart.json"]:
            raise OSError("injected receipt publication failure")
        real_write(path, body)

    monkeypatch.setattr(autostart, "_write", write)
    result = autostart.register_autostart(installation)
    assert result["registered"] is True
    assert result["activation"] == "blocked"
    assert "receipt publication failure" in str(result["blockers"])
    for name, body in originals.items():
        assert (directory / name).read_bytes() == body


def test_registry_failure_leaves_no_unowned_bootstrap(
    installation: Path, windows: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object) -> None:
        raise OSError("injected registry failure")

    monkeypatch.setattr(autostart, "_registry_write", refuse)
    result = autostart.register_autostart(installation)
    assert result["activation"] == "blocked" and result["registered"] is False
    assert not (installation / "automatic-updates/startup.py").exists()
    assert not (installation / "automatic-updates/autostart.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX bootstrap protocol")
def test_linux_startup_execs_retained_worker_without_acquisition(
    installation: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = startup.completed_slot(installation) / "bin/python"
    probe = Mock(side_effect=AssertionError("startup must not run an acquisition command"))
    monkeypatch.setattr(startup.subprocess, "run", probe)
    command = Mock(side_effect=RuntimeError("exec observed"))
    monkeypatch.setattr(startup.sys, "platform", "linux")
    monkeypatch.setattr(startup.os, "execve", command)
    monkeypatch.setattr(startup.os, "chdir", Mock())
    with pytest.raises(RuntimeError, match="exec observed"):
        startup.startup(installation)
    executable, argv, environment = command.call_args.args
    assert executable == str(expected)
    assert argv == startup.selected_command(installation, windows=False)
    assert environment["WORKTREE_MANAGER_ROOT"] == str(installation)
    probe.assert_not_called()


def test_disabled_unit_is_not_reported_as_activated(
    installation: Path, linux: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    autostart.register_autostart(installation)
    monkeypatch.setattr(
        autostart, "_systemctl",
        lambda *a: subprocess.CompletedProcess(
            a, 0, "ActiveState=inactive\nUnitFileState=disabled\n", "",
        ),
    )
    result = autostart.inspect_autostart(installation)
    assert result["registered"] is True and result["enabled"] is False
    assert result["activation"] == "blocked"
    assert "startup-unit-not-enabled" in str(result["blockers"])


def test_late_foreign_service_is_not_deleted(
    installation: Path, linux: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered = autostart.register_autostart(installation)
    unit = autostart._unit_path(str(registered["name"]))
    original = autostart._systemctl

    def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
        if args[0] == "disable":
            unit.write_text("late foreign service", encoding="utf-8")
        return original(*args)

    monkeypatch.setattr(autostart, "_systemctl", systemctl)
    result = autostart.remove_autostart(installation)
    assert result["activation"] == "blocked"
    assert unit.read_text(encoding="utf-8") == "late foreign service"


def test_registration_preserves_unchanged_bootstrap_identity(
    installation: Path, windows: dict,
) -> None:
    autostart.register_autostart(installation)
    path = installation / "automatic-updates/startup.py"
    before = path.stat()
    autostart.register_autostart(installation)
    after = path.stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)


def test_null_ownership_receipt_is_not_adopted(installation: Path, linux: list) -> None:
    directory = installation / "automatic-updates"
    directory.mkdir()
    receipt = directory / "autostart.json"
    receipt.write_text("null", encoding="utf-8")
    assert autostart.register_autostart(installation)["activation"] == "blocked"
    assert receipt.read_text(encoding="utf-8") == "null"


def test_missing_interpreter_refuses_registration(
    installation: Path, linux: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (startup.completed_slot(installation) / "bin/python").unlink()
    result = autostart.register_autostart(installation)
    assert result["activation"] == "blocked"
    assert "python" in str(result["blockers"])


def test_windows_startup_uses_fixed_native_interpreter(
    installation: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    wrapper = tmp_path / "uv.cmd"
    wrapper.write_text("unsafe wrapper", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))
    command = startup.selected_command(installation, windows=True)
    assert command[0] == str(startup.completed_slot(installation) / "Scripts/python.exe")
    assert str(wrapper) not in command


def test_inactive_broken_runtime_can_still_be_unregistered(
    installation: Path, linux: list,
) -> None:
    registered = autostart.register_autostart(installation)
    (installation / "current-version").write_text("../invalid", encoding="utf-8")
    result = autostart.remove_autostart(installation)
    assert result["registered"] is False
    assert not autostart._unit_path(str(registered["name"])).exists()


def test_repeat_remove_does_not_disable_a_missing_unit(
    installation: Path, linux: list,
) -> None:
    autostart.register_autostart(installation)
    autostart.remove_autostart(installation)
    linux.clear()
    assert autostart.remove_autostart(installation)["registered"] is False
    assert all(call[0] not in {"disable", "daemon-reload"} for call in linux)


@pytest.fixture
def native_owner(
    installation: Path, linux: list, monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, str], dict[int, tuple]]:
    registered = autostart.register_autostart(installation)
    unit = str(registered["unit"])
    name = str(registered["name"])
    group = f"/user.slice/user-1000.slice/user@1000.service/app.slice/{unit}"
    fields = {
        "Id": unit, "ActiveState": "active", "UnitFileState": "enabled",
        "MainPID": str(os.getpid() + 100000), "ControlGroup": group,
        "FragmentPath": str(autostart._unit_path(name)), "NeedDaemonReload": "no",
        "KillMode": "process", "SendSIGKILL": "no", "TimeoutStopUSec": "infinity",
        "DropInPaths": "",
    }
    snapshots = {
        int(fields["MainPID"]): (
            100, 1, (sys.executable, "-I", "-S",
                     str(installation / "automatic-updates/startup.py"),
                     "--root", str(installation)), (group,),
        ),
        os.getpid(): (
            200, int(fields["MainPID"]),
            (sys.executable, "-m", "worktree_manager", "automatic-updates",
             "run", "--root", str(installation)), (group,),
        ),
        1: (1, 0, ("init",), ("/",)),
    }
    monkeypatch.setattr(
        autostart, "_systemctl",
        lambda *args: subprocess.CompletedProcess(
            args, 0, "\n".join(f"{key}={value}" for key, value in fields.items()), "",
        ),
    )
    monkeypatch.setattr(startup, "process_snapshot", lambda pid: snapshots[pid])
    monkeypatch.delenv(autostart.UNIT_ENV, raising=False)
    return fields, snapshots


def test_native_supervisor_proves_owner_without_trusting_environment(
    installation: Path, native_owner: tuple,
) -> None:
    assert autostart.native_supervisor_state(installation) == "systemd"


def test_native_supervisor_accepts_retained_exec_replacing_mainpid(
    installation: Path, native_owner: tuple,
) -> None:
    fields, snapshots = native_owner
    fields["MainPID"] = str(os.getpid())
    started, _, command, groups = snapshots[os.getpid()]
    snapshots[os.getpid()] = (
        started, 1, tuple(startup.selected_command(installation, windows=False)), groups,
    )
    assert autostart.native_supervisor_state(installation) == "systemd"


@pytest.mark.parametrize(("field", "value"), [
    ("MainPID", "0"), ("MainPID", "not-a-pid"),
    ("ControlGroup", "/foreign.service"), ("FragmentPath", "/foreign.service"),
    ("Id", "foreign.service"), ("NeedDaemonReload", "yes"),
    ("KillMode", "control-group"), ("SendSIGKILL", "yes"),
    ("TimeoutStopUSec", "30s"), ("DropInPaths", "/foreign.conf"),
])
def test_native_supervisor_defers_unverified_or_unsafe_owner(
    installation: Path, native_owner: tuple, field: str, value: str,
) -> None:
    fields, _ = native_owner
    fields[field] = value
    assert autostart.native_supervisor_state(installation) == "ambiguous"


def test_native_supervisor_requires_real_mainpid_ancestry(
    installation: Path, native_owner: tuple,
) -> None:
    _, snapshots = native_owner
    started, _, argv, groups = snapshots[os.getpid()]
    snapshots[os.getpid()] = (started, 1, argv, groups)
    assert autostart.native_supervisor_state(installation) == "ambiguous"


def test_native_supervisor_requires_cgroup_membership(
    installation: Path, native_owner: tuple,
) -> None:
    _, snapshots = native_owner
    started, parent, argv, _ = snapshots[os.getpid()]
    snapshots[os.getpid()] = (started, parent, argv, ("/session.scope",))
    assert autostart.native_supervisor_state(installation) == "ambiguous"


def test_native_supervisor_rejects_foreign_process_command(
    installation: Path, native_owner: tuple,
) -> None:
    fields, snapshots = native_owner
    pid = int(fields["MainPID"])
    started, parent, _, groups = snapshots[pid]
    snapshots[pid] = (started, parent, ("/bin/foreign",), groups)
    assert autostart.native_supervisor_state(installation) == "ambiguous"


def test_native_supervisor_checks_process_start_identity_again(
    installation: Path, native_owner: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fields, snapshots = native_owner
    main = int(fields["MainPID"])
    calls = 0

    def snapshot(pid: int) -> tuple:
        nonlocal calls
        value = snapshots[pid]
        if pid == main:
            calls += 1
            if calls > 1:
                return (value[0] + 1, *value[1:])
        return value

    monkeypatch.setattr(startup, "process_snapshot", snapshot)
    assert autostart.native_supervisor_state(installation) == "ambiguous"


def test_exported_marker_alone_never_proves_native_ownership(
    installation: Path, linux: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, name = autostart._paths(installation)
    monkeypatch.setenv(autostart.UNIT_ENV, f"{name}.service")
    monkeypatch.setattr(
        startup, "process_snapshot", lambda pid: (1, 0, ("worker",), ("/session.scope",)),
    )
    assert autostart.native_supervisor_state(installation) == "ambiguous"


def test_standalone_worker_has_no_native_owner(
    installation: Path, linux: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(autostart.UNIT_ENV, raising=False)
    monkeypatch.setattr(
        startup, "process_snapshot", lambda pid: (1, 0, ("worker",), ("/session.scope",)),
    )
    assert autostart.native_supervisor_state(installation) == "none"


def test_native_unit_preserves_update_children(installation: Path, linux: list) -> None:
    registered = autostart.register_autostart(installation)
    body = autostart._unit_path(str(registered["name"])).read_text(encoding="utf-8")
    assert "KillMode=process\n" in body
    assert "SendSIGKILL=no\n" in body
    assert "TimeoutStopSec=infinity\n" in body
    assert "Restart=on-failure\n" in body


def test_activate_linux_starts_owned_unit_without_restart(
    installation: Path, native_owner: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fields, _ = native_owner
    fields["ActiveState"] = "inactive"
    original = autostart._systemctl
    calls: list[tuple[str, ...]] = []

    def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[0] == "start":
            fields["ActiveState"] = "active"
        return original(*args)

    monkeypatch.setattr(autostart, "_systemctl", systemctl)
    result = autostart.activate_autostart(installation)
    assert result["registered"] is True and result["service_active"] is True
    assert result["activation"] == "pending" and result["worker_readiness"] == "not-checked"
    assert ("start", fields["Id"]) in calls
    assert all(args[0] not in {"restart", "stop", "enable", "daemon-reload"} for args in calls)


def test_activate_preserves_blocked_registration(
    installation: Path, native_owner: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fields, _ = native_owner
    fields["UnitFileState"] = "disabled"
    original = autostart._systemctl
    recorder = Mock(side_effect=original)
    monkeypatch.setattr(autostart, "_systemctl", recorder)
    expected = autostart.inspect_autostart(installation)
    result = autostart.activate_autostart(installation)
    assert result == expected
    assert all(call.args[0] == "show" for call in recorder.call_args_list)


def test_activate_failure_preserves_registered_state_and_diagnostic(
    installation: Path, native_owner: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = autostart._systemctl

    def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
        if args[0] == "start":
            return subprocess.CompletedProcess(args, 1, "", "injected activation failure")
        return original(*args)

    monkeypatch.setattr(autostart, "_systemctl", systemctl)
    result = autostart.activate_autostart(installation)
    assert result["registered"] is True
    assert result["activation"] == "blocked"
    assert "injected activation failure" in str(result["blockers"])


def test_activate_refuses_unowned_registration(installation: Path, linux: list) -> None:
    result = autostart.activate_autostart(installation)
    assert result["activation"] == "blocked"
    assert all(args[0] == "show" for args in linux)


def test_activate_windows_is_pending_without_starting_anything(
    installation: Path, windows: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    autostart.register_autostart(installation)
    forbidden = Mock(side_effect=AssertionError("Windows activation must not spawn"))
    monkeypatch.setattr(autostart, "_systemctl", forbidden)
    result = autostart.activate_autostart(installation)
    assert result["activation"] == "pending" and result["registered"] is True
    forbidden.assert_not_called()


def test_unchanged_registration_never_publishes_or_reloads(
    installation: Path, linux: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    autostart.register_autostart(installation)
    linux.clear()
    forbidden = Mock(side_effect=AssertionError("unchanged registration must not publish"))
    monkeypatch.setattr(autostart, "_publish", forbidden)
    monkeypatch.setattr(autostart, "_lock", forbidden)
    assert autostart.register_autostart(installation)["registered"] is True
    forbidden.assert_not_called()
    assert all(args[0] == "show" for args in linux)


def test_active_owned_bootstrap_refresh_does_not_reload_unit(
    installation: Path, native_owner: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = autostart._systemctl
    recorder = Mock(side_effect=original)
    monkeypatch.setattr(autostart, "_systemctl", recorder)
    monkeypatch.setattr(autostart, "_bootstrap_body", lambda: "# qualified new bootstrap\n")
    result = autostart.register_autostart(installation)
    assert result["registered"] is True and result["activation"] == "pending"
    assert (installation / "automatic-updates/startup.py").read_text() == "# qualified new bootstrap\n"
    assert all(call.args[0] == "show" for call in recorder.call_args_list)


@pytest.mark.skipif(os.name == "nt", reason="POSIX bootstrap protocol")
@pytest.mark.parametrize("payload", [
    {}, {"version": "foreign", "payload_hash": "a" * 64},
    {"version": "0.1.0-dev97", "payload_hash": ""},
])
def test_startup_refuses_unattributed_completion(
    installation: Path, payload: dict,
) -> None:
    slot = startup.completed_slot(installation)
    (slot / ".install-complete.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="attributable constructed completion"):
        startup.selected_command(installation, windows=False)


@pytest.mark.skipif(os.name == "nt", reason="POSIX bootstrap protocol")
def test_missing_retention_bootstrap_never_executes_worker(
    installation: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (startup.completed_slot(installation) / ".runtime-bootstrap/runtime_launcher.py").unlink()
    forbidden = Mock(side_effect=AssertionError("failed preparation must not exec"))
    monkeypatch.setattr(startup.os, "execve", forbidden)
    with pytest.raises(FileNotFoundError):
        startup.startup(installation)
    forbidden.assert_not_called()


@pytest.mark.skipif(os.name == "nt", reason="POSIX bootstrap protocol")
def test_startup_rejects_linked_interpreter_parent(
    installation: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    slot = startup.completed_slot(installation)
    (slot / "bin").rename(slot / "original-bin")
    (slot / "bin").symlink_to(slot / "original-bin", target_is_directory=True)
    with pytest.raises(ValueError, match="linked or reparsed"):
        startup.selected_command(installation, windows=False)


@pytest.mark.skipif(os.name == "nt", reason="POSIX bootstrap protocol")
@pytest.mark.parametrize("marker", ["directory", "file", "dangling-link"])
def test_startup_rejects_interpreter_target_in_source_checkout(
    installation: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, marker: str,
) -> None:
    checkout = tmp_path / "untrusted-source"
    checkout.mkdir()
    if marker == "directory":
        (checkout / ".git").mkdir()
    elif marker == "file":
        (checkout / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    else:
        (checkout / ".git").symlink_to(checkout / "missing")
    python = checkout / "python"
    python.write_bytes(b"foreign executable")
    python.chmod(0o700)
    expected = startup.completed_slot(installation) / "bin/python"
    expected.unlink()
    expected.symlink_to(python)
    with pytest.raises(ValueError, match="source checkout"):
        startup.selected_command(installation, windows=False)


@pytest.mark.skipif(os.name == "nt", reason="POSIX bootstrap protocol")
def test_selected_retained_command_does_not_follow_later_selector_change(
    installation: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = startup.selected_command(installation, windows=False)
    old = startup.completed_slot(installation)
    (installation / "current-version").write_text("0.2.0", encoding="utf-8")
    assert command[0] == str(old / "bin/python")
    assert command[6] == str(old / ".runtime-bootstrap/runtime_launcher.py")


def test_owned_bootstrap_infers_root_not_ambient_configuration(
    installation: Path, windows: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert autostart.register_autostart(installation)["registered"] is True
    script = installation / "automatic-updates/startup.py"
    monkeypatch.setattr(startup, "__file__", str(script))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(installation.parent / "unrelated"))
    assert startup.resolve_root(None) == installation.resolve()
    run = Mock(return_value=0)
    monkeypatch.setattr(startup, "startup", run)
    monkeypatch.setattr(sys, "argv", [str(script)])
    assert startup.main() == 0
    run.assert_called_once_with(installation.resolve())


def test_source_module_requires_explicit_root(
    installation: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    run = Mock(side_effect=AssertionError("source execution cannot infer a deployment"))
    monkeypatch.setattr(startup, "startup", run)
    monkeypatch.setattr(sys, "argv", [str(startup.__file__)])
    with pytest.raises(SystemExit) as rejected:
        startup.main()
    assert rejected.value.code == 2
    assert "explicit --root" in capsys.readouterr().err
    run.assert_not_called()
    monkeypatch.setattr(sys, "argv", [str(startup.__file__), "--root", str(installation)])
    run.side_effect, run.return_value = None, 0
    assert startup.main() == 0
    run.assert_called_once_with(installation.resolve())


@pytest.mark.parametrize(("field", "value"), [
    ("root", "/different-installation"), ("name", "foreign-owner"),
    ("bootstrap", "0" * 64), ("version", True), ("platform", "foreign"),
])
def test_implicit_root_requires_exact_owned_receipt(
    installation: Path, windows: dict, monkeypatch: pytest.MonkeyPatch,
    field: str, value: object,
) -> None:
    assert autostart.register_autostart(installation)["registered"] is True
    directory = installation / "automatic-updates"
    owner = json.loads((directory / "autostart.json").read_text(encoding="utf-8"))
    owner[field] = value
    (directory / "autostart.json").write_text(json.dumps(owner), encoding="utf-8")
    monkeypatch.setattr(startup, "__file__", str(directory / "startup.py"))
    with pytest.raises(ValueError, match="exact bootstrap receipt"):
        startup.resolve_root(None)


def test_implicit_root_never_infers_a_checkout(
    installation: Path, windows: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert autostart.register_autostart(installation)["registered"] is True
    (installation / ".git").mkdir()
    monkeypatch.setattr(startup, "__file__", str(installation / "automatic-updates/startup.py"))
    with pytest.raises(ValueError, match="source checkout or worktree"):
        startup.resolve_root(None)
    assert startup.resolve_root(installation) == installation.resolve()


@pytest.mark.parametrize(("entry", "units"), [
    ("a" * 260, 260), ("\U0001f642" * 130, 260),
])
def test_windows_run_budget_accepts_260_utf16_units(entry: str, units: int) -> None:
    assert autostart._validate_run_entry(entry) == units


@pytest.mark.parametrize(("entry", "units"), [
    ("a" * 261, 261), ("\U0001f642" * 130 + "a", 261),
    ("\U0001f642" * 131, 262),
])
def test_windows_run_budget_counts_utf16_not_python_characters(entry: str, units: int) -> None:
    with pytest.raises(ValueError, match=f"{units} UTF-16"):
        autostart._validate_run_entry(entry)


def oversized_gui(
    root: Path, monkeypatch: pytest.MonkeyPatch, factory: pytest.TempPathFactory,
) -> Path:
    directory = factory.mktemp("long-gui")
    gui = directory / "pythonw.exe"
    monkeypatch.setattr(autostart, "windowless_python", lambda *a, **kw: str(gui))
    for _ in range(16):
        directory.mkdir(exist_ok=True)
        gui = directory / "pythonw.exe"
        gui.write_bytes(b"GUI interpreter fixture")
        command = autostart._command(root, root / "automatic-updates", "windows")
        if len(subprocess.list2cmdline(command).encode("utf-16-le")) // 2 > 260:
            return gui
        directory /= "g" * 20
    raise AssertionError("failed to construct an actual oversized Windows command")


def test_oversized_new_run_entry_publishes_no_artifacts_or_registry(
    installation: Path, windows: dict, monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    oversized_gui(installation, monkeypatch, tmp_path_factory)
    publication = Mock(side_effect=AssertionError("oversized entry cannot be published"))
    monkeypatch.setattr(autostart, "_publish", publication)
    before = sorted(installation.rglob("*"))
    result = autostart.register_autostart(installation)
    assert result["activation"] == "blocked"
    assert "UTF-16 code units" in str(result["blockers"])
    assert not windows
    assert sorted(installation.rglob("*")) == before
    assert not (installation / "automatic-updates-autostart.lock").exists()
    publication.assert_not_called()


def test_rejected_long_replacement_preserves_healthy_owned_registration(
    installation: Path, windows: dict, monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    assert autostart.register_autostart(installation)["registered"] is True
    directory = installation / "automatic-updates"
    before = {name: (directory / name).read_bytes() for name in ("startup.py", "autostart.json")}
    registry = dict(windows)
    oversized_gui(installation, monkeypatch, tmp_path_factory)
    publication = Mock(side_effect=AssertionError("invalid replacement cannot publish"))
    monkeypatch.setattr(autostart, "_publish", publication)
    result = autostart.register_autostart(installation)
    assert result["registered"] is True and result["activation"] == "blocked"
    assert windows == registry
    for name, body in before.items():
        assert (directory / name).read_bytes() == body
    publication.assert_not_called()


def test_existing_owned_long_entry_is_blocked_and_repairable(
    installation: Path, windows: dict,
) -> None:
    registered = autostart.register_autostart(installation)
    directory = installation / "automatic-updates"
    owner = json.loads((directory / "autostart.json").read_text(encoding="utf-8"))
    owner["entry"] = "x" * 261
    (directory / "autostart.json").write_text(json.dumps(owner), encoding="utf-8")
    windows[str(registered["name"])] = owner["entry"]
    result = autostart.inspect_autostart(installation)
    assert result["registered"] is True and result["enabled"] is False
    assert result["activation"] == "blocked" and "261 UTF-16" in str(result["blockers"])
    repaired = autostart.register_autostart(installation)
    assert repaired["registered"] is True and repaired["activation"] == "pending"
    assert repaired["command_utf16_units"] <= 260
    assert "--root" not in windows[str(registered["name"])]


def test_linux_registered_command_keeps_explicit_root(installation: Path, linux: list) -> None:
    command = autostart._command(installation, installation / "automatic-updates", "linux")
    assert command[-2:] == ["--root", str(installation)]


def test_short_windows_command_fits_where_duplicated_root_exceeds_budget() -> None:
    root = PureWindowsPath(
        r"C:\Users\ExampleUserWithLongName\AppData\Local\Programs\RuntimeTools\Manager"
    )
    gui = r"C:\Users\ExampleUserWithLongName\AppData\Local\Programs\Python\Python314\pythonw.exe"
    command = [gui, "-I", "-S", str(root / "automatic-updates/startup.py")]
    short = subprocess.list2cmdline(command)
    legacy = subprocess.list2cmdline([*command, "--root", str(root)])
    assert autostart._validate_run_entry(short) <= 260
    with pytest.raises(ValueError, match="maximum is 260"):
        autostart._validate_run_entry(legacy)
    assert short.count(str(root)) == 1

"""Exercise the complete Logger uninstaller without host task mutations."""

from __future__ import annotations

import os
import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import time
import venv
from types import SimpleNamespace
from typing import Any, cast

import pytest

from logger_uninstall_test_support import DERIVED, assert_state as _assert_state, fixture as _fixture, load_module

REPO = Path(__file__).resolve().parents[1]
INSTALLERS = REPO / "plugins" / "agent-logger" / "scripts"


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("ownership", [
    "owned", "stamped", "foreign", "missing", "malformed", "foreign-stamp", "outside-stamp",
    "foreign-binding", "foreign-lifecycle", "cell", "managed",
])
def test_native_logger_uninstall_removes_runtime_but_retains_state(
    tmp_path: Path, host: str, ownership: str,
) -> None:
    executable = shutil.which(host) if os.name == "nt" else None
    if executable is None:
        pytest.skip(f"native Windows {host} unavailable")
    root, durable, env = _fixture(tmp_path, ownership)
    runner = tmp_path / "uninstall.ps1"
    runner.write_text(
        "param($Installer, $Root, [switch]$Preview)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "function Get-ScheduledTask { param($TaskName, $ErrorAction) }\n"
        "function Unregister-ScheduledTask { throw 'unexpected host task removal' }\n"
        "& $Installer uninstall -InstallDir $Root -DryRun:$Preview\n",
        encoding="utf-8",
    )

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File", str(runner),
             str(INSTALLERS / "install.ps1"), str(root), *args],
            capture_output=True, text=True, timeout=45, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result

    preview = run("-Preview")
    if ownership not in ("owned", "stamped"):
        assert preview.returncode != 0, preview.stdout + preview.stderr
        _assert_state(root, durable, removed=False, ownership=ownership)
        actual = run()
        assert actual.returncode != 0, actual.stdout + actual.stderr
        _assert_state(root, durable, removed=False, ownership=ownership)
        return
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert "would remove runtime generations" in preview.stdout
    _assert_state(root, durable, removed=False, ownership=ownership)
    for _ in range(2):
        result = run()
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_state(root, durable, removed=True, ownership=ownership)


@pytest.mark.parametrize("ownership", [
    "owned", "stamped", "foreign", "missing", "malformed", "foreign-stamp", "outside-stamp",
    "foreign-binding", "foreign-lifecycle", "cell", "managed",
])
def test_posix_logger_uninstall_removes_runtime_but_retains_state(tmp_path: Path, ownership: str) -> None:
    bash = shutil.which("bash") if os.name != "nt" else None
    if bash is None:
        pytest.skip("native POSIX Bash required")
    root, durable, env = _fixture(tmp_path, ownership)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    systemctl = fakebin / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 1\n")
    systemctl.chmod(0o755)
    env["PATH"] = str(fakebin) + os.pathsep + env["PATH"]

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [bash, str(INSTALLERS / "install.sh"), "uninstall",
             "--install-dir", str(root), *args],
            capture_output=True, text=True, timeout=30, env=env,
        )
        return result

    preview = run("--dry-run")
    if ownership not in ("owned", "stamped"):
        assert preview.returncode != 0, preview.stdout + preview.stderr
        _assert_state(root, durable, removed=False, ownership=ownership)
        actual = run()
        assert actual.returncode != 0, actual.stdout + actual.stderr
        _assert_state(root, durable, removed=False, ownership=ownership)
        return
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert "would remove runtime generations" in preview.stdout
    _assert_state(root, durable, removed=False, ownership=ownership)
    for _ in range(2):
        result = run()
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_state(root, durable, removed=True, ownership=ownership)


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_logger_refuses_running_task_before_deletion(tmp_path: Path, host: str) -> None:
    executable = shutil.which(host) if os.name == "nt" else None
    if executable is None:
        pytest.skip("native Windows shell required")
    root, durable, env = _fixture(tmp_path, "owned")
    runner = tmp_path / "running-task.ps1"
    marker = tmp_path / "task-disabled"
    env["LOGGER_TEST_DISABLED"] = str(marker)
    runner.write_text(
        "param($Installer, $Root)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "function Get-ScheduledTask { param($TaskName, $ErrorAction) [pscustomobject]@{State='Running'} }\n"
        "function Disable-ScheduledTask { param($TaskName, $ErrorAction) "
        "[IO.File]::WriteAllText($env:LOGGER_TEST_DISABLED, 'disabled') }\n"
        "function Unregister-ScheduledTask { throw 'must not remove a running task' }\n"
        "& $Installer uninstall -InstallDir $Root\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(runner),
         str(INSTALLERS / "install.ps1"), str(root)],
        capture_output=True, text=True, timeout=45, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != 0
    assert "Active Logger sync task" in result.stderr, result.stdout + result.stderr
    assert marker.read_text() == "disabled"
    _assert_state(root, durable, removed=False)


def test_posix_logger_refuses_running_service_before_deletion(tmp_path: Path) -> None:
    bash = shutil.which("bash") if os.name != "nt" else None
    if bash is None:
        pytest.skip("native POSIX Bash required")
    root, durable, env = _fixture(tmp_path, "owned")
    suffix = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    unit_dir = root.parent / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    for kind in ("service", "timer"):
        (unit_dir / f"agent-logger-sync-{suffix}.{kind}").write_text("owned unit")
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    marker = tmp_path / "timer-disabled"
    env["LOGGER_TEST_DISABLED"] = str(marker)
    systemctl = fakebin / "systemctl"
    systemctl.write_text(
        '#!/bin/sh\ncase "$2" in\n'
        'disable) printf disabled > "$LOGGER_TEST_DISABLED"; exit 0;;\n'
        'is-active) exit 0;;\n*) exit 1;;\nesac\n',
    )
    systemctl.chmod(0o755)
    env["PATH"] = str(fakebin) + os.pathsep + env["PATH"]
    result = subprocess.run(
        [bash, str(INSTALLERS / "install.sh"), "uninstall", "--install-dir", str(root)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert result.returncode != 0
    assert "Active Logger sync service" in result.stdout + result.stderr
    assert marker.read_text() == "disabled"
    assert len(list(unit_dir.iterdir())) == 2
    _assert_state(root, durable, removed=False)


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh", "bash"])
def test_logger_refuses_live_runtime_before_deletion(tmp_path: Path, host: str) -> None:
    if (host == "bash") == (os.name == "nt"):
        pytest.skip("native OS shell only")
    executable = shutil.which(host)
    if executable is None:
        pytest.skip("native shell unavailable")
    root, durable, env = _fixture(tmp_path, "owned")
    slot = root / "versions" / "1.0.0"
    venv.EnvBuilder(with_pip=False).create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    kwargs: dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    process = subprocess.Popen(
        [str(python), "-I", "-c", "print('ready', flush=True); input()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, text=True, **kwargs,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        if os.name == "nt":
            runner = tmp_path / "live-runtime.ps1"
            runner.write_text(
                "param($Installer, $Root)\n"
                "function Get-ScheduledTask { param($TaskName, $ErrorAction) }\n"
                "& $Installer uninstall -InstallDir $Root\n",
            )
            command = [
                executable, "-NoProfile", "-NonInteractive", "-File", str(runner),
                str(INSTALLERS / "install.ps1"), str(root),
            ]
        else:
            command = [executable, str(INSTALLERS / "install.sh"), "uninstall", "--install-dir", str(root)]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=45, env=env, **kwargs,
        )
        assert result.returncode != 0
        assert "Active Logger runtime" in result.stdout + result.stderr
        assert process.poll() is None
        _assert_state(root, durable, removed=False)
    finally:
        process.communicate("\n", timeout=15)
    assert process.returncode == 0


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh", "bash"])
def test_logger_stamp_retry_survives_partial_snapshot_removal(tmp_path: Path, host: str) -> None:
    if (host == "bash") == (os.name == "nt"):
        pytest.skip("native OS shell only")
    executable = shutil.which(host)
    if executable is None:
        pytest.skip("native shell unavailable")
    root, durable, env = _fixture(tmp_path, "stamped")
    kwargs: dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    prepared = subprocess.run(
        [sys.executable, "-I", "-B", str(INSTALLERS / "uninstall_guard.py"),
         "--root", str(root), "--idle", "--prepare"],
        capture_output=True, text=True, timeout=45, env=env, cwd=INSTALLERS.parent, **kwargs,
    )
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    assert json.loads((root / "deploy-manifest.json").read_text())["service"] == "agent-logger"
    (root / "snapshots" / "1.0.0" / "pyproject.toml").unlink()
    if os.name == "nt":
        runner = tmp_path / "retry.ps1"
        runner.write_text(
            "param($Installer, $Root)\n"
            "function Get-ScheduledTask { param($TaskName, $ErrorAction) }\n"
            "& $Installer uninstall -InstallDir $Root\n",
        )
        command = [
            executable, "-NoProfile", "-NonInteractive", "-File", str(runner),
            str(INSTALLERS / "install.ps1"), str(root),
        ]
    else:
        command = [executable, str(INSTALLERS / "install.sh"), "uninstall", "--install-dir", str(root)]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=45, env=env, cwd=INSTALLERS.parent, **kwargs,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_state(root, durable, removed=True, ownership="stamped")


def test_failed_stamped_teardown_cannot_authorize_foreign_command_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root, durable, environment = _fixture(tmp_path, "stamped")
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    for key in ("COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE",
                "COPILOT_PLUGIN_STAGED_FROM", "AGENT_LOGGER_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(INSTALLERS.parent)
    helper = load_module("logger_retry_authority_test", INSTALLERS / "uninstall_guard.py")
    primitive, receipt = helper.validate(root)
    assert receipt is not None

    def fail_removal(_root: Path, _shell: str) -> None:
        (root / "snapshots" / "1.0.0" / "pyproject.toml").unlink()
        raise OSError("injected partial snapshot removal")

    monkeypatch.setattr(helper, "remove_runtime", fail_removal)
    monkeypatch.setattr(sys, "argv", [
        "uninstall_guard.py", "--root", str(root), "--idle", "--prepare",
        "--remove-runtime", "--shell", "must-not-launch",
    ])
    assert helper.main() == 1
    assert "injected partial snapshot removal" in capsys.readouterr().err
    manifest = root / "deploy-manifest.json"
    assert json.loads(manifest.read_text()) == {"schema_version": 3, "service": "agent-logger"}
    assert helper.validate(root) is not None
    foreign = root.parent / ".local" / "bin" / ("agent-logger.ps1" if os.name == "nt" else "agent-logger")
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"foreign command")
    before = {path: path.read_bytes() for path in (manifest, foreign)}
    binding = helper._load("_logger_retry_authority_binding", "command_binding.py")
    with pytest.raises(binding.CommandOwnershipConflict, match="Unrecognized global commands"):
        binding.publish_legacy_binding(
            primitive, payload_root=INSTALLERS.parent, plugin_id="agent-logger",
            legacy_root=root, project_root=INSTALLERS.parent, environment=environment,
        )
    assert all(path.read_bytes() == value for path, value in before.items())
    assert all((root / name).read_bytes() == value for name, value in durable.items())


@pytest.mark.parametrize("replacement", ["publisher", "identity"])
def test_logger_command_removal_preserves_publication_custody_and_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str,
) -> None:
    root, durable, environment = _fixture(tmp_path, "owned")
    default = root.parent / ".agent-logger"
    root.rename(default)
    root = default
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    for key in ("COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE",
                "COPILOT_PLUGIN_STAGED_FROM", "AGENT_LOGGER_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(INSTALLERS.parent)
    command = root.parent / ".local" / "bin" / ("agent-logger.ps1" if os.name == "nt" else "agent-logger")
    command.parent.mkdir(parents=True)
    preserved = command.with_name("agent-logger" if os.name == "nt" else "agent-logger.ps1")
    helper = load_module("logger_command_custody_test", INSTALLERS / "uninstall_guard.py")
    templates = helper._load("_logger_command_templates", "installer_engine_commands.py")._templates(
        INSTALLERS.parent, root, "agent-logger", home=root.parent,
    )
    content = templates[command.name][0].encode()
    command.write_bytes(content)
    preserved_content = templates[preserved.name][0].encode()
    preserved.write_bytes(preserved_content)
    inventory = helper.owned_command_names

    def concurrent_replacement(selected_root: Path) -> tuple[str, ...]:
        names = inventory(selected_root)
        assert command.name in names
        if replacement == "identity":
            candidate = command.with_name("replacement.ps1")
            candidate.write_bytes(command.read_bytes())
            os.replace(candidate, command)
        else:
            script = (
                "import importlib.util,os,sys\nfrom pathlib import Path\n"
                "spec=importlib.util.spec_from_file_location('binding',sys.argv[1])\n"
                "binding=importlib.util.module_from_spec(spec); spec.loader.exec_module(binding)\n"
                "payload=Path(sys.argv[2]); root=Path(sys.argv[3])\n"
                "primitive=binding._load_primitive(payload)\n"
                "with binding._load_lifecycle().legacy_lifecycle("
                "binding._rules(),primitive,reserve=False,lock_timeout=0,"
                "payload_root=payload,plugin_id='agent-logger',legacy_root=root,project_root=payload,"
                "mode_arguments={'os_profile':root.parent,'wsl_distro':"
                "(os.environ.get('WSL_DISTRO_NAME') or 'Fixture') if primitive._is_wsl_guest() else None}):\n"
                " Path(sys.argv[4]).write_bytes(b'replacement publisher command')\n"
            )
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script, str(INSTALLERS / "command_binding.py"),
                 str(INSTALLERS.parent), str(root), str(command)],
                capture_output=True, text=True, timeout=15, env=dict(os.environ),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            assert result.returncode != 0, result.stdout + result.stderr
            assert "lock" in result.stderr.lower(), result.stdout + result.stderr
            assert command.read_bytes() == content
        return cast(tuple[str, ...], names)

    monkeypatch.setattr(helper, "owned_command_names", concurrent_replacement)
    if replacement == "identity":
        with pytest.raises(RuntimeError, match="command identity changed"):
            helper.remove_owned_commands(root)
        assert command.read_bytes() == content
    else:
        assert helper.remove_owned_commands(root) == (command.name,)
        assert not command.exists()
    assert preserved.read_bytes() == preserved_content
    _assert_state(root, durable, removed=False)


@pytest.mark.parametrize("host", ["pwsh", "bash"])
@pytest.mark.parametrize("custody", ["snapshot", "retention"])
def test_logger_preserves_busy_generation_custody(tmp_path: Path, host: str, custody: str) -> None:
    if (host == "bash") == (os.name == "nt"):
        pytest.skip("native OS shell only")
    executable = shutil.which(host)
    if executable is None:
        pytest.skip("native shell unavailable")
    root, durable, env = _fixture(tmp_path, "owned")
    helper = load_module("logger_custody_test", INSTALLERS / "uninstall_guard.py")
    leases = helper._load("_logger_test_leases", "versioned_runtime_leases.py")
    if custody == "snapshot":
        lease = leases.construction_lease(root / "snapshots", "derived", timeout=0)
    else:
        (root / "versions" / "1.0.0").mkdir()
        lease = leases._kernel_lease(root, "1.0.0", kind="retention", shared=True, timeout=0)
    kwargs: dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    if os.name == "nt":
        runner = tmp_path / "busy.ps1"
        runner.write_text(
            "param($Installer, $Root)\n"
            "function Get-ScheduledTask { param($TaskName, $ErrorAction) }\n"
            "& $Installer uninstall -InstallDir $Root\n",
        )
        command = [
            executable, "-NoProfile", "-NonInteractive", "-File", str(runner),
            str(INSTALLERS / "install.ps1"), str(root),
        ]
    else:
        command = [executable, str(INSTALLERS / "install.sh"), "uninstall", "--install-dir", str(root)]
    with lease:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=45, env=env, **kwargs,
        )
    assert result.returncode != 0
    expected = "construction" if custody == "snapshot" else "retention"
    assert expected in result.stdout + result.stderr, result.stdout + result.stderr
    _assert_state(root, durable, removed=False)


def test_logger_refuses_removable_in_root_helpers(tmp_path: Path) -> None:
    root, durable, env = _fixture(tmp_path, "owned")
    scripts = root / "snapshots" / "1.0.0" / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / "uninstall_guard.py"
    helper.write_bytes((INSTALLERS / "uninstall_guard.py").read_bytes())
    kwargs: dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(helper), "--root", str(root)],
        capture_output=True, text=True, timeout=15, env=env, **kwargs,
    )
    assert result.returncode != 0
    assert "helpers are inside removable runtime content" in result.stderr
    _assert_state(root, durable, removed=False)


def test_logger_allows_preserved_in_root_staging_helpers(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / "runtime"
    scripts = root / ".install-stage" / "owner" / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / "uninstall_guard.py"
    helper.write_bytes((INSTALLERS / "uninstall_guard.py").read_bytes())
    kwargs: dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(helper), "--root", str(root)],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)}, **kwargs,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert helper.is_file()


def test_posix_logger_refuses_path_launched_ambiguous_runtime(tmp_path: Path) -> None:
    if os.name == "nt" or not sys.platform.startswith("linux"):
        pytest.skip("native Linux PATH/symlink process attribution")
    root, durable, env = _fixture(tmp_path, "owned")
    slot = root / "versions" / "1.0.0"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(slot)
    site = next((slot / "lib").glob("python*/site-packages"))
    (site / "agent_logger.py").write_text("print('ready', flush=True)\ninput()\n")
    process_env = {**env, "PATH": str(slot / "bin") + os.pathsep + env["PATH"]}
    process = subprocess.Popen(
        ["python", "-m", "agent_logger"], env=process_env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        assert (slot / "bin" / "python").is_symlink()
        assert Path(f"/proc/{process.pid}/cmdline").read_bytes().split(b"\0")[0] == b"python"
        assert not Path(os.readlink(f"/proc/{process.pid}/exe")).is_relative_to(root)
        bash = shutil.which("bash")
        assert bash
        result = subprocess.run(
            [bash, str(INSTALLERS / "install.sh"),
             "uninstall", "--install-dir", str(root)],
            capture_output=True, text=True, timeout=30, env=env,
        )
        assert result.returncode != 0
        assert "Unattributed Logger runtime process" in result.stderr, result.stdout + result.stderr
        assert process.poll() is None
        _assert_state(root, durable, removed=False)
    finally:
        process.communicate("\n", timeout=15)
    assert process.returncode == 0


@pytest.mark.parametrize("inventory", ["empty", "missing-inspector"])
def test_logger_uninstall_refuses_unverified_process_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], inventory: str,
) -> None:
    root, durable, _ = _fixture(tmp_path, "owned")
    helper = load_module("logger_census_test", INSTALLERS / "uninstall_guard.py")
    runtime = SimpleNamespace(_iter_all_pids=lambda: [] if inventory == "empty" else [os.getpid() + 1])
    monkeypatch.setattr(helper, "_load", lambda *args: runtime)
    monkeypatch.setattr(helper, "validate", lambda _: (object(), None))
    monkeypatch.setattr(sys, "argv", [
        "uninstall_guard.py", "--root", str(root), "--idle", "--prepare",
        "--remove-runtime", "--shell", "must-not-launch",
    ])
    assert helper.main() == 1
    assert "process inventory is unavailable or missing the inspector" in capsys.readouterr().err
    _assert_state(root, durable, removed=False)


def test_logger_runtime_guard_reuses_validated_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = load_module("logger_census_reuse", INSTALLERS / "uninstall_guard.py")
    calls: list[bool] = []

    def census() -> list[int]:
        calls.append(True)
        return [os.getpid()]

    runtime = SimpleNamespace(
        _iter_all_pids=census, _versions_with_live_process=lambda _: set(),
    )
    monkeypatch.setattr(helper, "_load", lambda *args: runtime)
    helper.refuse_live_runtime(tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh", "bash"])
def test_logger_parent_traversal_refuses_before_lock_or_root_mutation(tmp_path: Path, host: str) -> None:
    if (host == "bash") == (os.name == "nt"):
        pytest.skip("native OS shell only")
    executable = shutil.which(host)
    if executable is None:
        pytest.skip("native shell unavailable")
    root, durable, env = _fixture(tmp_path / "first", "owned")
    other, other_durable, _ = _fixture(tmp_path / "other", "owned")
    destination = other.parent / "nested"
    destination.mkdir()
    alias = root.parent / "alias"
    try:
        alias.symlink_to(destination, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    requested = str(alias / ".." / root.name)
    kwargs: dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    if os.name == "nt":
        command = [executable, "-NoProfile", "-NonInteractive", "-File",
                   str(INSTALLERS / "install.ps1"), "uninstall", "-InstallDir", requested]
    else:
        command = [executable, str(INSTALLERS / "install.sh"), "uninstall", "--install-dir", requested]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=30, env=env, **kwargs,
    )
    assert result.returncode != 0
    assert "parent-traversal components" in result.stdout + result.stderr
    _assert_state(root, durable, removed=False)
    _assert_state(other, other_durable, removed=False)
    assert not (root / ".install.lock").exists()
    assert not (other / ".install.lock").exists()


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_logger_relative_root_uses_provider_not_process_cwd(tmp_path: Path, host: str) -> None:
    executable = shutil.which(host) if os.name == "nt" else None
    if executable is None:
        pytest.skip("native Windows shell required")
    root, durable, env = _fixture(tmp_path / "wanted", "owned")
    other, other_durable, _ = _fixture(tmp_path / "other", "owned")
    runner = tmp_path / "relative-root.ps1"
    runner.write_text(
        "param($Installer, $WantedParent, $OtherParent)\n"
        "$ErrorActionPreference='Stop'\n"
        "function Get-ScheduledTask { param($TaskName, $ErrorAction) }\n"
        "Set-Location -LiteralPath $WantedParent\n"
        "[IO.Directory]::SetCurrentDirectory($OtherParent)\n"
        "& $Installer uninstall -InstallDir scoped-runtime\n",
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(runner),
         str(INSTALLERS / "install.ps1"), str(root.parent), str(other.parent)],
        capture_output=True, text=True, timeout=45, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_state(root, durable, removed=True)
    _assert_state(other, other_durable, removed=False)
    assert not (other / ".install.lock").exists()


def test_posix_logger_relative_root_survives_marketplace_self_staging(tmp_path: Path) -> None:
    bash = shutil.which("bash") if os.name != "nt" else None
    if bash is None:
        pytest.skip("native POSIX Bash required")
    root, durable, env = _fixture(tmp_path / "wanted", "owned")
    other, other_durable, _ = _fixture(tmp_path / "other", "owned")
    env["HOME"] = env["USERPROFILE"] = str(other.parent)
    fixture = load_module(
        "logger_release_fixture", REPO / "plugins" / "agent-logger" / "tests" / "test_install_binstub.py",
    )
    payload = fixture._stage_release_payload(tmp_path / "release-fixture")
    installed = other.parent / ".copilot" / "installed-plugins" / "fixture" / "agent-logger"
    shutil.copytree(payload, installed)
    result = subprocess.run(
        [bash, str(installed / "scripts" / "install.sh"), "uninstall",
         "--install-dir", "scoped-runtime"],
        cwd=root.parent, capture_output=True, text=True, timeout=60, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_state(root, durable, removed=True)
    _assert_state(other, other_durable, removed=False)
    assert not (other / ".install.lock").exists()


@pytest.mark.parametrize("alias", ["trailing-slash", "dot-component"])
@pytest.mark.parametrize("active", [False, True])
def test_posix_logger_default_root_alias_uses_default_units_and_commands(
    tmp_path: Path, alias: str, active: bool,
) -> None:
    bash = shutil.which("bash") if os.name != "nt" else None
    if bash is None:
        pytest.skip("native POSIX Bash required")
    root, durable, env = _fixture(tmp_path, "owned")
    default = root.parent / ".agent-logger"
    root.rename(default)
    root = default
    units = root.parent / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    for kind in ("timer", "service"):
        (units / f"agent-logger-sync.{kind}").write_text("default unit")
    commands = root.parent / ".local" / "bin"
    commands.mkdir(parents=True)
    names = (
        "session-sync", "agent-logger", "collate-session", "read-session-digest",
        "prepare-session-log", "ramp-up-session",
    )
    renderer = load_module(
        "logger_posix_default_command_templates", REPO / "libs" / "installer-engine" / "installer_engine_commands.py",
    )
    templates = renderer._templates(INSTALLERS.parent, root, "agent-logger", home=root.parent)
    for name in names:
        (commands / name).write_text(templates[name][0], encoding="utf-8")
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    log = tmp_path / "systemctl-calls"
    env["LOGGER_TEST_CALLS"] = str(log)
    env["LOGGER_TEST_ACTIVE"] = "0" if active else "3"
    env["LOGGER_TEST_SERVICE"] = "agent-logger-sync.service"
    systemctl = fakebin / "systemctl"
    systemctl.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$LOGGER_TEST_CALLS"\n'
        'case "$2" in\n'
        'disable|daemon-reload) exit 0;;\n'
        'is-active) if [ "${4:-}" = "$LOGGER_TEST_SERVICE" ]; then exit "$LOGGER_TEST_ACTIVE"; fi; exit 4;;\n'
        '*) exit 1;;\nesac\n',
    )
    systemctl.chmod(0o755)
    env["PATH"] = str(fakebin) + os.pathsep + env["PATH"]
    requested = str(root) + ("/" if alias == "trailing-slash" else "/./")
    result = subprocess.run(
        [bash, str(INSTALLERS / "install.sh"), "uninstall", "--install-dir", requested],
        capture_output=True, text=True, timeout=45, env=env,
    )
    assert result.returncode == (1 if active else 0), result.stdout + result.stderr
    calls = log.read_text().splitlines()
    assert "--user disable --now agent-logger-sync.timer" in calls
    assert "--user is-active --quiet agent-logger-sync.service" in calls
    if active:
        assert "Active Logger sync service" in result.stderr
    _assert_state(root, durable, removed=not active)
    for kind in ("timer", "service"):
        assert (units / f"agent-logger-sync.{kind}").exists() is active
    for name in names:
        assert (commands / name).exists() is active


@pytest.mark.parametrize("lifecycle", [False, True])
def test_logger_bom_manifest_provenance_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lifecycle: bool,
) -> None:
    root, durable, _ = _fixture(tmp_path, "owned")
    record: dict[str, object] = {"schema_version": 3, "service": "agent-logger"}
    if lifecycle:
        record["commandLifecycle"] = {"owner": "incumbent"}
    manifest = root / "deploy-manifest.json"
    manifest.write_text(json.dumps(record), encoding="utf-8-sig")
    helper = load_module("logger_bom_test", INSTALLERS / "uninstall_guard.py")
    binding = helper._load("_logger_bom_binding", "command_binding.py")
    primitive = binding._load_primitive(INSTALLERS.parent)
    monkeypatch.setattr(primitive, "probe_legacy_entrypoint", lambda **kwargs: {"allowMutation": True})
    monkeypatch.setattr(binding, "_load_primitive", lambda _: primitive)

    def unavailable(*args: object, **kwargs: object) -> None:
        raise binding.SourceProvenanceUnavailable("fixture provenance unavailable")

    monkeypatch.setattr(binding, "publish_legacy_binding", unavailable)
    monkeypatch.setattr(helper, "_load", lambda *args: binding)
    before = manifest.read_bytes()
    assert before.startswith(b"\xef\xbb\xbf")
    if lifecycle:
        with pytest.raises(RuntimeError, match="lifecycle ownership cannot be revalidated"):
            helper.validate(root)
    else:
        assert helper.validate(root) == (primitive, None)
    assert manifest.read_bytes() == before
    _assert_state(root, durable, removed=False)


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_logger_refuses_external_python_module_consumer(tmp_path: Path, host: str) -> None:
    executable = shutil.which(host) if os.name == "nt" else None
    if executable is None:
        pytest.skip("native Windows shell required")
    root, durable, env = _fixture(tmp_path, "owned")
    modules = root / "versions" / "1.0.0" / "modules"
    modules.mkdir(parents=True)
    ready = tmp_path / "consumer-ready"
    (modules / "agent_logger.py").write_text(
        "import os, pathlib, time\n"
        "pathlib.Path(os.environ['LOGGER_TEST_READY']).write_text('ready')\n"
        "time.sleep(120)\n",
    )
    consumer_env = {**env, "PYTHONPATH": str(modules), "LOGGER_TEST_READY": str(ready)}
    consumer = subprocess.Popen(
        [sys.executable, "-m", "agent_logger"], cwd=tmp_path, env=consumer_env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and consumer.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "external module consumer did not become ready"
        assert consumer.poll() is None
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File",
             str(INSTALLERS / "install.ps1"), "uninstall", "-InstallDir", str(root)],
            capture_output=True, text=True, timeout=90, env=env, cwd=tmp_path,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode != 0
        assert "Unattributed Logger runtime process" in result.stderr, result.stdout + result.stderr
        assert consumer.poll() is None
        _assert_state(root, durable, removed=False)
        assert (modules / "agent_logger.py").is_file()
    finally:
        consumer.terminate()
        consumer.communicate(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="native Windows process inventory")
@pytest.mark.parametrize("outcome", ["failure", "missing-inspector", "ambiguous", "idle"])
def test_logger_native_process_inventory_refuses_uncertain_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    helper = load_module("logger_windows_census", INSTALLERS / "uninstall_guard.py")
    runtime = SimpleNamespace(
        _iter_all_pids=lambda: [os.getpid()], _versions_with_live_process=lambda _: set(),
    )
    monkeypatch.setattr(helper, "_load", lambda *args: runtime)
    monkeypatch.setattr(helper.shutil, "which", lambda _: "powershell.exe")
    rows = [{"pid": os.getpid(), "image": str(Path(sys.executable)), "command": "inspector"}]
    if outcome == "missing-inspector":
        rows = []
    elif outcome == "ambiguous":
        rows.append({"pid": os.getpid() + 1, "image": str(Path(sys.executable)),
                     "command": "python.exe -m agent_logger"})
    result = subprocess.CompletedProcess(
        [], 1 if outcome == "failure" else 0, json.dumps(rows), "inspection unavailable",
    )
    monkeypatch.setattr(helper.subprocess, "run", lambda *args, **kwargs: result)
    if outcome == "idle":
        helper.refuse_live_runtime(tmp_path)
    else:
        expected = {
            "failure": "native process inspection failed: inspection unavailable",
            "missing-inspector": "native process inventory did not include the inspector",
            "ambiguous": "Unattributed Logger runtime process",
        }
        with pytest.raises(RuntimeError, match=expected[outcome]):
            helper.refuse_live_runtime(tmp_path)


@pytest.mark.parametrize("alias", ["/", "/./"])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("selector", [".venv", "versions/1.0.0"])
def test_posix_logger_custom_alias_normalizes_and_retires_legacy_units(
    tmp_path: Path, alias: str, active: bool, selector: str,
) -> None:
    bash = shutil.which("bash") if os.name != "nt" else None
    if bash is None:
        pytest.skip("native POSIX Bash required")
    root, durable, env = _fixture(tmp_path, "owned")
    requested = str(root) + alias
    canonical_suffix = hashlib.sha256(str(root).lower().encode()).hexdigest()[:12]
    legacy_suffix = hashlib.sha256(requested.lower().encode()).hexdigest()[:12]
    canonical_name = f"agent-logger-sync-{canonical_suffix}"
    legacy_name = f"agent-logger-sync-{legacy_suffix}"
    units = root.parent / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    service = units / f"{legacy_name}.service"
    service.write_text(
        "[Unit]\nDescription=Historical Logger scheduled sync\n\n"
        "[Service]\nType=oneshot\n"
        f"Environment=AGENT_LOGGER_HOME={requested}\n"
        f"ExecStart={requested}/{selector}/bin/session-sync run --prune\n"
        "TimeoutStartSec=1800\n", encoding="utf-8",
    )
    timer = units / f"{legacy_name}.timer"
    timer.write_text(
        "[Timer]\nOnBootSec=5min\nOnUnitActiveSec=4h\nRandomizedDelaySec=10min\n"
        "Persistent=true\n\n[Install]\nWantedBy=timers.target\n", encoding="utf-8",
    )
    assert f"ExecStart={requested}/{selector}/bin/session-sync run --prune" in service.read_text()
    foreign = units / "agent-logger-sync-foreign.service"
    foreign.write_text("Environment=AGENT_LOGGER_HOME=/another/installation\n")
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    log = tmp_path / "systemctl-calls"
    env["LOGGER_TEST_CALLS"] = str(log)
    env["LOGGER_TEST_ACTIVE"] = "0" if active else "3"
    env["LOGGER_TEST_SERVICE"] = legacy_name + ".service"
    systemctl = fakebin / "systemctl"
    systemctl.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$LOGGER_TEST_CALLS"\n'
        'case "$2" in\n'
        'disable|daemon-reload) exit 0;;\n'
        'is-active) if [ "${4:-}" = "$LOGGER_TEST_SERVICE" ]; then exit "$LOGGER_TEST_ACTIVE"; fi; exit 4;;\n'
        '*) exit 1;;\nesac\n',
    )
    systemctl.chmod(0o755)
    env["PATH"] = str(fakebin) + os.pathsep + env["PATH"]
    status = subprocess.run(
        [bash, str(INSTALLERS / "install.sh"), "status", "--install-dir", requested],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert status.returncode == 0, status.stdout + status.stderr
    assert f"--user is-active {canonical_name}.timer" in log.read_text().splitlines()
    result = subprocess.run(
        [bash, str(INSTALLERS / "install.sh"), "uninstall", "--install-dir", str(root)],
        capture_output=True, text=True, timeout=45, env=env,
    )
    assert result.returncode == (1 if active else 0), result.stdout + result.stderr
    calls = log.read_text().splitlines()
    assert f"--user disable --now {legacy_name}.timer" in calls
    assert f"--user is-active --quiet {legacy_name}.service" in calls
    assert service.exists() is active
    assert timer.exists() is active
    assert foreign.read_text() == "Environment=AGENT_LOGGER_HOME=/another/installation\n"
    _assert_state(root, durable, removed=not active)


def test_posix_logger_refuses_external_python_running_rooted_script(tmp_path: Path) -> None:
    if os.name == "nt" or not sys.platform.startswith("linux"):
        pytest.skip("native Linux process inspection")
    root, durable, env = _fixture(tmp_path, "owned")
    script = root / "versions" / "1.0.0" / "sync-consumer.py"
    script.parent.mkdir()
    script.write_text("print('ready', flush=True)\ninput()\n")
    consumer = subprocess.Popen(
        [sys.executable, "-I", str(script)], env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert consumer.stdout is not None
        assert consumer.stdout.readline().strip() == "ready"
        assert not Path(os.readlink(f"/proc/{consumer.pid}/exe")).is_relative_to(root)
        assert b"agent_logger" not in Path(f"/proc/{consumer.pid}/cmdline").read_bytes()
        bash = shutil.which("bash")
        assert bash
        result = subprocess.run(
            [bash, str(INSTALLERS / "install.sh"), "uninstall",
             "--install-dir", str(root)],
            capture_output=True, text=True, timeout=30, env=env,
        )
        assert result.returncode != 0
        assert "Active Logger runtime process" in result.stderr, result.stdout + result.stderr
        assert consumer.poll() is None
        assert script.is_file()
        _assert_state(root, durable, removed=False)
    finally:
        consumer.communicate("\n", timeout=15)
    assert consumer.returncode == 0


@pytest.mark.parametrize("preview", [False, True])
def test_posix_logger_disables_loaded_timer_without_unit_files(tmp_path: Path, preview: bool) -> None:
    bash = shutil.which("bash") if os.name != "nt" else None
    if bash is None:
        pytest.skip("native POSIX Bash required")
    root, durable, env = _fixture(tmp_path, "owned")
    suffix = hashlib.sha256(str(root).lower().encode()).hexdigest()[:12]
    name = f"agent-logger-sync-{suffix}.timer"
    disabled = tmp_path / "timer-disabled"
    env.update(LOGGER_TEST_TIMER=name, LOGGER_TEST_DISABLED=str(disabled))
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    systemctl = fakebin / "systemctl"
    systemctl.write_text(
        '#!/bin/sh\ncase "$2" in\n'
        'is-active) if [ "${4:-}" = "$LOGGER_TEST_TIMER" ] && '
        '[ ! -f "$LOGGER_TEST_DISABLED" ]; then exit 0; fi; exit 3;;\n'
        'disable) [ "$4" = "$LOGGER_TEST_TIMER" ] || exit 2; '
        'printf disabled > "$LOGGER_TEST_DISABLED"; exit 0;;\n'
        'daemon-reload) exit 0;;\n*) exit 1;;\nesac\n',
    )
    systemctl.chmod(0o755)
    env["PATH"] = str(fakebin) + os.pathsep + env["PATH"]
    assert not (root.parent / ".config" / "systemd" / "user").exists()
    result = subprocess.run(
        [bash, str(INSTALLERS / "install.sh"), "uninstall", "--install-dir", str(root),
         *(["--dry-run"] if preview else [])],
        capture_output=True, text=True, timeout=45, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert disabled.exists() is not preview
    if preview:
        assert f"would disable + remove: {name.removesuffix('.timer')}.service / .timer" in result.stdout
    _assert_state(root, durable, removed=not preview)


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("alias", ["separator", "dot-component"])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("legacy", ["none", "owned", "foreign"])
def test_native_logger_default_root_alias_uses_default_task_and_commands(
    tmp_path: Path, host: str, alias: str, active: bool, legacy: str,
) -> None:
    executable = shutil.which(host) if os.name == "nt" else None
    if executable is None:
        pytest.skip("native Windows shell required")
    root, durable, env = _fixture(tmp_path, "owned")
    default = root.parent / ".agent-logger"
    root.rename(default)
    root = default
    commands = root.parent / ".local" / "bin"
    commands.mkdir(parents=True)
    names = (
        "session-sync", "agent-logger", "collate-session", "read-session-digest",
        "prepare-session-log", "ramp-up-session",
    )
    renderer = load_module(
        "logger_default_command_templates", REPO / "libs" / "installer-engine" / "installer_engine_commands.py",
    )
    templates = renderer._templates(INSTALLERS.parent, root, "agent-logger", home=root.parent)
    for name in names:
        for extension in ("ps1", "cmd"):
            command_name = f"{name}.{extension}"
            (commands / command_name).write_text(templates[command_name][0], encoding="utf-8")
    disabled = tmp_path / "task-disabled"
    removed = tmp_path / "task-removed"
    env.update(
        LOGGER_TEST_DISABLED=str(disabled), LOGGER_TEST_REMOVED=str(removed),
        LOGGER_TEST_TASK_STATE="Running" if active else "Ready",
        LOGGER_TEST_ROOT=str(root), LOGGER_TEST_LEGACY=legacy,
        LOGGER_TEST_TASK_NAME="Agent Logger Session Sync" if legacy == "none"
        else "Agent Logger Session Sync - abcdef123456",
    )
    wrapper = tmp_path / "default-task.ps1"
    wrapper.write_text(
        "param($Installer, $Root)\n$ErrorActionPreference='Stop'\n"
        "function Get-ScheduledTask { param($TaskName, $ErrorAction) "
        "if (-not $TaskName -and $env:LOGGER_TEST_LEGACY -ne 'none') { "
        "$owner=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
        "if ($env:LOGGER_TEST_LEGACY -eq 'foreign') { $owner='S-1-5-18' }; "
        "$launcher=Join-Path $env:LOGGER_TEST_ROOT 'bin\\session-sync-task.ps1'; "
        "[pscustomobject]@{TaskName=$env:LOGGER_TEST_TASK_NAME; TaskPath='\\'; "
        "Actions=@([pscustomobject]@{Arguments=('-I \"' + $launcher + '\"')}); "
        "Principal=[pscustomobject]@{UserId=$owner}}; return }; "
        "if ($TaskName -eq $env:LOGGER_TEST_TASK_NAME) { "
        "[pscustomobject]@{State=$env:LOGGER_TEST_TASK_STATE} } }\n"
        "function Disable-ScheduledTask { param($TaskName, $ErrorAction) "
        "if ($TaskName -ne $env:LOGGER_TEST_TASK_NAME) { throw 'wrong task' }; "
        "[IO.File]::WriteAllText($env:LOGGER_TEST_DISABLED, 'disabled') }\n"
        "function Unregister-ScheduledTask { param($TaskName, $Confirm) "
        "if ($TaskName -ne $env:LOGGER_TEST_TASK_NAME) { throw 'wrong task' }; "
        "[IO.File]::WriteAllText($env:LOGGER_TEST_REMOVED, 'removed') }\n"
        "& $Installer uninstall -InstallDir $Root\n",
        encoding="utf-8",
    )
    requested = str(root) + ("\\" if alias == "separator" else "\\.\\")
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(wrapper),
         str(INSTALLERS / "install.ps1"), requested],
        capture_output=True, text=True, timeout=90, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    refused = active or legacy == "foreign"
    assert result.returncode == (1 if refused else 0), result.stdout + result.stderr
    assert disabled.exists() is (legacy != "foreign")
    assert removed.exists() is not refused
    if legacy == "foreign":
        assert "owned by another account" in result.stderr
    _assert_state(root, durable, removed=not refused)
    for name in names:
        for extension in ("ps1", "cmd"):
            assert (commands / f"{name}.{extension}").exists() is refused


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_logger_removes_case_aliased_runtime_entries_before_ownership(
    tmp_path: Path, host: str,
) -> None:
    executable = shutil.which(host) if os.name == "nt" else None
    if executable is None:
        pytest.skip("native Windows shell required")
    root, durable, env = _fixture(tmp_path, "owned")
    for name in DERIVED:
        path = root / name
        temporary = root / "case-stage"
        path.rename(temporary)
        temporary.rename(root / name.upper())
    wrapper = tmp_path / "case-aliased.ps1"
    wrapper.write_text(
        "param($Installer,$Root)\n$ErrorActionPreference='Stop'\n"
        "function Get-ScheduledTask { param($TaskName,$ErrorAction) }\n"
        "& $Installer uninstall -InstallDir $Root\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(wrapper),
         str(INSTALLERS / "install.ps1"), str(root)],
        capture_output=True, text=True, timeout=90, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_state(root, durable, removed=True)

"""Exercise the complete Logger uninstaller without host task mutations."""

from __future__ import annotations

import os
import json
import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import time
import venv
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALLERS = REPO / "plugins" / "agent-logger" / "scripts"
DERIVED = (
    "versions", ".venv", "snapshots", "bin", "current-version", "last-known-good",
    "payload-dir", "stamped-version", "deploy-manifest.json", "running-version.json",
)


def _fixture(tmp_path, ownership):
    home = tmp_path / "home"
    root = home / "scoped-runtime"
    root.mkdir(parents=True)
    for name in DERIVED:
        path = root / name
        if name in ("versions", ".venv", "snapshots", "bin"):
            path.mkdir()
            (path / "derived").write_text("rebuildable")
        else:
            path.write_text("selected-runtime")
    manifest = root / "deploy-manifest.json"
    if ownership in ("missing", "stamped", "foreign-stamp", "outside-stamp"):
        manifest.unlink()
    elif ownership == "malformed":
        manifest.write_text("{")
    else:
        manifest.write_text(json.dumps({
            "schema_version": 3,
            "service": "agent-logger" if ownership == "owned" else "another-product",
        }))
    if ownership in ("foreign-binding", "foreign-lifecycle", "cell", "managed"):
        record = {"schema_version": 3, "service": "agent-logger"}
        if ownership == "foreign-binding":
            record["commandBinding"] = {"sourceFingerprint": "another-source"}
        elif ownership == "foreign-lifecycle":
            record["commandLifecycle"] = {"owner": "another-source"}
        else:
            (root / ("install.json" if ownership == "cell" else "runtime-deployment.json")).write_text("{}")
        manifest.write_text(json.dumps(record))
    if ownership in ("stamped", "foreign-stamp", "outside-stamp"):
        snapshot = (
            tmp_path / "outside-snapshot" if ownership == "outside-stamp"
            else root / "snapshots" / "1.0.0"
        )
        snapshot.mkdir()
        name = "another-product" if ownership == "foreign-stamp" else "agent-logger"
        (snapshot / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "1.0.0"\n',
        )
        (root / "payload-dir").write_text(str(snapshot))
    durable = {
        "config.yaml": b"enabled: true\n",
        "sessions.db": b"durable database fixture",
        "unknown.txt": b"operator content",
        "session-state/events.jsonl": b'{"preserved":true}\n',
        "snapshots/.construction-leases/held.lock": b"persistent snapshot coordination",
    }
    for name, content in durable.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
    }
    for name in (
        "COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_CONTEXT",
        "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN", "COPILOT_PLUGIN_INSTALL_STAGED",
        "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "AGENT_LOGGER_HOME",
    ):
        env.pop(name, None)
    return root, durable, env


def _assert_state(root, durable, *, removed, ownership="owned"):
    for name, content in durable.items():
        assert (root / name).read_bytes() == content
    for name in DERIVED:
        if name == "snapshots" and removed:
            assert sorted(child.name for child in (root / name).iterdir()) == [".construction-leases"]
            continue
        if name == "deploy-manifest.json" and ownership in (
            "missing", "stamped", "foreign-stamp", "outside-stamp",
        ):
            assert not (root / name).exists()
            continue
        assert (root / name).exists() is not removed, name


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("ownership", [
    "owned", "stamped", "foreign", "missing", "malformed", "foreign-stamp", "outside-stamp",
    "foreign-binding", "foreign-lifecycle", "cell", "managed",
])
def test_native_logger_uninstall_removes_runtime_but_retains_state(tmp_path, host, ownership):
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

    def run(*args):
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File", str(runner),
             str(INSTALLERS / "install.ps1"), str(root), *args],
            capture_output=True, text=True, timeout=45, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
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
def test_posix_logger_uninstall_removes_runtime_but_retains_state(tmp_path, ownership):
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

    def run(*args):
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
def test_native_logger_refuses_running_task_before_deletion(tmp_path, host):
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
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert "Active Logger sync task" in result.stderr, result.stdout + result.stderr
    assert marker.read_text() == "disabled"
    _assert_state(root, durable, removed=False)


def test_posix_logger_refuses_running_service_before_deletion(tmp_path):
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
def test_logger_refuses_live_runtime_before_deletion(tmp_path, host):
    if (host == "bash") == (os.name == "nt"):
        pytest.skip("native OS shell only")
    executable = shutil.which(host)
    if executable is None:
        pytest.skip("native shell unavailable")
    root, durable, env = _fixture(tmp_path, "owned")
    slot = root / "versions" / "1.0.0"
    venv.EnvBuilder(with_pip=False).create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = subprocess.Popen(
        [str(python), "-I", "-c", "print('ready', flush=True); input()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, text=True, **kwargs,
    )
    try:
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
def test_logger_stamp_retry_survives_partial_snapshot_removal(tmp_path, host):
    if (host == "bash") == (os.name == "nt"):
        pytest.skip("native OS shell only")
    executable = shutil.which(host)
    if executable is None:
        pytest.skip("native shell unavailable")
    root, durable, env = _fixture(tmp_path, "stamped")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
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


@pytest.mark.parametrize("host", ["pwsh", "bash"])
@pytest.mark.parametrize("custody", ["snapshot", "retention"])
def test_logger_preserves_busy_generation_custody(tmp_path, host, custody):
    if (host == "bash") == (os.name == "nt"):
        pytest.skip("native OS shell only")
    executable = shutil.which(host)
    if executable is None:
        pytest.skip("native shell unavailable")
    root, durable, env = _fixture(tmp_path, "owned")
    spec = importlib.util.spec_from_file_location("logger_custody_test", INSTALLERS / "uninstall_guard.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    leases = helper._load("_logger_test_leases", "versioned_runtime_leases.py")
    if custody == "snapshot":
        lease = leases.construction_lease(root / "snapshots", "derived", timeout=0)
    else:
        (root / "versions" / "1.0.0").mkdir()
        lease = leases._kernel_lease(root, "1.0.0", kind="retention", shared=True, timeout=0)
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
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


def test_logger_refuses_removable_in_root_helpers(tmp_path):
    root, durable, env = _fixture(tmp_path, "owned")
    scripts = root / "snapshots" / "1.0.0" / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / "uninstall_guard.py"
    helper.write_bytes((INSTALLERS / "uninstall_guard.py").read_bytes())
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(helper), "--root", str(root)],
        capture_output=True, text=True, timeout=15, env=env, **kwargs,
    )
    assert result.returncode != 0
    assert "helpers are inside removable runtime content" in result.stderr
    _assert_state(root, durable, removed=False)


def test_logger_allows_preserved_in_root_staging_helpers(tmp_path):
    home = tmp_path / "home"
    root = home / "runtime"
    scripts = root / ".install-stage" / "owner" / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / "uninstall_guard.py"
    helper.write_bytes((INSTALLERS / "uninstall_guard.py").read_bytes())
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(helper), "--root", str(root)],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)}, **kwargs,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert helper.is_file()


def test_posix_logger_refuses_path_launched_ambiguous_runtime(tmp_path):
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
        assert process.stdout.readline().strip() == "ready"
        assert (slot / "bin" / "python").is_symlink()
        assert Path(f"/proc/{process.pid}/cmdline").read_bytes().split(b"\0")[0] == b"python"
        assert not Path(os.readlink(f"/proc/{process.pid}/exe")).is_relative_to(root)
        result = subprocess.run(
            [shutil.which("bash"), str(INSTALLERS / "install.sh"),
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
def test_logger_uninstall_refuses_unverified_process_inventory(tmp_path, monkeypatch, capsys, inventory):
    root, durable, _ = _fixture(tmp_path, "owned")
    spec = importlib.util.spec_from_file_location("logger_census_test", INSTALLERS / "uninstall_guard.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
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


def test_logger_runtime_guard_reuses_validated_inventory(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("logger_census_reuse", INSTALLERS / "uninstall_guard.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    calls = []

    def census():
        calls.append(True)
        return [os.getpid()]

    runtime = SimpleNamespace(
        _iter_all_pids=census, _versions_with_live_process=lambda _: set(),
    )
    monkeypatch.setattr(helper, "_load", lambda *args: runtime)
    helper.refuse_live_runtime(tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh", "bash"])
def test_logger_parent_traversal_refuses_before_lock_or_root_mutation(tmp_path, host):
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
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
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
def test_native_logger_relative_root_uses_provider_not_process_cwd(tmp_path, host):
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
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_state(root, durable, removed=True)
    _assert_state(other, other_durable, removed=False)
    assert not (other / ".install.lock").exists()


def test_posix_logger_relative_root_survives_marketplace_self_staging(tmp_path):
    bash = shutil.which("bash") if os.name != "nt" else None
    if bash is None:
        pytest.skip("native POSIX Bash required")
    root, durable, env = _fixture(tmp_path / "wanted", "owned")
    other, other_durable, _ = _fixture(tmp_path / "other", "owned")
    env["HOME"] = env["USERPROFILE"] = str(other.parent)
    spec = importlib.util.spec_from_file_location(
        "logger_release_fixture", REPO / "plugins" / "agent-logger" / "tests" / "test_install_binstub.py",
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
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
def test_posix_logger_default_root_alias_uses_default_units_and_commands(tmp_path, alias, active):
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
    for name in names:
        (commands / name).write_text("default compatibility command")
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
def test_logger_bom_manifest_provenance_fallback(tmp_path, monkeypatch, lifecycle):
    root, durable, _ = _fixture(tmp_path, "owned")
    record = {"schema_version": 3, "service": "agent-logger"}
    if lifecycle:
        record["commandLifecycle"] = {"owner": "incumbent"}
    manifest = root / "deploy-manifest.json"
    manifest.write_text(json.dumps(record), encoding="utf-8-sig")
    spec = importlib.util.spec_from_file_location("logger_bom_test", INSTALLERS / "uninstall_guard.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    binding = helper._load("_logger_bom_binding", "command_binding.py")
    primitive = binding._load_primitive(INSTALLERS.parent)
    monkeypatch.setattr(primitive, "probe_legacy_entrypoint", lambda **kwargs: {"allowMutation": True})
    monkeypatch.setattr(binding, "_load_primitive", lambda _: primitive)

    def unavailable(*args, **kwargs):
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
def test_native_logger_refuses_external_python_module_consumer(tmp_path, host):
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
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
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
            creationflags=subprocess.CREATE_NO_WINDOW,
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
def test_logger_native_process_inventory_refuses_uncertain_consumers(tmp_path, monkeypatch, outcome):
    spec = importlib.util.spec_from_file_location("logger_windows_census", INSTALLERS / "uninstall_guard.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
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
def test_posix_logger_custom_alias_normalizes_and_retires_legacy_units(tmp_path, alias, active, selector):
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
    source = (INSTALLERS / "install.sh").read_text(encoding="utf-8")
    start = source.index("write_units() {")
    end = source.index('\ncase "$ACTION" in\n  install|update|provision)', start)
    writer = source[start:end]
    generated = subprocess.run(
        [bash, "-c",
         'set -euo pipefail\nINSTALL_DIR="$1"; UNIT_DIR="$2"; TIMER_NAME="$3"; VENV="$INSTALL_DIR/$4"\n'
         '_fail() { printf "%s\\n" "$1" >&2; }; chg() { :; }\n'
         + writer + "\nwrite_units\n",
         "logger-unit-fixture", requested, str(units), legacy_name, selector],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    timer = units / f"{legacy_name}.timer"
    assert timer.is_file()
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


def test_posix_logger_refuses_external_python_running_rooted_script(tmp_path):
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
        assert consumer.stdout.readline().strip() == "ready"
        assert not Path(os.readlink(f"/proc/{consumer.pid}/exe")).is_relative_to(root)
        assert b"agent_logger" not in Path(f"/proc/{consumer.pid}/cmdline").read_bytes()
        result = subprocess.run(
            [shutil.which("bash"), str(INSTALLERS / "install.sh"), "uninstall",
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
def test_posix_logger_disables_loaded_timer_without_unit_files(tmp_path, preview):
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
def test_native_logger_default_root_alias_uses_default_task_and_commands(tmp_path, host, alias, active, legacy):
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
    for name in names:
        for extension in ("ps1", "cmd"):
            (commands / f"{name}.{extension}").write_text("default compatibility command")
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
        creationflags=subprocess.CREATE_NO_WINDOW,
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
def test_native_logger_removes_case_aliased_runtime_entries_before_ownership(tmp_path, host):
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
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_state(root, durable, removed=True)

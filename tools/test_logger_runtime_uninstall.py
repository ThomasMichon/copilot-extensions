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
import venv

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

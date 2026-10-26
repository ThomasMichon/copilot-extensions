"""Exercise the complete Logger uninstaller without host task mutations."""

from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import subprocess

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
            "service": "agent-logger" if ownership == "owned" else "another-product",
        }))
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
        if name == "deploy-manifest.json" and ownership in (
            "missing", "stamped", "foreign-stamp", "outside-stamp",
        ):
            assert not (root / name).exists()
            continue
        assert (root / name).exists() is not removed, name


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("ownership", [
    "owned", "stamped", "foreign", "missing", "malformed", "foreign-stamp", "outside-stamp",
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

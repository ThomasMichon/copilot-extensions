"""Cold policy readers use prepared managed Python before any runtime effects."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from . import source_publication_fixture as fixture
from .test_action_native_ps1 import function, quote
from .test_managed_bootstrap_reader import (
    prepared_managed_helper as prepared_managed_helper,
    prepared_reader_payload as prepared_reader_payload,
)


def reader_environment(
    tmp_path: Path, uv: Path, managed_root: Path, scenario: str,
) -> tuple[dict[str, str], Path, Path]:
    env = fixture.environment(tmp_path)
    commands = tmp_path / "commands"
    commands.mkdir()
    marker = tmp_path / "ambient-ran"
    for name in ("python", "python3", "py"):
        command = commands / (name + (".cmd" if os.name == "nt" else ""))
        command.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo invoked\nexit /b 91\n'
            if os.name == "nt" else '#!/bin/sh\nprintf invoked > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        command.chmod(0o700)
    if scenario != "missing-uv":
        shutil.copy2(uv, commands / uv.name)
    if os.name != "nt":
        mkdir = shutil.which("mkdir")
        assert mkdir
        (commands / "mkdir").symlink_to(mkdir)
    empty = tmp_path / "unprepared-python"
    empty.mkdir()
    env.update(
        PATH=str(commands), AMBIENT_MARKER=str(marker), UV_OFFLINE="1",
        UV_PYTHON_INSTALL_DIR=str(empty if scenario == "unprepared" else managed_root),
        UV_CACHE_DIR=str(tmp_path / "uv-cache"), UV_CONFIG_FILE="missing-relative.toml",
        UV_NO_CONFIG="false", UV_PYTHON=str(commands / "python"),
        UV_NO_MANAGED_PYTHON="1", VIRTUAL_ENV="unrelated", CONDA_PREFIX="unrelated",
        PYTHONHOME="unrelated", PYTHONPATH="unrelated",
    )
    return env, marker, empty


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize("entry", ["runtime-gate.ps1", "bootstrap-check.ps1"])
@pytest.mark.parametrize("scenario", [
    "managed", "unprepared", "missing-uv", "missing-provider", "missing-request",
    "different-request",
])
def test_powershell_cold_readers_select_managed_python_before_policy(
    tmp_path: Path, shell: str, entry: str, scenario: str,
    prepared_reader_payload: Path, prepared_managed_helper: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    host = shutil.which(shell)
    if not host or shell == "powershell" and os.name != "nt":
        pytest.skip("This control requires the addressed native shell")
    uv, _, managed_root = prepared_managed_helper
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    shutil.copytree(prepared_reader_payload, payload)
    shutil.copytree(fixture.PLUGIN / "src", payload / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("plugin.json", "pyproject.toml"):
        shutil.copy2(fixture.PLUGIN / name, payload / name)
    if scenario in {"missing-provider", "missing-request"}:
        name = (
            "bootstrap-uv.ps1" if scenario == "missing-provider"
            else "installer-instance-python-version.txt"
        )
        (scripts / name).rename(scripts / (name + ".unavailable"))
    if scenario == "different-request":
        (scripts / "installer-instance-python-version.txt").write_text(
            "cpython@3.11.999\n", encoding="utf-8",
        )
    env, ambient, empty = reader_environment(tmp_path, uv, managed_root, scenario)
    root = Path(env["HOME"]) / ".agent-dispatch"
    root.mkdir()
    (root / "action-controls.json").write_text(json.dumps({
        "automatic-runtime-reconcile": {"disabled": True, "reason": "managed reader witness"},
    }), encoding="utf-8")
    installer_called = tmp_path / "installer-called"
    (scripts / "install.ps1").write_text(
        f"# 'stamp'\n[IO.File]::WriteAllText({quote(installer_called)}, 'called')\nexit 0\n",
        encoding="utf-8",
    )
    runner = tmp_path / "reader.ps1"
    if entry == "runtime-gate.ps1":
        body = (
            f"$payloadRoot = {quote(payload)}\n$runtimeRoot = {quote(root)}\n"
            + function(scripts / entry, "Assert-AutomaticRuntimeReconcile")
            + "\nAssert-AutomaticRuntimeReconcile\nthrow 'provision boundary reached'\n"
        )
    else:
        body = f"& {quote(scripts / entry)}\nexit $LASTEXITCODE\n"
    runner.write_text("$ErrorActionPreference = 'Stop'\n" + body, encoding="utf-8")
    samples: list[float] = []
    for _ in range(2 if scenario == "managed" else 1):
        begin = time.monotonic()
        result = subprocess.run(
            [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(runner)], env=env, cwd=tmp_path, input="caller input\n",
            capture_output=True, text=True, timeout=25, creationflags=fixture.HEADLESS,
        )
        samples.append(time.monotonic() - begin)
        assert result.returncode == (3 if scenario == "managed" else 126), result.stdout + result.stderr
        assert result.stdout == ("{}" if entry == "bootstrap-check.ps1" else "")
        assert ("managed reader witness" if scenario == "managed" else "prepared") in result.stderr
        assert not ambient.exists() and not installer_called.exists()
        assert sorted(path.name for path in root.iterdir()) == ["action-controls.json"]
        assert not list(empty.iterdir())
    if scenario == "managed":
        record_property("dispatch_powershell_cold_reader_performance", json.dumps({
            "entry": entry, "shell": shell, "samples_seconds": samples, "network": "disabled",
            "scope": "cold policy refusal; not provisioning or daemon readiness",
        }))


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_raw_powershell_cold_reader_preserves_caller_environment(
    tmp_path: Path, shell: str, prepared_managed_helper: tuple[Path, Path, Path],
) -> None:
    host = shutil.which(shell)
    if not host or shell == "powershell" and os.name != "nt":
        pytest.skip("This control requires the addressed native shell")
    uv, selected, managed_root = prepared_managed_helper
    env, marker, empty = reader_environment(tmp_path, uv, managed_root, "managed")
    runner = tmp_path / "raw-reader.ps1"
    runner.write_text(
        "$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version 2.0\n"
        f". {quote(fixture.SCRIPTS / 'versioned_runtime_native.ps1')}\n"
        f". {quote(fixture.SCRIPTS / 'dispatch-bootstrap-python.ps1')}\n"
        "$names = @('PATH','UV_CONFIG_FILE','UV_NO_CONFIG','UV_PYTHON',"
        "'UV_NO_MANAGED_PYTHON','VIRTUAL_ENV','CONDA_PREFIX','PYTHONHOME','PYTHONPATH')\n"
        "$before = @($names | ForEach-Object { [Environment]::GetEnvironmentVariable($_) })\n"
        f"$python = Get-DispatchPreparedBootstrapPython -Root {quote(tmp_path / 'runtime')} "
        f"-ScriptsRoot {quote(fixture.SCRIPTS)}\n"
        "$after = @($names | ForEach-Object { [Environment]::GetEnvironmentVariable($_) })\n"
        "if (Compare-Object $before $after -CaseSensitive) { throw 'Caller environment changed' }\n"
        "ConvertTo-Json -Compress -InputObject ([string]$python)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(runner)], env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=25, creationflags=fixture.HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == str(selected)
    assert not marker.exists() and not list(empty.iterdir())
    assert not (tmp_path / "runtime").exists()

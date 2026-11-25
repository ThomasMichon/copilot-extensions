"""Prepared helper selection preserves explicit runtime and driver bindings."""
from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from . import source_publication_fixture as fixture


@pytest.fixture(scope="module")
def prepared_reader_payload(tmp_path_factory: pytest.TempPathFactory) -> Path:
    payload = tmp_path_factory.mktemp("dispatch-bootstrap-reader") / "agent-dispatch"
    shutil.copytree(
        fixture.SCRIPTS, payload / "scripts",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    previous_path = sys.path[:]
    try:
        release = runpy.run_path(str(fixture.REPO / "tools/materialize_main.py"))
        materialized = release["materialize_installer_engine_ref_into"](
            source_consumer_dir=fixture.PLUGIN,
            dest_consumer_dir=payload,
            canonical_root=fixture.REPO,
        )
    finally:
        sys.path[:] = previous_path
    assert materialized and not any(line.startswith("SKIP") for line in materialized), materialized
    return payload


@pytest.fixture(scope="module")
def prepared_managed_helper() -> tuple[Path, Path, Path]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("This acquisition-free control requires existing uv")
    environment = dict(os.environ)
    if "UV_PYTHON_INSTALL_DIR" not in environment:
        # The contained runner changes HOME after preparing its interpreter.
        environment["UV_PYTHON_INSTALL_DIR"] = str(
            Path(str(getattr(sys, "_base_executable", sys.executable))).resolve().parents[2]
        )
    selected = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        capture_output=True, text=True, timeout=15, creationflags=fixture.HEADLESS, env=environment,
    )
    if selected.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.11.18")
    root = subprocess.check_output(
        [uv, "python", "dir"], text=True, timeout=15, creationflags=fixture.HEADLESS, env=environment,
    ).strip()
    return Path(uv), Path(selected.stdout.strip()).resolve(), Path(root)


@pytest.fixture(scope="module")
def raw_reader_payload(tmp_path_factory: pytest.TempPathFactory) -> Path:
    payload = tmp_path_factory.mktemp("raw-dispatch-reader")
    scripts = payload / "scripts"
    scripts.mkdir()
    for name in ("install.ps1", "versioned_runtime_native.ps1", "base-python.ps1"):
        shutil.copy2(fixture.SCRIPTS / name, scripts / name)
    return payload


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize("scenario", [
    "managed", "unprepared", "driver", "wheel", "runtime", "external", "invalid",
    "raw-managed", "raw-unprepared", "incomplete",
    "cache", "cache-root", "cache-directory",
])
def test_managed_bootstrap_reader_preserves_bindings_and_external_root(
    tmp_path: Path, shell: str, scenario: str, prepared_reader_payload: Path,
    raw_reader_payload: Path,
    prepared_managed_helper: tuple[Path, Path, Path],
) -> None:
    host = shutil.which(shell)
    if not host or shell == "powershell" and os.name != "nt":
        pytest.skip("This control requires the addressed native shell")
    uv, selected, managed_root = prepared_managed_helper
    raw = scenario.startswith("raw-")
    mode = scenario.removeprefix("raw-")
    payload = raw_reader_payload if raw else prepared_reader_payload
    if mode == "incomplete":
        payload = tmp_path / "incomplete-payload"
        shutil.copytree(prepared_reader_payload, payload)
        provider = payload / "scripts/bootstrap-uv.ps1"
        provider.rename(provider.with_suffix(".unavailable"))
    engine = (
        fixture.REPO / "libs/installer-engine/installer-engine.ps1" if raw
        else payload / "scripts/installer-engine.ps1"
    )
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
    if mode in {"managed", "unprepared", "external"} or mode.startswith("cache"):
        shutil.copy2(uv, commands / uv.name)
    empty_root = tmp_path / "unprepared-python"
    empty_root.mkdir()
    env.update(
        PATH=str(commands), AMBIENT_MARKER=str(marker), UV_OFFLINE="1",
        UV_PYTHON_INSTALL_DIR=str(
            managed_root if mode in {"managed", "external"} or mode.startswith("cache")
            else empty_root
        ),
        UV_CACHE_DIR=str(tmp_path / "uv-cache"), UV_CONFIG_FILE="missing-relative.toml",
        UV_NO_CONFIG="false",
    )
    runner = tmp_path / "reader.ps1"
    runner.write_text(
        "param($Scripts,$Engine,$Root,$Scenario,$Prepared,$ManagedRoot)\n"
        "Set-StrictMode -Version 2.0\n$ErrorActionPreference = 'Stop'\n"
        ". $Engine\n"
        ". (Join-Path $Scripts 'versioned_runtime_native.ps1')\n"
        ". (Join-Path $Scripts 'base-python.ps1')\n"
        + fixture.function("ps1", "Get-BootstrapPython") + "\n"
        "$InstallDir = $Root; $Action = 'status'; $WheelProfile = ''; $WheelPython = ''\n"
        "$script:DispatchBootstrapPython = $null\n"
        "if ($Scenario -eq 'driver') { $script:DispatchBootstrapPython = $Prepared }\n"
        "if ($Scenario -eq 'wheel') { $WheelProfile = 'base'; $WheelPython = 'explicit-wheel' }\n"
        "if ($Scenario -eq 'runtime') { function Resolve-DispatchBasePython { return $Prepared } }\n"
        "if ($Scenario -eq 'invalid') { $env:AGENT_DISPATCH_BASE_PYTHON = 'relative-python.exe' }\n"
        "if ($Scenario -eq 'external') { $InstallDir = $ManagedRoot }\n"
        "$before = $env:PATH\n"
        "$script:NativeCalls = 0\n"
        "if ($Scenario -like 'cache*') {\n"
        "  $script:ReaderCapture = ${function:Invoke-Hidden}\n"
        "  function Invoke-Hidden {\n"
        "    param([string]$FilePath,[string[]]$ArgList,[ref]$ErrorOutput,"
        "[hashtable]$Environment,[Text.Encoding]$CaptureEncoding)\n"
        "    $script:NativeCalls++\n"
        "    $output = & $script:ReaderCapture @PSBoundParameters\n"
        "    $code = $LASTEXITCODE; $global:LASTEXITCODE = $code\n"
        "    return $output\n"
        "  }\n"
        "}\n"
        "$selected = Get-BootstrapPython -ExternalOnly:($Scenario -eq 'external')\n"
        "if (-not $selected) { throw 'Prepared managed helper is unavailable' }\n"
        "$counts = @($script:NativeCalls)\n"
        "if ($Scenario -eq 'cache') {\n"
        "  if ((Get-BootstrapPython) -cne $selected) { throw 'Cached selection changed' }\n"
        "  $counts += $script:NativeCalls\n"
        "  if ((Get-BootstrapPython -ExternalOnly) -cne $selected) "
        "{ throw 'External selection changed' }\n"
        "  $counts += $script:NativeCalls\n"
        "  if ((Get-BootstrapPython -ExternalOnly) -cne $selected) "
        "{ throw 'External cached selection changed' }\n"
        "  $counts += $script:NativeCalls\n"
        "}\n"
        "if ($Scenario -eq 'cache-root') {\n"
        "  $InstallDir = $ManagedRoot\n"
        "  if ((Get-BootstrapPython) -cne $selected) { throw 'Root changed selection' }\n"
        "  $counts += $script:NativeCalls\n"
        "  if (Get-BootstrapPython -ExternalOnly) { throw 'Cached base escaped root refusal' }\n"
        "  $counts += $script:NativeCalls\n"
        "}\n"
        "if ($Scenario -eq 'cache-directory') {\n"
        "  $directory = Join-Path (Split-Path -Parent $Root) 'incomplete-bootstrap'\n"
        "  [void][IO.Directory]::CreateDirectory($directory)\n"
        "  $script:InstallerDependencyPolicy = Join-Path $directory 'installer_dependency_policy.py'\n"
        "  $refused = $false\n"
        "  try { [void](Get-BootstrapPython) } catch {\n"
        "    if ($_.Exception.Message -notlike '*bootstrap-uv.ps1*') { throw }\n"
        "    $refused = $true\n"
        "  }\n"
        "  if (-not $refused) { throw 'Cached selection hid missing bootstrap assets' }\n"
        "  $counts += $script:NativeCalls\n"
        "}\n"
        "if ($env:PATH -cne $before -or $env:UV_CONFIG_FILE -cne 'missing-relative.toml' "
        "-or $env:UV_NO_CONFIG -cne 'false') { throw 'Caller environment changed' }\n"
        "if ($Scenario -like 'cache*') {\n"
        "  @{python=[string]$selected; calls=$counts} | ConvertTo-Json -Compress\n"
        "  return\n"
        "}\n"
        "ConvertTo-Json -Compress -InputObject ([string]$selected)\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(runner), str(payload / "scripts"), str(engine), str(runtime),
         mode, str(selected), str(managed_root)],
        env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30, creationflags=fixture.HEADLESS,
    )
    assert not marker.exists(), result.stdout + result.stderr
    if mode in {"unprepared", "external", "invalid", "incomplete"}:
        assert result.returncode != 0, result.stdout + result.stderr
        expected = (
            "bootstrap-uv.ps1" if mode == "incomplete"
            else "absolute executable path" if mode == "invalid"
            else "Prepared managed helper is unavailable"
        )
        assert expected in result.stderr
        assert not result.stdout.strip()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        value = json.loads(result.stdout)
        if mode.startswith("cache"):
            assert value["python"] == str(selected)
            assert value["calls"] == {
                "cache": [3, 4, 7, 8], "cache-root": [3, 6, 9], "cache-directory": [3, 3],
            }[mode]
        else:
            assert value == ("explicit-wheel" if mode == "wheel" else str(selected))
    assert not runtime.exists()
    assert not list(empty_root.iterdir())

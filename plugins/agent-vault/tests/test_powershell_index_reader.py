"""Native index readers use managed Python and construction's capture context."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import cast

import pytest
from agent_procutil import no_window_kwargs

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
_readers = import_module(".test_installer_managed_readers", __package__)
powershell_reader_payload: object = _readers.powershell_reader_payload
prepared_native_bootstrap: object = _readers.prepared_native_bootstrap
_isolated_install_env = cast(
    Callable[[Path], dict[str, str]], _readers._isolated_install_env,
)


@pytest.mark.skipif(os.name != "nt", reason="native Windows index reader")
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize(("composed", "setting"), [
    (False, "file"), (True, "file"), (False, "environment"),
    (False, "uv-override"), (True, "uv-project"), (True, "relative-config"),
    (False, "provider-location"), (True, "active-venv"), (True, "active-conda"),
    (True, "malformed"), (True, "unprepared"),
    (True, "unicode"),
])
def test_powershell_index_uses_managed_policy_and_construction_context(
    tmp_path: Path, shell: str, composed: bool, setting: str,
    powershell_reader_payload: Path, prepared_native_bootstrap: tuple[Path, Path, Path],
) -> None:
    host = shutil.which(shell)
    if not host:
        pytest.skip("This control requires the addressed native shell")
    uv, _python, managed_root = prepared_native_bootstrap
    payload = powershell_reader_payload if composed else PLUGIN
    engine = (
        payload / "scripts/installer-engine.ps1" if composed
        else REPO / "libs/installer-engine/installer-engine.ps1"
    )
    env = _isolated_install_env(tmp_path / "home")
    for name in (
        "UV_DEFAULT_INDEX", "UV_INDEX_URL", "PIP_INDEX_URL", "PIP_CONFIG_FILE",
        "UV_PROJECT", "UV_CONFIG_FILE", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY",
        "UV_ISOLATED", "CONDA_PREFIX",
    ):
        env.pop(name, None)
    commands = tmp_path / "poisoned-path"
    commands.mkdir()
    marker = tmp_path / "ambient-ran"
    for name in ("python", "python3", "py", "pip", "pip3"):
        (commands / (name + ".cmd")).write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo invoked\n'
            "echo https://wrong.invalid/simple\nexit /b 0\n", encoding="utf-8",
        )
    addressed_uv = commands / Path(uv).name
    if setting != "uv-override":
        shutil.copy2(uv, addressed_uv)
    empty_root = tmp_path / "unprepared"
    empty_root.mkdir()
    env.update(
        PATH=str(commands), AMBIENT_MARKER=str(marker), UV_NO_SYSTEM_CONFIG="1",
        UV_OFFLINE="1", UV_NO_CONFIG="false",
        UV_PYTHON_INSTALL_DIR=str(empty_root if setting == "unprepared" else managed_root),
    )
    location = tmp_path / "provider caf\u00e9"
    location.mkdir()
    uv_config = tmp_path / "uv.toml"
    uv_config.write_text("", encoding="utf-8")
    env["UV_CONFIG_FILE"] = str(uv_config)
    configuration = tmp_path / "pip.ini"
    configuration.write_text(
        "[invalid" if setting == "malformed"
        else "[global]\nindex-url=https://file.invalid/simple\n", encoding="utf-8",
    )
    env["PIP_CONFIG_FILE"] = str(configuration)
    expected = "https://file.invalid/simple"
    if setting == "environment":
        expected = env["PIP_INDEX_URL"] = "https://environment.invalid/simple"
    elif setting == "unicode":
        expected = env["PIP_INDEX_URL"] = "https://index.invalid/caf\u00e9/simple"
    elif setting == "uv-override":
        expected = env["UV_DEFAULT_INDEX"] = "https://uv.invalid/simple"
    elif setting in {"uv-project", "provider-location", "relative-config"}:
        env.pop("UV_CONFIG_FILE")
        selected = location
        if setting != "provider-location":
            selected = location / "working directory"
            selected.mkdir()
            env["UV_WORKING_DIR"] = selected.name
        document = '[[index]]\nurl="https://project.invalid/simple"\ndefault=true\n'
        if setting == "relative-config":
            (selected / "selected.toml").write_text(document, encoding="utf-8")
            env["UV_CONFIG_FILE"] = "selected.toml"
        else:
            project = selected / "project"
            project.mkdir()
            (project / "uv.toml").write_text(document, encoding="utf-8")
            env["UV_PROJECT"] = "project"
        expected = ""
    elif setting in {"active-venv", "active-conda"}:
        active = tmp_path / "active environment"
        active.mkdir()
        if setting == "active-venv":
            (active / "pyvenv.cfg").write_text("home=not-executed\n", encoding="utf-8")
            env["VIRTUAL_ENV"] = str(active)
        else:
            (active / "conda-meta").mkdir()
            env["CONDA_PREFIX"] = str(active)
        expected = "https://site.invalid/simple"
        (active / "pip.ini").write_text("[global]\nindex-url=" + expected + "\n", encoding="utf-8")
        configuration.write_text("[global]\ntimeout=5\n", encoding="utf-8")
    runner = tmp_path / "index-reader.ps1"
    runner.write_text(
        "param($Payload,$Engine,$Root,$Uv,$Location)\n"
        "Set-StrictMode -Version 2.0\n$ErrorActionPreference = 'Stop'\n"
        ". $Engine\n"
        ". (Join-Path $Payload 'scripts/versioned_runtime_native.ps1')\n"
        "$errors = $null; $tokens = $null\n"
        "$ast = [Management.Automation.Language.Parser]::ParseFile("
        "(Join-Path $Payload 'scripts/install.ps1'),[ref]$tokens,[ref]$errors)\n"
        "if ($errors.Count) { throw 'Installer parse failed' }\n"
        "foreach ($name in @('Get-BootstrapPython','Ensure-UvIndex')) {\n"
        "  $definition = $ast.Find({param($node) $node -is "
        "[Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -eq $name},$true)\n"
        "  . ([scriptblock]::Create($definition.Extent.Text))\n}\n"
        "$InstallDir = $Root; $Action = 'provision'\n"
        "$WheelProfile = ''; $script:RuntimeProfilePython = ''\n"
        "function Write-Step { param($Message) }\n"
        "$nativeDirectory = [Environment]::CurrentDirectory\n"
        "Set-Location -LiteralPath $Location\n"
        "$configuration = $env:UV_CONFIG_FILE; $workingDirectory = $env:UV_WORKING_DIR\n"
        "Ensure-UvIndex -UvCommand $Uv\n"
        "if ([Environment]::CurrentDirectory -cne $nativeDirectory) "
        "{ throw 'Caller cwd changed' }\n"
        "if ($env:UV_CONFIG_FILE -cne $configuration "
        "-or $env:UV_WORKING_DIR -cne $workingDirectory "
        "-or $env:UV_NO_CONFIG -cne 'false') { throw 'Caller configuration changed' }\n"
        "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$env:UV_DEFAULT_INDEX))\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"
    result = subprocess.run(  # noqa: S603 - addressed shell and owned fixture paths
        [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(runner), str(payload), str(engine), str(runtime),
         str(addressed_uv), str(location)],
        env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert not marker.exists(), result.stdout + result.stderr
    if setting in {"malformed", "unprepared"}:
        assert result.returncode != 0, result.stdout + result.stderr
        diagnostic = (
            "configuration reader failed" if setting == "malformed"
            else "prepared uv-managed bootstrap Python"
        )
        assert diagnostic in result.stderr
        assert not result.stdout.strip()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert base64.b64decode(result.stdout.strip()).decode("utf-8") == expected
    assert not runtime.exists()
    assert not list(empty_root.iterdir())

"""Explicit setup readers reuse the existing prepared managed binding."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
import os
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import cast

from agent_procutil import no_window_kwargs
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
_snapshot = import_module(".test_install_snapshot_ps1", __package__)
_stage_payload = cast(Callable[[Path], Path], _snapshot._stage_payload)
_isolated_install_env = cast(
    Callable[[Path], dict[str, str]], _snapshot._isolated_install_env,
)


@pytest.mark.skipif(os.name != "nt", reason="native Windows installer readers")
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize("function", ["Get-BootstrapPython", "Resolve-PythonCommand"])
@pytest.mark.parametrize("scenario", ["managed", "unprepared", "wheel", "profile"])
def test_powershell_setup_readers_ignore_ambient_python(
    tmp_path: Path, shell: str, function: str, scenario: str,
) -> None:
    host, uv = shutil.which(shell), shutil.which("uv")
    if not host or not uv:
        pytest.skip("This control requires uv and the addressed native shell")
    selected = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    if selected.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.11.18")
    managed_root = subprocess.check_output(
        [uv, "python", "dir"], text=True, timeout=15, **no_window_kwargs(),
    ).strip()
    payload = _stage_payload(tmp_path / "composed")
    env = _isolated_install_env(tmp_path / "home")
    commands = tmp_path / "poisoned-path"
    commands.mkdir()
    marker = tmp_path / "ambient-ran"
    for name in ("python", "python3", "py", "pip", "pip3"):
        (commands / (name + ".cmd")).write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo invoked\n'
            "echo Python 3.9.25\nexit /b 0\n", encoding="utf-8",
        )
    shutil.copy2(uv, commands / Path(uv).name)
    empty_root = tmp_path / "unprepared-python"
    empty_root.mkdir()
    env.update(
        PATH=str(commands), AMBIENT_MARKER=str(marker), UV_OFFLINE="1",
        UV_PYTHON_INSTALL_DIR=str(empty_root) if scenario == "unprepared" else managed_root,
    )
    runner = tmp_path / "reader.ps1"
    runner.write_text(
        "param($Payload,$Root,$Function,$Scenario)\n"
        "Set-StrictMode -Version 2.0\n$ErrorActionPreference = 'Stop'\n"
        ". (Join-Path $Payload 'scripts/versioned_runtime_native.ps1')\n"
        "$errors = $null; $tokens = $null\n"
        "$ast = [Management.Automation.Language.Parser]::ParseFile("
        "(Join-Path $Payload 'scripts/install.ps1'),[ref]$tokens,[ref]$errors)\n"
        "if ($errors.Count) { throw 'Installer parse failed' }\n"
        "foreach ($name in @('Get-BootstrapPython','Resolve-PythonCommand')) {\n"
        "  $definition = $ast.Find({param($node) $node -is "
        "[Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)\n"
        "  . ([scriptblock]::Create($definition.Extent.Text))\n}\n"
        "$InstallDir = $Root; $Action = 'provision'\n"
        "$WheelProfile = ''; $WheelPython = ''; $script:RuntimeProfilePython = ''\n"
        "if ($Scenario -eq 'wheel') { $WheelProfile = 'base'; $WheelPython = 'explicit-wheel' }\n"
        "if ($Scenario -eq 'profile') { $script:RuntimeProfilePython = 'explicit-profile' }\n"
        "$result = & $Function\n"
        "if (-not $result) { throw 'Prepared managed bootstrap Python is unavailable' }\n"
        "ConvertTo-Json -Compress -InputObject ([string]$result)\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(runner), str(payload), str(runtime), function, scenario],
        env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert not marker.exists(), result.stdout + result.stderr
    if scenario == "unprepared":
        assert result.returncode != 0, result.stdout + result.stderr
        assert "Prepared managed bootstrap Python is unavailable" in result.stderr
        assert not result.stdout.strip()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        expected = (
            "explicit-" + scenario if scenario in {"wheel", "profile"}
            else str(Path(selected.stdout.strip()).resolve())
        )
        assert json.loads(result.stdout) == expected
    assert not runtime.exists()
    assert not list(empty_root.iterdir())


def _function(script: Path, name: str) -> str:
    text = script.read_text(encoding="utf-8")
    start = text.index(name + "() {")
    return text[start:text.index("\n}", start) + 2]


def _environment(root: Path, scenario: str) -> tuple[dict[str, str], Path, Path]:
    home, commands = root / "home", root / "commands"
    home.mkdir()
    commands.mkdir()
    marker, trace = root / "ambient-ran", root / "uv-calls"
    for name in ("python", "python3", "python3.11", "python3.12", "python3.13", "pip", "pip3"):
        command = commands / name
        command.write_text(
            "#!/bin/sh\nprintf invoked > " + shlex.quote(str(marker))
            + "\nprintf 'https://wrong.invalid/simple\\n'\n",
            encoding="utf-8",
        )
        command.chmod(0o700)
    if scenario != "missing":
        uv = commands / "uv"
        uv.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" >> " + shlex.quote(str(trace)) + "\n"
            "if IFS= read -r ignored; then exit 95; fi\n"
            'case "$1" in\n'
            "  --version) printf 'uv 0.13.7\\n'; exit 0 ;;\n"
            '  python) [ "$2" = find ] || exit 91 ;;\n'
            "  *) exit 92 ;;\nesac\n"
            + ("exit 2\n" if scenario == "unprepared" else
               "printf '%s\\n' " + shlex.quote(sys.executable) + "\n"
               + ("exit 9\n" if scenario == "failed-lookup" else "")),
            encoding="utf-8",
        )
        uv.chmod(0o700)
    for name in ("dirname", "wc", "cat", "grep", "sed", "head", "tr"):
        executable = shutil.which(name)
        assert executable, f"{name} unavailable"
        (commands / name).symlink_to(executable)
    config = home / "uv.toml"
    config.write_text("", encoding="utf-8")
    return {
        "HOME": str(home), "PATH": str(commands), "UV_CONFIG_FILE": str(config),
        "UV_NO_SYSTEM_CONFIG": "1",
    }, trace, marker


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer reader")
@pytest.mark.parametrize("function", ["_bootstrap_python", "_find_python"])
@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("scenario", ["managed", "unprepared", "failed-lookup", "missing"])
def test_setup_bootstrap_never_discovers_ambient_python(
    tmp_path: Path, function: str, profile: bool, scenario: str,
) -> None:
    bash = shutil.which("bash")
    assert bash
    env, trace, marker = _environment(tmp_path, scenario)
    installer = PLUGIN / "scripts" / "install.sh"
    script = (
        "set -uo pipefail\nSCRIPT_DIR=" + shlex.quote(str(installer.parent))
        + "\nINSTALL_DIR=" + shlex.quote(str(tmp_path / "runtime"))
        + "\nPROFILE=" + ("ssh" if profile else "''") + "\n"
        + _function(installer, "_bootstrap_python") + "\n"
        + _function(installer, "_find_python") + "\n" + function + "\n"
    )
    result = subprocess.run(
        [bash, "-c", script], env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert not marker.exists(), result.stdout + result.stderr
    if scenario == "managed":
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == sys.executable
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        assert not result.stdout
        assert "prepared" in result.stderr
    if scenario == "missing":
        assert not trace.exists()
    else:
        assert trace.read_text().splitlines() == [
            "--version", "python", "find", "--no-project", "--system",
            "--managed-python", "--no-python-downloads", "cpython@3.11.18",
        ]
    assert not (tmp_path / "runtime").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX index bridge")
@pytest.mark.parametrize("composed", [False, True])
@pytest.mark.parametrize("setting", [
    "file", "environment", "uv-override", "malformed", "uv-project", "uv-isolated",
    "relative-project", "relative-config", "active-venv", "active-conda",
])
def test_index_bridge_reads_canonical_policy_without_ambient_pip(
    tmp_path: Path, composed: bool, setting: str,
) -> None:
    bash = shutil.which("bash")
    assert bash
    env, trace, marker = _environment(tmp_path, "managed")
    payload = _stage_payload(tmp_path / "composed") if composed else PLUGIN
    engine = payload / "scripts" / "installer-engine.sh" if composed else REPO / "libs/installer-engine/installer-engine.sh"
    configuration = tmp_path / "pip.conf"
    configuration.write_text(
        "[invalid" if setting == "malformed" else "[global]\nindex-url=https://file.invalid/simple\n",
        encoding="utf-8",
    )
    env["PIP_CONFIG_FILE"] = str(configuration)
    expected = "https://file.invalid/simple"
    if setting == "environment":
        expected = env["PIP_INDEX_URL"] = "https://environment.invalid/simple"
    elif setting == "uv-override":
        expected = env["UV_DEFAULT_INDEX"] = "https://uv.invalid/simple"
    elif setting in {"uv-project", "uv-isolated", "relative-project", "relative-config"}:
        env.pop("UV_CONFIG_FILE")
        env["PIP_INDEX_URL"] = "https://environment.invalid/simple"
        if setting == "uv-project":
            selected = tmp_path / "selected project"
            selected.mkdir()
            (selected / "pyproject.toml").write_text(
                '[[tool.uv.index]]\nurl="https://project.invalid/simple"\ndefault=true\n',
                encoding="utf-8",
            )
            env["UV_PROJECT"] = str(selected)
            expected = ""
        elif setting == "uv-isolated":
            (Path(env["HOME"]) / "pyproject.toml").write_text(
                '[[tool.uv.index]]\nurl="https://cwd.invalid/simple"\ndefault=true\n',
                encoding="utf-8",
            )
            env["UV_ISOLATED"] = "1"
            expected = env["PIP_INDEX_URL"]
        else:
            selected = Path(env["HOME"]) / "selected working directory"
            selected.mkdir()
            env["UV_WORKING_DIR"] = selected.name
            if setting == "relative-project":
                project = selected / "project"
                project.mkdir()
                (project / "uv.toml").write_text(
                    '[[index]]\nurl="https://project.invalid/simple"\ndefault=true\n',
                    encoding="utf-8",
                )
                env["UV_PROJECT"] = "project"
            else:
                (selected / "selected.toml").write_text(
                    '[[index]]\nurl="https://config.invalid/simple"\ndefault=true\n',
                    encoding="utf-8",
                )
                env["UV_CONFIG_FILE"] = "selected.toml"
            expected = ""
    elif setting in {"active-venv", "active-conda"}:
        active = tmp_path / "active environment"
        active.mkdir()
        if setting == "active-venv":
            (active / "pyvenv.cfg").write_text("home=/not-executed\n", encoding="utf-8")
            env["VIRTUAL_ENV"] = str(active)
        else:
            (active / "conda-meta").mkdir()
            env["CONDA_PREFIX"] = str(active)
        expected = "https://site.invalid/simple"
        (active / "pip.conf").write_text("[global]\nindex-url=" + expected + "\n", encoding="utf-8")
        configuration.write_text("[global]\ntimeout=5\n", encoding="utf-8")
    installer = payload / "scripts" / "install.sh"
    script = (
        "set -uo pipefail\nSCRIPT_DIR=" + shlex.quote(str(installer.parent))
        + "\nINSTALL_DIR=" + shlex.quote(str(tmp_path / "runtime"))
        + "\nPROFILE=''\n_fail() { printf '%s\\n' \"$*\" >&2; }\n_step() { :; }\n"
        + ". " + shlex.quote(str(engine)) + "\n"
        + _function(installer, "_bootstrap_python") + "\n"
        + _function(installer, "_ensure_uv_index") + "\n"
        + "_ensure_uv_index " + shlex.quote(str(Path(env["PATH"]) / "uv"))
        + '\ncode=$?\nprintf "%s\\n" "${UV_DEFAULT_INDEX-}"\nexit "$code"\n'
    )
    result = subprocess.run(
        [bash, "-c", script], env=env, cwd=Path(env["HOME"]), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert not marker.exists(), result.stdout + result.stderr
    if setting == "malformed":
        assert result.returncode != 0 and not result.stdout.strip(), result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == expected
    if setting == "uv-override":
        assert not trace.exists()
    else:
        calls = trace.read_text().splitlines()
        assert calls.count("find") == 1
        assert "install" not in calls
    assert not (tmp_path / "runtime").exists()

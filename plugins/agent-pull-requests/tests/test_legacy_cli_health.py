"""The native gate must reject a broken installed CLI before completion."""

import os
from pathlib import Path
import shlex
import subprocess
import venv

import pytest
from agent_procutil import no_window_kwargs


@pytest.mark.parametrize("missing_python", [False, True])
def test_legacy_cli_health_rejects_importable_package_with_broken_entrypoint(
    tmp_path, missing_python,
):
    slot = tmp_path / "candidate"
    venv.EnvBuilder(with_pip=False).create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = subprocess.run(
        [str(python), "-I", "-B", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True, text=True, check=True, timeout=15, **no_window_kwargs(),
    )
    package = Path(site.stdout.strip()) / "agent_pull_requests"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        'raise RuntimeError("fixture installed CLI is broken")\n', encoding="utf-8",
    )
    imported = subprocess.run(
        [str(python), "-I", "-B", "-c", "import agent_pull_requests"],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert imported.returncode == 0, imported.stderr
    if missing_python:
        python.rename(python.with_suffix(".unavailable"))
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    marker = tmp_path / "completed"
    if os.name == "nt":
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        body = (scripts / "install.ps1").read_text().split(
            "function Invoke-VersionedActivate {", 1,
        )[1].split("# === end install-contract:v3 versioned-venv ===", 1)[0]
        helper = scripts / "versioned_runtime_native.ps1"
        program = (
            f". {quote(helper)}\n$ErrorActionPreference = 'Stop'\n"
            f"$VenvPython={quote(python)}; $LinkPython=$VenvPython\n"
            f"$InstallDir={quote(tmp_path)}; $SrcVersion='fixture'; $VersionedRuntime=$true\n"
            "function Test-SourceRuntimeUnchanged {}\n"
            "function Write-Fail { param($Message); [Console]::Error.WriteLine($Message) }\n"
            f"function Invoke-VersionedMarkComplete {{ [IO.File]::WriteAllText({quote(marker)}, 'bad'); return $true }}\n"
            "function Invoke-VersionedActivate {" + body +
            "\nif (Invoke-VersionedActivate) { exit 99 }; exit 0\n"
        )
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        arguments = [str(shell), "-NoProfile", "-NonInteractive", "-File"]
        entry = tmp_path / "probe.ps1"
    else:
        body = (scripts / "install.sh").read_text().split(
            "_versioned_activate() {", 1,
        )[1].split("# === end install-contract:v3 versioned-venv ===", 1)[0]
        program = (
            "set -eu\n"
            f"VENV_DIR={shlex.quote(str(slot))}; LINK_DIR=$VENV_DIR; VENV_PYTHON={shlex.quote(str(python))}\n"
            f"INSTALL_DIR={shlex.quote(str(tmp_path))}; SCRIPT_DIR=$INSTALL_DIR; SRC_VERSION=fixture\n"
            "_source_runtime_unchanged() { :; }\n"
            "_fail() { printf '%s\\n' \"$1\" >&2; }\n"
            f"_versioned_mark_complete() {{ printf bad >{shlex.quote(str(marker))}; }}\n"
            "_versioned_activate() {" + body +
            "\nif _versioned_activate; then exit 99; fi\nexit 0\n"
        )
        arguments = ["bash"]
        entry = tmp_path / "probe.sh"
    entry.write_text(program, encoding="utf-8")
    result = subprocess.run(
        [*arguments, str(entry)], capture_output=True, text=True,
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "not activating" in result.stderr if missing_python
        else "fixture installed CLI is broken" in result.stderr
    )
    assert not marker.exists(), "an importable package must not complete a broken CLI"

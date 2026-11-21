"""Prepared uv entry retains the existing shell-to-worker lifetime protocol."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
HOSTS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell")))))
pytestmark = pytest.mark.installation_context_smoke
SELECT = r"""
$env:PATH = $env:FIXTURE_PATH
. $env:FIXTURE_NATIVE
. $env:FIXTURE_PROVIDER
$invoke = {
    param($FilePath, $ArgList, $Environment)
    $errors = ''
    $output = Invoke-Hidden -FilePath $FilePath -ArgList $ArgList `
        -Environment $Environment -ErrorOutput ([ref]$errors) -InputText '' `
        -TimeoutMilliseconds 5000 -CleanupMilliseconds 500
    [pscustomobject]@{ExitCode=$LASTEXITCODE; Stdout=[string]$output; Stderr=$errors}
}
$selected = Resolve-UvBootstrapPython -RequestFile $env:FIXTURE_REQUEST -InvokeCaptured $invoke -IsolateSelection
if ($selected.Status -cne 'selected') {
    [Console]::Error.WriteLine('Prepared managed bootstrap Python is unavailable: ' + $selected.Reason)
    exit 1
}
"""


@pytest.fixture
def prepared_uv() -> tuple[str, str, str]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("This acquisition-free control requires an already available uv.")
    result = subprocess.run(
        [uv, "python", "find", "--no-project", "--system",
         "--managed-python", "--no-python-downloads", ">=3.10"],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0,
    )
    if result.returncode:
        pytest.skip("This control requires an already prepared managed test interpreter: " + result.stderr)
    python = result.stdout.rstrip("\r\n")
    version = subprocess.run(
        [python, "-I", "-S", "-B", "-c",
         "import sys; print('cpython@' + '.'.join(map(str, sys.version_info[:3])))"],
        capture_output=True, text=True, timeout=15, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0,
    )
    return uv, version.stdout.strip(), python


def _run(
    host: str, script: Path, environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8", timeout=45,
        env={
            **os.environ, "COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON": "",
            "FIXTURE_NATIVE": str(REPO / "libs/versioned-runtime/versioned_runtime_native.ps1"),
            "FIXTURE_PROVIDER": str(REPO / "libs/installer-engine/bootstrap-uv.ps1"),
            **environment,
        },
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("host", HOSTS or [None])
def test_real_uv_keeps_literal_packet_exit_and_original_caller_environment(
    tmp_path: Path, host: str | None, prepared_uv: tuple[str, str, str],
) -> None:
    if host is None:
        pytest.skip("PowerShell is unavailable")
    uv, request, python = prepared_uv
    request_file = tmp_path / "bootstrap-python-version.txt"
    request_file.write_text(request + "\n", encoding="ascii")
    tools = tmp_path / "uv-only"
    tools.mkdir()
    shutil.copyfile(uv, tools / Path(uv).name)
    (tools / Path(uv).name).chmod(0o755)
    (tools / ("python.exe" if os.name == "nt" else "python")).write_bytes(b"not Python")
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import json,os,sys\n"
        "def main(request):\n"
        "    sys.path.insert(0,os.environ['FIXTURE_ARCHIVE'])\n"
        "    from agent_procutil._shell_caller_owned import serve\n"
        "    def callback():\n"
        "        print(json.dumps({'literal':request['literal'],'python':sys.executable,\n"
        "                          'foreign':os.environ.get('VIRTUAL_ENV')}))\n"
        "        return 37\n"
        "    return serve(callback, **request['lifetime'])\n",
        encoding="utf-8",
    )
    script = tmp_path / "managed.ps1"
    script.write_text(
        r"""
$ErrorActionPreference = 'Stop'
function Get-Command {
    param($Name, $CommandType, $ErrorAction)
    if ($Name -cne 'uv' -or $CommandType -cne 'Application') {
        throw 'Ambient Python discovery must not run.'
    }
    return Microsoft.PowerShell.Core\Get-Command -Name $Name -CommandType $CommandType -ErrorAction $ErrorAction
}
""" + SELECT + r"""
$literal = "space 'quoted' `"double`" \slash &|<>()`$?; " + [char]0x3bb + "`n" + ('x' * 120000)
$result = @(& $env:FIXTURE_TRANSPORT -Worker $env:FIXTURE_WORKER `
    -Request @{literal=$literal} -Python $selected.Python)
$code = $LASTEXITCODE
@{
    output = ($result -join "`n") | ConvertFrom-Json
    code = $code
    literal = $literal
    request = $selected.Requirement
    noManaged = $env:UV_NO_MANAGED_PYTHON
    foreign = $env:VIRTUAL_ENV
} | ConvertTo-Json -Compress
""",
        encoding="utf-8-sig",
    )
    result = _run(host, script, {
        "FIXTURE_PATH": str(tools), "FIXTURE_WORKER": str(worker),
        "FIXTURE_TRANSPORT": str(ROOT / "shell-caller-owned.ps1"),
        "FIXTURE_ARCHIVE": str(ROOT / "daemon_instance_runtime.pyz"),
        "FIXTURE_REQUEST": str(request_file),
        "UV_PYTHON": "must-not-be-selected", "UV_NO_MANAGED_PYTHON": "1",
        "UV_PYTHON_PREFERENCE": "only-system", "VIRTUAL_ENV": str(tmp_path / "foreign"),
    })
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr
    value: dict[str, Any] = json.loads(result.stdout)
    assert value["code"] == 37
    assert value["output"]["literal"] == value["literal"]
    assert Path(value["output"]["python"]).samefile(python)
    assert value["output"]["foreign"] == str(tmp_path / "foreign")
    assert value["request"] == request
    assert value["noManaged"] == "1"
    assert value["foreign"] == str(tmp_path / "foreign")


@pytest.mark.parametrize("host", HOSTS or [None])
def test_unprepared_request_does_not_acquire_or_reach_application(
    tmp_path: Path, host: str | None, prepared_uv: tuple[str, str, str],
) -> None:
    if host is None:
        pytest.skip("PowerShell is unavailable")
    uv, request, _python = prepared_uv
    request_file = tmp_path / "bootstrap-python-version.txt"
    request_file.write_text(request + "\n", encoding="ascii")
    tools = tmp_path / "uv-only"
    tools.mkdir()
    shutil.copyfile(uv, tools / Path(uv).name)
    (tools / Path(uv).name).chmod(0o755)
    worker = tmp_path / "must-not-run.py"
    worker.write_text("raise AssertionError('unprepared application ran')\n", encoding="utf-8")
    script = tmp_path / "unprepared.ps1"
    script.write_text(
        SELECT +
        "& $env:FIXTURE_TRANSPORT -Worker $env:FIXTURE_WORKER -Request @{} -Python $selected.Python\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    result = _run(host, script, {
        "FIXTURE_PATH": str(tools), "FIXTURE_WORKER": str(worker),
        "FIXTURE_TRANSPORT": str(ROOT / "shell-caller-owned.ps1"),
        "FIXTURE_REQUEST": str(request_file),
        "UV_PYTHON_INSTALL_DIR": str(tmp_path / "unprepared"),
        "UV_CACHE_DIR": str(tmp_path / "cache"),
        "UV_PYTHON_DOWNLOADS": "automatic",
    })
    assert result.returncode == 1
    assert not result.stdout
    assert "managed-python-not-prepared" in result.stderr
    assert "unprepared application ran" not in result.stderr
    assert not (tmp_path / "unprepared").exists()

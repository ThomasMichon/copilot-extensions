"""Execute the installer publication callers against the standalone helper."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_installer_slot_clean_posix import _BASH, _bash_path, _function_body


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SHELLS = [
    ("powershell", shutil.which("powershell")),
    ("pwsh", shutil.which("pwsh")),
    ("bash", _BASH),
]
DIGEST = "a" * 64


def _ps_function(text: str, name: str) -> str:
    match = re.search(
        rf"^function\s+{re.escape(name)}\s*\{{.*?^\}}[ \t]*\r?$",
        text, re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"top-level installer function unavailable: {name}"
    return match.group()


def _ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run(shell: str, executable: str, script: str, tmp_path: Path):
    path = tmp_path / ("probe.sh" if shell == "bash" else "probe.ps1")
    path.write_text(script, encoding="utf-8")
    args = (
        [executable, _bash_path(path)]
        if shell == "bash"
        else [executable, "-NoProfile", "-File", str(path)]
    )
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8",
        timeout=20 if shell == "bash" else 60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("shell,executable", SHELLS)
@pytest.mark.parametrize("case", [
    "fresh", "reuse", "conflict", "malformed", "no-python", "no-hash",
    "no-helper", "storage-error", "python-directory", "python-missing",
])
def test_completion_publication_caller_preserves_native_result(
    tmp_path: Path, shell: str, executable: str | None, case: str,
):
    if not executable:
        pytest.skip(f"{shell} unavailable")
    if case != "no-helper":
        shutil.copyfile(SCRIPTS / "versioned_runtime.py", tmp_path / "versioned_runtime.py")
    root = tmp_path / "runtime"
    slot = root / "versions" / "1.2.3"
    slot.mkdir(parents=True)
    marker = slot / ".install-complete.json"
    if case in {"reuse", "conflict"}:
        marker.write_text(json.dumps({
            "version": "1.2.3", "completed_at": "2026-01-01T00:00:00Z",
            "pid": 123, "payload_hash": DIGEST if case == "reuse" else "b" * 64,
        }), encoding="utf-8")
    elif case == "malformed":
        marker.write_text("{invalid", encoding="utf-8")
    elif case == "storage-error":
        (root / ".publication-leases").write_text("not a directory", encoding="utf-8")
    before = (
        marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns
    ) if marker.exists() else None
    digest = "" if case == "no-hash" else DIGEST
    python = "" if case == "no-python" else sys.executable
    if case in {"python-directory", "python-missing"}:
        python = str(tmp_path / "bad-python")
        if case == "python-directory":
            Path(python).mkdir()
    if shell == "bash":
        text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
        function = _function_body(text, "_versioned_mark_complete") + "\n}\n"
        script = f"""
set -euo pipefail
SCRIPT_DIR={shlex.quote(_bash_path(tmp_path))}
INSTALL_DIR={shlex.quote(_bash_path(root))}
LINK_DIR="$INSTALL_DIR/.venv"
SRC_VERSION=1.2.3
VERSIONED_RUNTIME=1
err() {{ echo "$*" >&2; }}
_bootstrap_python() {{ printf '%s' {shlex.quote(_bash_path(Path(python)) if python else '')}; }}
_payload_hash() {{ printf '%s' {shlex.quote(digest)}; }}
{function}
if _versioned_mark_complete; then echo RESULT=True; else echo RESULT=False; fi
"""
    else:
        text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
        function = _ps_function(text, "Invoke-VersionedMarkComplete")
        capture = _ps_function(text, "Invoke-NativeCapture")
        script = f"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$InstallDir = {_ps_quote(root)}
$LinkDir = Join-Path $InstallDir '.venv'
$SrcVersion = '1.2.3'
$VersionedRuntime = $true
function Write-ServiceErr($message) {{ [Console]::Error.WriteLine($message) }}
function Get-BootstrapPython {{ return {_ps_quote(python)} }}
function Get-PayloadHash {{ return {_ps_quote(digest)} }}
{capture}
{function}
[Console]::Out.WriteLine("RESULT=$(Invoke-VersionedMarkComplete)")
"""
    result = _run(shell, executable, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = case in {"fresh", "reuse"}
    assert f"RESULT={expected}" in result.stdout
    assert all(
        line.startswith(("  ...    ", "RESULT="))
        for line in result.stdout.splitlines()
    ), result.stdout
    if before is not None:
        assert (marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns) == before
    else:
        assert marker.exists() == expected
    if not expected:
        assert "refusing" in result.stderr.lower(), result.stdout + result.stderr


@pytest.mark.skipif(_BASH is None, reason="bash unavailable")
def test_bare_posix_publication_reports_failure_before_errexit(tmp_path: Path):
    function = _function_body(
        (SCRIPTS / "install.sh").read_text(encoding="utf-8"),
        "_versioned_mark_complete",
    ) + "\n}\n"
    script = f"""
set -euo pipefail
SCRIPT_DIR={shlex.quote(_bash_path(tmp_path))}
INSTALL_DIR={shlex.quote(_bash_path(tmp_path / 'runtime'))}
LINK_DIR="$INSTALL_DIR/.venv"
SRC_VERSION=1.2.3
VERSIONED_RUNTIME=1
err() {{ echo "$*" >&2; }}
_bootstrap_python() {{ printf '%s' {shlex.quote(_bash_path(Path(sys.executable)))}; }}
_payload_hash() {{ printf '%s' {shlex.quote(DIGEST)}; }}
{function}
_versioned_mark_complete
echo UNEXPECTED
"""
    result = _run("bash", _BASH, script, tmp_path)
    assert result.returncode == 2
    assert "Completion publication failed" in result.stderr
    assert "UNEXPECTED" not in result.stdout


@pytest.mark.skipif(_BASH is None, reason="bash unavailable")
@pytest.mark.parametrize("formatter_status,consume_output", [(9, True), (127, False)])
def test_posix_publication_refuses_formatter_failure(
    tmp_path: Path, formatter_status: int, consume_output: bool,
):
    shutil.copyfile(SCRIPTS / "versioned_runtime.py", tmp_path / "versioned_runtime.py")
    function = _function_body(
        (SCRIPTS / "install.sh").read_text(encoding="utf-8"),
        "_versioned_mark_complete",
    ) + "\n}\n"
    root = tmp_path / "runtime"
    script = f"""
set -euo pipefail
SCRIPT_DIR={shlex.quote(_bash_path(tmp_path))}
INSTALL_DIR={shlex.quote(_bash_path(root))}
LINK_DIR="$INSTALL_DIR/.venv"
SRC_VERSION=1.2.3
VERSIONED_RUNTIME=1
err() {{ echo "$*" >&2; }}
sed() {{ {'command sed "$@";' if consume_output else ''} return {formatter_status}; }}
_bootstrap_python() {{ printf '%s' {shlex.quote(_bash_path(Path(sys.executable)))}; }}
_payload_hash() {{ printf '%s' {shlex.quote(DIGEST)}; }}
{function}
if _versioned_mark_complete; then echo UNEXPECTED; else echo RESULT=$?; fi
"""
    result = _run("bash", _BASH, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"RESULT={formatter_status}" in result.stdout
    assert f"Completion output formatter failed (exit {formatter_status};" in result.stderr
    assert "Completion publication failed" in result.stderr
    if consume_output:
        assert (root / "versions" / "1.2.3" / ".install-complete.json").is_file()


@pytest.mark.guard
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_ps_extraction_preserves_nested_body_with_either_line_ending(newline):
    text = newline.join([
        "function Probe {",
        "    if ($true) { '}' }",
        "}   ",
        "function Other { }",
    ])
    body = _ps_function(text, "Probe")
    assert "if ($true) { '}' }" in body
    assert "Other" not in body


@pytest.mark.parametrize("shell,executable", SHELLS)
def test_activation_stops_at_failed_publication(
    tmp_path: Path, shell: str, executable: str | None,
):
    if not executable:
        pytest.skip(f"{shell} unavailable")
    if shell == "bash":
        text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
        body = _function_body(text, "_versioned_activate_inner")
        body = body[body.index("    _versioned_mark_complete"):]
        script = f"""
set -euo pipefail
_versioned_mark_complete() {{ return 7; }}
probe() {{
{body}
}}
if probe; then echo UNEXPECTED; else echo RESULT=$?; fi
"""
        expected = "RESULT=7"
    else:
        text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
        body = _ps_function(text, "Invoke-VersionedActivateInner")
        body = body[body.index("    if (-not (Invoke-VersionedMarkComplete))"):]
        script = f"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-VersionedMarkComplete {{ return $false }}
function Probe {{
{body}
[Console]::Out.WriteLine("RESULT=$(Probe)")
"""
        expected = "RESULT=False"
    result = _run(shell, executable, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout


@pytest.mark.parametrize("shell,executable", SHELLS)
@pytest.mark.parametrize("interpreter_shape", ["missing", "directory", "wildcard"])
def test_activation_refuses_missing_interpreter(
    tmp_path: Path, shell: str, executable: str | None, interpreter_shape: str,
):
    if not executable:
        pytest.skip(f"{shell} unavailable")
    missing = tmp_path / "missing-python"
    if interpreter_shape == "directory":
        missing.mkdir()
    elif interpreter_shape == "wildcard":
        missing = tmp_path / "python[12]"
        (tmp_path / "python1").write_text("not the requested interpreter", encoding="utf-8")
    if shell == "bash":
        text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
        function = _function_body(text, "_versioned_activate_inner") + "\n}\n"
        script = f"""
set -euo pipefail
VERSIONED_RUNTIME=1
SCRIPT_DIR={shlex.quote(_bash_path(tmp_path))}
VENV_PYTHON={shlex.quote(_bash_path(missing))}
SRC_VERSION=1.2.3
err() {{ echo "$*" >&2; }}
{function}
if _versioned_activate_inner; then echo UNEXPECTED; else echo RESULT=$?; fi
"""
        expected = "RESULT=1"
    else:
        text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
        function = _ps_function(text, "Invoke-VersionedActivateInner")
        script = f"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$VersionedRuntime = $true
$VenvPython = {_ps_quote(missing)}
$LinkPython = $VenvPython
$SrcVersion = '1.2.3'
function Write-ServiceErr($message) {{ [Console]::Error.WriteLine($message) }}
{function}
[Console]::Out.WriteLine("RESULT=$(Invoke-VersionedActivateInner)")
"""
        expected = "RESULT=False"
    result = _run(shell, executable, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout
    assert "Runtime interpreter missing" in result.stderr


@pytest.mark.guard
def test_missing_interpreter_cannot_pass_activation_gate():
    ps = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    sh = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    assert re.search(
        r"if \(-not \(Test-Path -LiteralPath \$py -PathType Leaf\)\) \{\s*"
        r'Write-ServiceErr "Runtime interpreter missing.*?\s*return \$false',
        _ps_function(ps, "Invoke-VersionedActivateInner"),
    )
    assert re.search(
        r'if \[\[ ! -f "\$py" \|\| ! -x "\$py" \]\]; then\s*'
        r'err "Runtime interpreter missing.*?\s*return 1',
        _function_body(sh, "_versioned_activate_inner"),
    )

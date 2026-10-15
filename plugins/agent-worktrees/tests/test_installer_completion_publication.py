"""Execute installer publication callers with attached construction ownership."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from functools import lru_cache
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


def _copy_helper(tmp_path: Path, shell: str = "bash", *, missing_core: bool = False) -> None:
    for name in (
        "versioned_runtime.py",
        "versioned_runtime_leases.py",
        "versioned_runtime_construction.py",
    ):
        if missing_core and name == "versioned_runtime.py":
            continue
        shutil.copyfile(SCRIPTS / name, tmp_path / name)
    if shell == "bash" and os.name == "nt" and not missing_core:
        # Git Bash uses native Windows Python, not a POSIX interpreter. Model
        # POSIX pipe framing without changing the canonical JSON/lease protocol.
        path = tmp_path / "versioned_runtime.py"
        source = path.read_text(encoding="utf-8")
        entrypoint = 'if __name__ == "__main__":\n'
        assert source.count(entrypoint) == 1
        path.write_text(
            source.replace(entrypoint, entrypoint + '    sys.stdout.reconfigure(newline="\\n")\n'),
            encoding="utf-8",
        )


@lru_cache(maxsize=2)
def _construction_adapter(shell: str) -> str:
    spec = importlib.util.spec_from_file_location(
        "installer_completion_protocol", SCRIPTS / "versioned_runtime_construction.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    adapter = module.construction_shell_helpers("sh" if shell == "bash" else "ps1")
    if shell == "bash" and os.name == "nt":
        # Native Windows Python cannot read an MSYS FIFO handle. Convert only
        # the fixture transport to an anonymous pipe; keep the protocol real.
        assert adapter.count('        exec "$@"\n') == 1
        adapter = adapter.replace('        exec "$@"\n', '''
        exec 7< <(cat)
        relay=$!
        rc=0
        "$@" <&7 || rc=$?
        kill "$relay" 2>/dev/null || true
        wait "$relay" 2>/dev/null || true
        exit "$rc"
''')
    return adapter


def _construction_script(
    shell: str, tmp_path: Path, root: Path, *, python: str = sys.executable,
) -> str:
    if shell == "bash":
        text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
        functions = "\n".join(
            _function_body(text, name) + "\n}\n"
            for name in ("_versioned_slot_clean", "_versioned_mark_complete")
        )
        return f"""
set -euo pipefail
SCRIPT_DIR={shlex.quote(_bash_path(tmp_path))}
INSTALL_DIR={shlex.quote(_bash_path(root))}
LINK_DIR="$INSTALL_DIR/.venv"
SRC_VERSION=1.2.3
VERSIONED_RUNTIME=1
FINGERPRINT={shlex.quote(DIGEST)}
err() {{ echo "$*" >&2; }}
_bootstrap_python() {{ printf '%s' {shlex.quote(_bash_path(Path(python)) if python else '')}; }}
_payload_hash() {{ printf '%s' "$FINGERPRINT"; }}
{_construction_adapter(shell)}
{functions}
"""
    text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    functions = "\n".join(
        _ps_function(text, name)
        for name in ("Invoke-VersionedSlotClean", "Invoke-VersionedMarkComplete")
    )
    return f"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$InstallDir = {_ps_quote(root)}
$LinkDir = Join-Path $InstallDir '.venv'
$SrcVersion = '1.2.3'
$VersionedRuntime = $true
$script:Fingerprint = {_ps_quote(DIGEST)}
function Write-ServiceErr($message) {{ [Console]::Error.WriteLine($message) }}
function Get-BootstrapPython {{ return {_ps_quote(python)} }}
function Get-PayloadHash {{ return $script:Fingerprint }}
{_construction_adapter(shell)}
{functions}
"""


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
    _copy_helper(tmp_path, shell, missing_core=case == "no-helper")
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
    python = "" if case == "no-python" else sys.executable
    if case in {"python-directory", "python-missing"}:
        python = str(tmp_path / "bad-python")
        if case == "python-directory":
            Path(python).mkdir()
    script = _construction_script(shell, tmp_path, root, python=python)
    if shell == "bash":
        script += f"""
if ! _versioned_slot_clean; then
    echo 'Construction admission refused' >&2
    echo RESULT=False
    exit 1
fi
{'FINGERPRINT=""' if case == 'no-hash' else ''}
if _versioned_mark_complete; then echo RESULT=True; else echo RESULT=False; exit 1; fi
_runtime_construction_finish
"""
    else:
        script += f"""
$code = 0
try {{
    if (-not (Invoke-VersionedSlotClean)) {{ throw 'Construction was not admitted' }}
    {'$script:Fingerprint = ""' if case == 'no-hash' else ''}
    [Console]::Out.WriteLine("RESULT=$(Invoke-VersionedMarkComplete)")
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    [Console]::Out.WriteLine('RESULT=False')
    $code = 1
}} finally {{
    if (Get-Command Stop-RuntimeConstruction -ErrorAction SilentlyContinue) {{
        try {{ Stop-RuntimeConstruction }} catch {{
            [Console]::Error.WriteLine($_.Exception.Message)
            $code = 1
        }}
    }}
}}
exit $code
"""
    result = _run(shell, executable, script, tmp_path)
    expected = case in {"fresh", "reuse"}
    assert result.returncode == (0 if expected else 1), result.stdout + result.stderr
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
        assert result.stderr.strip(), result.stdout + result.stderr
    if case == "no-hash":
        assert "fingerprint unavailable" in result.stderr.lower()
    assert not (root / ".venv").exists()
    assert not (root / "current-version").exists()


@pytest.mark.skipif(_BASH is None, reason="bash unavailable")
def test_bare_posix_publication_reports_failure_before_errexit(tmp_path: Path):
    _copy_helper(tmp_path)
    root = tmp_path / "runtime"
    script = _construction_script("bash", tmp_path, root) + """
_versioned_slot_clean
FINGERPRINT=""
_versioned_mark_complete
echo UNEXPECTED
"""
    result = _run("bash", _BASH, script, tmp_path)
    assert result.returncode == 1
    assert "fingerprint unavailable -- refusing completion publication" in result.stderr
    assert "UNEXPECTED" not in result.stdout
    assert not (root / "versions" / "1.2.3").exists()


@pytest.mark.skipif(_BASH is None, reason="bash unavailable")
@pytest.mark.parametrize("reply_failure", ["invalid-json", "wrong-record", "read-9", "read-127"])
def test_posix_publication_refuses_completion_reply_failure(
    tmp_path: Path, reply_failure: str,
):
    """JSON replies replace human-output formatting; neither may license success."""
    _copy_helper(tmp_path)
    root = tmp_path / "runtime"
    failure = {
        "invalid-json": "reply='{invalid'",
        "wrong-record": "reply='{\"settled\": true}'",
        "read-9": "return 9",
        "read-127": "return 127",
    }[reply_failure]
    script = _construction_script("bash", tmp_path, root) + f"""
_versioned_slot_clean
read() {{
    builtin read "$@" || return $?
    if [[ "${{FIXTURE_REPLY_FAULT:-0}}" == 1 ]]; then
        FIXTURE_REPLY_FAULT=0
        {failure}
    fi
}}
FIXTURE_REPLY_FAULT=1
if _versioned_mark_complete; then echo UNEXPECTED; else echo RESULT=$?; fi
"""
    result = _run("bash", _BASH, script, tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "RESULT=1" in result.stdout
    assert "UNEXPECTED" not in result.stdout
    assert (root / "versions" / "1.2.3" / ".install-complete.json").is_file()
    assert not (root / ".venv").exists()
    assert not (root / "current-version").exists()


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
    _copy_helper(tmp_path, shell)
    root = tmp_path / "runtime"
    script = _construction_script(shell, tmp_path, root)
    if shell == "bash":
        text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
        body = _function_body(text, "_versioned_activate_inner")
        body = body[body.index("    _versioned_mark_complete"):]
        script += f"""
_versioned_slot_clean
FINGERPRINT=""
probe() {{
{body}
}}
if probe; then echo UNEXPECTED; else echo RESULT=$?; exit 1; fi
"""
        expected = "RESULT=1"
    else:
        text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
        body = _ps_function(text, "Invoke-VersionedActivateInner")
        body = body[body.index("    if (-not (Invoke-VersionedMarkComplete))"):]
        script += f"""
function Probe {{
{body}
$code = 0
try {{
    if (-not (Invoke-VersionedSlotClean)) {{ throw 'Construction was not admitted' }}
    $script:Fingerprint = ''
    [Console]::Out.WriteLine("RESULT=$(Probe)")
    [Console]::Out.WriteLine('UNEXPECTED')
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    [Console]::Out.WriteLine('RESULT=False')
    $code = 1
}} finally {{
    if (Get-Command Stop-RuntimeConstruction -ErrorAction SilentlyContinue) {{
        try {{ Stop-RuntimeConstruction }} catch {{ $code = 1 }}
    }}
}}
exit $code
"""
        expected = "RESULT=False"
    result = _run(shell, executable, script, tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert expected in result.stdout
    assert "fingerprint unavailable" in result.stderr.lower()
    assert "UNEXPECTED" not in result.stdout
    assert not (root / "versions" / "1.2.3").exists()
    assert not (root / ".venv").exists()
    assert not (root / "current-version").exists()


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
    for text, health, publication, activation in (
        (
            _ps_function(ps, "Invoke-VersionedActivateInner"),
            "if (-not $slotOk)", "if (-not (Invoke-VersionedMarkComplete))",
            "activate $SrcVersion --no-link",
        ),
        (
            _function_body(sh, "_versioned_activate_inner"),
            "if ! health_out=", "    _versioned_mark_complete || return $?",
            'activate "$SRC_VERSION" --no-link',
        ),
    ):
        assert text.index(health) < text.index(publication) < text.index(activation)
        assert "m._load_full_command_surface()" in text

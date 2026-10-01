"""Executable behavioral coverage for the installer's `Invoke-UvVenvWithRetry`
helper (install.ps1).

Deliberately NOT part of the `@pytest.mark.guard` lane: these tests spawn a
real PowerShell subprocess per case, which the guard lane -- run on every PR
via `tools/run-plugin-tests.py --guards` -- requires to stay cheap,
deterministic, and subprocess-free (see `.github/workflows/ci.yml` and
`TESTING.md`). The cheap source-level wiring assertion
(`test_deploy_venv_calls_uv_retry_helper`) stays in
`test_installer_powershell51.py`; this module runs with the full suite
instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"


def _run_uv_retry_script(pwsh: str, tmp_path: Path, uv_body: str) -> subprocess.CompletedProcess:
    """Extract Invoke-UvVenvWithRetry from the real installer via its AST and
    execute it with a scripted fake `uv`, capturing both its result and how
    many times `uv` was actually invoked -- real behavioral coverage of the
    retry/backoff/attempt-limit logic, not just a source-text assertion."""
    script = f"""
$tokens = $null
$errors = $null
$source = Get-Content -LiteralPath $env:INSTALLER -Raw
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$errors
)
$functionAst = $ast.Find({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Invoke-UvVenvWithRetry'
}}, $true)
if (-not $functionAst) {{ throw 'Missing installer function: Invoke-UvVenvWithRetry' }}
Invoke-Expression $functionAst.Extent.Text

function Write-ServiceWarn {{ param($msg) }}
function Invoke-NativeCapture {{ param($Script) & $Script }}
function Start-Sleep {{ param($Milliseconds) }}  # skip real backoff delay in tests

$script:callCount = 0
function uv {{
{uv_body}
}}

$result = Invoke-UvVenvWithRetry -VenvDir $env:FAKE_VENV_DIR
[Console]::Out.Write("$($result.ExitCode)|$script:callCount")
"""
    return subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "INSTALLER": str(INSTALLER), "FAKE_VENV_DIR": str(tmp_path)},
        timeout=30,
    )


def test_uv_venv_retry_succeeds_without_retrying_on_first_try():
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_uv_retry_script(
            pwsh,
            Path(tmp),
            """
            $script:callCount++
            [pscustomobject]@{ ExitCode = 0; Output = '' }
            """,
        )
    assert proc.returncode == 0, proc.stderr
    exit_code, call_count = proc.stdout.strip().split("|")
    assert exit_code == "0"
    assert call_count == "1"


def test_uv_venv_retry_recovers_after_transient_access_denied():
    """A transient Access Denied on the first attempt must be retried and
    succeed on the second -- the whole update must not abort on one hit."""
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_uv_retry_script(
            pwsh,
            Path(tmp),
            """
            $script:callCount++
            if ($script:callCount -eq 1) {
                [pscustomobject]@{ ExitCode = 1; Output = 'failed to persist temporary file: Access is denied. (os error 5)' }
            } else {
                [pscustomobject]@{ ExitCode = 0; Output = '' }
            }
            """,
        )
    assert proc.returncode == 0, proc.stderr
    exit_code, call_count = proc.stdout.strip().split("|")
    assert exit_code == "0"
    assert call_count == "2"


def test_uv_venv_retry_gives_up_after_three_attempts_on_persistent_transient_failure():
    """A persistently-transient failure must not retry forever -- exactly 3
    retry iterations (each iteration tries the version-constrained venv call,
    then the unconstrained fallback, so 6 total `uv` invocations here), then
    surface the last failure."""
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_uv_retry_script(
            pwsh,
            Path(tmp),
            """
            $script:callCount++
            [pscustomobject]@{ ExitCode = 1; Output = 'Access is denied. (os error 5)' }
            """,
        )
    assert proc.returncode == 0, proc.stderr
    exit_code, call_count = proc.stdout.strip().split("|")
    assert exit_code == "1"
    assert call_count == "6"


def test_uv_venv_retry_does_not_retry_non_transient_failure():
    """A non-transient failure (e.g. uv missing/misconfigured) must fail
    immediately after the first retry iteration (version-constrained call
    plus its unconstrained fallback -- 2 total `uv` invocations) -- never
    burn the rest of the retry budget on a genuine, persistent error."""
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_uv_retry_script(
            pwsh,
            Path(tmp),
            """
            $script:callCount++
            [pscustomobject]@{ ExitCode = 1; Output = 'error: no such command: venv' }
            """,
        )
    assert proc.returncode == 0, proc.stderr
    exit_code, call_count = proc.stdout.strip().split("|")
    assert exit_code == "1"
    assert call_count == "2"

"""Regression coverage for the transient SRE-module-mismatch retry wrapper
(#6785): a shared uv-managed Python interpreter can momentarily disagree with
its own compiled `_sre` extension when several installers hit it in quick
succession during a big `agent-worktrees update --force` sweep. The installer
must retry with backoff on that specific signature and otherwise behave
exactly like a plain `uv pip install` -- surfacing any other failure
immediately, and still failing if the SRE mismatch persists through every
retry."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = PLUGIN / "scripts" / "install.ps1"


def _extract_ps1_functions(*names: str) -> str:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    chunks = []
    for name in names:
        rest = install_ps1.split(f"function {name}", 1)[1]
        # Each of these functions is closed by a `}` at column 0 (the file's
        # top-level function-closing convention).
        body = rest.split("\n}\n", 1)[0]
        chunks.append(f"function {name}{body}\n}}")
    return "\n\n".join(chunks)


def _run_harness(
    tmp_path: Path, shell: str, uv_stub_body: str, extra_script: str, delays_file: Path
) -> subprocess.CompletedProcess:
    exe = shutil.which(shell)
    if not exe:
        pytest.skip(f"{shell} is not installed")
    delays_file_ps = str(delays_file).replace("\\", "\\\\")
    harness = tmp_path / f"harness-{shell.replace('.exe', '')}.ps1"
    harness.write_text(
        _extract_ps1_functions("Test-IsSreModuleMismatch", "Invoke-UvPipInstallResilient")
        + f"""

function Write-Warn {{ param([string]$m) Write-Host "WARN: $m" }}
# Stub out the real wait so the test is fast, while recording each requested
# delay so the caller can assert the actual backoff schedule (3s/6s/10s), not
# just the retry count.
function Start-Sleep {{ param([int]$Seconds) Add-Content -LiteralPath '{delays_file_ps}' -Value $Seconds }}

{uv_stub_body}

{extra_script}
""",
        encoding="utf-8",
    )
    return subprocess.run(
        [exe, "-NoProfile", "-File", str(harness)],
        check=True,
        capture_output=True,
        text=True,
    )


def _delays(delays_file: Path) -> list[int]:
    if not delays_file.exists():
        return []
    return [int(line) for line in delays_file.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_sre_mismatch_retries_then_succeeds(tmp_path: Path, shell: str) -> None:
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    counter_file_ps = str(counter_file).replace("\\", "\\\\")
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
function uv {{
    $countPath = '{counter_file_ps}'
    $n = [int](Get-Content -LiteralPath $countPath)
    $n += 1
    Set-Content -LiteralPath $countPath -Value $n
    if ($n -lt 3) {{
        Write-Output 'AssertionError: SRE module mismatch'
        $global:LASTEXITCODE = 1
        return
    }}
    Write-Output 'Installed 1 package'
    $global:LASTEXITCODE = 0
}}
"""
    extra = """
$result = Invoke-UvPipInstallResilient @('--python', 'fake-python', 'some-package', '--quiet')
Write-Host "EXIT:$($result.ExitCode)"
Write-Host "OUT:$($result.Output)"
"""
    result = _run_harness(tmp_path, shell, uv_stub, extra, delays_file)
    # Two retries needed (three total attempts) -- both backoff warnings fire.
    assert result.stdout.count("uv build hit a transient SRE module mismatch") == 2
    assert "EXIT:0" in result.stdout
    assert "OUT:Installed 1 package" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "3"
    # The first two entries of the 3s/6s/10s backoff schedule, in order.
    assert _delays(delays_file) == [3, 6]


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_unrelated_failure_is_not_retried(tmp_path: Path, shell: str) -> None:
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    counter_file_ps = str(counter_file).replace("\\", "\\\\")
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
function uv {{
    $countPath = '{counter_file_ps}'
    $n = [int](Get-Content -LiteralPath $countPath)
    $n += 1
    Set-Content -LiteralPath $countPath -Value $n
    Write-Output 'error: network unreachable'
    $global:LASTEXITCODE = 1
}}
"""
    extra = """
$result = Invoke-UvPipInstallResilient @('--python', 'fake-python', 'some-package', '--quiet')
Write-Host "EXIT:$($result.ExitCode)"
Write-Host "OUT:$($result.Output)"
"""
    result = _run_harness(tmp_path, shell, uv_stub, extra, delays_file)
    assert "uv build hit a transient SRE module mismatch" not in result.stdout
    assert "EXIT:1" in result.stdout
    assert "OUT:error: network unreachable" in result.stdout
    # Only one attempt -- an unrelated failure must not trigger the retry.
    assert counter_file.read_text(encoding="utf-8").strip() == "1"
    assert _delays(delays_file) == []


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_persisting_sre_mismatch_still_fails_after_all_retries(
    tmp_path: Path, shell: str
) -> None:
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    counter_file_ps = str(counter_file).replace("\\", "\\\\")
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
function uv {{
    $countPath = '{counter_file_ps}'
    $n = [int](Get-Content -LiteralPath $countPath)
    $n += 1
    Set-Content -LiteralPath $countPath -Value $n
    Write-Output 'AssertionError: SRE module mismatch'
    $global:LASTEXITCODE = 1
}}
"""
    extra = """
$result = Invoke-UvPipInstallResilient @('--python', 'fake-python', 'some-package', '--quiet')
Write-Host "EXIT:$($result.ExitCode)"
"""
    result = _run_harness(tmp_path, shell, uv_stub, extra, delays_file)
    assert "uv build hit a transient SRE module mismatch" in result.stdout
    assert "EXIT:1" in result.stdout
    # One initial attempt plus three backoff retries -- four total, never more.
    assert counter_file.read_text(encoding="utf-8").strip() == "4"
    # The complete 3s/6s/10s backoff schedule, in order.
    assert _delays(delays_file) == [3, 6, 10]

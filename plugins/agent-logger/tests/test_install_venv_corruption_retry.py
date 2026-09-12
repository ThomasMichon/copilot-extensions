"""Regression coverage for the transient venv-corruption retry wrapper
(#6852) in agent-logger's install.ps1 -- mirrored from agent-bridge's fix for
the same shared uv-managed-interpreter race. A concurrent `uv venv` from
another installer landing on the same slot can leave `python.exe` present
but `pyvenv.cfg` missing/incomplete, so `uv venv --allow-existing` fails
immediately with uv exit code 106 / "failed to locate pyvenv.cfg" -- a
distinct signature from #6785's `AssertionError: SRE module mismatch` that
the original retry classifier does not catch. The installer must retry with
backoff on either signature, must also retry a "successful" run that didn't
actually leave a pyvenv.cfg behind, and must otherwise behave exactly like a
plain `uv venv` -- surfacing any other failure immediately."""

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
        _extract_ps1_functions(
            "Test-IsSreModuleMismatch", "Test-IsVenvCorruption", "Invoke-UvVenvResilient"
        )
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
def test_pyvenv_cfg_corruption_retries_then_succeeds(tmp_path: Path, shell: str) -> None:
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    counter_file_ps = str(counter_file).replace("\\", "\\\\")
    venv_dir_ps = str(venv_dir).replace("\\", "\\\\")
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
function uv {{
    $countPath = '{counter_file_ps}'
    $n = [int](Get-Content -LiteralPath $countPath)
    $n += 1
    Set-Content -LiteralPath $countPath -Value $n
    if ($n -lt 3) {{
        Write-Output 'failed to locate pyvenv.cfg: The system cannot find the file specified.'
        $global:LASTEXITCODE = 106
        return
    }}
    Set-Content -LiteralPath (Join-Path '{venv_dir_ps}' 'pyvenv.cfg') -Value 'home = fake'
    Write-Output 'Created venv'
    $global:LASTEXITCODE = 0
}}
"""
    extra = """
$result = Invoke-UvVenvResilient -VenvDir 'VENV_DIR_PLACEHOLDER' -Arguments @('--python', '3.10', '--allow-existing')
Write-Host "EXIT:$($result.ExitCode)"
""".replace("VENV_DIR_PLACEHOLDER", str(venv_dir).replace("\\", "\\\\"))
    result = _run_harness(tmp_path, shell, uv_stub, extra, delays_file)
    assert result.stdout.count("uv venv hit a transient pyvenv.cfg corruption") == 2
    assert "EXIT:0" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "3"
    assert (venv_dir / "pyvenv.cfg").exists()
    assert _delays(delays_file) == [3, 6]


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_unrelated_failure_is_not_retried(tmp_path: Path, shell: str) -> None:
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
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
$result = Invoke-UvVenvResilient -VenvDir 'VENV_DIR_PLACEHOLDER' -Arguments @('--allow-existing')
Write-Host "EXIT:$($result.ExitCode)"
""".replace("VENV_DIR_PLACEHOLDER", str(venv_dir).replace("\\", "\\\\"))
    result = _run_harness(tmp_path, shell, uv_stub, extra, delays_file)
    assert "uv venv hit a transient" not in result.stdout
    assert "pyvenv.cfg is missing" not in result.stdout
    assert "EXIT:1" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "1"
    assert _delays(delays_file) == []

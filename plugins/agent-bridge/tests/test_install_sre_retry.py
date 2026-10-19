"""Regression coverage for the transient SRE-module-mismatch retry wrapper
(#6785): a shared uv-managed Python interpreter can momentarily disagree with
its own compiled `_sre` extension when several installers hit it in quick
succession during a big `agent-worktrees update --force` sweep. The installer
must retry with backoff on that specific signature and otherwise behave
exactly like a plain `uv pip install` -- surfacing any other failure
immediately, and still failing if the SRE mismatch persists through every
retry."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = PLUGIN.parents[1] / "libs" / "installer-engine" / "installer-engine.ps1"


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
    if shell == "powershell.exe" and os.name != "nt":
        pytest.skip("Windows PowerShell cannot consume this POSIX fixture's paths and executables")
    exe = shutil.which(shell)
    if not exe:
        pytest.skip(f"{shell} is not installed")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # UV is reached by the canonical Python policy across a process boundary.
    # Settings probes exit before executing the install-only counter/SRE body.
    uv_script = fake_bin / "uv.py"
    uv_script.write_text(
        f"#!{sys.executable}\n"
        "import datetime, sys\n"
        "from pathlib import Path\n"
        "arguments = sys.argv[1:]\n"
        "assert arguments[:2] == ['pip', 'install']\n"
        "if '--show-settings' in arguments:\n"
        "    print('PipInstallSettings { settings: PipSettings {\\n"
        "    index_locations: IndexLocations { indexes: [], flat_index: [], },\\n"
        "    exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },\\n"
        "}, }')\n"
        "    sys.exit(0)\n"
        "assert '--reinstall' in arguments and '--quiet' in arguments\n"
        "cutoff = datetime.datetime.fromisoformat(arguments[arguments.index('--exclude-newer') + 1].replace('Z', '+00:00'))\n"
        "assert cutoff <= datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)\n"
        + uv_stub_body,
        encoding="utf-8",
    )
    if os.name == "nt":
        (fake_bin / "uv.cmd").write_text(
            f'@echo off\n"{sys.executable}" "{uv_script}" %*\n', encoding="utf-8"
        )
        uv_command = fake_bin / "uv.cmd"
    else:
        uv_script.rename(fake_bin / "uv")
        (fake_bin / "uv").chmod(0o755)
        uv_command = fake_bin / "uv"
    delays_file_ps = str(delays_file)
    policy = _INSTALL_PS1.with_name("installer_dependency_policy.py")
    harness = tmp_path / f"harness-{shell.replace('.exe', '')}.ps1"
    harness.write_text(
        f"$script:InstallerDependencyPolicy = '{policy}'\n"
        + _extract_ps1_functions("Invoke-NativeCapture", "Test-IsSreModuleMismatch", "Invoke-UvPipInstallResilient")
        + f"""

function Write-Warn {{ param([string]$m) Write-Host "WARN: $m" }}
# Stub out the real wait so the test is fast, while recording each requested
# delay so the caller can assert the actual backoff schedule (3s/6s/10s), not
# just the retry count.
function Start-Sleep {{ param([int]$Seconds) Add-Content -LiteralPath '{delays_file_ps}' -Value $Seconds }}

{extra_script.replace('fake-python', str(sys.executable)).replace('Invoke-UvPipInstallResilient @(', f"Invoke-UvPipInstallResilient -UvCommand '{uv_command}' @(")}
""",
        encoding="utf-8",
    )
    return subprocess.run(
        [exe, "-NoProfile", "-File", str(harness)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"},
    )


def _delays(delays_file: Path) -> list[int]:
    if not delays_file.exists():
        return []
    return [int(line) for line in delays_file.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_sre_mismatch_retries_then_succeeds(tmp_path: Path, shell: str) -> None:
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
count_path = Path({str(counter_file)!r})
n = int(count_path.read_text(encoding='utf-8')) + 1
count_path.write_text(str(n), encoding='utf-8')
if n < 3:
    print('AssertionError: SRE module mismatch')
    sys.exit(1)
print('Installed 1 package')
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
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
count_path = Path({str(counter_file)!r})
n = int(count_path.read_text(encoding='utf-8')) + 1
count_path.write_text(str(n), encoding='utf-8')
print('error: network unreachable')
sys.exit(1)
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
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
count_path = Path({str(counter_file)!r})
n = int(count_path.read_text(encoding='utf-8')) + 1
count_path.write_text(str(n), encoding='utf-8')
print('AssertionError: SRE module mismatch')
sys.exit(1)
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

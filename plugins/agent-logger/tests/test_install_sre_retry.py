"""Regression coverage for the transient SRE-module-mismatch retry wrapper
(#6785) in agent-logger's ``install.ps1`` -- mirrors the equivalent coverage
in agent-bridge's ``test_install_sre_retry.py``/``test_install_sh_sre_retry.py``.
A shared uv-managed Python interpreter can momentarily disagree with its own
compiled `_sre` extension when several installers hit it in quick succession
during a big `agent-worktrees update --force` sweep. The installer must retry
with backoff on that specific signature and otherwise behave exactly like a
plain `uv pip install` -- surfacing any other failure immediately, and still
failing if the SRE mismatch persists through every retry.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = PLUGIN / "scripts" / "install.ps1"
_INSTALL_SH = PLUGIN / "scripts" / "install.sh"
_BASH = shutil.which("bash")


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


def _run_ps_harness(
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
def test_windows_sre_mismatch_retries_then_succeeds(tmp_path: Path, shell: str) -> None:
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
    result = _run_ps_harness(tmp_path, shell, uv_stub, extra, delays_file)
    assert result.stdout.count("uv build hit a transient SRE module mismatch") == 2
    assert "EXIT:0" in result.stdout
    assert "OUT:Installed 1 package" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "3"
    assert _delays(delays_file) == [3, 6]


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_windows_unrelated_failure_is_not_retried(tmp_path: Path, shell: str) -> None:
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
    result = _run_ps_harness(tmp_path, shell, uv_stub, extra, delays_file)
    assert "uv build hit a transient SRE module mismatch" not in result.stdout
    assert "EXIT:1" in result.stdout
    assert "OUT:error: network unreachable" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "1"
    assert _delays(delays_file) == []


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_windows_persisting_sre_mismatch_still_fails_after_all_retries(
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
    result = _run_ps_harness(tmp_path, shell, uv_stub, extra, delays_file)
    assert "uv build hit a transient SRE module mismatch" in result.stdout
    assert "EXIT:1" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "4"
    assert _delays(delays_file) == [3, 6, 10]


def _extract_sh_functions(*names: str) -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    chunks = []
    for name in names:
        start = text.index(f"{name}()")
        end = text.index("\n}\n", start)
        chunks.append(text[start : end + 2])
    return "\n\n".join(chunks)


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _run_sh_harness(
    tmp_path: Path, uv_stub_body: str, extra_script: str, delays_file: Path
) -> subprocess.CompletedProcess:
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        + _extract_sh_functions("_is_sre_module_mismatch", "_uv_pip_install_resilient")
        # Matches the real `warn() { log "WARN" "$1"; }` -- routed to stdout,
        # since agent-logger's own `log()` helper (unlike agent-bridge's
        # `_warn`) does not redirect to stderr.
        + """
warn() { echo "WARN: $*"; }

"""
        + uv_stub_body
        + "\n\n"
        + extra_script
        + "\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    _executable(fake_bin / "sleep", f'#!/bin/sh\necho "$1" >> "{delays_file}"\nexit 0\n')
    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
    return subprocess.run(
        [_BASH, str(harness)],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=True,
    )


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_posix_sre_mismatch_retries_then_succeeds(tmp_path: Path) -> None:
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
uv() {{
    n=$(cat '{counter_file}')
    n=$((n + 1))
    echo "$n" > '{counter_file}'
    if [ "$n" -lt 3 ]; then
        echo 'AssertionError: SRE module mismatch'
        return 1
    fi
    echo 'Installed 1 package'
    return 0
}}
"""
    extra = """
if out=$(_uv_pip_install_resilient --python fake-python some-package --quiet); then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
echo "OUT:$out"
"""
    result = _run_sh_harness(tmp_path, uv_stub, extra, delays_file)
    assert result.stdout.count("uv build hit a transient SRE module mismatch") == 2
    assert "EXIT:0" in result.stdout
    # agent-logger's `warn()` (unlike agent-bridge's `_warn`) writes to
    # stdout, so the captured `$out` interleaves the retry warnings with the
    # final payload -- check for substring presence, not exact adjacency.
    assert "Installed 1 package" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "3"
    assert _delays(delays_file) == [3, 6]


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_posix_unrelated_failure_is_not_retried(tmp_path: Path) -> None:
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
uv() {{
    n=$(cat '{counter_file}')
    n=$((n + 1))
    echo "$n" > '{counter_file}'
    echo 'error: network unreachable'
    return 1
}}
"""
    extra = """
if out=$(_uv_pip_install_resilient --python fake-python some-package --quiet); then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
"""
    result = _run_sh_harness(tmp_path, uv_stub, extra, delays_file)
    assert "uv build hit a transient SRE module mismatch" not in result.stdout
    assert "EXIT:1" in result.stdout
    assert "error: network unreachable" in result.stderr
    assert counter_file.read_text(encoding="utf-8").strip() == "1"
    assert _delays(delays_file) == []


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_posix_persisting_sre_mismatch_still_fails_after_all_retries(tmp_path: Path) -> None:
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    delays_file = tmp_path / "delays.txt"
    uv_stub = f"""
uv() {{
    n=$(cat '{counter_file}')
    n=$((n + 1))
    echo "$n" > '{counter_file}'
    echo 'AssertionError: SRE module mismatch'
    return 1
}}
"""
    extra = """
if out=$(_uv_pip_install_resilient --python fake-python some-package --quiet); then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
echo "OUT:$out"
"""
    result = _run_sh_harness(tmp_path, uv_stub, extra, delays_file)
    assert result.stdout.count("uv build hit a transient SRE module mismatch") == 3
    assert "EXIT:1" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "4"
    assert _delays(delays_file) == [3, 6, 10]

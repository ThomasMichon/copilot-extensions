"""Regression coverage for the transient SRE-module-mismatch retry wrapper
(#6785) in the shared installer engine -- mirrors the equivalent coverage
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
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = PLUGIN.parents[1] / "libs" / "installer-engine" / "installer-engine.ps1"
_INSTALL_SH = PLUGIN.parents[1] / "libs" / "installer-engine" / "installer-engine.sh"
# A bare shutil.which("bash") can resolve to a Windows App Execution Alias
# stub or the classic `C:\Windows\System32\bash.exe` WSL launcher (both
# invoke an actual WSL distro rather than running this script in the
# environment under test). Prefer the real Git Bash location when present;
# otherwise filter both known WSL-launcher locations out of PATH before
# falling back to shutil.which, so this never silently selects one.
_GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
def _resolve_bash() -> str | None:
    if _GIT_BASH.is_file():
        return str(_GIT_BASH)
    path = os.environ.get("PATH")
    if not path:
        return None
    filtered = os.pathsep.join(
        part for part in path.split(os.pathsep)
        if "windowsapps" not in part.lower()
        and part.rstrip("\\").lower() != r"c:\windows\system32"
    )
    return shutil.which("bash", path=filtered)
_BASH = _resolve_bash()

_UV_SETTINGS = """PipInstallSettings { settings: PipSettings {
    index_locations: IndexLocations {
        indexes: [],
        flat_index: [],
    },
    exclude_newer: ExcludeNewer {
        global: None,
        package: ExcludeNewerPackage({}),
    },
}, }
"""


def _physical_uv(tmp_path: Path, failure: str, success_after: int | None) -> Path:
    """Move the simulated outcome behind the real policy subprocess boundary."""
    counter = tmp_path / "attempt-count.txt"
    script = tmp_path / "fake-uv.py"
    script.write_text(
        f"""#!{sys._base_executable}
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

args = sys.argv[1:]
assert args[:2] == ['pip', 'install'], args
if '--show-settings' in args:
    assert '--quiet' not in args
    print({_UV_SETTINGS!r})
    raise SystemExit(0)
assert '--reinstall' in args
assert '--quiet' in args and 'some-package' in args
assert '--no-deps' not in args and '--no-build-isolation' not in args
cutoff = datetime.fromisoformat(args[args.index('--exclude-newer') + 1].replace('Z', '+00:00'))
assert cutoff <= datetime.now(timezone.utc) - timedelta(days=7)
counter = Path({str(counter)!r})
n = int(counter.read_text()) + 1
counter.write_text(str(n))
success_after = {success_after!r}
if success_after is not None and n >= success_after:
    print('Installed 1 package')
    raise SystemExit(0)
print({failure!r})
raise SystemExit(1)
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        command = tmp_path / "fake-uv.cmd"
        command.write_text(f'@"{sys._base_executable}" "{script}" %*\n', encoding="utf-8")
    else:
        command = script
        command.chmod(0o755)
    return command


@pytest.mark.parametrize("failure", ["AssertionError: SRE module mismatch", "error: network unreachable"])
def test_uv_settings_probe_has_no_install_effects(tmp_path: Path, failure: str) -> None:
    counter = tmp_path / "attempt-count.txt"
    counter.write_text("0", encoding="utf-8")
    uv = _physical_uv(tmp_path, failure, None)
    before = set(tmp_path.iterdir())
    result = subprocess.run(
        [str(uv), "pip", "install", "--show-settings", "some-package"],
        capture_output=True, text=True, timeout=20, check=True,
    )
    assert result.stdout.strip() == _UV_SETTINGS.strip()
    assert counter.read_text(encoding="utf-8") == "0"
    assert set(tmp_path.iterdir()) == before


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
    tmp_path: Path, shell: str, failure: str, extra_script: str, delays_file: Path,
    success_after: int | None = None,
) -> subprocess.CompletedProcess:
    exe = shutil.which(shell)
    if not exe:
        pytest.skip(f"{shell} is not installed")
    delays_file_ps = str(delays_file).replace("\\", "\\\\")
    uv = _physical_uv(tmp_path, failure, success_after)
    policy = _INSTALL_PS1.with_name("installer_dependency_policy.py")
    extra_script = extra_script.replace("'fake-python'", f"'{sys._base_executable}'")
    extra_script = extra_script.replace(
        "Invoke-UvPipInstallResilient @(",
        f"Invoke-UvPipInstallResilient -UvCommand '{uv}' -Arguments @(",
    )
    harness = tmp_path / f"harness-{shell.replace('.exe', '')}.ps1"
    harness.write_text(
        _extract_ps1_functions("Invoke-NativeCapture", "Test-IsSreModuleMismatch", "Invoke-UvPipInstallResilient")
        + f"""
$script:InstallerDependencyPolicy = '{policy}'

function Write-Warn {{ param([string]$m) Write-Host "WARN: $m" }}
function Start-Sleep {{ param([int]$Seconds) Add-Content -LiteralPath '{delays_file_ps}' -Value $Seconds }}

{extra_script}
""",
        encoding="utf-8",
    )
    return subprocess.run(
        [exe, "-NoProfile", "-File", str(harness)],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _delays(delays_file: Path) -> list[int]:
    if not delays_file.exists():
        return []
    return [int(line) for line in delays_file.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
@pytest.mark.timeout(210)
def test_windows_sre_mismatch_retries_then_succeeds(tmp_path: Path, shell: str) -> None:
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    delays_file = tmp_path / "delays.txt"
    extra = """
$result = Invoke-UvPipInstallResilient @('--python', 'fake-python', 'some-package', '--quiet')
Write-Host "EXIT:$($result.ExitCode)"
Write-Host "OUT:$($result.Output)"
"""
    result = _run_ps_harness(
        tmp_path, shell, "AssertionError: SRE module mismatch", extra, delays_file, 3
    )
    assert result.stdout.count("uv build hit a transient SRE module mismatch") == 2
    assert "EXIT:0" in result.stdout
    assert "OUT:Installed 1 package" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "3"
    assert _delays(delays_file) == [3, 6]


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
@pytest.mark.timeout(210)
def test_windows_unrelated_failure_is_not_retried(tmp_path: Path, shell: str) -> None:
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    delays_file = tmp_path / "delays.txt"
    extra = """
$result = Invoke-UvPipInstallResilient @('--python', 'fake-python', 'some-package', '--quiet')
Write-Host "EXIT:$($result.ExitCode)"
Write-Host "OUT:$($result.Output)"
"""
    result = _run_ps_harness(tmp_path, shell, "error: network unreachable", extra, delays_file)
    assert "uv build hit a transient SRE module mismatch" not in result.stdout
    assert "EXIT:1" in result.stdout
    assert "OUT:error: network unreachable" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "1"
    assert _delays(delays_file) == []


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
@pytest.mark.timeout(210)
def test_windows_persisting_sre_mismatch_still_fails_after_all_retries(
    tmp_path: Path, shell: str
) -> None:
    counter_file = tmp_path / "attempt-count.txt"
    counter_file.write_text("0", encoding="utf-8")
    delays_file = tmp_path / "delays.txt"
    extra = """
$result = Invoke-UvPipInstallResilient @('--python', 'fake-python', 'some-package', '--quiet')
Write-Host "EXIT:$($result.ExitCode)"
"""
    result = _run_ps_harness(
        tmp_path, shell, "AssertionError: SRE module mismatch", extra, delays_file
    )
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
    tmp_path: Path, failure: str, extra_script: str, delays_file: Path,
    success_after: int | None = None,
) -> subprocess.CompletedProcess:
    harness = tmp_path / "harness.sh"
    uv = _physical_uv(tmp_path, failure, success_after)
    policy = _INSTALL_SH.with_name("installer_dependency_policy.py")
    extra_script = extra_script.replace(
        "invoke_uv_pip_install_resilient uv --python fake-python",
        f"invoke_uv_pip_install_resilient '{uv}' --python '{sys._base_executable}'",
    )
    harness.write_text(
        f"#!/bin/bash\nset -eu\nINSTALLER_ENGINE_DEPENDENCY_POLICY='{policy}'\n"
        + _extract_sh_functions("test_is_sre_module_mismatch", "invoke_uv_pip_install_resilient")
        # Matches the real `warn() { log "WARN" "$1"; }` -- routed to stdout,
        # since agent-logger's own `log()` helper (unlike agent-bridge's
        # `_warn`) does not redirect to stderr.
        + """
_warn() { echo "WARN: $*"; }

"""
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
    extra = """
if out=$(invoke_uv_pip_install_resilient uv --python fake-python some-package --quiet); then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
echo "OUT:$out"
"""
    result = _run_sh_harness(
        tmp_path, "AssertionError: SRE module mismatch", extra, delays_file, 3
    )
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
    extra = """
if out=$(invoke_uv_pip_install_resilient uv --python fake-python some-package --quiet); then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
echo "OUT:$out"
"""
    result = _run_sh_harness(tmp_path, "error: network unreachable", extra, delays_file)
    assert "uv build hit a transient SRE module mismatch" not in result.stdout
    assert "EXIT:1" in result.stdout
    assert "OUT:error: network unreachable" in result.stdout
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
    extra = """
if out=$(invoke_uv_pip_install_resilient uv --python fake-python some-package --quiet); then
    echo "EXIT:0"
else
    echo "EXIT:1"
fi
echo "OUT:$out"
"""
    result = _run_sh_harness(
        tmp_path, "AssertionError: SRE module mismatch", extra, delays_file
    )
    assert result.stdout.count("uv build hit a transient SRE module mismatch") == 3
    assert "EXIT:1" in result.stdout
    assert counter_file.read_text(encoding="utf-8").strip() == "4"
    assert _delays(delays_file) == [3, 6, 10]

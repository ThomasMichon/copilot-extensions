"""The runtime installer takes a fail-closed install-transaction lock.

Every mutating installer action (stamp/install/update/provision) must hold one
exclusive lock on ``<install-dir>/.install.lock`` for the whole build + publish
transaction, so two concurrent runs (a launch-time reconcile, ``update``, a
first-use self-provision) never interleave inside the same versioned slot. A
run that cannot acquire the lock within its timeout must fail with a clear
message and a non-zero exit -- it must never proceed unlocked.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
PS_SCRIPT = PLUGIN / "scripts" / "install.ps1"
SH_SCRIPT = PLUGIN / "scripts" / "install.sh"
LOCK_START = "# === install-transaction lock (fail-closed) ==="
LOCK_END = "# === end install-transaction lock ==="
SELF_STAGE_END = "# === end install-contract:v4 self-stage ==="
SMOKE_END = "# === end install-contract:v4 smoke seam ==="
PS_FIRST_MUTATION = "    switch ($Action) {"
SH_FIRST_MUTATION = "case \"$ACTION\" in\n    install)   do_install ;;"
LOCK_FAILURE = "Could not acquire the install transaction lock"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _block(path: Path) -> str:
    text = _text(path)
    start = text.index(LOCK_START)
    return text[start : text.index(LOCK_END, start) + len(LOCK_END)]


def _ps_cleanup() -> str:
    tail = _text(PS_SCRIPT).rsplit("\n} finally {\n", 1)[1]
    body, after = tail.split("\n}\n", 1)
    assert after.strip() == "exit 0"
    return body + "\n"


def _pwsh() -> str | None:
    return shutil.which("pwsh")


def _posix_bash() -> str | None:
    # Windows ``bash`` resolves to a WSL relay or Git Bash (no flock, no real
    # symlinks), neither of which is the POSIX installer's target.
    return None if os.name == "nt" else shutil.which("bash")


def _isolated_env(home: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COPILOT_PLUGIN_")
        and key not in {"COPILOT_EXTENSIONS_CONTEXT", "COPILOT_EXT_NO_FLOCK"}
    }
    env.update({"HOME": str(home), "USERPROFILE": str(home)})
    env["COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC"] = "1"
    return env


# ---------------------------------------------------------------------------
# Static contract: acquired after self-stage, before any mutation, fail-closed
# ---------------------------------------------------------------------------

@pytest.mark.guard
@pytest.mark.parametrize("script", [PS_SCRIPT, SH_SCRIPT], ids=lambda p: p.name)
def test_lock_is_acquired_after_self_stage_and_before_first_mutation(script: Path) -> None:
    text = _text(script)
    first_mutation = PS_FIRST_MUTATION if script.suffix == ".ps1" else SH_FIRST_MUTATION
    acquire = "(Enter-InstallLock)" if script.suffix == ".ps1" else "if ! _enter_install_lock; then"

    assert text.count(LOCK_START) == 1
    assert text.index(SELF_STAGE_END) < text.index(SMOKE_END) < text.index(acquire)
    assert text.index(acquire) < text.index(first_mutation)


@pytest.mark.guard
def test_powershell_lock_fails_closed_and_releases_in_finally() -> None:
    text = _text(PS_SCRIPT)
    acquire = text.index("(Enter-InstallLock)")
    failure = text[acquire : text.index("\n}\n", acquire)]
    assert LOCK_FAILURE in failure
    assert "exit 1" in failure
    tail = text[acquire:]
    assert "\ntry {\n" in tail
    cleanup = _ps_cleanup()
    assert cleanup.startswith("    try {\n")
    assert cleanup.endswith("    } finally {\n        Exit-InstallLock\n    }\n")
    assert cleanup.index("Stop-RuntimeConstruction") < cleanup.index("Exit-InstallLock")
    assert cleanup.count("Exit-InstallLock") == 1
    block = _block(PS_SCRIPT)
    assert "[System.IO.FileShare]::None" in block
    assert "return $false" in block


@pytest.mark.guard
def test_posix_lock_fails_closed_and_releases_on_exit() -> None:
    text = _text(SH_SCRIPT)
    acquire = text.index("if ! _enter_install_lock; then")
    failure = text[acquire : text.index("fi\n", acquire)]
    assert LOCK_FAILURE in failure
    assert "exit 1" in failure
    assert "trap '_exit_install_lock' EXIT" in text[acquire:]
    assert 'flock -w "$__timeout" 8' in _block(SH_SCRIPT)


# ---------------------------------------------------------------------------
# PowerShell behavior
# ---------------------------------------------------------------------------

def _ps_holder(pwsh: str, lock: Path) -> subprocess.Popen[str]:
    script = (
        f"$fs = [System.IO.File]::Open('{lock}', 'OpenOrCreate', 'ReadWrite', 'None'); "
        "[Console]::Out.WriteLine('held'); [Console]::Out.Flush(); Start-Sleep -Seconds 60"
    )
    holder = subprocess.Popen(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "held"
    return holder


def _run_ps_block(pwsh: str, install_dir: Path, body: str) -> subprocess.CompletedProcess[str]:
    script = (
        "Set-StrictMode -Version 2.0\n$ErrorActionPreference = 'Stop'\n"
        "function Write-Fail { param([string]$Msg) Write-Output \"FAIL:$Msg\" }\n"
        f"$InstallDir = '{install_dir}'\n" + _block(PS_SCRIPT) + "\n" + body
    )
    return subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=60,
    )


def test_powershell_lock_acquires_releases_and_reacquires(tmp_path: Path) -> None:
    pwsh = _pwsh()
    if pwsh is None:
        pytest.skip("pwsh not available")
    result = _run_ps_block(
        pwsh, tmp_path / "rt",
        "$a = Enter-InstallLock -TimeoutSec 1; Exit-InstallLock; "
        "$b = Enter-InstallLock -TimeoutSec 1; Exit-InstallLock; Write-Output \"$a/$b\"",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True/True"
    assert (tmp_path / "rt" / ".install.lock").is_file()


def test_powershell_lock_releases_when_construction_cleanup_throws(tmp_path: Path) -> None:
    pwsh = _pwsh()
    if pwsh is None:
        pytest.skip("pwsh not available")
    result = _run_ps_block(pwsh, tmp_path / "rt", f"""
function Stop-RuntimeConstruction {{
    if (-not $script:InstallLockHandle) {{ throw 'lock-released-before-construction' }}
    Write-Output 'cleanup-held=True'
    throw 'construction-cleanup-failure'
}}
if (-not (Enter-InstallLock -TimeoutSec 1)) {{ throw 'lock-not-acquired' }}
try {{
    try {{ throw 'install-body-failure' }} finally {{
{_ps_cleanup()}
    }}
}} catch {{ Write-Output "caught=$($_.Exception.Message)" }}
Write-Output "released=$($null -eq $script:InstallLockHandle)"
$reacquired = Enter-InstallLock -TimeoutSec 1
Exit-InstallLock
Write-Output "reacquired=$reacquired"
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == [
        "cleanup-held=True", "caught=construction-cleanup-failure",
        "released=True", "reacquired=True",
    ]


def test_powershell_lock_times_out_while_held(tmp_path: Path) -> None:
    pwsh = _pwsh()
    if pwsh is None:
        pytest.skip("pwsh not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    holder = _ps_holder(pwsh, install_dir / ".install.lock")
    try:
        started = time.monotonic()
        result = _run_ps_block(
            pwsh, install_dir, "Write-Output \"acquired=$(Enter-InstallLock -TimeoutSec 1)\""
        )
        assert result.stdout.strip().splitlines()[-1] == "acquired=False", result.stderr
        assert time.monotonic() - started < 30
    finally:
        holder.kill()
        holder.wait()


def test_powershell_installer_fails_closed_without_mutating(tmp_path: Path) -> None:
    pwsh = _pwsh()
    if pwsh is None:
        pytest.skip("pwsh not available")
    home = tmp_path / "home"
    home.mkdir()
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    holder = _ps_holder(pwsh, install_dir / ".install.lock")
    try:
        result = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(PS_SCRIPT), "-Action", "stamp", "-InstallDir", str(install_dir)],
            capture_output=True, text=True, timeout=120, env=_isolated_env(home),
            cwd=tmp_path,
        )
    finally:
        holder.kill()
        holder.wait()
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert LOCK_FAILURE in output
    assert sorted(p.name for p in install_dir.iterdir()) == [".install.lock"]
    assert not (home / ".local").exists()


# ---------------------------------------------------------------------------
# POSIX behavior
# ---------------------------------------------------------------------------

def _run_sh_block(
    bash: str, install_dir: Path, body: str, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    script = (
        "set -euo pipefail\n_fail() { printf 'FAIL:%s\\n' \"$1\"; }\n"
        f"INSTALL_DIR='{install_dir}'\n" + _block(SH_SCRIPT) + "\n" + body
    )
    return subprocess.run(
        [bash, "-c", script], capture_output=True, text=True, timeout=60,
        env=env if env is not None else dict(os.environ),
    )


def _flock_holder(bash: str, lock: Path) -> subprocess.Popen[str]:
    holder = subprocess.Popen(
        [bash, "-c", 'exec 8>"$1"; flock 8; echo held; sleep 60', "_", str(lock)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "held"
    return holder


def test_posix_flock_acquires_releases_and_reacquires(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None or shutil.which("flock") is None:
        pytest.skip("POSIX bash with flock not available")
    result = _run_sh_block(
        bash, tmp_path / "rt",
        "_enter_install_lock 1 && _exit_install_lock && _enter_install_lock 1 && echo ok",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_posix_flock_times_out_while_held(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None or shutil.which("flock") is None:
        pytest.skip("POSIX bash with flock not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    holder = _flock_holder(bash, install_dir / ".install.lock")
    try:
        result = _run_sh_block(
            bash, install_dir, "if _enter_install_lock 1; then echo acquired; else echo refused; fi"
        )
    finally:
        holder.kill()
        holder.wait()
    assert result.stdout.strip() == "refused", result.stderr


def test_posix_pid_fallback_waits_for_live_owner_and_reaps_dead_one(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    link = install_dir / ".install.lock.pid"
    env = {**os.environ, "COPILOT_EXT_NO_FLOCK": "1"}
    probe = "if _enter_install_lock 1; then echo acquired; else echo refused; fi"

    os.symlink(str(os.getpid()), link)
    live = _run_sh_block(bash, install_dir, probe, env=env)
    assert live.stdout.strip() == "refused", live.stderr
    assert os.readlink(link) == str(os.getpid())

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    link.unlink()
    os.symlink(str(dead.pid), link)
    reaped = _run_sh_block(bash, install_dir, probe, env=env)
    assert reaped.stdout.strip() == "acquired", reaped.stderr


def test_posix_installer_fails_closed_without_mutating(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None or shutil.which("flock") is None:
        pytest.skip("POSIX bash with flock not available")
    home = tmp_path / "home"
    home.mkdir()
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    holder = _flock_holder(bash, install_dir / ".install.lock")
    try:
        result = subprocess.run(
            [bash, str(SH_SCRIPT), "stamp", "--install-dir", str(install_dir)],
            capture_output=True, text=True, timeout=120, env=_isolated_env(home),
            cwd=tmp_path,
        )
    finally:
        holder.kill()
        holder.wait()
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert LOCK_FAILURE in output
    assert sorted(p.name for p in install_dir.iterdir()) == [".install.lock"]
    assert not (home / ".local").exists()


def _sh_contender(bash: str, install_dir: Path, log: Path) -> subprocess.Popen[str]:
    script = (
        "set -euo pipefail\n_fail() { printf 'FAIL:%s\\n' \"$1\"; }\n"
        f"INSTALL_DIR='{install_dir}'\n" + _block(SH_SCRIPT) + "\n"
        "_enter_install_lock 30 || exit 3\n"
        f"echo start >> '{log}'; sleep 0.2; echo end >> '{log}'\n"
        "_exit_install_lock\n"
    )
    return subprocess.Popen(
        [bash, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )


def _dead_pid() -> int:
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    return dead.pid


def test_posix_pid_fallback_serializes_simultaneous_stale_recovery(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    os.symlink(str(_dead_pid()), install_dir / ".install.lock.pid")
    log = tmp_path / "log"
    contenders = [_sh_contender(bash, install_dir, log) for _ in range(6)]
    for proc in contenders:
        _out, err = proc.communicate(timeout=90)
        assert proc.returncode == 0, err
    # Every transaction ran alone: no start ever follows another start.
    assert log.read_text().split() == ["start", "end"] * 6
    assert not os.path.lexists(install_dir / ".install.lock.pid")
    assert not os.path.lexists(install_dir / ".install.lock.pid.reap")


def test_posix_pid_fallback_fails_closed_on_a_dead_reapers_mutex(tmp_path: Path) -> None:
    """A reap mutex left by a reaper that died mid-reap is never reclaimed
    automatically (that would reintroduce the check-then-unlink race):
    simultaneous contenders all fail closed and leave both links untouched."""
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    stale_owner, stale_reaper = str(_dead_pid()), str(_dead_pid())
    os.symlink(stale_owner, install_dir / ".install.lock.pid")
    os.symlink(stale_reaper, install_dir / ".install.lock.pid.reap")
    script = (
        "set -euo pipefail\n_fail() { printf 'FAIL:%s\\n' \"$1\"; }\n"
        f"INSTALL_DIR='{install_dir}'\n" + _block(SH_SCRIPT) + "\n"
        "if _enter_install_lock 2; then echo acquired; else echo refused; fi\n"
    )
    contenders = [
        subprocess.Popen(
            [bash, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
        )
        for _ in range(3)
    ]
    for proc in contenders:
        out, err = proc.communicate(timeout=60)
        assert out.strip() == "refused", err
    assert os.readlink(install_dir / ".install.lock.pid") == stale_owner
    assert os.readlink(install_dir / ".install.lock.pid.reap") == stale_reaper


# ---------------------------------------------------------------------------
# Deterministic interleavings (shimmed filesystem; runs under any bash)
# ---------------------------------------------------------------------------

def _shim_bash() -> str | None:
    """A bash that can run the shimmed harness. On Windows prefer Git Bash:
    ``bash`` on PATH may be a WSL relay that cannot see host paths."""
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git is None:
        return None
    root = Path(git).resolve().parents[1]
    for candidate in (root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


# Replaces ln/readlink/rm/kill/sleep with shell functions over plain files (a
# "link" is a file holding its target), so a scenario can inject another
# process's action at an exact point of the acquisition loop through the
# before_read/after_read hooks (called with the 1-based count of reads of the
# lock link). ``rm`` records a VIOLATION whenever the lock link is removed
# while it names a live owner -- i.e. whenever a contender deletes a lock it
# does not own.
_SHIM = r"""
LINK="$INSTALL_DIR/.install.lock.pid"
_count() {
    local c; c=$(( $(cat "$STATE/$1" 2>/dev/null || echo 0) + 1 ))
    echo "$c" > "$STATE/$1"; echo "$c"
}
sleep() { :; }
kill() { [[ "$1" == -0 ]] && grep -qx -- "$2" "$STATE/live"; }
ln() { [[ "$1" == -s && ! -e "$3" ]] || return 1; printf '%s' "$2" > "$3"; }
rm() {
    local p
    for p in "$@"; do
        if [[ "$p" == "$LINK" && -e "$p" ]] && kill -0 "$(cat "$p")"; then
            echo VIOLATION >> "$STATE/violations"
        fi
    done
    command rm "$@"
}
readlink() {
    local n="" rc=1
    if [[ "$1" == "$LINK" ]]; then n="$(_count reads)"; before_read "$n"; fi
    if [[ -e "$1" ]]; then cat "$1"; rc=0; fi
    if [[ -n "$n" ]]; then after_read "$n"; fi
    return "$rc"
}
"""


def _run_shimmed(tmp_path: Path, owner: str, live: list[str], hooks: str) -> tuple[str, str]:
    bash = _shim_bash()
    if bash is None:
        pytest.skip("bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (install_dir / ".install.lock.pid").write_bytes(owner.encode())
    (state / "live").write_bytes("".join(f"{pid}\n" for pid in live).encode())

    def posix(path: Path) -> str:
        return str(path).replace("\\", "/")

    script = (
        "set -euo pipefail\n_fail() { printf 'FAIL:%s\\n' \"$1\"; }\n"
        f"INSTALL_DIR='{posix(install_dir)}'\nSTATE='{posix(state)}'\n"
        + _block(SH_SCRIPT) + "\n" + _SHIM + hooks
        + 'if _enter_install_lock 30; then '
        + 'echo "acquired $$ $(cat "$LINK")"; else echo refused; fi\n'
    )
    script_path = tmp_path / "harness.sh"
    script_path.write_bytes(script.encode())
    result = subprocess.run(
        [bash, posix(script_path)], capture_output=True, text=True, timeout=60,
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    violations = state / "violations"
    return result.stdout.strip(), violations.read_text() if violations.exists() else ""


@pytest.mark.guard
def test_pid_fallback_never_unlinks_a_lock_that_vanished_before_readlink(
    tmp_path: Path,
) -> None:
    """The holder releases between the failed ``ln -s`` and ``readlink`` (so
    the owner reads empty), and a competitor takes the lock right after the
    next read sees it absent. The empty owner must never lead to an unlink:
    unlinking then would delete the competitor's live lock and let two
    contenders into the transaction at once."""
    hooks = (
        'before_read() { if [[ "$1" == 1 ]]; then command rm -f "$LINK"; fi; }\n'
        'after_read() { if [[ "$1" == 2 && ! -e "$LINK" ]]; then printf 5151 > "$LINK"; fi; }\n'
    )
    out, violations = _run_shimmed(tmp_path, "4242", ["4242", "5151"], hooks)
    assert violations == ""
    status, pid, holder = out.split()
    assert status == "acquired" and holder == pid


@pytest.mark.guard
def test_pid_fallback_reaper_rechecks_liveness_under_the_mutex(tmp_path: Path) -> None:
    """The owner looked dead before the reap mutex was taken but names a live
    holder by the time the reaper re-reads the link (pid reuse: the stale lock
    was reaped and a new holder with the same pid took it). The reaper must
    re-derive staleness under the mutex and leave the live lock alone."""
    hooks = (
        'before_read() {\n'
        '    case "$1" in\n'
        '        2) echo 6161 >> "$STATE/live" ;;\n'
        '        3) command rm -f "$LINK" ;;\n'
        '    esac\n'
        '}\n'
        'after_read() { :; }\n'
    )
    out, violations = _run_shimmed(tmp_path, "6161", [], hooks)
    assert violations == ""
    status, pid, holder = out.split()
    assert status == "acquired" and holder == pid


@pytest.mark.guard
def test_pid_fallback_reaper_never_unlinks_a_newer_holder_released_mid_check(
    tmp_path: Path,
) -> None:
    """Guard against a re-read-then-probe unlink of a newer holder: the owner
    is dead at the first check, but by the under-mutex re-read a live holder
    P has taken the lock, and P releases while Q re-creates it before any
    liveness probe of the re-read value. Only the owner judged stale in this
    iteration may be removed, so Q's live lock must survive."""
    hooks = (
        'before_read() {\n'
        '    case "$1" in\n'
        '        2) printf 7171 > "$LINK"; echo 7171 > "$STATE/live" ;;\n'
        '        3) command rm -f "$LINK" ;;\n'
        '    esac\n'
        '}\n'
        'after_read() {\n'
        '    if [[ "$1" == 2 ]]; then echo 8181 > "$STATE/live"; printf 8181 > "$LINK"; fi\n'
        '}\n'
    )
    out, violations = _run_shimmed(tmp_path, "6161", [], hooks)
    assert violations == ""
    status, pid, holder = out.split()
    assert status == "acquired" and holder == pid


def test_posix_pid_fallback_fails_closed_on_an_unattributable_lock_path(tmp_path: Path) -> None:
    """A non-symlink at the lock path names no owner: it is never unlinked,
    and the run fails closed at its deadline."""
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    lock = install_dir / ".install.lock.pid"
    lock.write_text("not a symlink")
    script = (
        "set -euo pipefail\n_fail() { printf 'FAIL:%s\\n' \"$1\"; }\n"
        f"INSTALL_DIR='{install_dir}'\n" + _block(SH_SCRIPT) + "\n"
        "if _enter_install_lock 1; then echo acquired; else echo refused; fi\n"
    )
    result = subprocess.run(
        [bash, "-c", script], capture_output=True, text=True, timeout=60,
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.stdout.strip() == "refused", result.stderr
    assert lock.read_text() == "not a symlink"

"""The installer's no-flock pid-symlink fallback serializes stale-owner recovery."""

from __future__ import annotations

import os
import hashlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SH_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


def test_uninstall_keeps_the_existing_transaction_lock_identity() -> None:
    sh = SH_SCRIPT.read_text(encoding="utf-8")
    ps1 = SH_SCRIPT.with_suffix(".ps1").read_text(encoding="utf-8")
    condition = sh[sh.index('if [[ "$ACTION" == "install"'):].split("then", 1)[0]
    assert '"$ACTION" == "uninstall"' in condition
    lock_actions = re.search(r"\$needsInstallLock = \$Action -in @\((.*?)\)", ps1)
    assert lock_actions is not None and "'uninstall'" in lock_actions.group(1)
    assert "uninstall_state_preserving_runtime || exit 1" in sh
    assert "Invoke-StatePreservingUninstall\n" in ps1
    assert sh.index("uninstall_state_preserving_runtime || exit 1") > sh.index("if ! _enter_install_lock; then")
    assert ps1.index("        Invoke-StatePreservingUninstall\n") > ps1.index("if ($needsInstallLock")
    assert 'rm -rf "$INSTALL_DIR"' not in sh
    assert "Remove-Item $InstallDir -Recurse" not in ps1


def test_native_powershell_uninstall_preserves_the_coordination_root(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh") if os.name == "nt" else None
    if pwsh is None:
        pytest.skip("native Windows PowerShell required")
    home, root = tmp_path / "home", tmp_path / "runtime"
    home.mkdir()
    root.mkdir()
    (root / "state").write_text("preserve unknown operator state", encoding="utf-8")
    result = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(SH_SCRIPT.with_suffix(".ps1")),
         "-Action", "uninstall", "-InstallDir", str(root)],
        capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        env={
            **{key: value for key, value in os.environ.items()
               if not key.startswith("COPILOT_PLUGIN_") and key != "COPILOT_EXTENSIONS_CONTEXT"},
            "HOME": str(home), "USERPROFILE": str(home),
        },
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / ".install.lock").is_file()
    assert (root / "state").read_text(encoding="utf-8") == "preserve unknown operator state"
    assert not (home / ".local").exists()


def _cold_payload(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    source = SH_SCRIPT.parents[1]
    repo = source.parents[1]
    home = tmp_path / "home"
    payload = home / ".copilot" / "installed-plugins" / "example" / source.name
    shutil.copytree(source / "scripts", payload / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("plugin.json", "pyproject.toml"):
        shutil.copyfile(source / name, payload / name)
    monkeypatch.syspath_prepend(str(repo / "tools"))
    import materialize_installer_engine

    materialize_installer_engine.materialize_ref_into(
        source_consumer_dir=source, dest_consumer_dir=payload,
        canonical_root=repo, dest_root=home,
    )
    assert (payload / "scripts" / "installer-engine.ps1").is_file()
    assert (payload / "scripts" / "installer-engine.sh").is_file()
    return home, payload


def _cold_environment(home: Path) -> dict[str, str]:
    return {
        **{key: value for key, value in os.environ.items()
           if not key.startswith("COPILOT_PLUGIN_") and key != "COPILOT_EXTENSIONS_CONTEXT"},
        "HOME": str(home), "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
    }


@pytest.mark.parametrize("host", ("powershell.exe", "pwsh"))
def test_native_payload_hash_does_not_require_optional_cmdlet(tmp_path: Path, host: str) -> None:
    executable = shutil.which(host) if os.name == "nt" else None
    if executable is None:
        pytest.skip(f"native Windows {host} unavailable")
    source = SH_SCRIPT.with_name("payload-mode.ps1").read_text(encoding="utf-8")
    prefix = source[:source.index("$payload =")]
    content = tmp_path / "content"
    content.write_bytes(b"source bytes\x00\xff")
    script = tmp_path / "hash.ps1"
    script.write_text(
        "param($Content)\n"
        "function Get-FileHash { throw 'Optional cmdlet must not be required' }\n"
        + prefix + "\nGet-PayloadSourceHash -Path $Content\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(script), str(content)],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().casefold() == hashlib.sha256(content.read_bytes()).hexdigest()


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_materialized_uninstall_self_stage_on_first_touch(
    tmp_path: Path, monkeypatch, host: str,
) -> None:
    executable = shutil.which(host) if os.name == "nt" else None
    if executable is None:
        pytest.skip(f"native Windows {host} unavailable")
    home, payload = _cold_payload(tmp_path, monkeypatch)
    root = home / ".agent-pull-requests"
    assert not root.exists()
    previous = []
    active = root / ".install-stage" / f"20000101T000000-{os.getpid()}" / "active"
    for cycle in range(2):
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File",
             str(payload / "scripts" / "install.ps1"), "-Action", "uninstall",
             "-InstallDir", str(root)],
            capture_output=True, text=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
            env=_cold_environment(home), cwd=tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if cycle == 0:
            previous = list((root / ".install-stage").iterdir())
            active.parent.mkdir(parents=True)
            active.write_text("another live stage", encoding="utf-8")
        else:
            assert all(not path.exists() for path in previous)
            assert active.read_text(encoding="utf-8") == "another live stage"
    assert (root / ".install.lock").is_file()
    assert (root / ".install-stage").is_dir()
    assert not (root / "versions").exists()
    assert (payload / "scripts" / "install.ps1").is_file()
    assert not (home / ".local").exists()


def test_posix_materialized_uninstall_self_stage_on_first_touch(
    tmp_path: Path, monkeypatch,
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("native POSIX Bash required")
    home, payload = _cold_payload(tmp_path, monkeypatch)
    root = home / ".agent-pull-requests"
    assert not root.exists()
    previous = []
    active = root / ".install-stage" / f"20000101T000000-{os.getpid()}" / "active"
    for cycle in range(2):
        result = subprocess.run(
            [bash, str(payload / "scripts" / "install.sh"), "uninstall", "--install-dir", str(root)],
            capture_output=True, text=True, timeout=60, env=_cold_environment(home), cwd=tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if cycle == 0:
            previous = list((root / ".install-stage").iterdir())
            active.parent.mkdir(parents=True)
            active.write_text("another live stage", encoding="utf-8")
        else:
            assert all(not path.exists() for path in previous)
            assert active.read_text(encoding="utf-8") == "another live stage"
    assert root.is_dir()
    assert (root / ".install-stage").is_dir()
    assert not (root / "versions").exists()
    assert (payload / "scripts" / "install.sh").is_file()
    assert not (home / ".local").exists()


def _posix_bash() -> str | None:
    if os.name == "nt":
        return None
    return shutil.which("bash")


def _lock_functions() -> str:
    text = SH_SCRIPT.read_text(encoding="utf-8")
    parts = [
        re.search(rf"^{name}\(\) {{\n.*?^}}\n", text, re.M | re.S)
        for name in ("_exit_install_lock", "_enter_install_lock")
    ]
    assert all(parts), "install.sh must define both lock functions"
    return '__install_lock_link=""\n__install_lock_fd=""\n' + "".join(p.group(0) for p in parts)


def _contender(bash: str, install_dir: Path, body: str) -> subprocess.Popen[str]:
    script = f"set -euo pipefail\nINSTALL_DIR='{install_dir}'\n" + _lock_functions() + body
    return subprocess.Popen(
        [bash, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )


def _dead_pid() -> int:
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    return dead.pid


def test_simultaneous_stale_recovery_never_overlaps(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    os.symlink(str(_dead_pid()), install_dir / ".install.lock.pid")
    log = tmp_path / "log"
    body = (
        "_enter_install_lock || exit 3\n"
        f"echo start >> '{log}'; sleep 0.2; echo end >> '{log}'\n"
        "_exit_install_lock\n"
    )
    contenders = [_contender(bash, install_dir, body) for _ in range(6)]
    for proc in contenders:
        _out, err = proc.communicate(timeout=90)
        assert proc.returncode == 0, err
    assert log.read_text().split() == ["start", "end"] * 6
    assert not os.path.lexists(install_dir / ".install.lock.pid")
    assert not os.path.lexists(install_dir / ".install.lock.pid.reap")


def test_dead_reapers_mutex_is_never_reclaimed(tmp_path: Path) -> None:
    """A reap mutex left by a reaper that died mid-reap is never reclaimed
    automatically (that would reintroduce the check-then-unlink race): every
    contender keeps waiting (and fails closed at its deadline) without
    touching either link."""
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    stale_owner, stale_reaper = str(_dead_pid()), str(_dead_pid())
    os.symlink(stale_owner, install_dir / ".install.lock.pid")
    os.symlink(stale_reaper, install_dir / ".install.lock.pid.reap")
    contenders = [
        _contender(bash, install_dir, "_enter_install_lock && echo acquired\n") for _ in range(3)
    ]
    try:
        time.sleep(3)
        assert all(proc.poll() is None for proc in contenders)
        assert os.readlink(install_dir / ".install.lock.pid") == stale_owner
        assert os.readlink(install_dir / ".install.lock.pid.reap") == stale_reaper
    finally:
        for proc in contenders:
            proc.kill()
            proc.communicate()


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
        + _lock_functions() + "\n" + _SHIM + hooks
        + 'if _enter_install_lock; then '
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


ENGINE_SH = (
    Path(__file__).resolve().parents[3] / "libs" / "installer-engine" / "installer-engine.sh"
)


def _generated_binstub_lock(bash: str, tmp_path: Path) -> str:
    """Generate the simple binstub with the canonical installer-engine and
    return its provisioning-lock section (from the lock path through the
    acquisition loop)."""
    posix = str(tmp_path).replace("\\", "/")
    engine = str(ENGINE_SH).replace("\\", "/")
    gen = tmp_path / "gen.sh"
    gen.write_bytes((
        "_ok() { :; }; _warn() { :; }; _fail() { :; }\n"
        f". '{engine}'\n"
        f"write_simple_binstub stub stub_mod '{posix}/root' '{posix}/bin' '{posix}/root/bin' "
        "scripts/install.sh STUB_NO_SELFPROVISION\n"
    ).encode())
    result = subprocess.run([bash, f"{posix}/gen.sh"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    stub = (tmp_path / "bin" / "stub").read_text(encoding="utf-8")
    start = stub.index('_lock="$_root/.provision.lock"\n')
    end = stub.index("trap '_unlock_provision' EXIT INT TERM\n", start)
    return stub[start:end]


@pytest.mark.guard
def test_generated_binstub_never_unlinks_a_provision_lock_that_vanished(tmp_path: Path) -> None:
    """The simple binstub written by the canonical installer-engine has the
    same no-flock pid-symlink fallback: when the holder releases between the
    failed ``ln -s`` and ``readlink`` and a competitor takes the lock right
    after the re-read sees it absent, the empty owner must never lead to an
    unlink of the competitor's live lock."""
    bash = _shim_bash()
    if bash is None:
        pytest.skip("bash not available")
    section = _generated_binstub_lock(bash, tmp_path)
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    state = tmp_path / "state"
    state.mkdir()
    (root / ".provision.lock.pid").write_bytes(b"4242")
    (state / "live").write_bytes(b"4242\n5151\n")
    posix = str(tmp_path).replace("\\", "/")
    shim = _SHIM.replace(
        'LINK="$INSTALL_DIR/.install.lock.pid"', 'LINK="$_root/.provision.lock.pid"'
    )
    hooks = (
        'before_read() { if [[ "$1" == 1 ]]; then command rm -f "$LINK"; fi; }\n'
        'after_read() { if [[ "$1" == 2 && ! -e "$LINK" ]]; then printf 5151 > "$LINK"; fi; }\n'
    )
    script = (
        f"_root='{posix}/root'\nSTATE='{posix}/state'\n" + shim + hooks + section
        + 'echo "acquired $$ $(cat "$LINK")"\n'
    )
    (tmp_path / "harness.sh").write_bytes(script.encode())
    result = subprocess.run(
        [bash, f"{posix}/harness.sh"], capture_output=True, text=True, timeout=60,
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    violations = state / "violations"
    assert not violations.exists(), violations.read_text()
    status, pid, holder = result.stdout.split()
    assert status == "acquired" and holder == pid

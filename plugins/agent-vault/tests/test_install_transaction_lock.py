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
    env.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
    })
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
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
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
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
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


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_powershell_purge_keeps_live_lock_stage_and_link_target(tmp_path: Path, host: str) -> None:
    executable = shutil.which(host) if os.name == "nt" else None
    if executable is None:
        pytest.skip(f"native {host} unavailable")
    root = tmp_path / "rt"
    stage = root / ".install-stage" / "owner"
    stage.mkdir(parents=True)
    (stage / "active").write_text("active", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("outside", encoding="utf-8")
    (root / "state").write_text("remove", encoding="utf-8")
    engine = PLUGIN.parents[1] / "libs" / "installer-engine" / "installer-engine.ps1"
    result = _run_ps_block(executable, root, f"""
. '{str(engine).replace("'", "''")}'
New-Item -ItemType Junction -Path (Join-Path $InstallDir 'linked-state') -Target '{str(outside).replace("'", "''")}' | Out-Null
if (-not (Enter-InstallLock -TimeoutSec 0)) {{ throw 'initial lock failed' }}
$active = [IO.File]::Open((Join-Path $InstallDir '.install-stage\\owner\\active'), 'Open', 'Read', 'None')
try {{
    Remove-InstallRuntimeContent -InstallRoot $InstallDir
    if (Enter-InstallLock -TimeoutSec 0) {{ throw 'purge replaced the live lock' }}
}} finally {{ $active.Dispose(); Exit-InstallLock }}
if (-not (Enter-InstallLock -TimeoutSec 0)) {{ throw 'released lock did not reacquire' }}
Exit-InstallLock
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in root.iterdir()) == [".install-stage", ".install.lock"]
    assert (stage / "active").read_text(encoding="utf-8") == "active"
    assert (outside / "sentinel").read_text(encoding="utf-8") == "outside"


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


@pytest.mark.parametrize("action", ["stamp", "uninstall", "purge"])
def test_powershell_installer_fails_closed_without_mutating(tmp_path: Path, action: str) -> None:
    pwsh = _pwsh()
    if pwsh is None:
        pytest.skip("pwsh not available")
    home = tmp_path / "home"
    home.mkdir()
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    sentinel = install_dir / "sentinel"
    sentinel.write_text("must survive contention", encoding="utf-8")
    runner = tmp_path / "invoke.ps1"
    runner.write_text(
        "param($Installer, $Action, $InstallRoot)\n"
        "function Get-ScheduledTask {}\n"
        "function Stop-ScheduledTask { throw 'unexpected service mutation' }\n"
        "function Unregister-ScheduledTask { throw 'unexpected service mutation' }\n"
        "if ($Action -eq 'purge') {\n"
        "    & $Installer -Action uninstall -InstallDir $InstallRoot -Purge\n"
        "} else { & $Installer -Action $Action -InstallDir $InstallRoot }\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    holder = _ps_holder(pwsh, install_dir / ".install.lock")
    try:
        result = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(runner), str(PS_SCRIPT), action, str(install_dir)],
            capture_output=True, text=True, timeout=120, env=_isolated_env(home),
            cwd=tmp_path,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
    finally:
        holder.kill()
        holder.wait()
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert LOCK_FAILURE in output
    assert sorted(p.name for p in install_dir.iterdir()) == [".install.lock", "sentinel"]
    assert sentinel.read_text(encoding="utf-8") == "must survive contention"
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


@pytest.mark.parametrize("action", ["stamp", "uninstall", "purge"])
@pytest.mark.parametrize("lock_kind", ["flock", "pid"])
def test_posix_installer_fails_closed_without_mutating(
    tmp_path: Path, action: str, lock_kind: str,
) -> None:
    bash = _posix_bash()
    if bash is None or (lock_kind == "flock" and shutil.which("flock") is None):
        pytest.skip(f"native POSIX bash with {lock_kind} unavailable")
    home = tmp_path / "home"
    home.mkdir()
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    fake_bin = home / "fake-bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    systemctl.chmod(0o755)
    env = _isolated_env(home)
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + str(home / "no-session-bus")
    holder = None
    lock_name = ".install.lock" if lock_kind == "flock" else ".install.lock.pid"
    if lock_kind == "flock":
        holder = _flock_holder(bash, install_dir / lock_name)
    else:
        os.symlink(str(os.getpid()), install_dir / lock_name)
        env["COPILOT_EXT_NO_FLOCK"] = "1"
    try:
        actions = ["uninstall", "--purge"] if action == "purge" else [action]
        result = subprocess.run(
            [bash, str(SH_SCRIPT), *actions, "--install-dir", str(install_dir)],
            capture_output=True, text=True, timeout=120, env=env,
            cwd=tmp_path,
        )
    finally:
        if holder is not None:
            holder.kill()
            holder.wait()
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert LOCK_FAILURE in output
    assert sorted(p.name for p in install_dir.iterdir()) == [lock_name]
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


def _wait_fixture(path: Path, processes: list[subprocess.Popen[str]]) -> None:
    deadline = time.monotonic() + 8
    while not path.exists():
        assert time.monotonic() < deadline, f"fixture phase not reached: {path.name}"
        assert all(proc.poll() is None for proc in processes), "fixture child exited before synchronization"
        time.sleep(0.005)


def test_posix_pid_fallback_empty_owner_interval_never_overlaps(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    prefix = (
        "set -euo pipefail\n_fail() { printf 'FAIL:%s\\n' \"$1\"; }\n"
        f"INSTALL_DIR='{install_dir}'\n" + _block(SH_SCRIPT) + "\n"
    )
    # Schedule real owner reads; retain the legacy shim for pre-repair reproduction.
    shim = """
_fixture_owner_probe() {
    local count=0 value
    [[ ! -f "$FIXTURE/count" ]] || count=$(cat "$FIXTURE/count")
    count=$((count + 1)); echo "$count" > "$FIXTURE/count"
    if [[ "$count" == 1 ]]; then
        touch "$FIXTURE/first-read"
        while [[ ! -f "$FIXTURE/resume-first" ]]; do sleep 0.005; done
    fi
    value=$("$@" 2>/dev/null || true)
    if [[ "$count" == 2 && -z "$value" ]]; then
        touch "$FIXTURE/empty-comparison"
        while [[ ! -f "$FIXTURE/resume-comparison" ]]; do sleep 0.005; done
    fi
    printf '%s\\n' "$value"
}
if declare -F _read_install_lock_owner >/dev/null; then
    eval "$(declare -f _read_install_lock_owner | sed '1s/_read_install_lock_owner/_fixture_original_owner/')"
    _read_install_lock_owner() { _fixture_owner_probe _fixture_original_owner "$@"; }
else
    readlink() { _fixture_owner_probe command readlink "$@"; }
fi
"""
    env = {**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1", "FIXTURE": str(tmp_path)}
    processes: list[subprocess.Popen[str]] = []

    def spawn(body: str, instrument: bool = False) -> subprocess.Popen[str]:
        proc = subprocess.Popen(
            [bash, "-c", prefix + (shim if instrument else "") + body],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        processes.append(proc)
        return proc

    def transaction(label: str) -> str:
        return (
            "_enter_install_lock 10 || exit 3\n"
            f'echo start >> "$FIXTURE/log"; touch "$FIXTURE/{label}-entered"\n'
            f'while [[ ! -f "$FIXTURE/release-{label}" ]]; do sleep 0.005; done\n'
            'echo end >> "$FIXTURE/log"\n_exit_install_lock\n'
        )

    try:
        original = spawn(
            '_enter_install_lock 10 || exit 3\ntouch "$FIXTURE/original-entered"\n'
            'while [[ ! -f "$FIXTURE/release-original" ]]; do sleep 0.005; done\n_exit_install_lock\n'
        )
        _wait_fixture(tmp_path / "original-entered", [original])
        checker = spawn(transaction("checker"), instrument=True)
        _wait_fixture(tmp_path / "first-read", [original, checker])
        (tmp_path / "release-original").touch()
        out, err = original.communicate(timeout=8)
        assert original.returncode == 0, out + err
        (tmp_path / "resume-first").touch()
        deadline = time.monotonic() + 8
        while not any((tmp_path / name).exists() for name in ("empty-comparison", "checker-entered")):
            assert time.monotonic() < deadline
            assert checker.poll() is None
            time.sleep(0.005)
        contender = spawn(transaction("contender"))
        if (tmp_path / "empty-comparison").exists():
            # The old code lets this holder enter while its empty comparison is paused.
            _wait_fixture(tmp_path / "contender-entered", [checker, contender])
            (tmp_path / "resume-comparison").touch()
        _wait_fixture(tmp_path / "checker-entered", [checker])
        (tmp_path / "release-checker").touch()
        out, err = checker.communicate(timeout=8)
        assert checker.returncode == 0, out + err
        _wait_fixture(tmp_path / "contender-entered", [contender])
        (tmp_path / "release-contender").touch()
        out, err = contender.communicate(timeout=8)
        assert contender.returncode == 0, out + err
        assert (tmp_path / "log").read_text().split() == ["start", "end"] * 2
        assert not os.path.lexists(install_dir / ".install.lock.pid")
        assert not os.path.lexists(install_dir / ".install.lock.pid.reap")
    finally:
        for name in ("release-original", "resume-first", "resume-comparison",
                     "release-checker", "release-contender"):
            (tmp_path / name).touch()
        for proc in processes:
            if proc.poll() is None:
                os.killpg(proc.pid, 9)
            proc.wait(timeout=5)


@pytest.mark.parametrize("leaf", [".install.lock.pid", ".install.lock.pid.reap"])
@pytest.mark.parametrize("shape", ["directory", "directory-symlink", "numeric-directory-symlink"])
def test_posix_pid_fallback_directory_nodes_never_admit_or_mutate(
    tmp_path: Path, leaf: str, shape: str,
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    node = install_dir / leaf
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("outside unchanged")
    if shape == "directory":
        node.mkdir()
        contents = node
    else:
        contents = outside
        target = str(outside)
        if shape == "numeric-directory-symlink":
            target = str(_dead_pid())
            contents = install_dir / target
            contents.mkdir()
        node.symlink_to(target, target_is_directory=True)
    (contents / "sentinel").write_text("unchanged")
    identity = node.lstat()
    target = os.readlink(node) if node.is_symlink() else None
    log = tmp_path / "log"
    script = (
        "set -euo pipefail\n_fail() { printf 'FAIL:%s\\n' \"$1\"; }\n"
        f"INSTALL_DIR='{install_dir}'\n" + _block(SH_SCRIPT) + "\n"
        "if _enter_install_lock 1; then\n"
        f" echo start >> '{log}'; sleep 0.2; echo end >> '{log}'\n"
        " _exit_install_lock; echo entered\nelse echo refused\nfi\n"
    )
    procs = [
        subprocess.Popen(
            [bash, "-c", script], env={**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        for _ in range(2)
    ]
    try:
        outputs = [proc.communicate(timeout=8) for proc in procs]
        assert all(proc.returncode == 0 for proc in procs), outputs
        assert [out.strip() for out, _ in outputs] == ["refused"] * 2, (
            outputs, log.read_text() if log.exists() else "no transactions"
        )
        assert not log.exists()
        assert (node.lstat().st_dev, node.lstat().st_ino) == (identity.st_dev, identity.st_ino)
        if target is not None:
            assert os.readlink(node) == target
        assert sorted(path.name for path in contents.iterdir()) == ["sentinel"]
        assert (contents / "sentinel").read_text() == "unchanged"
        assert sorted(path.name for path in outside.iterdir()) == ["sentinel"]
        assert (outside / "sentinel").read_text() == (
            "unchanged" if contents == outside else "outside unchanged"
        )
    finally:
        for proc in procs:
            if proc.poll() is None:
                os.killpg(proc.pid, 9)
            proc.wait(timeout=5)


@pytest.mark.parametrize("leaf", [".install.lock.pid", ".install.lock.pid.reap"])
def test_posix_pid_fallback_creation_race_does_not_follow_new_directory(tmp_path: Path, leaf: str) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    node = install_dir / leaf
    injected = tmp_path / "injected"
    shim = f"""
_inject_directory() {{
    if [[ "$1" == "{node}" && ! -f "{injected}" ]]; then
        mkdir "{node}"
        echo unchanged > "{node}/sentinel"
        touch "{injected}"
    fi
}}
ln() {{ _inject_directory "${{@: -1}}"; command ln "$@"; }}
link() {{ _inject_directory "${{@: -1}}"; command link "$@"; }}
"""
    result = _run_sh_block(
        bash, install_dir, shim + "\nif _enter_install_lock 1; then echo entered; else echo refused; fi",
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"
    assert injected.exists()
    assert node.is_dir() and not node.is_symlink()
    assert sorted(path.name for path in node.iterdir()) == ["sentinel"]
    assert (node / "sentinel").read_text().strip() == "unchanged"


@pytest.mark.parametrize("record", ["", "123\n", "copilot-extensions.install-lock-pid:v1:not-a-pid\n",
                                    "copilot-extensions.install-lock-pid:v1:123\nextra\n",
                                    "copilot-extensions.install-lock-pid:v1:" + "1" * 100 + "\n"])
def test_posix_pid_fallback_record_reader_refuses_unknown_content(tmp_path: Path, record: str) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    path = install_dir / ".install.lock.pid"
    path.write_text(record)
    result = _run_sh_block(
        bash, install_dir,
        f'if _read_install_lock_owner "{path}"; then echo trusted; else echo refused; fi',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"
    assert path.read_text() == record
    assert sorted(path.name for path in install_dir.iterdir()) == [".install.lock.pid"]


@pytest.mark.parametrize("leaf", [".install.lock.pid", ".install.lock.pid.reap"])
@pytest.mark.parametrize("shape", [
    "trailing-newline", "trailing-newlines", "trailing-cr", "trailing-crlf",
    "internal-newline", "internal-cr", "leading-newline", "leading-cr",
])
def test_posix_pid_fallback_legacy_target_never_admits_or_mutates(
    tmp_path: Path, leaf: str, shape: str,
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    path = install_dir / leaf
    owner = str(_dead_pid())
    target = {
        "trailing-newline": owner + "\n", "trailing-newlines": owner + "\n\n",
        "trailing-cr": owner + "\r", "trailing-crlf": owner + "\r\n",
        "internal-newline": owner[:1] + "\n" + owner[1:],
        "internal-cr": owner[:1] + "\r" + owner[1:],
        "leading-newline": "\n" + owner, "leading-cr": "\r" + owner,
    }[shape]
    path.symlink_to(target)
    identity = path.lstat()
    result = _run_sh_block(
        bash, install_dir,
        f'if _read_install_lock_owner "{path}"; then echo trusted; else echo untrusted; fi\n'
        "if _enter_install_lock 1; then echo entered; _exit_install_lock; else echo refused; fi",
        env={**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["untrusted", "refused"]
    assert os.readlink(path) == target
    assert (path.lstat().st_dev, path.lstat().st_ino) == (identity.st_dev, identity.st_ino)
    assert sorted(node.name for node in install_dir.iterdir()) == [leaf]


@pytest.mark.parametrize("suffix", [r"\n", r"\n\n", r"\r", r"\r\n"])
def test_posix_pid_fallback_legacy_target_never_authorizes_exit_cleanup(
    tmp_path: Path, suffix: str,
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    path = install_dir / ".install.lock.pid"
    result = _run_sh_block(
        bash, install_dir,
        "_enter_install_lock 5 || exit 3\n"
        f'rm -f "{path}"; ln -s "$$"$\'{suffix}\' "{path}"\n'
        '_exit_install_lock\necho "$$"',
        env={**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert os.readlink(path) == result.stdout.strip() + suffix.encode("ascii").decode("unicode_escape")
    assert sorted(node.name for node in install_dir.iterdir()) == [path.name]


@pytest.mark.parametrize(("digits", "trusted"), [(63, True), (64, False), (1024, False)])
def test_posix_pid_fallback_legacy_target_reader_enforces_byte_bound(
    tmp_path: Path, digits: int, trusted: bool,
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    path = install_dir / ".install.lock.pid"
    target = "1" * digits
    path.symlink_to(target)
    identity = path.lstat()
    result = _run_sh_block(
        bash, install_dir, f'if ! _read_install_lock_owner "{path}"; then echo refused; fi',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (target if trusted else "refused")
    assert os.readlink(path) == target
    assert (path.lstat().st_dev, path.lstat().st_ino) == (identity.st_dev, identity.st_ino)
    assert sorted(node.name for node in install_dir.iterdir()) == [path.name]


@pytest.mark.parametrize(("ending", "status"), [
    ("", 0), (r"\n\n", 0), (r"\0\n", 0), (r"\n", 1),
])
def test_posix_pid_fallback_legacy_reader_refuses_malformed_or_failed_utility_output(
    tmp_path: Path, ending: str, status: int,
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    path = install_dir / ".install.lock.pid"
    target = str(_dead_pid())
    path.symlink_to(target)
    identity = path.lstat()
    result = _run_sh_block(
        bash, install_dir,
        f"readlink() {{ printf '%s{ending}' '{target}'; return {status}; }}\n"
        f'if _read_install_lock_owner "{path}"; then echo trusted; else echo untrusted; fi\n'
        "if _enter_install_lock 1; then echo entered; _exit_install_lock; else echo refused; fi",
        env={**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["untrusted", "refused"]
    assert os.readlink(path) == target
    assert (path.lstat().st_dev, path.lstat().st_ino) == (identity.st_dev, identity.st_ino)
    assert sorted(node.name for node in install_dir.iterdir()) == [path.name]


@pytest.mark.parametrize("leaf", [".install.lock.pid", ".install.lock.pid.reap"])
@pytest.mark.parametrize("position", [
    "prefix-start", "prefix-body", "pid-start", "pid-body", "before-newline", "after-newline",
])
def test_posix_pid_fallback_nul_record_never_admits_or_mutates(
    tmp_path: Path, leaf: str, position: str,
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    path = install_dir / leaf
    prefix = b"copilot-extensions.install-lock-pid:v1:"
    record = prefix + str(_dead_pid()).encode("ascii") + b"\n"
    index = {
        "prefix-start": 0, "prefix-body": 12, "pid-start": len(prefix),
        "pid-body": len(prefix) + 1, "before-newline": len(record) - 1,
        "after-newline": len(record),
    }[position]
    record = record[:index] + b"\0" + record[index:]
    path.write_bytes(record)
    identity = path.stat()
    result = _run_sh_block(
        bash, install_dir,
        f'if _read_install_lock_owner "{path}"; then echo trusted; else echo untrusted; fi\n'
        "if _enter_install_lock 1; then echo entered; _exit_install_lock; else echo refused; fi",
        env={**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["untrusted", "refused"]
    assert path.read_bytes() == record
    assert (path.stat().st_dev, path.stat().st_ino) == (identity.st_dev, identity.st_ino)
    assert sorted(node.name for node in install_dir.iterdir()) == [leaf]


@pytest.mark.parametrize("after_newline", [False, True])
def test_posix_pid_fallback_nul_record_never_authorizes_exit_cleanup(
    tmp_path: Path, after_newline: bool,
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    path = install_dir / ".install.lock.pid"
    ending = r"\n\0" if after_newline else r"\0\n"
    result = _run_sh_block(
        bash, install_dir,
        "_enter_install_lock 1 || exit 3\n"
        f'printf "copilot-extensions.install-lock-pid:v1:%s{ending}" "$$" > "{path}"\n'
        '_exit_install_lock\necho "$$"',
        env={**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == (
        b"copilot-extensions.install-lock-pid:v1:" + result.stdout.strip().encode("ascii")
        + (b"\n\0" if after_newline else b"\0\n")
    )
    assert sorted(node.name for node in install_dir.iterdir()) == [path.name]


@pytest.mark.parametrize("live", [False, True])
def test_posix_pid_fallback_typed_receipt_preserves_live_and_reaps_dead_owner(
    tmp_path: Path, live: bool,
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    path = install_dir / ".install.lock.pid"
    owner = os.getpid() if live else _dead_pid()
    record = f"copilot-extensions.install-lock-pid:v1:{owner}\n".encode("ascii")
    path.write_bytes(record)
    result = _run_sh_block(
        bash, install_dir,
        "if _enter_install_lock 1; then echo entered; _exit_install_lock; else echo refused; fi",
        env={**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ("refused" if live else "entered")
    if live:
        assert path.read_bytes() == record
    else:
        assert not path.exists()
    assert sorted(node.name for node in install_dir.iterdir()) == ([path.name] if live else [])


@pytest.mark.parametrize(("size", "ending", "trusted"), [
    (64, b"\n", True), (65, b"\n", False), (63, b"", False), (64, b"\n\n", False),
])
def test_posix_pid_fallback_record_reader_enforces_byte_bound_and_newline(
    tmp_path: Path, size: int, ending: bytes, trusted: bool,
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    path = install_dir / ".install.lock.pid"
    prefix = b"copilot-extensions.install-lock-pid:v1:"
    owner = b"1" * (size - len(prefix) - len(ending))
    record = prefix + owner + ending
    path.write_bytes(record)
    result = _run_sh_block(
        bash, install_dir,
        f'if ! _read_install_lock_owner "{path}"; then echo refused; fi',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (owner.decode("ascii") if trusted else "refused")
    assert path.read_bytes() == record
    assert sorted(node.name for node in install_dir.iterdir()) == [path.name]


def test_posix_pid_fallback_record_snapshot_refuses_raced_fifo_without_opening(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    path = install_dir / ".install.lock.pid"
    body = f"""
_create_install_lock_owner "{path}" || exit 3
link() {{
    if [[ "$1" == "{path}" ]]; then
        command rm -f "{path}"
        mkfifo "{path}"
    fi
    command link "$@"
}}
if _read_install_lock_owner "{path}"; then echo trusted; else echo refused; fi
"""
    result = _run_sh_block(bash, install_dir, body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"
    assert path.is_fifo()
    assert sorted(path.name for path in install_dir.iterdir()) == [".install.lock.pid"]


def test_posix_pid_fallback_fresh_reacquisition_and_exit_ownership(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    link = tmp_path / "rt/.install.lock.pid"
    result = _run_sh_block(
        bash, link.parent,
        '_enter_install_lock 1 && _exit_install_lock && _enter_install_lock 1 || exit 3\n'
        f'rm -f "{link}"; ln -s "{os.getpid()}" "{link}"\n_exit_install_lock\necho ok',
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert os.readlink(link) == str(os.getpid())


@pytest.mark.parametrize("owner", ["", "not-a-pid"])
def test_posix_pid_fallback_unknown_owner_never_authorizes_deletion(tmp_path: Path, owner: str) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    link = install_dir / ".install.lock.pid"
    if owner:
        link.symlink_to(owner)
    else:
        link.write_text("")
    result = _run_sh_block(
        bash, install_dir, "if _enter_install_lock 1; then echo acquired; else echo refused; fi",
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"
    if owner:
        assert os.readlink(link) == owner
    else:
        assert link.read_text() == ""


@pytest.mark.parametrize("exit_code", [0, 7])
def test_posix_pid_fallback_exit_trap_releases_its_owner(tmp_path: Path, exit_code: int) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    result = _run_sh_block(
        bash, install_dir,
        f"_enter_install_lock 1 || exit 3\ntrap '_exit_install_lock' EXIT\nexit {exit_code}",
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == exit_code, result.stderr
    assert not os.path.lexists(install_dir / ".install.lock.pid")
    assert not os.path.lexists(install_dir / ".install.lock.pid.reap")


def test_posix_pid_fallback_mutex_release_failure_never_admits(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    result = _run_sh_block(
        bash, install_dir,
        'rm() { if [[ "${@: -1}" == *.reap ]]; then return 1; fi; command rm "$@"; }\n'
        "if _enter_install_lock 1; then echo acquired; else echo refused; fi",
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"
    assert not os.path.lexists(install_dir / ".install.lock.pid")
    assert os.path.lexists(install_dir / ".install.lock.pid.reap")


def test_posix_pid_fallback_dead_admission_mutex_blocks_fresh_acquisition(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    mutex = install_dir / ".install.lock.pid.reap"
    owner = str(_dead_pid())
    mutex.symlink_to(owner)
    result = _run_sh_block(
        bash, install_dir, "if _enter_install_lock 1; then echo acquired; else echo refused; fi",
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"
    assert os.readlink(mutex) == owner
    assert not os.path.lexists(install_dir / ".install.lock.pid")


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


def test_posix_pid_fallback_create_survives_concurrent_staging_dir_removal(tmp_path: Path) -> None:
    """A concurrent `uninstall --purge` can reclaim `_create_install_lock_owner`'s
    private staging directory the instant after its `link(1)` call already
    published the receipt -- that publication is a second, independent
    directory entry on the same inode, so it survives the staging directory's
    removal. Reporting the call as failed anyway (because best-effort cleanup
    of now-vanished scratch failed) would make every caller skip releasing the
    mutex it just admitted, stranding it until manual repair."""
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    link = install_dir / ".install.lock.pid.reap"
    result = _run_sh_block(
        bash, install_dir,
        # Simulate the purge: the instant link(1) itself succeeds, its
        # private staging directory is reclaimed out from under the caller.
        'link() { command link "$@" && rm -rf "$(dirname -- "$1")"; }\n'
        f'if _create_install_lock_owner "{link}"; then echo created; else echo failed; fi',
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "created"
    assert os.path.lexists(link)


def test_posix_pid_fallback_attempts_once_despite_clock_second_boundary(tmp_path: Path) -> None:
    """The deadline computation and the acquire loop's own `date +%s` call are
    two separate invocations; a short timeout (the suites' own 1s default) can
    straddle a wall-clock second boundary between them. A pre-check-only loop
    condition would then skip even an uncontended acquisition. Stub `date` to
    force exactly that boundary and confirm the first attempt still happens
    before the deadline is ever enforced."""
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    # The deadline calculation and the loop's `date +%s` run in separate
    # command-substitution subshells, so the call count must be tracked in a
    # file -- a plain shell variable resets to 0 in each new subshell and
    # never actually reaches the second scripted value.
    stub = (
        'rm -f "$INSTALL_DIR/.fixture-date-calls"\n'
        "date() {\n"
        "    local __n=0\n"
        '    [[ ! -f "$INSTALL_DIR/.fixture-date-calls" ]] || __n=$(cat "$INSTALL_DIR/.fixture-date-calls")\n'
        "    __n=$((__n + 1))\n"
        '    printf \'%s\' "$__n" > "$INSTALL_DIR/.fixture-date-calls"\n'
        '    if [[ "$1" == "+%s" && "$__n" -le 2 ]]; then\n'
        '        if [[ "$__n" -eq 1 ]]; then echo 1000; else echo 1001; fi\n'
        "        return 0\n"
        "    fi\n"
        '    command date "$@"\n'
        "}\n"
    )
    result = _run_sh_block(
        bash, install_dir,
        stub + "if _enter_install_lock 1; then echo acquired; else echo refused; fi",
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "acquired"

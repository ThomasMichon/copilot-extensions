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
PS_SCRIPT = PLUGIN / "scripts" / "init.ps1"
SH_SCRIPT = PLUGIN / "scripts" / "init.sh"
LOCK_START = "# === install-transaction lock (fail-closed) ==="
LOCK_END = "# === end install-transaction lock ==="
SELF_STAGE_END = "# === end install-contract:v4 self-stage ==="
SMOKE_END = "# === end install-contract:v4 smoke seam ==="
PS_FIRST_MUTATION = "if ($Action -eq 'stamp') {"
SH_FIRST_MUTATION = "if [[ \"$ACTION\" == \"stamp\" ]]; then"
LOCK_FAILURE = "Could not acquire the install transaction lock"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _block(path: Path) -> str:
    text = _text(path)
    start = text.index(LOCK_START)
    block = text[start : text.index(LOCK_END, start) + len(LOCK_END)]
    if path.suffix == ".sh" and '. "$SCRIPT_DIR/legacy-transaction.sh"' in block:
        return _text(path.with_name("legacy-transaction.sh"))
    return block


def _ps_cleanup() -> str:
    tail = _text(PS_SCRIPT).rsplit("\n} finally {\n", 1)[1]
    assert tail.endswith("\n}\n")
    return tail[:-len("\n}\n")] + "\n"


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
    acquire = (
        "(Enter-InstallLock)" if script.suffix == ".ps1" else "if ! _enter_install_lock; then"
    )

    assert text.count(LOCK_START) == 1
    assert text.index(SELF_STAGE_END) < text.index(SMOKE_END) < text.index(acquire)
    assert text.index(acquire) < text.index(first_mutation)
    profile_call = (
        "$profileJson = Invoke-Hidden -FilePath $pythonCmd"
        if script.suffix == ".ps1" else 'profile_result="$("$PYTHON_CMD"'
    )
    assert text.index(acquire) < text.index(profile_call)


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
    assert cleanup == "    Exit-InstallLock\n"
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


def test_powershell_lock_releases_when_profile_command_fails(tmp_path: Path) -> None:
    pwsh = _pwsh()
    if pwsh is None:
        pytest.skip("pwsh not available")
    result = _run_ps_block(pwsh, tmp_path / "rt", f"""
if (-not (Enter-InstallLock -TimeoutSec 1)) {{ throw 'lock-not-acquired' }}
try {{
    try {{
        & '{sys.executable}' -I -c 'raise SystemExit(23)'
        if ($LASTEXITCODE -ne 23) {{ throw 'wrong-profile-result' }}
        Write-Output "profile-held=$($null -ne $script:InstallLockHandle)"
        throw 'profile-provision-failure'
    }} finally {{
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
        "profile-held=True", "caught=profile-provision-failure",
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
    environment = _isolated_env(home)
    control = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", "exit 0"],
        capture_output=True, text=True, timeout=30, env=environment, cwd=tmp_path,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert control.returncode == 0, control.stdout + control.stderr
    local = home / ".local"

    def local_state() -> dict[str, bytes | None]:
        return {
            path.relative_to(local).as_posix(): path.read_bytes() if path.is_file() else None
            for path in local.rglob("*")
        }

    local_existed = local.exists()
    before = local_state()
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    holder = _ps_holder(pwsh, install_dir / ".install.lock")
    try:
        result = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(PS_SCRIPT), "-Action", "stamp", "-InstallDir", str(install_dir)],
            capture_output=True, text=True, timeout=120, env=environment,
            cwd=tmp_path,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        holder.kill()
        holder.wait()
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert LOCK_FAILURE in output
    assert sorted(p.name for p in install_dir.iterdir()) == [".install.lock"]
    assert local.exists() == local_existed and local_state() == before


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
    result = subprocess.run(
        [bash, "-c", script], capture_output=True, text=True, timeout=60,
        env=env if env is not None else dict(os.environ),
    )
    assert "command not found" not in result.stderr, result.stderr
    return result


def _flock_holder(bash: str, lock: Path) -> subprocess.Popen[str]:
    holder = subprocess.Popen(
        [bash, "-c", 'exec 8>>"$1"; flock 8; echo held; IFS= read -r release || :',
         "_", str(lock)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "held"
    return holder


def test_posix_flock_acquires_releases_and_reacquires(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None or shutil.which("flock") is None:
        pytest.skip("POSIX bash with flock not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    lock = install_dir / ".install.lock"
    lock.write_bytes(b"persistent contents")
    before = lock.stat().st_ino, lock.read_bytes()
    result = _run_sh_block(
        bash, install_dir,
        "_enter_install_lock 1 && _exit_install_lock && _enter_install_lock 1 && echo ok",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert (lock.stat().st_ino, lock.read_bytes()) == before


def test_posix_flock_times_out_while_held(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None or shutil.which("flock") is None:
        pytest.skip("POSIX bash with flock not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    holder = _flock_holder(bash, install_dir / ".install.lock")
    try:
        result = _run_sh_block(
            bash, install_dir,
            "if _enter_install_lock 1; then echo acquired; else echo refused; fi",
        )
    finally:
        holder.communicate("\n", timeout=5)
        assert holder.returncode == 0
    assert result.stdout.strip() == "refused", result.stderr


def test_posix_legacy_symlink_preserves_live_and_dead_claims(tmp_path: Path) -> None:
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
    assert live.returncode == 0, live.stderr
    assert live.stdout.strip() == "refused", live.stderr
    assert os.readlink(link) == str(os.getpid())

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    link.unlink()
    os.symlink(str(dead.pid), link)
    refused = _run_sh_block(bash, install_dir, probe, env=env)
    assert refused.returncode == 0, refused.stderr
    assert refused.stdout.strip() == "refused", refused.stderr
    assert os.readlink(link) == str(dead.pid)
    assert sorted(path.name for path in install_dir.iterdir()) == [link.name]


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
        holder.communicate("\n", timeout=5)
        assert holder.returncode == 0
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


def _wait_fixture(path: Path, processes: list[subprocess.Popen[str]]) -> None:
    deadline = time.monotonic() + 8
    while not path.exists():
        assert time.monotonic() < deadline, f"fixture phase not reached: {path.name}"
        assert all(proc.poll() is None for proc in processes), (
            "fixture child exited before synchronization"
        )
        time.sleep(0.005)


def test_posix_python_fd_waiter_rechecks_legacy_receipt_after_acquisition(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    prefix = (
        "set -euo pipefail\n_fail() { printf 'FAIL:%s\\n' \"$1\"; }\n"
        f"INSTALL_DIR='{install_dir}'\n" + _block(SH_SCRIPT) + "\n"
    )
    shim = """
eval "$(
    declare -f _python_install_fd_lock |
        sed '1s/_python_install_fd_lock/_fixture_fd_lock/'
)"
_python_install_fd_lock() {
    touch "$FIXTURE/waiting"
    _fixture_fd_lock "$@"
}
"""
    env = {**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1", "FIXTURE": str(tmp_path)}
    processes: list[subprocess.Popen[str]] = []

    def spawn(body: str, instrument: bool = False) -> subprocess.Popen[str]:
        proc = subprocess.Popen(
            [bash, "-c", prefix + (shim if instrument else "") + body],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        processes.append(proc)
        return proc

    try:
        original = spawn(
            '_enter_install_lock 10 || exit 3\ntouch "$FIXTURE/original-entered"\n'
            'while [[ ! -f "$FIXTURE/release-original" ]]; do sleep 0.005; done\n'
            '_exit_install_lock\n'
        )
        _wait_fixture(tmp_path / "original-entered", [original])
        lock = install_dir / ".install.lock"
        before = lock.stat().st_ino, lock.read_bytes()
        checker = spawn(
            'if _enter_install_lock 10; then echo entered; _exit_install_lock; exit 9; fi\n'
            '[[ ! -e /dev/fd/8 ]] || exit 8\necho refused\n', instrument=True,
        )
        _wait_fixture(tmp_path / "waiting", [original, checker])
        receipt = install_dir / ".install.lock.pid"
        receipt.write_bytes(b"unverified claimant appeared while waiting\n")
        identity = receipt.stat().st_ino, receipt.read_bytes()
        (tmp_path / "release-original").touch()
        out, err = original.communicate(timeout=8)
        assert original.returncode == 0, out + err
        out, err = checker.communicate(timeout=8)
        assert checker.returncode == 0, out + err
        assert out.strip() == "refused"
        assert (receipt.stat().st_ino, receipt.read_bytes()) == identity
        assert (lock.stat().st_ino, lock.read_bytes()) == before
        assert not os.path.lexists(install_dir / ".install.lock.pid.reap")
    finally:
        (tmp_path / "release-original").touch()
        for proc in processes:
            if proc.poll() is None:
                os.killpg(proc.pid, 9)
            proc.wait(timeout=5)


@pytest.mark.parametrize("leaf", [".install.lock.pid", ".install.lock.pid.reap"])
@pytest.mark.parametrize("shape", ["directory", "directory-symlink", "numeric-directory-symlink"])
def test_posix_legacy_directory_nodes_never_admit_or_mutate(
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
def test_posix_fd_post_acquisition_check_refuses_new_legacy_directory(
    tmp_path: Path, leaf: str,
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    node = install_dir / leaf
    injected = tmp_path / "injected"
    shim = f"""
eval "$(
    declare -f _legacy_install_receipts_clear |
        sed '1s/_legacy_install_receipts_clear/_fixture_clear/'
)"
_fixture_checks=0
_legacy_install_receipts_clear() {{
    _fixture_checks=$((_fixture_checks + 1))
    if [[ "$_fixture_checks" == 2 ]]; then
        mkdir "{node}"
        echo unchanged > "{node}/sentinel"
        touch "{injected}"
    fi
    _fixture_clear
}}
"""
    result = _run_sh_block(
        bash, install_dir,
        shim + "\nif _enter_install_lock 1; then echo entered; else echo refused; fi",
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"
    assert injected.exists()
    assert node.is_dir() and not node.is_symlink()
    assert sorted(path.name for path in node.iterdir()) == ["sentinel"]
    assert (node / "sentinel").read_text().strip() == "unchanged"
    assert sorted(path.name for path in install_dir.iterdir()) == [".install.lock", leaf]


@pytest.mark.parametrize("record", [
    "", "123\n", "copilot-extensions.install-lock-pid:v1:not-a-pid\n",
    "copilot-extensions.install-lock-pid:v1:123\nextra\n",
    "copilot-extensions.install-lock-pid:v1:" + "1" * 100 + "\n",
])
def test_posix_legacy_unknown_record_refuses_admission(tmp_path: Path, record: str) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    path = install_dir / ".install.lock.pid"
    path.write_text(record)
    result = _run_sh_block(
        bash, install_dir,
        'if _enter_install_lock 1; then echo entered; _exit_install_lock; else echo refused; fi',
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
def test_posix_legacy_malformed_symlink_never_admits_or_mutates(
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
        "if _enter_install_lock 1; then echo entered; _exit_install_lock; else echo refused; fi",
        env={**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["refused"]
    assert os.readlink(path) == target
    assert (path.lstat().st_dev, path.lstat().st_ino) == (identity.st_dev, identity.st_ino)
    assert sorted(node.name for node in install_dir.iterdir()) == [leaf]


@pytest.mark.parametrize("suffix", [r"\n", r"\n\n", r"\r", r"\r\n"])
def test_posix_fd_exit_preserves_legacy_malformed_symlink(
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
        f'ln -s "$$"$\'{suffix}\' "{path}"\n'
        '_exit_install_lock\necho "$$"',
        env={**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert os.readlink(path) == (
        result.stdout.strip() + suffix.encode("ascii").decode("unicode_escape")
    )
    assert sorted(node.name for node in install_dir.iterdir()) == [".install.lock", path.name]


@pytest.mark.parametrize("digits", [63, 64, 1024])
def test_posix_legacy_numeric_symlink_is_never_lock_authority(
    tmp_path: Path, digits: int,
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
        bash, install_dir,
        'if _enter_install_lock 1; then echo entered; _exit_install_lock; else echo refused; fi',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"
    assert os.readlink(path) == target
    assert (path.lstat().st_dev, path.lstat().st_ino) == (identity.st_dev, identity.st_ino)
    assert sorted(node.name for node in install_dir.iterdir()) == [path.name]


@pytest.mark.parametrize(("ending", "status"), [
    ("", 0), (r"\n\n", 0), (r"\0\n", 0), (r"\n", 1),
])
def test_posix_legacy_claim_never_relies_on_readlink_output(
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
        f"readlink() {{ touch '{tmp_path / 'reader-called'}';\n"
        f"printf '%s{ending}' '{target}'; return {status}; }}\n"
        "if _enter_install_lock 1; then echo entered; _exit_install_lock; else echo refused; fi",
        env={**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["refused"]
    assert not (tmp_path / "reader-called").exists()
    assert os.readlink(path) == target
    assert (path.lstat().st_dev, path.lstat().st_ino) == (identity.st_dev, identity.st_ino)
    assert sorted(node.name for node in install_dir.iterdir()) == [path.name]


@pytest.mark.parametrize("leaf", [".install.lock.pid", ".install.lock.pid.reap"])
@pytest.mark.parametrize("position", [
    "prefix-start", "prefix-body", "pid-start", "pid-body", "before-newline", "after-newline",
])
def test_posix_legacy_nul_record_never_admits_or_mutates(
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
        "if _enter_install_lock 1; then echo entered; _exit_install_lock; else echo refused; fi",
        env={**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["refused"]
    assert path.read_bytes() == record
    assert (path.stat().st_dev, path.stat().st_ino) == (identity.st_dev, identity.st_ino)
    assert sorted(node.name for node in install_dir.iterdir()) == [leaf]


@pytest.mark.parametrize("after_newline", [False, True])
def test_posix_fd_exit_preserves_legacy_nul_record(
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
    assert sorted(node.name for node in install_dir.iterdir()) == [".install.lock", path.name]


@pytest.mark.parametrize("live", [False, True])
def test_posix_legacy_typed_receipt_preserves_live_and_dead_claimants(
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
    assert result.stdout.strip() == "refused"
    assert path.read_bytes() == record
    assert sorted(node.name for node in install_dir.iterdir()) == [path.name]


@pytest.mark.parametrize(("size", "ending"), [
    (64, b"\n"), (65, b"\n"), (63, b""), (64, b"\n\n"),
])
def test_posix_legacy_record_size_and_newline_never_authorize_admission(
    tmp_path: Path, size: int, ending: bytes,
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
        'if _enter_install_lock 1; then echo entered; _exit_install_lock; else echo refused; fi',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"
    assert path.read_bytes() == record
    assert sorted(node.name for node in install_dir.iterdir()) == [path.name]


@pytest.mark.parametrize("leaf", [".install.lock.pid", ".install.lock.pid.reap"])
def test_posix_fd_post_acquisition_check_refuses_fifo_without_opening(
    tmp_path: Path, leaf: str,
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    path = install_dir / leaf
    body = f"""
eval "$(
    declare -f _python_install_fd_lock |
        sed '1s/_python_install_fd_lock/_fixture_fd_lock/'
)"
_python_install_fd_lock() {{
    mkfifo "{path}"
    _fixture_fd_lock "$@"
}}
if _enter_install_lock 1; then echo entered; _exit_install_lock; else echo refused; fi
[[ ! -e /dev/fd/8 ]] || exit 8
"""
    result = _run_sh_block(
        bash, install_dir, body, env={**_isolated_env(tmp_path), "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"
    assert path.is_fifo()
    assert sorted(path.name for path in install_dir.iterdir()) == [".install.lock", leaf]


def test_posix_python_fd_reacquisition_and_exit_preserve_new_legacy_claim(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    link = tmp_path / "rt/.install.lock.pid"
    result = _run_sh_block(
        bash, link.parent,
        '_enter_install_lock 1 && _exit_install_lock && _enter_install_lock 1 || exit 3\n'
        f'ln -s "{os.getpid()}" "{link}"\n_exit_install_lock\necho ok',
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert os.readlink(link) == str(os.getpid())
    assert sorted(path.name for path in link.parent.iterdir()) == [".install.lock", link.name]


@pytest.mark.parametrize("owner", ["", "not-a-pid"])
def test_posix_legacy_unknown_owner_never_authorizes_deletion(tmp_path: Path, owner: str) -> None:
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
def test_posix_python_fd_exit_trap_releases_same_inode(tmp_path: Path, exit_code: int) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    lock = install_dir / ".install.lock"
    lock.write_bytes(b"persistent contents")
    before = lock.stat().st_ino, lock.read_bytes()
    result = _run_sh_block(
        bash, install_dir,
        f"_enter_install_lock 1 || exit 3\ntrap '_exit_install_lock' EXIT\nexit {exit_code}",
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == exit_code, result.stderr
    assert not os.path.lexists(install_dir / ".install.lock.pid")
    assert not os.path.lexists(install_dir / ".install.lock.pid.reap")
    assert (lock.stat().st_ino, lock.read_bytes()) == before
    retry = _run_sh_block(
        bash, install_dir, "_enter_install_lock 1 && _exit_install_lock && echo acquired",
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert retry.returncode == 0 and retry.stdout.strip() == "acquired", retry.stderr
    assert (lock.stat().st_ino, lock.read_bytes()) == before


def test_posix_python_fd_release_never_unlinks_coordination_or_legacy_claims(
    tmp_path: Path,
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    lock = install_dir / ".install.lock"
    lock.write_bytes(b"persistent contents")
    before = lock.stat().st_ino, lock.read_bytes()
    result = _run_sh_block(
        bash, install_dir,
        '_enter_install_lock 1 || exit 3\n'
        'printf "unknown claimant\\n" > "$INSTALL_DIR/.install.lock.pid.reap"\n'
        'rm() { echo unexpected-unlink; return 9; }\n'
        '_exit_install_lock\n'
        '[[ ! -e /dev/fd/8 ]] || exit 8\n'
        "if _enter_install_lock 1; then echo acquired; else echo refused; fi",
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"
    assert not os.path.lexists(install_dir / ".install.lock.pid")
    assert (install_dir / ".install.lock.pid.reap").read_bytes() == b"unknown claimant\n"
    assert (lock.stat().st_ino, lock.read_bytes()) == before


def test_posix_legacy_dead_reaper_claim_blocks_fresh_acquisition(tmp_path: Path) -> None:
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


def test_posix_python_fd_serializes_simultaneous_transactions(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    lock = install_dir / ".install.lock"
    lock.write_bytes(b"persistent contents")
    before = lock.stat().st_ino, lock.read_bytes()
    log = tmp_path / "log"
    contenders = [_sh_contender(bash, install_dir, log) for _ in range(6)]
    for proc in contenders:
        _out, err = proc.communicate(timeout=90)
        assert proc.returncode == 0, err
    # Every transaction ran alone: no start ever follows another start.
    assert log.read_text().split() == ["start", "end"] * 6
    assert not os.path.lexists(install_dir / ".install.lock.pid")
    assert not os.path.lexists(install_dir / ".install.lock.pid.reap")
    assert (lock.stat().st_ino, lock.read_bytes()) == before


def test_posix_legacy_dead_owner_and_reaper_claims_refuse_all_contenders(tmp_path: Path) -> None:
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


def test_posix_legacy_unattributable_claim_is_not_reclaimed(tmp_path: Path) -> None:
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


def test_posix_python_fd_admission_does_not_depend_on_private_staging_links(
    tmp_path: Path,
) -> None:
    """The persistent descriptor protocol must not create receipt staging links."""
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    lock = install_dir / ".install.lock"
    lock.write_bytes(b"persistent contents")
    before = lock.stat().st_ino, lock.read_bytes()
    result = _run_sh_block(
        bash, install_dir,
        'link() { echo unexpected-link; return 9; }\n'
        'ln() { echo unexpected-ln; return 9; }\n'
        'mktemp() { echo unexpected-staging; return 9; }\n'
        '_enter_install_lock 1 && _exit_install_lock && echo acquired',
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "acquired"
    assert (lock.stat().st_ino, lock.read_bytes()) == before
    assert sorted(path.name for path in install_dir.iterdir()) == [lock.name]


def test_posix_python_fd_admission_does_not_depend_on_wall_clock_seconds(tmp_path: Path) -> None:
    """The fallback uses a monotonic deadline, not shell date observations."""
    bash = _posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")
    install_dir = tmp_path / "rt"
    install_dir.mkdir()
    stub = 'date() { touch "$INSTALL_DIR/.fixture-date-calls"; return 9; }\n'
    result = _run_sh_block(
        bash, install_dir,
        stub + "if _enter_install_lock 1; then echo acquired; else echo refused; fi",
        env={**os.environ, "COPILOT_EXT_NO_FLOCK": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "acquired"
    assert not (install_dir / ".fixture-date-calls").exists()
    assert sorted(path.name for path in install_dir.iterdir()) == [".install.lock"]

"""#5416 regression guard (POSIX side): a still-dirty runtime slot means
another process may genuinely own (or still be building into) $VENV_DIR right
now. `deploy_venv` must not race that writer by building into the slot
anyway -- it must retry `_versioned_slot_clean` briefly, then hard-fail
(return 1, loud `err`) rather than calling `uv venv ... --allow-existing`
against a slot it knows is still dirty.

This mirrors the Windows-side guard in
`test_installer_powershell51.py::test_slot_clean_reports_failure_instead_of_silently_downgrading_signed_venv`,
adapted for install.sh's bash implementation.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

_INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"

# A bare shutil.which("bash") can resolve to a Windows App Execution Alias
# stub or the classic `C:\Windows\System32\bash.exe` WSL launcher, which
# invoke an actual WSL distro (a different filesystem namespace) rather than
# running this script in the environment under test -- see
# test_installer_pipefail.py for the full rationale. Prefer the real Git
# Bash location when present; otherwise filter both known WSL-launcher
# locations out of PATH before falling back to shutil.which.
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

# The no-`flock` fallback delegates to Python's `fcntl.flock` -- a POSIX-only
# stdlib module absent from native Windows Python (even under Git Bash,
# whose bash is POSIX but whose `python`/`python3` on PATH is typically a
# native Windows build). This fallback is only ever exercised in PRODUCTION
# on a genuine POSIX host lacking the `flock` CLI (e.g. stock macOS) --
# never on Windows, which uses install.ps1's own signed/uv venv path
# entirely and never touches install.sh at all. Gate the real behavioral
# test accordingly rather than skipping on `_BASH is None` alone.
_HAS_FCNTL = importlib.util.find_spec("fcntl") is not None


def _bash_path(p: Path) -> str:
    """A path suitable for embedding in a colon-delimited `PATH` string
    under Git Bash: a literal `C:/Users/...` (plain `.as_posix()`) breaks
    PATH-splitting because bash treats the drive letter's `:` as a PATH
    separator too. MSYS2's own `/c/Users/...` form has no such colon."""
    posix = p.as_posix()
    if len(posix) >= 2 and posix[1] == ":" and posix[0].isalpha():
        return f"/{posix[0].lower()}{posix[2:]}"
    return posix


def _function_body(text: str, name: str) -> str:
    """Extract a top-level `name() { ... }` function body (first match),
    assuming the closing brace is on its own line (this file's convention)."""
    start = text.index(f"\n{name}() {{\n")
    end = text.index("\n}\n", start)
    return text[start:end]


def test_versioned_slot_clean_propagates_real_exit_code():
    """`_versioned_slot_clean` must report the underlying `versioned_runtime.py
    slot` call's real exit code -- never force-succeed via a trailing
    `|| true`, which silently told every caller the slot was always clean."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    body = _function_body(text, "_versioned_slot_clean")

    assert "clean-incomplete 2>&1 | sed" in body
    assert 'return "${PIPESTATUS[0]}"' in body
    # The old unconditional-success bug: a trailing `|| true` on the pipeline
    # that fed `sed` made the function's own exit code always 0.
    assert "| sed 's/^/  ...    /' || true" not in body


def test_versioned_slot_clean_fails_closed_for_an_existing_slot_without_bootstrap_python():
    """When no bootstrap python can be resolved at all (e.g. a uv-only
    machine with no system python yet and no prior `venv` link to borrow
    from), `_versioned_slot_clean` must fail CLOSED (return 1, "can't
    verify") for an EXISTING $VENV_DIR -- never silently report "clean"
    and let the caller proceed straight to `uv venv --allow-existing` over
    a possibly-abandoned, never-actually-validated directory. An ABSENT
    slot needs no validation at all and is trivially, correctly clean."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    body = _function_body(text, "_versioned_slot_clean")

    assert 'py="$(_bootstrap_python exclude-venv-dir)"' in body
    no_py_branch = body.split('if [[ -z "$py" ]]; then', 1)[1].split(
        "\n    fi\n", 1
    )[0]
    assert '[[ -e "$VENV_DIR" ]] && return 1' in no_py_branch
    assert "return 0" in no_py_branch


def test_bootstrap_python_excludes_the_target_slot_even_when_link_dir_resolves_into_it():
    """`_versioned_slot_clean` inspects whether a live process is running
    FROM the target slot -- using that slot's OWN interpreter to run the
    census would make the helper process itself show up as such a
    process, permanently self-reporting an incomplete slot with a stale
    python as still in use on every retry. `LINK_DIR` (the `.venv` symlink)
    is not guaranteed to be a different directory from `VENV_DIR` -- it can
    physically resolve into the target slot mid-migration even when the
    two variables hold different literal strings -- so `exclude-venv-dir`
    must compare REAL (symlink-resolved) paths, not the variable strings."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    body = _function_body(text, "_bootstrap_python")

    assert 'exclude_venv_dir="${1:-}"' in body
    assert 'link_real="$(cd "$LINK_DIR" 2>/dev/null && pwd -P)"' in body
    assert 'venv_real="$(cd "$VENV_DIR" 2>/dev/null && pwd -P)"' in body
    assert '"$link_real" == "$venv_real"' in body

    # The lease's own resident helper must also be excluded -- it runs an
    # actual interpreter and would show up in the very census it's trying
    # to avoid contaminating if resolved from inside the slot.
    fallback_body = _function_body(
        text, "_acquire_versioned_slot_lease_python_fallback"
    )
    assert 'py="$(_bootstrap_python exclude-venv-dir)"' in fallback_body


@pytest.mark.skipif(_BASH is None, reason="bash is unavailable")
def test_bootstrap_python_exclude_venv_dir_behavioral(tmp_path: Path):
    """Behavioral (not just textual) regression guard: with `LINK_DIR`
    SYMLINKED into `VENV_DIR` (the exact mid-migration scenario a plain
    string comparison of the two variables wouldn't catch),
    `_bootstrap_python exclude-venv-dir` must never return the slot's own
    interpreter -- it must fall through to `python3`/`python` on PATH."""
    venv_dir = tmp_path / "versions" / "1.0.0"
    (venv_dir / "bin").mkdir(parents=True)
    target_python = venv_dir / "bin" / "python"
    target_python.write_text("#!/bin/sh\necho target-slot-python\n")
    target_python.chmod(0o755)

    link_dir = tmp_path / ".venv"
    try:
        link_dir.symlink_to(venv_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"cannot create symlinks in this environment: {exc}")

    fallback_bin = tmp_path / "fallback-bin"
    fallback_bin.mkdir()
    fallback_python = fallback_bin / "python3"
    fallback_python.write_text("#!/bin/sh\necho fallback-python\n")
    fallback_python.chmod(0o755)

    text = _INSTALL_SH.read_text(encoding="utf-8")
    fn_body = _function_body(text, "_bootstrap_python")

    harness = f"""
set -uo pipefail
LINK_DIR="{link_dir.as_posix()}"
VENV_DIR="{venv_dir.as_posix()}"
PATH="{_bash_path(fallback_bin)}:$PATH"
{fn_body}
}}
_bootstrap_python exclude-venv-dir
"""
    with tempfile.TemporaryDirectory() as td:
        harness_path = Path(td) / "harness.sh"
        harness_path.write_text(harness, encoding="utf-8")
        r = subprocess.run(
            [_BASH, str(harness_path)],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, f"harness failed: {r.stdout} {r.stderr}"
        result = r.stdout.strip()
        assert result == _bash_path(fallback_python), (
            "_bootstrap_python exclude-venv-dir must never select the "
            f"target slot's own interpreter; got {result!r}"
        )


@pytest.mark.skipif(_BASH is None, reason="bash is unavailable")
def test_bootstrap_python_exclude_venv_dir_filters_the_path_fallback_too(
    tmp_path: Path,
):
    """`exclude-venv-dir` must reject the `python3`/`python` PATH fallback
    too, not just the explicit `$LINK_DIR/bin/python` candidate: if an
    installer is launched with the target venv's own `bin/` on PATH (e.g.
    an activated venv), `command -v python3` would otherwise resolve
    straight back into `$VENV_DIR` and defeat the whole exclusion."""
    venv_dir = tmp_path / "versions" / "1.0.0"
    (venv_dir / "bin").mkdir(parents=True)
    excluded_python = venv_dir / "bin" / "python3"
    excluded_python.write_text("#!/bin/sh\necho excluded\n")
    excluded_python.chmod(0o755)

    fallback_bin = tmp_path / "fallback-bin"
    fallback_bin.mkdir()
    # Named "python" (not "python3"): `command -v NAME` only ever returns
    # the FIRST PATH match for that exact name -- once the loop's "python3"
    # iteration finds and excludes the venv's own copy, it moves on to try
    # the NEXT candidate NAME ("python"), not a second PATH match for
    # "python3". This fallback deliberately occupies that second slot.
    fallback_python = fallback_bin / "python"
    fallback_python.write_text("#!/bin/sh\necho fallback-python\n")
    fallback_python.chmod(0o755)

    link_dir = tmp_path / "nonexistent-link"  # no bin/python here at all

    text = _INSTALL_SH.read_text(encoding="utf-8")
    fn_body = _function_body(text, "_bootstrap_python")

    harness = f"""
set -uo pipefail
LINK_DIR="{link_dir.as_posix()}"
VENV_DIR="{venv_dir.as_posix()}"
PATH="{_bash_path(venv_dir)}/bin:{_bash_path(fallback_bin)}:$PATH"
{fn_body}
}}
_bootstrap_python exclude-venv-dir
"""
    with tempfile.TemporaryDirectory() as td:
        harness_path = Path(td) / "harness.sh"
        harness_path.write_text(harness, encoding="utf-8")
        r = subprocess.run(
            [_BASH, str(harness_path)],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, f"harness failed: {r.stdout} {r.stderr}"
        result = r.stdout.strip()
        assert result == _bash_path(fallback_python), (
            "_bootstrap_python exclude-venv-dir must reject a PATH "
            f"fallback resolving into $VENV_DIR; got {result!r}"
        )


def test_deploy_venv_retries_then_hard_fails_on_a_dirty_slot():
    """`deploy_venv` must capture `_versioned_slot_clean`'s result, retry
    briefly on failure, and refuse to call `uv venv` at all when the slot is
    still dirty after retries -- never silently build into a possibly-live,
    concurrently-written slot."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    body = _function_body(text, "deploy_venv")

    assert "_versioned_slot_clean && slot_clean=1" in body
    assert 'if [[ "$slot_clean" -ne 1 ]]; then' in body
    assert "for _i in 1 2 3; do" in body

    # The hard-fail branch must precede the `uv venv` call textually, and
    # must return non-zero without ever invoking uv.
    err_idx = body.index("still in use after retries")
    uv_idx = body.index("uv venv")
    assert err_idx < uv_idx, (
        "deploy_venv must check slot cleanliness and fail before attempting "
        "any uv venv build"
    )
    fail_branch = body[err_idx:uv_idx]
    assert "return 1" in fail_branch


def test_deploy_venv_acquires_exclusive_build_lease_before_slot_clean():
    """A slot-clean liveness check alone is check-then-act -- two concurrent
    installer invocations could both observe a clean slot (neither has
    started its external build yet) and then both build into it (#5439).
    `deploy_venv` must acquire an OS-level exclusive build lease FIRST
    (before even attempting slot-clean), fail immediately if another live
    process already holds it, and `_versioned_activate` must release that
    lease afterward regardless of outcome."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    deploy_body = _function_body(text, "deploy_venv")
    activate_wrapper = _function_body(text, "_versioned_activate")

    lease_idx = deploy_body.index("_acquire_versioned_slot_lease")
    clean_idx = deploy_body.index("_versioned_slot_clean")
    assert lease_idx < clean_idx, (
        "the exclusive build lease must be acquired before the slot-clean "
        "check, not after"
    )
    assert "if ! _acquire_versioned_slot_lease; then" in deploy_body
    lease_fail_branch = deploy_body.split(
        "if ! _acquire_versioned_slot_lease; then", 1
    )[1][:1400]
    assert "return 1" in lease_fail_branch

    # The wrapper must release the lease regardless of how the inner
    # activation call returns.
    assert "_versioned_activate_inner" in activate_wrapper
    assert "_release_versioned_slot_lease" in activate_wrapper


def test_versioned_slot_lease_uses_flock_held_for_process_lifetime():
    """The lease primitive must be an OS-level `flock` held on an open fd
    (auto-released by the kernel on crash/exit -- never a PID-recorded
    marker file requiring staleness detection) on platforms that have it,
    layered on top of the universal mkdir gate as a crash-safety
    strengthening. Must use a literal fd number: bash's dynamic `{fd}`
    allocation needs bash 4.1+, and stock macOS -- a platform this script
    must still PARSE on even when `flock` isn't the path taken -- ships
    bash 3.2."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    acquire_body = _function_body(text, "_acquire_versioned_slot_lease")
    release_body = _function_body(text, "_release_versioned_slot_lease")

    assert "command -v flock" in acquire_body
    assert "{_VERSIONED_SLOT_LEASE_FD}" not in acquire_body, (
        "dynamic fd allocation ({fd}) needs bash 4.1+ and fails to parse "
        "under stock macOS's bash 3.2 -- use a literal fd number instead"
    )
    assert 'if { exec 8>"$lease_path"; } 2>/dev/null && flock -n 8 2>/dev/null; then' in acquire_body, (
        "a bare `exec 8>...` with no command makes `2>/dev/null` attached "
        "directly to it PERMANENT for the rest of the shell (silently "
        "nulling all subsequent stderr) -- it must be wrapped in a "
        "`{ ...; }` group so the stderr suppression is scoped to just "
        "this exec's own potential failure message"
    )
    assert "{_VERSIONED_SLOT_LEASE_FD}" not in release_body
    assert "exec 8>&-" in release_body


def test_versioned_slot_lease_falls_back_to_mkdir_when_neither_flock_nor_python_exist():
    """A brand-new machine with neither `flock` NOR any bootstrap python
    yet (uv itself can provision a Python with no system interpreter
    present at all -- that's the whole point of `uv venv`) is NOT a case
    where serialization can simply be skipped: this plugin's own first-use
    binstub (bin/agent-worktrees) explicitly permits running `install.sh
    provision` CONCURRENTLY in exactly this no-flock bootstrap scenario, so
    two simultaneous first installs are a real, supported race here. The
    mkdir-based universal gate is acquired FIRST, unconditionally, in every
    case -- never skipped when flock/python happen to be unavailable --
    `mkdir` is POSIX-atomic and needs neither flock nor python."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    acquire_body = _function_body(text, "_acquire_versioned_slot_lease")
    mkdir_fallback_body = _function_body(
        text, "_acquire_versioned_slot_lease_mkdir_fallback"
    )

    # The universal gate must be acquired before even checking whether
    # flock/python are available, and the overall acquire must fail
    # whenever the gate itself fails -- never fall through to "proceed
    # unlocked".
    gate_idx = acquire_body.index("_acquire_versioned_slot_lease_mkdir_fallback")
    flock_check_idx = acquire_body.index("command -v flock")
    assert gate_idx < flock_check_idx, (
        "the universal mkdir gate must be acquired before even checking "
        "flock availability"
    )
    assert "if ! _acquire_versioned_slot_lease_mkdir_fallback" in acquire_body
    gate_fail_branch = acquire_body.split(
        "if ! _acquire_versioned_slot_lease_mkdir_fallback", 1
    )[1][:100]
    assert "return 1" in gate_fail_branch

    # The mkdir fallback itself: atomic acquire, bounded retry, and a
    # single flat (not nested/unbounded) staleness check.
    assert 'if mkdir "$lock_dir" 2>/dev/null; then' in mkdir_fallback_body
    assert "kill -0 \"$holder_pid\"" in mkdir_fallback_body
    # The stale-reclaim removal must be recursive: the lock dir holds a
    # "pid" marker file, so a plain `rmdir` silently no-ops on it (non-empty
    # directory) instead of actually clearing a stale lock.
    assert 'rm -rf "$lock_dir"' in mkdir_fallback_body
    assert 'rmdir "$lock_dir"' not in mkdir_fallback_body, (
        "rmdir silently no-ops on the lock dir (it's non-empty -- it holds "
        "the pid marker file), so a stale lock would never actually clear"
    )


def test_versioned_slot_lease_shares_one_gate_regardless_of_flock_or_python_visibility():
    """Two concurrent processes can have DIFFERENT flock/python
    availability -- e.g. this plugin's own full launcher's richer PATH vs.
    its first-use binstub's restricted one -- so picking a lock primitive
    PER-PROCESS based on local availability would let each side acquire a
    different, non-communicating lock and both build the same slot. Every
    acquisition path must therefore share exactly ONE gate
    (the mkdir-based one): flock/python are layered on top of an ALREADY-
    HELD gate purely as an optional crash-safety strengthening, and a
    failure to strengthen must never fail the overall acquisition (the
    gate alone is already sufficient exclusivity) or be misreported as
    contention."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    acquire_body = _function_body(text, "_acquire_versioned_slot_lease")

    assert "if ! _acquire_versioned_slot_lease_mkdir_fallback" in acquire_body
    strengthen_body = acquire_body.split(
        "if ! _acquire_versioned_slot_lease_mkdir_fallback", 1
    )[1]
    # Nothing in the strengthening section may itself `return 1` -- a
    # failure to strengthen is not a failure to acquire.
    assert "return 1" not in strengthen_body.split("if command -v flock", 1)[1], (
        "the flock/python strengthening section must never fail the "
        "overall acquisition -- the universal gate is already sufficient"
    )
    assert (
        '_acquire_versioned_slot_lease_python_fallback "$lease_path" >/dev/null 2>&1 || true'
        in acquire_body
    )


@pytest.mark.skipif(_BASH is None, reason="bash is unavailable")
def test_versioned_slot_lease_mkdir_fallback_behavioral(tmp_path: Path):
    """Behavioral (not just textual) regression guard: a concurrent second
    acquire must be refused while the first holds the mkdir-based lock, a
    later acquire must succeed once released, AND a lock abandoned by a
    dead process (stale pid marker) must be reclaimed rather than
    deadlocking forever -- proving the bounded retry/reclaim loop actually
    works, not just that its source text looks right."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    acquire_fn = _function_body(
        text, "_acquire_versioned_slot_lease_mkdir_fallback"
    )
    release_fn = _function_body(
        text, "_release_versioned_slot_lease_mkdir_fallback"
    )

    harness = f"""
set -uo pipefail
_VERSIONED_SLOT_LEASE_MKDIR_DIR=""
_VERSIONED_SLOT_LEASE_FAILURE_REASON=""
{acquire_fn}
}}
{release_fn}
}}

lock_file="$1"

echo "--- first acquire ---"
_acquire_versioned_slot_lease_mkdir_fallback "$lock_file" && echo "FIRST=OK" || echo "FIRST=FAIL"

(
    _VERSIONED_SLOT_LEASE_MKDIR_DIR=""
    if _acquire_versioned_slot_lease_mkdir_fallback "$lock_file"; then
        echo "SECOND=BUG"
    else
        echo "SECOND=REFUSED"
    fi
)

_release_versioned_slot_lease_mkdir_fallback

(
    _VERSIONED_SLOT_LEASE_MKDIR_DIR=""
    if _acquire_versioned_slot_lease_mkdir_fallback "$lock_file"; then
        echo "THIRD=OK"
        _release_versioned_slot_lease_mkdir_fallback
    else
        echo "THIRD=FAIL"
    fi
)

echo "--- stale lock (dead holder pid) ---"
rm -rf "${{lock_file}}.d"
mkdir "${{lock_file}}.d"
( : ) &
dead_pid=$!
wait "$dead_pid"
printf '%s' "$dead_pid" > "${{lock_file}}.d/pid"
(
    _VERSIONED_SLOT_LEASE_MKDIR_DIR=""
    if _acquire_versioned_slot_lease_mkdir_fallback "$lock_file"; then
        echo "RECLAIM=OK"
        _release_versioned_slot_lease_mkdir_fallback
    else
        echo "RECLAIM=FAIL"
    fi
)
"""
    with tempfile.TemporaryDirectory() as td:
        harness_path = Path(td) / "harness.sh"
        harness_path.write_text(harness, encoding="utf-8")
        lock_file = str(Path(td) / "lease.lock")
        r = subprocess.run(
            [_BASH, str(harness_path), lock_file],
            capture_output=True, text=True, timeout=60,
        )
        assert r.returncode == 0, f"harness failed: {r.stdout} {r.stderr}"
        events = [line for line in r.stdout.splitlines() if "=" in line]
        assert events == [
            "FIRST=OK", "SECOND=REFUSED", "THIRD=OK", "RECLAIM=OK",
        ], events


def test_versioned_slot_lease_distinguishes_contention_from_a_persistent_failure():
    """Hardcoding "another process is building this slot" for EVERY
    acquisition failure would hide a genuinely persistent lease-machinery
    failure (lease file/FIFOs couldn't be created, the no-flock helper
    didn't respond, ...) behind a message that just tells an operator to
    retry -- `_VERSIONED_SLOT_LEASE_FAILURE_REASON` must distinguish the
    two so `deploy_venv`'s error message can too."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    fallback_body = _function_body(
        text, "_acquire_versioned_slot_lease_python_fallback"
    )
    mkdir_fallback_body = _function_body(
        text, "_acquire_versioned_slot_lease_mkdir_fallback"
    )
    deploy_body = _function_body(text, "deploy_venv")

    assert '_VERSIONED_SLOT_LEASE_FAILURE_REASON="contention"' in mkdir_fallback_body
    assert '_VERSIONED_SLOT_LEASE_FAILURE_REASON="could not create the lease lock directory' in mkdir_fallback_body
    for phrase in (
        "could not create a temp dir",
        "could not create the lease fallback's FIFOs",
        "could not open the lease status FIFO",
        "could not open the lease keep-alive FIFO",
    ):
        assert phrase in fallback_body, f"missing failure-reason text: {phrase!r}"
    assert '_VERSIONED_SLOT_LEASE_FAILURE_REASON="contention"' in fallback_body
    assert "did not respond" in fallback_body

    assert '"$_VERSIONED_SLOT_LEASE_FAILURE_REASON" != "contention"' in deploy_body
    assert "Could not acquire the build lease" in deploy_body
    assert "Another process is already building this runtime slot" in deploy_body


def test_versioned_slot_lease_python_fallback_bounds_the_status_read_even_if_the_helper_never_starts():
    """The `-t 10` timeout on the status read must cover the ENTIRE wait,
    including the FIFO's own open -- not just the `read` builtin after it.
    A plain `read -t 10 line <"$out_fifo"` performs a blocking read-ONLY
    open of the FIFO as part of setting up that redirection, before `read`
    or its timeout ever starts: if the helper never reaches the point of
    opening its own write end (e.g. python itself fails to start before
    `import fcntl`/`open(...)` runs), that open blocks forever and the
    timeout never gets a chance to fire. Opening a read-WRITE fd on the
    same FIFO first (which never blocks, since it isn't waiting for a
    writer) and reading from that already-open fd is what actually bounds
    the wait."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    fallback_body = _function_body(
        text, "_acquire_versioned_slot_lease_python_fallback"
    )

    assert 'exec 7<>"$out_fifo"' in fallback_body
    read_idx = fallback_body.index("IFS= read -r -t 10")
    open_idx = fallback_body.index('exec 7<>"$out_fifo"')
    assert open_idx < read_idx, (
        "the read-write fd on out_fifo must be opened BEFORE the helper "
        "is launched, so the subsequent bounded read never performs its "
        "own blocking read-only open"
    )
    assert "read -r -t 10 -u 7 line" in fallback_body
    assert '<"$out_fifo"' not in fallback_body.split("IFS= read -r -t 10", 1)[1][:30]


def test_versioned_slot_lease_python_fallback_delegates_to_real_fcntl_flock():
    """A hand-rolled dotlock protocol (create-if-absent, detect-and-reclaim
    stale owners, ...) is an unbounded chain of narrowing TOCTOU windows --
    every userspace "is it safe to reclaim?" check is itself a second
    check-then-act race one level down, which is exactly the class of
    problem flock/fcntl exist to solve. When the `flock` CLI is unavailable
    (e.g. stock macOS), the fallback must delegate to Python's
    `fcntl.flock` instead -- the SAME real kernel advisory lock the
    primary path uses, via a resident helper process whose lifetime holds
    the lock (never a separate marker file requiring explicit,
    crash-unsafe cleanup) -- rather than reinventing the primitive in
    shell. Python is already a hard dependency here
    (versioned_runtime.py), so this introduces no new dependency."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    acquire_body = _function_body(text, "_acquire_versioned_slot_lease")
    fallback_body = _function_body(
        text, "_acquire_versioned_slot_lease_python_fallback"
    )
    release_body = _function_body(text, "_release_versioned_slot_lease")

    assert "if command -v flock >/dev/null 2>&1; then" in acquire_body
    no_flock_branch = acquire_body.split(
        "if command -v flock >/dev/null 2>&1; then", 1
    )[1][:1300]
    assert "else" in no_flock_branch
    assert "_acquire_versioned_slot_lease_python_fallback" in no_flock_branch.split(
        "else", 1
    )[1]

    # A failed (non-blocking) flock attempt must propagate as a real
    # failure, never be swallowed into a false "acquired".
    assert "fcntl.flock" in fallback_body
    assert "LOCK_EX | fcntl.LOCK_NB" in fallback_body
    assert "sys.exit(1)" in fallback_body
    # The resident helper must hold the lock for exactly its own lifetime
    # (blocking until its stdin is closed), not acquire-then-exit -- an
    # exited helper's fd closing is what releases the kernel lock. A plain
    # background job redirected from `/dev/null` would hand the helper an
    # immediate EOF (instant release); a pair of FIFOs plus a literal fd
    # number (bash 3.2 has neither `coproc` -- a 4.0+ reserved word -- nor
    # dynamic `{fd}` allocation -- 4.1+) is what keeps the write end open
    # for the caller's lifetime on stock macOS, the one platform this
    # fallback exists for.
    assert "mkfifo" in fallback_body
    assert "sys.stdin.read()" in fallback_body
    assert "_VERSIONED_SLOT_LEASE_PY_PID" in fallback_body
    assert "_VERSIONED_SLOT_LEASE_PY_STDIN_FD" in fallback_body

    # Release must close bash's held write-end of the helper's stdin pipe
    # (letting the helper see EOF, exit, and have the kernel release its
    # flock automatically) -- never just `kill` the helper directly, which
    # would race the helper's own in-flight flock acquisition/teardown.
    assert 'eval "exec ${_VERSIONED_SLOT_LEASE_PY_STDIN_FD}>&-"' in release_body
    assert 'wait "$_VERSIONED_SLOT_LEASE_PY_PID"' in release_body


@pytest.mark.skipif(
    _BASH is None or not _HAS_FCNTL,
    reason="bash or a POSIX (fcntl-capable) python is unavailable -- this "
    "fallback is production-reachable only on a genuine POSIX host",
)
def test_versioned_slot_lease_python_fallback_enforces_real_cross_process_exclusion():
    """Behavioral (not just textual) regression guard: a second, genuinely
    independent attempt must be refused while the first resident helper
    holds the real OS-level flock, and a later attempt must succeed once
    the first is released -- proving this is backed by the kernel's own
    advisory lock, not a textual contract."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    fallback_fn = _function_body(
        text, "_acquire_versioned_slot_lease_python_fallback"
    )

    harness = f"""
set -uo pipefail
_bootstrap_python() {{ command -v "{sys.executable}"; }}
_VERSIONED_SLOT_LEASE_PY_PID=""
_VERSIONED_SLOT_LEASE_PY_STDIN_FD=""
{fallback_fn}
}}
_release_versioned_slot_lease_python_fallback() {{
    if [[ -n "$_VERSIONED_SLOT_LEASE_PY_PID" ]]; then
        if [[ -n "$_VERSIONED_SLOT_LEASE_PY_STDIN_FD" ]]; then
            eval "exec ${{_VERSIONED_SLOT_LEASE_PY_STDIN_FD}}>&-" 2>/dev/null || true
            _VERSIONED_SLOT_LEASE_PY_STDIN_FD=""
        fi
        wait "$_VERSIONED_SLOT_LEASE_PY_PID" 2>/dev/null || true
        _VERSIONED_SLOT_LEASE_PY_PID=""
    fi
}}

lock_file="$1"
out_file="$2"

if _acquire_versioned_slot_lease_python_fallback "$lock_file"; then
    echo "FIRST=OK" >> "$out_file"
else
    echo "FIRST=FAIL" >> "$out_file"
fi

(
    _VERSIONED_SLOT_LEASE_PY_PID=""
    if _acquire_versioned_slot_lease_python_fallback "$lock_file"; then
        echo "SECOND=OK" >> "$out_file"
    else
        echo "SECOND=REFUSED" >> "$out_file"
    fi
)

_release_versioned_slot_lease_python_fallback
sleep 0.5

(
    _VERSIONED_SLOT_LEASE_PY_PID=""
    if _acquire_versioned_slot_lease_python_fallback "$lock_file"; then
        echo "THIRD=OK" >> "$out_file"
    else
        echo "THIRD=FAIL" >> "$out_file"
    fi
)
"""
    with tempfile.TemporaryDirectory() as td:
        harness_path = Path(td) / "harness.sh"
        harness_path.write_text(harness, encoding="utf-8")
        lock_file = str(Path(td) / "lease.lock")
        out_file = Path(td) / "events.txt"
        out_file.write_text("")

        r = subprocess.run(
            [_BASH, str(harness_path), lock_file, str(out_file)],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, f"harness failed: {r.stdout} {r.stderr}"

        events = [
            line for line in out_file.read_text().splitlines() if line.strip()
        ]
        assert events == ["FIRST=OK", "SECOND=REFUSED", "THIRD=OK"], events

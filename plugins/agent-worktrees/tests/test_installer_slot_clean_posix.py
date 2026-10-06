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

    assert 'py="$(_bootstrap_python)"' in body
    no_py_branch = body.split('if [[ -z "$py" ]]; then', 1)[1].split(
        "\n    fi\n", 1
    )[0]
    assert '[[ -e "$VENV_DIR" ]] && return 1' in no_py_branch
    assert "return 0" in no_py_branch


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
    )[1][:600]
    assert "return 1" in lease_fail_branch

    # The wrapper must release the lease regardless of how the inner
    # activation call returns.
    assert "_versioned_activate_inner" in activate_wrapper
    assert "_release_versioned_slot_lease" in activate_wrapper


def test_versioned_slot_lease_uses_flock_held_for_process_lifetime():
    """The lease primitive must be an OS-level `flock` held on an open fd
    (auto-released by the kernel on crash/exit -- never a PID-recorded
    marker file requiring staleness detection) on platforms that have it,
    and must never silently treat "couldn't lock" as "no contention" --
    acquisition and release failures must propagate, not be swallowed."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    acquire_body = _function_body(text, "_acquire_versioned_slot_lease")
    release_body = _function_body(text, "_release_versioned_slot_lease")

    assert "command -v flock" in acquire_body
    assert 'exec {_VERSIONED_SLOT_LEASE_FD}>"$lease_path"' in acquire_body
    assert 'flock -n "$_VERSIONED_SLOT_LEASE_FD"' in acquire_body
    # A failed `exec` open must fail closed (return 1), never treat "we
    # couldn't even open the lease file" as a successful acquisition.
    exec_fail_branch = acquire_body.split(
        'if ! exec {_VERSIONED_SLOT_LEASE_FD}>"$lease_path"; then', 1
    )[1][:200]
    assert "return 1" in exec_fail_branch
    assert "exec {_VERSIONED_SLOT_LEASE_FD}>&-" in release_body


def test_versioned_slot_lease_python_fallback_delegates_to_real_fcntl_flock():
    """A hand-rolled dotlock protocol (create-if-absent, detect-and-reclaim
    stale owners, ...) hit an unbounded chain of narrowing TOCTOU windows
    across several rounds of review -- every userspace "is it safe to
    reclaim?" check is itself a second check-then-act race one level down,
    which is exactly the class of problem flock/fcntl exist to solve. When
    the `flock` CLI is unavailable (e.g. stock macOS), the fallback must
    delegate to Python's `fcntl.flock` instead -- the SAME real kernel
    advisory lock the primary path uses, via a resident helper process
    whose lifetime holds the lock (never a separate marker file requiring
    explicit, crash-unsafe cleanup) -- rather than reinventing the
    primitive in shell. Python is already a hard dependency here
    (versioned_runtime.py), so this introduces no new dependency."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    acquire_body = _function_body(text, "_acquire_versioned_slot_lease")
    fallback_body = _function_body(
        text, "_acquire_versioned_slot_lease_python_fallback"
    )
    release_body = _function_body(text, "_release_versioned_slot_lease")

    assert "if ! command -v flock >/dev/null 2>&1; then" in acquire_body
    no_flock_branch = acquire_body.split(
        "if ! command -v flock >/dev/null 2>&1; then", 1
    )[1][:200]
    assert "_acquire_versioned_slot_lease_python_fallback" in no_flock_branch
    assert "return 0" not in no_flock_branch, (
        "the no-flock branch must defer to the python fallback's own "
        "return code, never hardcode success"
    )

    # No bootstrap python resolvable at all must fail closed, not silently
    # succeed.
    assert 'py="$(_bootstrap_python)" || return 1' in fallback_body
    assert "fcntl.flock" in fallback_body
    assert "LOCK_EX | fcntl.LOCK_NB" in fallback_body
    # A failed (non-blocking) flock attempt must propagate as a real
    # failure, never be swallowed into a false "acquired".
    assert "sys.exit(1)" in fallback_body
    # The resident helper must hold the lock for exactly its own lifetime
    # (blocking on its own stdin until closed), not acquire-then-exit --
    # an exited helper's fd closing is what releases the kernel lock.
    assert "sys.stdin.read()" in fallback_body
    assert "_VERSIONED_SLOT_LEASE_PY_PID" in fallback_body

    # Release must kill the resident helper (closing its fd set / exiting
    # it releases the kernel-held flock automatically) rather than needing
    # any explicit lock-file cleanup.
    assert 'kill "$_VERSIONED_SLOT_LEASE_PY_PID"' in release_body


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
{fallback_fn}
}}
_release_versioned_slot_lease_python_fallback() {{
    if [[ -n "$_VERSIONED_SLOT_LEASE_PY_PID" ]]; then
        kill "$_VERSIONED_SLOT_LEASE_PY_PID" 2>/dev/null || true
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

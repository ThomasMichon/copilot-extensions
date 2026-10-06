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

import os
import shutil
import subprocess
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


def test_versioned_slot_lease_noclobber_fallback_fails_closed_without_flock():
    """When `flock` isn't available (e.g. stock macOS), the lease must fall
    back to a portable, PID-liveness-checked lock -- never silently succeed
    (fail-open) just because the preferred primitive is missing, which
    would let every lockless host build the same slot unlocked. Ownership
    (the holder's pid) must already be fully committed to disk at the
    instant the lock file becomes visible under its final name -- via
    write-to-temp-then-`ln` (atomic hard-link creation), never a
    create-then-write-into-place sequence (even `set -C; echo $$ > file`
    is NOT atomic for this: the O_CREAT|O_EXCL open and the PID write are
    two separate syscalls, leaving a window where a contender sees an
    empty, apparently-unowned file and misreads it as stale)."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    acquire_body = _function_body(text, "_acquire_versioned_slot_lease")
    fallback_body = _function_body(
        text, "_acquire_versioned_slot_lease_noclobber_fallback"
    )
    publish_body = _function_body(text, "_try_publish_versioned_slot_lease")

    assert "if ! command -v flock >/dev/null 2>&1; then" in acquire_body
    no_flock_branch = acquire_body.split(
        "if ! command -v flock >/dev/null 2>&1; then", 1
    )[1][:200]
    assert "_acquire_versioned_slot_lease_noclobber_fallback" in no_flock_branch
    assert "return 0" not in no_flock_branch, (
        "the no-flock branch must defer to the mkdir fallback's own return "
        "code, never hardcode success"
    )

    assert "_try_publish_versioned_slot_lease" in fallback_body
    assert "_reclaim_versioned_slot_lease" in fallback_body
    assert 'kill -0 "$holder_pid"' in fallback_body
    assert 'printf \'%s\' "$$" > "$tmp_file"' in publish_body
    assert 'ln "$tmp_file" "$lock_file"' in publish_body


def test_versioned_slot_lease_reclaim_is_itself_serialized():
    """The stale-lock reclaim sequence (remove, then republish) is TWO
    separate steps -- without mutual exclusion around the whole sequence,
    two concurrent reclaimers could both observe the SAME stale lock, both
    unlink it, and each believe it alone now owns the lock it just
    recreated (an unconditional `rm` doesn't verify it's removing the
    entry it just inspected). `_reclaim_versioned_slot_lease` must guard
    this with a SECOND, fixed-name atomic `ln`-based sentinel so only one
    process at a time may ever be mid-reclaim for a given lock, and must
    re-verify staleness after winning that sentinel (another process may
    have already reclaimed and republished the lock as live first)."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    reclaim_body = _function_body(text, "_reclaim_versioned_slot_lease")

    assert 'sentinel="${lock_file}.reclaiming"' in reclaim_body
    assert 'ln "$tmp_sentinel" "$sentinel"' in reclaim_body
    # Losing the sentinel race must back off, never force past it.
    sentinel_fail_branch = reclaim_body.split(
        'if ! ln "$tmp_sentinel" "$sentinel" 2>/dev/null; then', 1
    )[1].split("\n    fi\n", 1)[0]
    assert "return 1" in sentinel_fail_branch
    # Staleness must be re-checked AFTER winning the sentinel, not assumed
    # from the caller's earlier (now possibly stale) observation.
    assert reclaim_body.index('ln "$tmp_sentinel" "$sentinel"') < reclaim_body.index(
        'kill -0 "$holder_pid"'
    )
    # The sentinel must always be released, on both the reclaim-succeeded
    # and reclaim-lost-the-race-after-all paths -- i.e. after the
    # re-verification branch, not nested only inside it.
    release_idx = reclaim_body.rindex('rm -f "$sentinel" 2>/dev/null || true')
    reverify_idx = reclaim_body.index('kill -0 "$holder_pid"')
    assert release_idx > reverify_idx


def test_versioned_slot_lease_reclaim_sentinel_recovers_from_a_crashed_reclaimer():
    """A reclaim sentinel can ONLY be left behind by a reclaimer that died
    mid-critical-section (a window normally microseconds long) -- if
    nothing ever recovered it, every future installer would lose the
    sentinel `ln` forever and the slot would become permanently
    unbuildable. Recovery must check the sentinel OWNER'S pid liveness
    FIRST and treat that as authoritative: a live holder can be
    legitimately SUSPENDED (process stop signal, VM pause, laptop sleep)
    for well over any age threshold and then resume still inside this
    exact critical section, so age alone must never override a
    confirmed-alive pid -- it is only the last-resort signal when no
    owner pid can be determined at all. Recovery must also never retry
    the sentinel `ln` within the SAME call (which would just move the
    exact same TOCTOU this guards against down one more level) -- only
    clear the way for a LATER, fresh call's own atomic `ln`."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    reclaim_body = _function_body(text, "_reclaim_versioned_slot_lease")
    age_check_body = _function_body(text, "_versioned_lock_is_stale_by_age")

    sentinel_fail_branch = reclaim_body.split(
        'if ! ln "$tmp_sentinel" "$sentinel" 2>/dev/null; then', 1
    )[1].split("\n        return 1\n    fi\n", 1)[0]
    # PID liveness must be checked -- and must return 1 (never steal)
    # when the recorded owner is still alive -- BEFORE age is ever
    # consulted.
    live_idx = sentinel_fail_branch.index('kill -0 "$sentinel_pid"')
    age_idx = sentinel_fail_branch.index("_versioned_lock_is_stale_by_age")
    assert live_idx < age_idx
    live_branch = sentinel_fail_branch[live_idx:age_idx]
    assert "return 1" in live_branch
    assert "_VERSIONED_RECLAIM_SENTINEL_MAX_AGE_SECONDS" in sentinel_fail_branch
    assert 'rm -f "$sentinel"' in sentinel_fail_branch
    # It must never retry _try_publish_versioned_slot_lease (or re-attempt
    # the sentinel ln) within this same call -- clearing a dead/aged-out
    # sentinel only clears the way for a LATER attempt (the caller's
    # existing retry loop), never a nested re-race here.
    assert "_try_publish_versioned_slot_lease" not in sentinel_fail_branch
    assert 'ln "$tmp_sentinel" "$sentinel"' not in sentinel_fail_branch

    assert "stat -c %Y" in age_check_body
    assert "stat -f %m" in age_check_body
    # A read failure must never be treated as "stale" -- only a
    # successfully-determined, sufficiently old mtime is.
    assert '[[ -n "$mtime" ]] || return 1' in age_check_body


@pytest.mark.skipif(_BASH is None, reason="bash is unavailable")
def test_versioned_slot_lease_reclaim_grants_exactly_one_winner_under_real_contention():
    """Behavioral (not just textual) regression guard for the stale-lock
    reclaim path specifically: seed a lock file recording a DEAD pid (a
    crashed prior holder), then launch many real, concurrently-forked bash
    subshells that all observe it as stale and race to reclaim it, and
    assert exactly one of them ever reports success."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    publish_fn = _function_body(text, "_try_publish_versioned_slot_lease")
    reclaim_fn = _function_body(text, "_reclaim_versioned_slot_lease")
    age_check_fn = _function_body(text, "_versioned_lock_is_stale_by_age")

    harness = f"""
set -uo pipefail
{publish_fn}
}}
{reclaim_fn}
}}
{age_check_fn}
}}
_VERSIONED_RECLAIM_SENTINEL_MAX_AGE_SECONDS=120

lock_file="$1"
out_file="$2"
# Seed a STALE lock recording a pid that is CERTAINLY dead: spawn a
# throwaway child, let it exit, then reuse its now-dead pid (999999 is not
# reliably invalid -- Linux permits a pid_max above it) so every racer
# below deterministically takes the reclaim path, not the fresh-acquire
# path.
sh -c 'exit 0' &
_dead_pid=$!
wait "$_dead_pid" 2>/dev/null || true
echo "$_dead_pid" > "$lock_file"

_racer() {{
    if _reclaim_versioned_slot_lease "$lock_file"; then
        echo "WIN $BASHPID" >> "$out_file"
        sleep 2
    fi
}}
for _i in $(seq 1 30); do
    _racer &
done
wait
"""
    with tempfile.TemporaryDirectory() as td:
        harness_path = Path(td) / "harness.sh"
        harness_path.write_text(harness, encoding="utf-8")
        lock_file = str(Path(td) / "race.lock")
        out_file = Path(td) / "winners.txt"
        out_file.write_text("")

        r = subprocess.run(
            [_BASH, str(harness_path), lock_file, str(out_file)],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, f"harness failed: {r.stdout} {r.stderr}"

        winners = [
            line for line in out_file.read_text().splitlines() if line.strip()
        ]
        assert len(winners) == 1, (
            f"expected exactly one winner of the stale-lock reclaim race, "
            f"got {len(winners)}: {winners}"
        )


@pytest.mark.skipif(_BASH is None, reason="bash is unavailable")
def test_versioned_slot_lease_fallback_grants_exactly_one_winner_under_real_contention():
    """Behavioral (not just textual) regression guard for the no-`flock`
    fallback: launch many real, concurrently-forked OS processes (bash
    background subshells -- genuine `fork()`s, not a simulation) racing to
    acquire the SAME fresh lock via the extracted
    `_try_publish_versioned_slot_lease` / `_acquire_versioned_slot_lease_
    noclobber_fallback` functions, and assert exactly one of them ever
    reports success -- proving the write-to-temp-then-`ln` protocol is a
    real atomic exclusion primitive, not merely a textual contract.

    All racers run as background jobs of ONE bash invocation (rather than
    each spawned as its own independent `bash.exe` process) deliberately:
    cross-process `kill -0` liveness checks between independently-launched
    MSYS2/Git-Bash instances on Windows proved unreliable in practice (each
    separately-invoked `bash.exe` builds its own local pid-mapping table,
    so one instance's `kill -0 <pid>` can false-negative on a pid a
    genuinely-still-running sibling `bash.exe` reported as its own `$$`) --
    a Windows test-environment artifact of that specific case, not a
    property of the real target platforms (native Linux/macOS, where `$$`
    and `kill -0` operate on real, globally-consistent kernel pids with no
    such per-process table). Background subshells of one bash process are
    still genuine, independently-scheduled forked processes -- they just
    share that one process's pid-table scope, which is exactly what makes
    the liveness check reliable here without masking the real exclusion
    property under test."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    publish_fn = _function_body(text, "_try_publish_versioned_slot_lease")
    fallback_fn = _function_body(text, "_acquire_versioned_slot_lease_noclobber_fallback")

    harness = f"""
set -uo pipefail
{publish_fn}
}}
{fallback_fn}
}}

lock_file="$1"
out_file="$2"
_racer() {{
    if _acquire_versioned_slot_lease_noclobber_fallback "$lock_file"; then
        echo "WIN $BASHPID" >> "$out_file"
        # Hold the lock briefly, like a real build would -- a winner that
        # exits instantly would let a later racer correctly reclaim it as
        # crash-abandoned (a DIFFERENT, also-correct property), which
        # would mask whether genuinely concurrent attempts are excluded.
        sleep 2
    fi
}}
for _i in $(seq 1 30); do
    _racer &
done
wait
"""
    with tempfile.TemporaryDirectory() as td:
        harness_path = Path(td) / "harness.sh"
        harness_path.write_text(harness, encoding="utf-8")
        lock_file = str(Path(td) / "race.lock")
        out_file = Path(td) / "winners.txt"
        out_file.write_text("")

        r = subprocess.run(
            [_BASH, str(harness_path), lock_file, str(out_file)],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, f"harness failed: {r.stdout} {r.stderr}"

        winners = [
            line for line in out_file.read_text().splitlines() if line.strip()
        ]
        assert len(winners) == 1, (
            f"expected exactly one winner of the lock race, got {len(winners)}: "
            f"{winners}"
        )


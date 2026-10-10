"""Real cross-process POSIX regression tests for the bounded build-lease
wait/retry (phase-3-runtime-admission, #5472/#5788/#5893): proves
`_wait_for_versioned_slot_lease` genuinely polls on a real wall-clock
deadline and reuses a winner's completed build, using actual separate
bash processes (not same-process subshells) -- mirroring the existing
PowerShell coverage in `test_installer_powershell51.py`.

Deliberately kept OUT of `test_installer_slot_clean_posix.py`'s
file-wide `pytest.mark.guard`: these tests spawn real processes and
include an intentional multi-second wait, which the guard lane requires
to stay sub-second and subprocess-free (`TESTING.md`, `.github/workflows/ci.yml`).
These are full-suite tests only.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from test_installer_slot_clean_posix import _BASH, _INSTALL_SH, _bash_path, _function_body


def _assemble_wait_for_lease_harness(text: str, install_dir: Path, version: str = "1.2.3") -> str:
    """Assemble a standalone, runnable copy of the real
    `_wait_for_versioned_slot_lease` and its full real dependency chain
    (`_versioned_slot_lease_path`, both mkdir-fallback halves, the
    no-flock python fallback, `_acquire_versioned_slot_lease`,
    `_release_versioned_slot_lease`) exactly as shipped -- behavioral
    parity with the installer's actual call graph, not a hand-reduced
    stand-in. `_acquire_versioned_slot_lease` itself already degrades
    gracefully to the universal mkdir gate alone when `flock` is absent
    and no fcntl-capable python resolves (the real, common case on this
    Windows/Git-Bash test runner) -- a stub `_bootstrap_python` that
    always fails reproduces exactly that real degraded-but-correct path,
    rather than skipping the no-flock branch out of the harness entirely."""
    lease_path_fn = _function_body(text, "_versioned_slot_lease_path")
    mkdir_acquire_fn = _function_body(text, "_acquire_versioned_slot_lease_mkdir_fallback")
    mkdir_release_fn = _function_body(text, "_release_versioned_slot_lease_mkdir_fallback")
    py_fallback_fn = _function_body(text, "_acquire_versioned_slot_lease_python_fallback")
    acquire_fn = _function_body(text, "_acquire_versioned_slot_lease")
    release_fn = _function_body(text, "_release_versioned_slot_lease")
    wait_fn = _function_body(text, "_wait_for_versioned_slot_lease")
    return f"""
set -uo pipefail
INSTALL_DIR="{_bash_path(install_dir)}"
SRC_VERSION="{version}"
VERSIONED_RUNTIME=1
_VERSIONED_SLOT_LEASE_MKDIR_DIR=""
_VERSIONED_SLOT_LEASE_FAILURE_REASON=""
_VERSIONED_SLOT_LEASE_FD=""
_VERSIONED_SLOT_LEASE_PY_PID=""
_VERSIONED_SLOT_LEASE_PY_STDIN_FD=""
_bootstrap_python() {{ return 1; }}
{lease_path_fn}
}}
{mkdir_acquire_fn}
}}
{mkdir_release_fn}
}}
{py_fallback_fn}
}}
{acquire_fn}
}}
{release_fn}
}}
{wait_fn}
}}
"""


@pytest.mark.skipif(_BASH is None, reason="bash is unavailable")
def test_wait_for_versioned_slot_lease_reuses_winner_within_bounded_budget(tmp_path: Path):
    """Bounded-join regression (phase-3-runtime-admission, #5472/#5788),
    POSIX side, real separate processes (not same-process subshells): a
    contender calling `_wait_for_versioned_slot_lease` while another real
    process holds the lease must poll rather than give up on first
    contention, and must acquire the lease itself once the holder
    releases within the configured budget -- proving a real bounded
    wait/retry, not merely a renamed single-shot refusal.

    The holder acquires and releases through the FULL real functions
    (never just the mkdir layer alone), so a broken release path would
    fail this test too. `_wait_for_versioned_slot_lease` itself is
    instrumented (its own `_acquire_versioned_slot_lease` call is
    wrapped to signal on its first observed contention) so the holder is
    never released before the wait function's OWN first internal attempt
    has genuinely hit contention -- a slow-to-schedule contender, or a
    regressed single-shot implementation, can't silently pass this test
    without the wait function itself actually overlapping the holder.
    Mirrors
    `test_installer_powershell51.py::test_wait_for_versioned_slot_lease_reuses_winner_within_bounded_budget`."""
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    text = _INSTALL_SH.read_text(encoding="utf-8")
    harness = _assemble_wait_for_lease_harness(text, install_dir)
    ready_marker = tmp_path / "holder-ready.txt"
    failed_marker = tmp_path / "holder-failed.txt"
    release_marker = tmp_path / "release-now.txt"
    contended_marker = tmp_path / "contended.txt"
    done_marker = tmp_path / "done.txt"

    holder_path = tmp_path / "holder.sh"
    holder_path.write_text(harness + f"""
if _acquire_versioned_slot_lease; then
    touch "{_bash_path(ready_marker)}"
    while [[ ! -f "{_bash_path(release_marker)}" ]]; do sleep 0.05; done
    _release_versioned_slot_lease
    # Stay alive, still holding nothing, until the test explicitly says
    # it is done (NOT a fixed sleep): if release were a no-op, this
    # process's PID would still be alive for as long as the test needs
    # it to be, so the contender's mkdir-fallback stale-PID reclaim path
    # (which only ever triggers once `kill -0` on the recorded holder
    # proves it dead) could never kick in before the test has already
    # asserted the result -- a passing RESULT=True can only mean the
    # explicit release genuinely cleared the lock, never a race against
    # this process's own exit timing.
    while [[ ! -f "{_bash_path(done_marker)}" ]]; do sleep 0.05; done
else
    touch "{_bash_path(failed_marker)}"
fi
""", encoding="utf-8")
    holder = subprocess.Popen(
        [_BASH, str(holder_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        for _ in range(100):  # up to ~10s
            if ready_marker.exists() or failed_marker.exists() or holder.poll() is not None:
                break
            time.sleep(0.1)
        assert not failed_marker.exists(), "holder failed to acquire its own lease"
        assert ready_marker.exists(), "holder process never reported readiness"

        contender_env = dict(os.environ)
        contender_env["AGENT_WORKTREES_SLOT_LEASE_WAIT_SEC"] = "20"
        contender_env["AGENT_WORKTREES_SLOT_LEASE_POLL_SEC"] = "1"
        contender_path = tmp_path / "contender.sh"
        # Wrap the real _acquire_versioned_slot_lease (renaming it, then
        # redefining the original name as a counting shim) so the signal
        # comes from _wait_for_versioned_slot_lease's OWN first internal
        # attempt -- not a separate probe call made before it. A separate
        # probe only proves contention existed at some earlier moment;
        # it does not prove the wait function's own polling loop is what
        # actually observed and waited through it (the contender could be
        # descheduled between the probe and the real call, letting the
        # holder be released first and a regressed single-shot
        # implementation pass anyway).
        instrumented = harness.replace(
            "_acquire_versioned_slot_lease() {", "_acquire_versioned_slot_lease_real() {", 1,
        ) + f"""
_acquire_versioned_slot_lease_attempt=0
_acquire_versioned_slot_lease() {{
    _acquire_versioned_slot_lease_attempt=$((_acquire_versioned_slot_lease_attempt + 1))
    if _acquire_versioned_slot_lease_real; then
        return 0
    fi
    if [[ "$_acquire_versioned_slot_lease_attempt" -eq 1 ]]; then
        touch "{_bash_path(contended_marker)}"
    fi
    return 1
}}
if _wait_for_versioned_slot_lease; then echo RESULT=True; else echo RESULT=False; fi
"""
        contender_path.write_text(instrumented, encoding="utf-8")
        contender = subprocess.Popen(
            [_BASH, str(contender_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=contender_env,
        )
        try:
            for _ in range(100):  # up to ~10s
                if contended_marker.exists() or contender.poll() is not None:
                    break
                time.sleep(0.1)
            assert contended_marker.exists(), (
                "_wait_for_versioned_slot_lease's own first internal "
                "acquisition attempt never observed genuine lease "
                "contention before this test released the holder"
            )
            release_marker.write_text("go")
            out, err = contender.communicate(timeout=25)
        except subprocess.TimeoutExpired:
            contender.kill()
            pytest.fail("contender's bounded wait did not return within its own budget")
        # The holder is STILL alive here (blocked on done_marker below) --
        # proven, not merely timed -- so this assertion can only pass via
        # the explicit release, never stale-PID reclaim of a holder that
        # has since exited.
        assert holder.poll() is None, "holder exited before the result was asserted"
        assert contender.returncode == 0, err
        assert "RESULT=True" in out, (
            "a bounded-wait contender must acquire the lease once the "
            f"holder releases it within budget; stdout={out!r} stderr={err!r}"
        )
    finally:
        release_marker.write_text("go")
        done_marker.write_text("go")
        try:
            holder.wait(timeout=30)
        except subprocess.TimeoutExpired:
            holder.kill()


@pytest.mark.skipif(_BASH is None, reason="bash is unavailable")
def test_wait_for_versioned_slot_lease_times_out_when_never_released(tmp_path: Path):
    """The POSIX bounded wait must actually be bounded: if the holder
    never releases, a contender configured with a short budget must
    return non-zero at (approximately) that budget, not hang
    indefinitely. The holder acquires through the full real function
    (never just the mkdir layer alone). Mirrors
    `test_installer_powershell51.py::test_wait_for_versioned_slot_lease_times_out_when_never_released`."""
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    text = _INSTALL_SH.read_text(encoding="utf-8")
    harness = _assemble_wait_for_lease_harness(text, install_dir)
    ready_marker = tmp_path / "holder-ready.txt"
    failed_marker = tmp_path / "holder-failed.txt"
    release_marker = tmp_path / "release-now.txt"

    holder_path = tmp_path / "holder.sh"
    holder_path.write_text(harness + f"""
if _acquire_versioned_slot_lease; then
    touch "{_bash_path(ready_marker)}"
    while [[ ! -f "{_bash_path(release_marker)}" ]]; do sleep 0.05; done
    _release_versioned_slot_lease
else
    touch "{_bash_path(failed_marker)}"
fi
""", encoding="utf-8")
    holder = subprocess.Popen(
        [_BASH, str(holder_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        for _ in range(100):  # up to ~10s
            if ready_marker.exists() or failed_marker.exists() or holder.poll() is not None:
                break
            time.sleep(0.1)
        assert not failed_marker.exists(), "holder failed to acquire its own lease"
        assert ready_marker.exists(), "holder process never reported readiness"

        contender_env = dict(os.environ)
        contender_env["AGENT_WORKTREES_SLOT_LEASE_WAIT_SEC"] = "2"
        contender_env["AGENT_WORKTREES_SLOT_LEASE_POLL_SEC"] = "1"
        contender_path = tmp_path / "contender.sh"
        contender_path.write_text(harness + """
if _wait_for_versioned_slot_lease; then echo RESULT=True; else echo RESULT=False; fi
""", encoding="utf-8")
        started = time.monotonic()
        contender = subprocess.run(
            [_BASH, str(contender_path)],
            capture_output=True, text=True, timeout=30,
            env=contender_env,
        )
        elapsed = time.monotonic() - started
        assert contender.returncode == 0, contender.stderr
        assert "RESULT=False" in contender.stdout, (
            "a bounded-wait contender must give up, not hang, once its "
            "configured budget elapses while the holder never releases; "
            f"stdout={contender.stdout!r} stderr={contender.stderr!r}"
        )
        assert elapsed < 8, (
            f"bounded wait took {elapsed:.1f}s against a 2s budget -- "
            "the timeout is not actually bounding the wait (a value this "
            "high suggests _VERSIONED_SLOT_LEASE_MKDIR_SINGLE_ATTEMPT "
            "stopped reaching the mkdir fallback, which would otherwise "
            "fall back to its own ~10s internal retry before reporting "
            "contention)"
        )
        assert elapsed >= 1.5, (
            f"bounded wait took only {elapsed:.1f}s against a 2s budget -- "
            "the configured wait duration is not actually being honored"
        )
    finally:
        release_marker.write_text("go")
        try:
            holder.wait(timeout=30)
        except subprocess.TimeoutExpired:
            holder.kill()

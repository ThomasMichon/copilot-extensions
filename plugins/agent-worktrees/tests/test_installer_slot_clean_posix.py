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

from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

_INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


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


def test_versioned_slot_lease_mkdir_fallback_fails_closed_without_flock():
    """When `flock` isn't available (e.g. stock macOS), the lease must fall
    back to a portable, PID-liveness-checked `mkdir` lock -- never silently
    succeed (fail-open) just because the preferred primitive is missing,
    which would let every lockless host build the same slot unlocked."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    acquire_body = _function_body(text, "_acquire_versioned_slot_lease")
    fallback_body = _function_body(
        text, "_acquire_versioned_slot_lease_mkdir_fallback"
    )

    assert "if ! command -v flock >/dev/null 2>&1; then" in acquire_body
    no_flock_branch = acquire_body.split(
        "if ! command -v flock >/dev/null 2>&1; then", 1
    )[1][:200]
    assert "_acquire_versioned_slot_lease_mkdir_fallback" in no_flock_branch
    assert "return 0" not in no_flock_branch, (
        "the no-flock branch must defer to the mkdir fallback's own return "
        "code, never hardcode success"
    )

    assert 'mkdir "$lock_dir"' in fallback_body
    assert 'kill -0 "$holder_pid"' in fallback_body


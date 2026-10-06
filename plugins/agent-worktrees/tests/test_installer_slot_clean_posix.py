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

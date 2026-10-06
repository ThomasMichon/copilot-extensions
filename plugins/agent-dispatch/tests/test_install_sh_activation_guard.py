"""Regression coverage for install.sh's `_versioned_activate` cross-version
ordering guard (parity with install.ps1's Invoke-VersionedActivate).

Two concurrent POSIX installs for DIFFERENT versions can legitimately build
fully in parallel (nothing in install.sh serializes the build itself), so a
slower, older-version invocation could still reach `_versioned_activate`
AFTER a faster, newer-version invocation already activated -- silently
regressing `current-version`. `versioned_runtime.py`'s own `activate`
performs no version comparison of its own, so the guard lives in
install.sh, mirroring the existing `_downgrade_guard`/`_version_lt` pattern.

These tests extract `_versioned_activate` (plus `_versioned_current` and
`_version_lt`, which it calls) from install.sh and execute them for real
under bash, with `versioned_runtime.py` replaced by a fake interpreter
script that just drops a marker file -- so a test can prove whether the
real activation call happened at all, not merely what it would have
printed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"


def _resolve_bash() -> str | None:
    """Resolve a REAL bash, not Windows' WSL-launcher `bash.exe` shim (see
    test_install_sh_version_ordering.py's identical helper for the full
    rationale)."""
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.is_file():
        return str(git_bash)
    path = os.environ.get("PATH")
    if path:
        filtered = os.pathsep.join(
            part for part in path.split(os.pathsep)
            if "WindowsApps" not in part
            and part.rstrip("\\").lower() != r"c:\windows\system32"
        )
        bash = shutil.which("bash", path=filtered)
        if bash:
            return bash
    bash = shutil.which("bash")
    if bash and "WindowsApps" not in bash and "\\system32\\" not in bash.lower():
        return bash
    return None


_BASH = _resolve_bash()
pytestmark = pytest.mark.skipif(_BASH is None, reason="a real bash is not available")


def _extract_function_block(name: str) -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start) + len("\n}")
    return text[start:end]


def _run_activate_harness(
    tmp_path: Path,
    *,
    src_version: str,
    current_active: str | None,
    force: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    install_dir = tmp_path / "install"
    venv_dir = install_dir / "versions" / src_version
    (venv_dir / "bin").mkdir(parents=True)
    link_dir = install_dir / ".venv"
    activated_marker = tmp_path / "activated"

    # Fake "python" at the slot's own bin/python -- a real executable shell
    # script, not a `pwsh`/batch stand-in, since install.sh always invokes it
    # as a plain POSIX executable. Only the ACTIVATE call reaches this (see
    # the _versioned_current stub below), so its presence alone proves the
    # real activation call happened.
    fake_python = venv_dir / "bin" / "python"
    fake_python.write_text(
        f'#!/bin/sh\necho activated > "{activated_marker}"\nexit 0\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    script = "\n".join([
        "#!/bin/sh",
        "set -eu",
        '_ok() { printf "OK: %s\\n" "$1"; }',
        '_warn() { printf "WARN: %s\\n" "$1" >&2; }',
        '_skip() { printf "SKIP: %s\\n" "$1"; }',
        '_fail() { printf "FAIL: %s\\n" "$1" >&2; }',
        '_step() { printf "STEP: %s\\n" "$1"; }',
        # Stubbed: _versioned_current normally shells out to
        # versioned_runtime.py's own `current` command -- irrelevant to the
        # ordering guard under test, and awkward to fake realistically
        # through the same single-purpose marker-dropping fake interpreter
        # the real activate call uses above.
        f'_versioned_current() {{ printf \'%s\' "{current_active or ""}"; }}',
        _extract_function_block("_version_lt"),
        _extract_function_block("_versioned_activate"),
        "VERSIONED_RUNTIME=1",
        f'SRC_VERSION="{src_version}"',
        f"FORCE={1 if force else 0}",
        f'INSTALL_DIR="{install_dir.as_posix()}"',
        f'VENV_DIR="{venv_dir.as_posix()}"',
        f'LINK_DIR="{link_dir.as_posix()}"',
        f'SCRIPT_DIR="{tmp_path.as_posix()}"',
        "_versioned_activate",
        'echo "RETURNED:$?"',
        'echo "SUPERSEDED:$ACTIVATION_SUPERSEDED"',
    ])

    fd, script_path = tempfile.mkstemp(suffix=".sh", dir=str(_INSTALL_SH.parent))
    try:
        with os.fdopen(fd, "w", newline="\n", encoding="utf-8") as f:
            f.write(script + "\n")
        env = dict(os.environ)
        # git-bash's `ln -s` otherwise fails outright ("No such file or
        # directory") on Windows without this -- it needs an explicit
        # request for a real, native symlink rather than its default
        # emulation. Irrelevant on a genuine POSIX bash (Linux/macOS CI),
        # where `ln -s` always creates a real symlink.
        env["MSYS"] = "winsymlinks:nativestrict"
        result = subprocess.run(
            [_BASH, os.path.basename(script_path)],
            cwd=str(_INSTALL_SH.parent),
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
    finally:
        os.unlink(script_path)
    return result, activated_marker


def test_activate_proceeds_normally_with_no_prior_active_version(tmp_path: Path) -> None:
    result, activated = _run_activate_harness(tmp_path, src_version="0.2.0-dev1", current_active=None)
    assert "RETURNED:0" in result.stdout, result.stdout + result.stderr
    assert activated.exists()
    assert "SUPERSEDED:0" in result.stdout, result.stdout + result.stderr


def test_activate_proceeds_when_newer_than_currently_active(tmp_path: Path) -> None:
    result, activated = _run_activate_harness(
        tmp_path, src_version="0.2.0-dev2", current_active="0.2.0-dev1"
    )
    assert "RETURNED:0" in result.stdout, result.stdout + result.stderr
    assert activated.exists()
    assert "SUPERSEDED:0" in result.stdout, result.stdout + result.stderr


def test_activate_skips_when_older_than_currently_active(tmp_path: Path) -> None:
    """A slower, older-version build that started first can still finish
    (health gate + mark-complete) AFTER a faster, newer-version build
    already activated -- since nothing in install.sh serializes concurrent
    builds for different versions. The cross-version ordering guard inside
    _versioned_activate must catch this at the one point where it matters
    (the actual activate/publish call) and skip rather than silently
    regress `current-version`; ACTIVATION_SUPERSEDED must also come back
    set, so _ensure_runtime/do_update can abort their own remaining steps."""
    result, activated = _run_activate_harness(
        tmp_path, src_version="0.2.0-dev1", current_active="0.2.0-dev2"
    )
    assert "Not activating" in result.stdout, result.stdout + result.stderr
    assert "RETURNED:0" in result.stdout, result.stdout + result.stderr
    assert not activated.exists()
    assert "SUPERSEDED:1" in result.stdout, result.stdout + result.stderr


def test_activate_force_overrides_the_cross_version_ordering_guard(tmp_path: Path) -> None:
    result, activated = _run_activate_harness(
        tmp_path, src_version="0.2.0-dev1", current_active="0.2.0-dev2", force=True
    )
    assert "RETURNED:0" in result.stdout, result.stdout + result.stderr
    assert activated.exists()
    assert "SUPERSEDED:0" in result.stdout, result.stdout + result.stderr


def test_do_update_holds_cutover_lock_through_the_real_cutover_call() -> None:
    """Structural check: do_update must hold the SAME global
    (.activate.lock) lock _versioned_activate itself uses, across BOTH the
    live _activation_superseded_now re-check AND the entire
    _coordinator_cutover call -- not release it right after the re-check.
    A newer invocation could otherwise activate and complete its ENTIRE
    cutover while this (older) invocation is merely queued on
    _coordinator_cutover's own internal cross-version cutover lease; once it
    finally acquires that lease, it would route the coordinator back to its
    own stale build. Holding this lock across the whole span means a newer
    invocation's own _versioned_activate call (needing this identical lock)
    cannot even start publishing its activation until this invocation's
    cutover attempt has fully finished and released it."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    idx = text.index("do_update() {")
    body = text[idx : text.index("\ndo_start() {", idx)]
    ensure_idx = body.index("_ensure_runtime")
    first_guard_idx = body.index("_activation_superseded_now", ensure_idx)
    lock_acquire_idx = body.index('ln -s "$$" "$_cutover_lock_link"')
    # The real call site (the `if` test), not the function definition.
    cutover_call_idx = body.index("if _coordinator_cutover; then")
    second_guard_idx = body.index("_activation_superseded_now", lock_acquire_idx)
    unlock_before_cutover_idx = body.index("_unlock_cutover", cutover_call_idx)
    assert ensure_idx < first_guard_idx < lock_acquire_idx < second_guard_idx < cutover_call_idx
    # _unlock_cutover must be called INSIDE each branch of the
    # `if _coordinator_cutover; then ... else ... fi`, i.e. AFTER
    # _coordinator_cutover has already run to completion -- not before it.
    assert unlock_before_cutover_idx > cutover_call_idx

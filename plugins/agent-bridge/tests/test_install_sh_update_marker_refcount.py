"""Regression coverage for the POSIX installer's update-in-progress marker.

ThomasMichon/copilot-extensions#5066: `do_update`/`do_start` write
`$INSTALL_DIR/update-in-progress` so a local liveness watchdog can tell
"legitimately mid-transition" from "actually dead". A review round on the
first implementation found the original single-owner ("last writer wins")
design unsafe -- a long `do_update` and a short, overlapping `do_start` both
touch the same marker, and the short call's own exit-time cleanup deleted it
out from under the still-running long one. These tests exercise the
replacement reference-counted design directly against the *actual* installer
source (extracted verbatim, not re-implemented), covering the three
reviewer-requested invariants: overlapping holders, no spurious writes on a
no-op route, and a fresh/missing install root.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"
_BASH = shutil.which("bash")


def _extract_marker_source() -> str:
    """Pull the marker helpers + their constants verbatim from install.sh.

    Extracted (not re-implemented) so these tests exercise the literal
    shipped logic -- a change to the real helpers is caught here without
    needing a parallel edit to a hand-maintained copy.
    """
    text = _INSTALL_SH.read_text(encoding="utf-8")

    def _const(name: str) -> str:
        match = re.search(rf'^{re.escape(name)}=.*$', text, re.MULTILINE)
        assert match, f"could not find constant {name!r} in install.sh"
        return match.group(0)

    def _func(name: str) -> str:
        match = re.search(
            rf'^{re.escape(name)}\(\) \{{.*?^\}}$', text, re.MULTILINE | re.DOTALL
        )
        assert match, f"could not find function {name!r} in install.sh"
        return match.group(0)

    # INSTALL_DIR is a caller-provided variable in the real script (resolved
    # via options/env); the test harness supplies it directly.
    pieces = [
        '_UPDATE_MARKER_HELD=false',
        'UPDATE_MARKER="${INSTALL_DIR}/update-in-progress"',
        _const("UPDATE_MARKER_TTL_DEFAULT"),
        _const("UPDATE_MARKER_REFCOUNT"),
        _const("UPDATE_MARKER_REFCOUNT_LOCK"),
        _func("_write_update_marker"),
        _func("_clear_update_marker"),
    ]
    return "\n".join(pieces) + "\n"


_MARKER_SOURCE = _extract_marker_source() if _BASH else ""


def _write_harness(tmp_path: Path, body: str) -> Path:
    import uuid

    script = tmp_path / f"harness-{uuid.uuid4().hex}.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f'INSTALL_DIR="{tmp_path / "install"}"\n'
        f"{_MARKER_SOURCE}\n{body}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script



pytestmark = pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)


def test_fresh_install_root_does_not_fail(tmp_path: Path) -> None:
    """The install root need not exist yet when the marker is first written."""
    script = _write_harness(
        tmp_path,
        '_write_update_marker\n'
        'echo "marker exists: $([[ -f "$INSTALL_DIR/update-in-progress" ]] && echo yes || echo no)"\n'
        '_clear_update_marker\n',
    )
    assert not (tmp_path / "install").exists()
    result = subprocess.run(
        [_BASH, str(script)], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "marker exists: yes" in result.stdout
    assert not (tmp_path / "install" / "update-in-progress").exists()


def test_nested_same_process_call_reuses_the_outer_holder(tmp_path: Path) -> None:
    """A same-process nested write (do_update calling do_start, which calls
    `_write_update_marker` a second time before the process-wide `trap ...
    EXIT` ever fires) must not double the refcount. In the real installer,
    `_clear_update_marker` is only ever invoked once per process -- via the
    EXIT trap -- regardless of how many nested `_write_update_marker` calls
    preceded it, so that single release must fully clear the marker."""
    script = _write_harness(
        tmp_path,
        '_write_update_marker   # outer holder (e.g. do_update)\n'
        '_write_update_marker   # nested call (e.g. do_start) -- must be a no-op\n'
        'cat "$INSTALL_DIR/update-in-progress.refcount"\n'
        '_clear_update_marker   # the one EXIT-trap release for the whole process\n'
        'echo "marker exists after release: '
        '$([[ -f "$INSTALL_DIR/update-in-progress" ]] && echo yes || echo no)"\n',
    )
    result = subprocess.run(
        [_BASH, str(script)], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr
    # Only one real slot was ever taken, regardless of the nested call.
    assert re.search(r"^1$", result.stdout, re.MULTILINE), result.stdout
    assert "marker exists after release: no" in result.stdout


def test_overlapping_processes_marker_survives_until_both_release(
    tmp_path: Path,
) -> None:
    """The actual cross-process race the reviewer flagged: a long `do_update`
    and a short, overlapping `do_start` both hold the marker; the SHORT
    process releasing first must not delete it out from under the long one.
    """
    long_script = _write_harness(
        tmp_path,
        '_write_update_marker\n'
        f'touch "{tmp_path}/long-holding"\n'
        'sleep 3\n'
        '_clear_update_marker\n'
        f'touch "{tmp_path}/long-released"\n',
    )
    short_script = _write_harness(
        tmp_path,
        '_write_update_marker\n'
        '_clear_update_marker\n',
    )

    long_proc = subprocess.Popen(
        [_BASH, str(long_script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "long-holding").exists():
            assert long_proc.poll() is None, (
                f"long holder exited early (rc={long_proc.returncode})"
            )
            assert time.monotonic() < deadline, "long holder never started"
            time.sleep(0.05)

        short_result = subprocess.run(
            [_BASH, str(short_script)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert short_result.returncode == 0, short_result.stderr

        # The short process released its own slot, but the long holder is
        # still mid-transition -- the marker must still be present.
        assert (tmp_path / "install" / "update-in-progress").exists(), (
            "short overlapping holder's release deleted the marker while "
            "the long holder was still running"
        )

        long_out, long_err = long_proc.communicate(timeout=10)
        assert long_proc.returncode == 0, long_err.decode()
        assert (tmp_path / "long-released").exists()

        # Now that every holder has released, the marker is gone.
        assert not (tmp_path / "install" / "update-in-progress").exists()
        assert not (tmp_path / "install" / "update-in-progress.refcount").exists()
    finally:
        if long_proc.poll() is None:
            long_proc.kill()
            long_proc.communicate(timeout=5)


def test_forward_route_never_invokes_the_marker_helpers() -> None:
    """A forwarded-host update must never write the marker -- there is no
    local lifecycle disruption to advertise. Asserts the real source's
    control flow, not just the helpers in isolation: the forwarded-route
    guard appears before (not after) the marker write in `do_update`.
    """
    text = _INSTALL_SH.read_text(encoding="utf-8")
    update_start = text.index("do_update()")
    update_body_end = text.index('_ok "Update complete"', update_start)
    update_body = text[update_start:update_body_end]

    forward_guard = update_body.index('active_forward=true')
    marker_write = update_body.index("_write_update_marker", forward_guard)
    guard_check = update_body.index('if [[ "$active_forward" != true ]]', forward_guard)

    assert guard_check < marker_write, (
        "the forwarded-route guard must gate the marker write in do_update"
    )

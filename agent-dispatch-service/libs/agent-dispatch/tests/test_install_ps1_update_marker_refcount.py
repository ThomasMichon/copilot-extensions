r"""Regression coverage for the Windows installer's update-in-progress marker.

ThomasMichon/copilot-extensions#5066: `Invoke-Update`/`Invoke-Start` write
`$InstallDir\update-in-progress` so a local liveness watchdog can tell
"legitimately mid-transition" from "actually dead". A review round on the
first implementation found the original single-owner ("last writer wins")
design unsafe -- a long `Invoke-Update` and a short, overlapping
`Invoke-Start` both touch the same marker, and the short call's own
exit-time cleanup deleted it out from under the still-running long one.
These tests exercise the replacement reference-counted/Mutex design
directly against the *actual* installer source (extracted verbatim, not
re-implemented).
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")


def _extract_function(name: str) -> str:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    start = text.index(f"function {name}")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting {name!r}")


_MARKER_FUNCTIONS = "\n\n".join(
    _extract_function(name)
    for name in ("Get-UpdateMarkerMutex", "Write-UpdateMarker", "Clear-UpdateMarker")
) if _PWSH else ""


def _write_harness(tmp_path: Path, body: str) -> Path:
    install_dir = tmp_path / "install"
    harness = tmp_path / f"harness-{uuid.uuid4().hex}.ps1"
    harness.write_text(
        f"$InstallDir = '{install_dir}'\n"
        f"$UpdateMarker = Join-Path $InstallDir 'update-in-progress'\n"
        "$UpdateMarkerTtlDefault = 1200\n"
        "$script:UpdateMarkerHeld = $false\n"
        f"{_MARKER_FUNCTIONS}\n\n{body}\n",
        encoding="utf-8",
    )
    return harness


def _run(harness: Path, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_fresh_install_root_does_not_fail(tmp_path: Path) -> None:
    """The install root need not exist yet when the marker is first written."""
    harness = _write_harness(
        tmp_path,
        "Write-UpdateMarker\n"
        "Write-Host \"marker exists: $(Test-Path $UpdateMarker)\"\n"
        "Clear-UpdateMarker\n",
    )
    assert not (tmp_path / "install").exists()
    result = _run(harness)
    assert result.returncode == 0, result.stderr
    assert "marker exists: True" in result.stdout
    assert not (tmp_path / "install" / "update-in-progress").exists()


def test_nested_same_process_call_reuses_the_outer_holder(tmp_path: Path) -> None:
    """A same-process nested write must not double the refcount.

    `Clear-UpdateMarker` is only ever invoked once per process (via the
    `finally` block wrapping the live-service body), regardless of how many
    nested `Write-UpdateMarker` calls preceded it -- that single release
    must fully clear the marker.
    """
    harness = _write_harness(
        tmp_path,
        "Write-UpdateMarker   # outer holder\n"
        "Write-UpdateMarker   # nested call -- must be a no-op\n"
        "Write-Host \"refcount=$(Get-Content \"$UpdateMarker.refcount\")\"\n"
        "Clear-UpdateMarker   # the one release for the whole process\n"
        "Write-Host \"marker exists after release: $(Test-Path $UpdateMarker)\"\n",
    )
    result = _run(harness)
    assert result.returncode == 0, result.stderr
    assert "refcount=1" in result.stdout
    assert "marker exists after release: False" in result.stdout


def test_overlapping_processes_marker_survives_until_both_release(
    tmp_path: Path,
) -> None:
    """The actual cross-process race the reviewer flagged: a long
    `Invoke-Update` and a short, overlapping `Invoke-Start` both hold the
    marker; the SHORT process releasing first must not delete it out from
    under the long one.
    """
    long_holding = tmp_path / "long-holding"
    long_released = tmp_path / "long-released"
    long_harness = _write_harness(
        tmp_path,
        "Write-UpdateMarker\n"
        f"New-Item -ItemType File -Path '{long_holding}' | Out-Null\n"
        "Start-Sleep -Seconds 3\n"
        "Clear-UpdateMarker\n"
        f"New-Item -ItemType File -Path '{long_released}' | Out-Null\n",
    )
    short_harness = _write_harness(
        tmp_path,
        "Write-UpdateMarker\nClear-UpdateMarker\n",
    )

    long_proc = subprocess.Popen(
        [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(long_harness)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while not long_holding.exists():
            assert long_proc.poll() is None, (
                f"long holder exited early (rc={long_proc.returncode})"
            )
            assert time.monotonic() < deadline, "long holder never started"
            time.sleep(0.05)

        short_result = _run(short_harness)
        assert short_result.returncode == 0, short_result.stderr

        # The short process released its own slot, but the long holder is
        # still mid-transition -- the marker must still be present.
        assert (tmp_path / "install" / "update-in-progress").exists(), (
            "short overlapping holder's release deleted the marker while "
            "the long holder was still running"
        )

        long_out, long_err = long_proc.communicate(timeout=10)
        assert long_proc.returncode == 0, long_err.decode()
        assert long_released.exists()

        # Now that every holder has released, the marker is gone.
        assert not (tmp_path / "install" / "update-in-progress").exists()
        assert not (tmp_path / "install" / "update-in-progress.refcount").exists()
    finally:
        if long_proc.poll() is None:
            long_proc.kill()
            long_proc.communicate(timeout=5)


def test_mutex_construction_does_not_throw_on_a_path_shaped_marker() -> None:
    """Regression for a real bug caught during development: a Windows named
    object (here, the Mutex backing the refcount lock) cannot contain a
    path separator in its name. An earlier version of
    `Get-UpdateMarkerMutex` sanitized only `\\` and `:`, leaving `/` (and
    the rest of a realistic path) in the name -- `New-Object
    System.Threading.Mutex` then threw 'The filename, directory name, or
    volume label syntax is incorrect.' on the very first call, on both
    Windows and pwsh/Linux. `$UpdateMarker` is inherently a full filesystem
    path, so this must never throw regardless of install-root shape.
    """
    tmp_path = Path("/tmp") / f"mutex-name-check-{uuid.uuid4().hex}"
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        harness = _write_harness(
            tmp_path=tmp_path,
            body=(
                "$mutex = Get-UpdateMarkerMutex\n"
                "Write-Host 'CONSTRUCTED OK'\n"
                "$mutex.Dispose()\n"
            ),
        )
        result = _run(harness)
        assert result.returncode == 0, result.stderr
        assert "CONSTRUCTED OK" in result.stdout, result.stdout
        assert "syntax is incorrect" not in result.stderr
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)

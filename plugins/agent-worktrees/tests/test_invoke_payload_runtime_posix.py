"""Behavioral tests for invoke-payload-runtime.sh's background-prune path.

Exercises the real POSIX functions (not just a text/grep inspection of the
script) by extracting ``boot_trace_maybe_prune`` / ``boot_trace_claim_prune_marker``
and sourcing them into a minimal bash harness -- mirroring the extraction
pattern test_installer_copilot_resolution.py uses for install.sh's bash
resolver.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
INVOKE_SH = PLUGIN / "scripts" / "invoke-payload-runtime.sh"


def _bash() -> str | None:
    candidate = shutil.which("bash")
    if candidate is None:
        return None
    if os.name == "nt" and "WindowsApps" in candidate:
        return None
    try:
        probe = subprocess.run(
            [candidate, "-c", "exit 7"], capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return candidate if probe.returncode == 7 else None


def _prune_functions_source() -> str:
    source = INVOKE_SH.read_text(encoding="utf-8")
    start = source.index("boot_trace_maybe_prune() {")
    end = source.index("\nboot_trace() {", start)
    return source[start:end]


def _run(bash: str, script: str, timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        [bash, "-c", script], capture_output=True, text=True, timeout=timeout,
    )


def _bash_path(bash: str, path: Path) -> str:
    """POSIX-style path for *path*, as the real script always sees it (its
    own paths come from ``$HOME``/``$RUNTIME_ROOT``, already POSIX-formatted
    under both WSL and git-bash) -- unlike pytest's native ``WindowsPath``
    fixtures, whose backslashes bash's own glob engine can't parse as a
    directory separator."""
    if os.name != "nt":
        return str(path)
    result = subprocess.run(
        [bash, "-c", 'cygpath -u -- "$1"', "_", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.skipif(_bash() is None, reason="a conformant Bash is unavailable")
def test_posix_maybe_prune_skips_small_file(tmp_path: Path):
    bash = _bash()
    assert bash is not None
    log = tmp_path / "activity.jsonl"
    log.write_text('{"ts": "2026-01-01T00:00:00+00:00"}\n', encoding="utf-8")
    script = (
        f"{_prune_functions_source()}\n"
        'AGENT_RT_PY=/does/not/matter\n'
        f'boot_trace_maybe_prune "{_bash_path(bash, log)}"\n'
    )
    result = _run(bash, script)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.glob("*.prune-marker.*")) == []


@pytest.mark.skipif(_bash() is None, reason="a conformant Bash is unavailable")
def test_posix_maybe_prune_skips_when_no_runtime_resolved(tmp_path: Path):
    bash = _bash()
    assert bash is not None
    log = tmp_path / "activity.jsonl"
    log.write_text("x" * 600_000, encoding="utf-8")
    script = f'{_prune_functions_source()}\nboot_trace_maybe_prune "{_bash_path(bash, log)}"\n'
    result = _run(bash, script)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.glob("*.prune-marker.*")) == [], (
        "must not dispatch when AGENT_RT_PY is unset -- no runtime to run the worker with"
    )


@pytest.mark.skipif(_bash() is None, reason="a conformant Bash is unavailable")
def test_posix_maybe_prune_dispatches_detached_and_does_not_block(tmp_path: Path):
    """A large file with a resolved runtime must claim exactly one marker
    for the current window and return almost immediately -- the dispatched
    worker runs detached, never inline on this path (between keypresses)."""
    bash = _bash()
    assert bash is not None
    log = tmp_path / "activity.jsonl"
    log.write_text("x" * 600_000, encoding="utf-8")
    # A stub "python" standing in for the real interpreter: proves the
    # dispatch is detached/non-blocking without re-testing the real worker's
    # prune logic (covered by the Python test suite). Sleeps well past the
    # assertion below if it were ever awaited inline.
    stub_py = tmp_path / "stub-python"
    stub_py.write_text(
        "#!/usr/bin/env bash\nsleep 2\necho ran >> \"$1\"\n", encoding="utf-8",
    )
    stub_py.chmod(0o755)
    script = (
        f"{_prune_functions_source()}\n"
        f'AGENT_RT_PY="{_bash_path(bash, stub_py)}"\n'
        f'boot_trace_maybe_prune "{_bash_path(bash, log)}"\n'
    )
    start = time.monotonic()
    result = _run(bash, script)
    elapsed = time.monotonic() - start
    assert result.returncode == 0, result.stderr
    assert elapsed < 1.5, (
        f"boot_trace_maybe_prune blocked for {elapsed:.2f}s -- dispatch must be detached"
    )
    markers = list(tmp_path.glob("activity.jsonl.prune-marker.*"))
    assert len(markers) == 1


@pytest.mark.skipif(_bash() is None, reason="a conformant Bash is unavailable")
def test_posix_claim_prune_marker_exclusive_per_window(tmp_path: Path):
    bash = _bash()
    assert bash is not None
    log = tmp_path / "activity.jsonl"
    log_posix = _bash_path(bash, log)
    script = (
        f"{_prune_functions_source()}\n"
        f'boot_trace_claim_prune_marker "{log_posix}"; echo "first=$?"\n'
        f'boot_trace_claim_prune_marker "{log_posix}"; echo "second=$?"\n'
    )
    result = _run(bash, script)
    assert result.returncode == 0, result.stderr
    assert "first=0" in result.stdout
    assert "second=1" in result.stdout


@pytest.mark.skipif(_bash() is None, reason="a conformant Bash is unavailable")
def test_posix_claim_prune_marker_cleans_up_older_windows(tmp_path: Path):
    bash = _bash()
    assert bash is not None
    log = tmp_path / "activity.jsonl"
    old_marker = tmp_path / "activity.jsonl.prune-marker.1"
    old_marker.write_text("", encoding="utf-8")
    script = (
        f"{_prune_functions_source()}\n"
        # Force a bucket far from "1" so the claim below lands in a
        # different, new window than the pre-seeded stale marker.
        "date() { echo 999999999999; }\n"
        f'boot_trace_claim_prune_marker "{_bash_path(bash, log)}"\n'
    )
    result = _run(bash, script)
    assert result.returncode == 0, result.stderr
    assert not old_marker.exists()

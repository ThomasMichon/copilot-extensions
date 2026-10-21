"""Finite native preflights own their diagnostic renderer, not session lifetime."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native Bash preflight contract")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("success_after", [1, 3, None])
@pytest.mark.parametrize("stream_progress", [False, True])
def test_preflight_frames_only_its_log_and_preserves_retry_contract(
    tmp_path: Path, success_after: int | None, stream_progress: bool,
) -> None:
    source = (ROOT / "worktree-manager/bin/launch-session.sh").read_text(encoding="utf-8")
    functions = source[source.index("setup_log() {"):source.index("\n# Write header")]
    start = source.index("run_runtime_preflight() {")
    preflight = source[start:source.index('\nif [[ -n "$PYTHON"', start)]
    python = getattr(sys, "_base_executable", sys.executable)
    spawns = tmp_path / "spawns.jsonl"
    wrapper = tmp_path / "python"
    wrapper.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(spawns))}\n"
        f"exec {shlex.quote(python)} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    child = tmp_path / "preflight.py"
    attempts = tmp_path / "attempts"
    child.write_text(
        "from pathlib import Path\nimport sys\n"
        f"path = Path({str(attempts)!r})\n"
        "attempt = int(path.read_text()) + 1 if path.exists() else 1\n"
        "path.write_text(str(attempt))\n"
        "print('child-out-' + str(attempt))\n"
        "print('child-err-' + str(attempt), file=sys.stderr)\n"
        "print('ghp_' + 'a' * 36)\n"
        f"raise SystemExit(0 if attempt == {success_after!r} else 7)\n",
        encoding="utf-8",
    )
    log = tmp_path / "setup.log"
    fixture = tmp_path / "launch.sh"
    fixture.write_text("\n".join([
        "set -euo pipefail",
        functions,
        preflight,
        f"PYTHON={shlex.quote(str(wrapper))}",
        f"resolve_runtime_python() {{ printf '%s\\n' {shlex.quote(str(wrapper))}; }}",
        f"_SETUP_LOG_DIR={shlex.quote(str(tmp_path))}; SETUP_LOG={shlex.quote(str(log))}",
        "date() { printf 'unexpected per-line clock process\\n' >&2; return 1; }",
        "if run_runtime_preflight fixture "
        + ("--stream-progress " if stream_progress else "")
        + shlex.quote(str(child)) + "; then code=0; else code=$?; fi",
        'printf \'native-exit=%s\\n\' "$code"',
        '[[ -z "${_SETUP_PROGRESS_DIR:-}" ]]',
        '[[ -z "$(jobs -pr)" ]]',
    ]), encoding="utf-8")
    result = subprocess.run(
        ["/bin/bash", str(fixture)], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "libs/update-telemetry/src")),
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == f"native-exit={0 if success_after else 1}\n"
    assert int(attempts.read_text()) == (success_after or 3)
    commands = spawns.read_text().splitlines()
    assert sum("-m update_telemetry.shell" in line for line in commands) == 1
    assert len(commands) == 1 + (success_after or 3)
    assert not list(tmp_path.glob("progress-*"))
    lines = log.read_text().splitlines()
    assert lines and all(re.fullmatch(
        r"\[\d{2}:\d{2}:\d{2}\.\d{3}Z\] .+ \[(?:elapsed|duration) \d+\.\d+s\]", line,
    ) for line in lines)
    assert "ghp_" + "a" * 36 not in log.read_text()
    assert f"launch/preflight/fixture {'done exit 0' if success_after else 'failed exit 1'}" in log.read_text()
    assert log.read_text().count("(exit 7)") == (success_after - 1 if success_after else 3)
    assert "child-out-" in log.read_text()
    if stream_progress:
        assert result.stderr.count("child-err-") == (success_after or 3)
        assert "child-err-" not in log.read_text()
    else:
        assert "child-err-" in log.read_text()
        assert "child-err-" not in result.stderr
    if success_after:
        assert "WARNING:" not in result.stderr
    else:
        assert "failed after 3 attempts -- continuing launch without it" in result.stderr

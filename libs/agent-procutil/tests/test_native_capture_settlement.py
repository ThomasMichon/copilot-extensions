"""Model capture settlement failures without signalling any process group."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

CAPTURE = Path(__file__).resolve().parents[1] / "src/agent_procutil/native_capture.sh"


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.skipif(os.name == "nt", reason="POSIX shell settlement diagnostics")
@pytest.mark.parametrize("condition,code,reason", [
    ("observation-unavailable", 1, "group-observation-unavailable: Operation not permitted"),
    ("group-present", 1, "group-present-at-cleanup-deadline"),
    ("directory-retained", 1, "private-directory-retained"),
    ("settled", 0, "settled"),
])
def test_settlement_diagnostic_retains_failure_and_original_observation(
    tmp_path: Path, condition: str, code: int, reason: str,
) -> None:
    bash = shutil.which("bash")
    assert bash
    directory = tmp_path / "capture"
    if condition == "directory-retained":
        directory.mkdir()
    script = r'''
set -euo pipefail
. "$1"
condition="$2"
__native_capture_directory="$3"
__native_capture_pid=424242
__native_capture_settlement_reason=not-started
builtin() {
    if [[ "$condition" == group-present ]]; then return 0; fi
    if [[ "$condition" == observation-unavailable ]]; then
        printf 'Operation not permitted\n' >&2
    else
        printf 'No such process\n' >&2
    fi
    return 1
}
sleep() {
    if [[ "$condition" == group-present ]]; then SECONDS=$((SECONDS + 10)); fi
    return 0
}
wait() { return 0; }
exec 8>/dev/null
code=0
_native_capture_settle || code=$?
printf '%s|%s\n' "$code" "$__native_capture_settlement_reason"
'''
    result = subprocess.run(
        [bash, "-c", script, "settlement-control", str(CAPTURE), condition, str(directory)],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"{code}|{reason}"
    assert directory.exists() is (condition == "directory-retained")

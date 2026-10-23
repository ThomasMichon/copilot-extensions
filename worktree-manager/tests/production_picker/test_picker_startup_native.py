"""Opt-in qualification: real executable, engine processes and Textual driver."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from agent_procutil import no_window_flags
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("WORKTREE_MANAGER_STARTUP_PROBE") != "1",
    reason="explicit native startup qualification only",
)


@pytest.mark.parametrize("count", [1, 100, 1000])
@pytest.mark.parametrize("batched", [False, True, "config-dir"])
def test_native_startup_current_feed(tmp_path, count, batched):
    if batched == "config-dir" and not os.environ.get("WORKTREE_MANAGER_STARTUP_ENGINE_PYTHON"):
        pytest.skip("source producer proof requires the isolated Worktrees test interpreter")
    helper = Path(__file__).with_name("picker_startup_probe.py")
    env = {**os.environ, "PICKER_PROBE_START": str(time.perf_counter())}
    output, errors = tmp_path / "output.txt", tmp_path / "errors.txt"
    with output.open("w", encoding="utf-8") as stdout, errors.open("w", encoding="utf-8") as stderr:
        result = subprocess.run(
            [
                sys.executable, str(helper), "consumer", str(tmp_path), str(count),
                "config-dir" if batched == "config-dir" else str(int(batched)),
            ],
            stdout=stdout, stderr=stderr, timeout=65, env=env,
            creationflags=no_window_flags(),
        )
    assert result.returncode == 0, output.read_text() + errors.read_text()
    samples = json.loads(output.read_text())
    assert len(samples) == 2
    for sample in samples:
        # Host/import latency is measured, not mistaken for proof of the full
        # vision budget. The controlled two-second engine gate must be later.
        assert (
            sample["entry_to_current_rows_rendered_ms"]
            - sample["entry_to_headless_refresh_ms"]
        ) >= 2000, sample
        assert sample["input_ack_ms"] < 100, sample
        calls = sample["calls"]
        assert sum(call["verb"] == "picker-bootstrap" for call in calls) == 1
        assert sum(call["verb"] == "list" for call in calls) == 1
        assert all("--follow" in call["args"] for call in calls if call["verb"] == "list")
        scalar_calls = [call for call in calls if call["verb"] == "get"]
        expected = 2 if batched == "config-dir" else (0 if batched else 3)
        assert len(scalar_calls) == expected
        if batched == "config-dir":
            assert {tuple(call["args"]) for call in scalar_calls} == {
                ("repo-dir",), ("machine",),
            }
        assert sample["entry_to_current_rows_rendered_ms"] > 2000
    report = os.environ.get("WORKTREE_MANAGER_STARTUP_REPORT")
    if report:
        with Path(report).open("a", encoding="utf-8") as stream:
            for sample in samples:
                stream.write(json.dumps(sample) + "\n")
    print(json.dumps(samples))

"""An external watchdog bounds cancellation tests, including a lost native proof."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_procutil import direct_python, no_window_kwargs
from agent_procutil.daemon_instances import DaemonLeasePolicy, daemon_test_lifecycle

pytestmark = pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1",
    reason="actual guarded subprocess needs canonical containment",
)


@pytest.mark.parametrize("mode", ["inner", "all", "late-lost"])
def test_cancelled_stop_owner_yields_and_never_invents_native_retirement(tmp_path, mode):
    registry = tmp_path / "instances"
    python, extra = direct_python(sys.executable)
    probe = Path(__file__).parent / "fixtures" / "roster_stop_owner.py"
    with daemon_test_lifecycle(
        owner="roster-stop-watchdog", registry_root=registry, lease=DaemonLeasePolicy(15, 30),
    ) as environment:
        process = subprocess.Popen(
            [python, "-B", str(probe), mode, str(registry)], cwd=tmp_path,
            env={**os.environ, **environment, **extra},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
        )
        timeout = False
        try:
            try:
                output, error = process.communicate(timeout=6)
            except subprocess.TimeoutExpired:
                timeout = True
                process.kill()
                output, error = process.communicate(timeout=3)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)
        observations = {row["marker"]: row for row in map(json.loads, output.splitlines())}
        assert "responsive" in observations, (output, error)
        assert observations["responsive"] == {
            "marker": "responsive", "state": "running", "pending": True, "native_held": True,
        }
        if mode == "inner":
            assert not timeout and process.returncode == 0, (output, error)
            assert observations["settled"]["state"] == "finished"
        else:
            assert timeout, "unknown native completion must not release admission"
            assert observations["unknown-hold"] == {
                "marker": "unknown-hold", "state": "running",
                "pending": True, "native_retired": True,
            }
            assert "settled" not in observations

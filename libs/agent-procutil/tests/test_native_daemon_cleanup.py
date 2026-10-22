"""Real pre-adoption process handles, refusal, and conservative cleanup outcomes."""

import json
import os
import subprocess
from pathlib import Path

import pytest

import agent_procutil as pu

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("PROCUTIL_NATIVE_DAEMON_LIFETIME") != "1",
    reason="explicit Windows native bootstrap lifetime qualification",
)


@pytest.mark.parametrize("mode", ["cleanup", "mismatch", "unconfirmed-launcher"])
def test_native_preack_cleanup_requires_verified_handles_and_observed_exit(tmp_path, mode):
    from test_native_daemon import observer

    base = Path(__file__).resolve().parents[2]
    helper = base / "agent-procutil" / "src" / "agent_procutil" / "native_daemon.ps1"
    fixture = Path(__file__).with_name("fixture_native_daemon_cleanup.ps1")
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    with observer() as observation:
        result = subprocess.run(
            [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(fixture),
             mode, str(helper), str(tmp_path)],
            capture_output=True, text=True, timeout=50, **pu.no_window_kwargs(),
        )
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["settled"] is (mode == "cleanup"), outcome
    if mode == "mismatch":
        assert "client identity changed" in outcome["error"]
        assert outcome["workerCode"] == 1, "unverified targets must retire without parent termination"
    else:
        assert "inside a Job" in outcome["error"]
        assert outcome["workerCode"] == -1, "the verified pinned worker must actually be terminated"
    assert not (tmp_path / "started").exists()
    assert observation.surfaced == {}
    assert observation.focus_changes == {}

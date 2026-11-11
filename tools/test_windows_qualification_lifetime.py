"""Controls for the selected native owner and captured descendant streams."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_procutil import no_window_kwargs
from windows_qualification_lifetime import capture_native_streams, worktrees_lifetime


def test_native_capture_retains_real_child_output_and_restores_streams_on_failure(tmp_path):
    original = sys.__stdout__, sys.__stderr__, sys.stdout, sys.stderr
    stdout, stderr = tmp_path / "stdout.log", tmp_path / "stderr.log"
    with stdout.open("w", encoding="utf-8") as out, stderr.open("w", encoding="utf-8") as err:
        with pytest.raises(RuntimeError, match="after native output"):
            with capture_native_streams(out, err):
                result = subprocess.run(
                    [sys.executable, "-I", "-B", "-c",
                     "import sys;print('native output');print('native error',file=sys.stderr)"],
                    stdin=subprocess.DEVNULL, stdout=sys.__stdout__, stderr=sys.__stderr__,
                    timeout=10, **no_window_kwargs(),
                )
                assert result.returncode == 0
                print("owner output")
                raise RuntimeError("after native output")
    assert (sys.__stdout__, sys.__stderr__, sys.stdout, sys.stderr) == original
    assert stdout.read_text().splitlines() == ["native output", "owner output"]
    assert stderr.read_text().splitlines() == ["native error"]


@pytest.mark.parametrize("packet,case", [
    ("native-worktrees-caller-jobs", "test_public_caller_job_lifetime"),
    ("native-worktrees-copilot-caller-jobs", "test_public_copilot_caller_job_lifetime"),
])
def test_worktrees_lifetime_uses_native_owner_without_weakening_default_containment(
    tmp_path, monkeypatch, packet, case,
):
    import plugin_test_cache
    import plugin_test_containment
    import plugin_test_resources

    runtime = tmp_path / "venvs" / "agent-worktrees"
    python = runtime / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(plugin_test_resources, "venv_root", lambda root: runtime.parent)
    monkeypatch.setattr(plugin_test_cache, "ordinary_cache_root", lambda root: root)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert kwargs["native_application"] is True
        assert kwargs["limits"] == plugin_test_containment.Limits(wall_seconds=900)
        assert kwargs["daemon_lease"].ttl_seconds == 15
        assert kwargs["daemon_lease"].max_lifetime_seconds == 900
        assert kwargs["env"]["WORKTREES_NATIVE_CALLER_JOBS"] == "1"
        assert kwargs["env"]["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
        assert Path(kwargs["env"]["COPILOT_EXTENSIONS_TEST_SANDBOX"]) == kwargs["sandbox"]
        return 0

    monkeypatch.setattr(plugin_test_containment, "run_contained", run)
    prepared = []
    root = tmp_path / "candidate"
    result = worktrees_lifetime(
        root, tmp_path / "output", prepare=lambda argv, timeout: prepared.append((argv, timeout)),
        packet=packet,
    )
    assert result == 0 and len(calls) == 1
    assert prepared == [([
        sys.executable, str(root / "tools/run-plugin-tests.py"), "agent-worktrees", "--prepare-only",
    ], 300)]
    command = calls[0][0]
    assert command[command.index("-k") + 1] == case
    assert "--timeout=900" in command
    assert os.environ["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"


def test_worktrees_lifetime_refuses_other_packets_before_preparation(tmp_path):
    with pytest.raises(ValueError, match="selected caller-Job packet"):
        worktrees_lifetime(
            tmp_path, tmp_path / "output", packet="native-worktrees-copilot",
            prepare=lambda *args: pytest.fail("unselected preparation"),
        )

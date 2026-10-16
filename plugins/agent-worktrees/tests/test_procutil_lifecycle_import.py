"""The standalone installer imports containment without loading daemon modules."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard


def test_real_standalone_lifecycle_loader_can_run_a_contained_child():
    payload = Path(__file__).resolve().parents[1]
    script = payload / "scripts" / "command_lifecycle.py"
    spec = importlib.util.spec_from_file_location("_standalone_lifecycle_fixture", script)
    lifecycle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lifecycle)
    assert not lifecycle.__package__
    helper = lifecycle._procutil(payload)
    process, job = helper.spawn_sync_in_kill_on_close_job(
        [sys.executable, "-c", "print('contained-fixture')"],
        require_job=True, allow_breakaway=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        output, error = process.communicate(timeout=10)
        assert process.returncode == 0, error
        assert output.strip() == "contained-fixture"
        assert (job is not None) is (os.name == "nt")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if job is not None:
            job.close()

"""Finite uninstall children must settle before caller-held custody can end."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import time

import pytest

ENGINE = Path(__file__).resolve().parents[1] / "libs" / "installer-engine"


def _supervisor():
    spec = importlib.util.spec_from_file_location(
        "uninstall_process_test", ENGINE / "installer_engine_process.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux process-group settlement")
def test_uninstall_timeout_settles_descendant_writer_before_return(tmp_path):
    supervisor = _supervisor()
    marker = tmp_path / "late-write"
    identity = tmp_path / "writer-pid"
    writer = "import pathlib,time; time.sleep(1.5); pathlib.Path(__import__('sys').argv[1]).write_text('late')"
    parent = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]], "
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "pathlib.Path(sys.argv[3]).write_text(str(child.pid)); time.sleep(60)"
    )
    with pytest.raises(supervisor.ChildTimeoutExpired):
        supervisor.run_owned(
            [sys.executable, "-c", parent, writer, str(marker), str(identity)],
            cwd=tmp_path, env=dict(os.environ), timeout=0.5,
            procutil=None, construction_fd=None,
        )
    pid = int(identity.read_text())
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        pass
    else:
        # Container PID 1 may defer reaping; terminal children cannot resume writes.
        assert state in {"Z", "X"}
    time.sleep(1.6)
    assert not marker.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux finite caller-held mode")
def test_uninstall_caller_held_mode_captures_success_without_runtime_dependencies(tmp_path):
    supervisor = _supervisor()
    result = supervisor.run_owned(
        [sys.executable, "-c", "print('complete')"],
        cwd=tmp_path, env=dict(os.environ), timeout=10,
        procutil=None, construction_fd=None,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "complete"

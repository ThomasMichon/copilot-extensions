"""Real Windows process reads without a daemon, WMI or shared installation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from agent_procutil.owned_process import run_caller_owned
import pytest

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Native Windows process observation"),
    pytest.mark.timeout(30),
]


def test_native_daemon_observation_reads_real_process_without_subprocess_probes(tmp_path):
    source = Path(__file__).resolve().parents[2] / "src"
    command = """
import json,os,subprocess,sys
sys.path.insert(0,sys.argv[1])
from worktree_manager import daemons_status
from worktree_manager import windows_daemon_observation as observation
def forbidden(*args,**kwargs):
    raise AssertionError('native observation started a subprocess')
subprocess.run = subprocess.Popen = forbidden
pid = os.getpid()
command = observation.process_command_line(pid)
current = observation.current_user_sid()
result = {
    'census': pid in observation.process_ids(),
    'mux_census': pid in observation.mux_daemon_pids(),
    'command': '-m worktree_manager mux-daemon run' in command,
    'literal': 'literal spaced value' in command,
    'owner': bool(current) and observation.process_user_sid(pid) == current,
    'verified_owner': daemons_status._pid_owned_by_current_user(pid),
    'root': daemons_status._status_pid_matches_root(pid,root=__import__('pathlib').Path(sys.argv[2])),
    'birth': bool(daemons_status._process_start_time(pid)),
}
print(json.dumps(result))
"""
    result = run_caller_owned(
        [
            sys.executable, "-I", "-B", "-c", command, str(source), str(tmp_path),
            "-m", "worktree_manager", "mux-daemon", "run",
            f"--root={tmp_path}", "literal spaced value",
        ],
        timeout=20, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
    assert all(json.loads(result.stdout).values()), result.stdout

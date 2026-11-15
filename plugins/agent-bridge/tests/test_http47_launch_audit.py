"""Audit argv shapes preserve the same contributing-entrypoint classification."""

import os
import subprocess

import pytest

from bridge_http47_runtime import launch_role


@pytest.mark.parametrize("role, arguments", [
    ("record-writer", [r"C:\fixture space\fixtures\worktree_record_owner.py", "quoted \"value"]),
    ("roster-source", [r"C:\fixture space\fixtures\worktree_list_source.py", '{"worktrees": []}']),
    ("host", ["-m", "agent_bridge.session_host", "--state-dir", r"C:\fixture space\state"]),
    ("worker", [r"C:\fixture space\cold_recovery_worker.py", r"trailing\\"]),
    (None, ["-c", "print('not a contributing launch')"]),
])
@pytest.mark.parametrize("command_line", [False, True])
def test_launch_role_preserves_native_and_popen_argument_shapes(role, arguments, command_line):
    argv = [r"C:\python space\python.exe", *arguments]
    if command_line:
        if os.name != "nt":
            pytest.skip("Windows Popen command line audit")
        argv = subprocess.list2cmdline(argv)
    assert launch_role(argv) == role

"""Opt-in local Windows visibility regression; no remote SSH or credentials."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("SSH_MANAGER_WINDOWS_PROXY_TEST") != "1",
    reason="set SSH_MANAGER_WINDOWS_PROXY_TEST=1 on Windows to exercise native OpenSSH",
)
@pytest.mark.parametrize("ssh_kind", ["native", "git"])
def test_native_proxy_stays_windowless_across_two_cycles(tmp_path, ssh_kind):
    ssh = (
        Path(os.environ["SystemRoot"]) / "System32" / "OpenSSH" / "ssh.exe"
        if ssh_kind == "native"
        else Path(os.environ.get("ProgramFiles", "")) / "Git" / "usr" / "bin" / "ssh.exe"
    )
    if not ssh.is_file():
        pytest.skip(f"The {ssh_kind} OpenSSH client is not installed")
    fixture = Path(__file__).parent / "fixtures" / "windows_proxy_probe.py"
    result = subprocess.run(
        [sys.executable, str(fixture), str(tmp_path)],
        env={
            **os.environ, "SSH_MANAGER_TEST_SSH": str(ssh),
            "SSH_MANAGER_TEST_SSH_KIND": ssh_kind,
        },
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["parent_console_handle"] == 0
    assert len(evidence["cycles"]) == 2
    assert all(
        not any(cycle["proxy_console_handles"])
        for cycle in evidence["cycles"]
    )
    assert evidence["new_visible_terminal_windows"] == 0, evidence
    assert evidence["new_terminal_took_foreground"] is False, evidence

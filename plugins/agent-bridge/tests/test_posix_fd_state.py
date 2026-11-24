"""Bridge launches never pass Win32-OpenSSH's stdio descriptor carrier on."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent_bridge import __main__ as bridge_main, transport
from agent_bridge.posix_fd_state import POWERSHELL_SCRUB, scrub_posix_fd_state

CARRIER = "C28FC6F98A2C44ABBBD89D6A3037D0D9_POSIX_FD_STATE"
# A real sshd value: the child's three stdio descriptors.
CARRIER_VALUE = "AAAAAAICAi8="


def test_scrub_removes_carrier_case_insensitively() -> None:
    env = {CARRIER.lower(): CARRIER_VALUE, "OTHER_POSIX_FD_STATE": "x", "PATH": "p"}
    assert sorted(scrub_posix_fd_state(env)) == sorted([CARRIER.lower(), "OTHER_POSIX_FD_STATE"])
    assert env == {"PATH": "p"}


@pytest.mark.asyncio
async def test_local_launch_environment_drops_carrier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv(CARRIER, CARRIER_VALUE)
    monkeypatch.setattr("agent_bridge.hosting_policy.require_target_hosting", AsyncMock())
    monkeypatch.setattr(transport, "_find_copilot", lambda *_a, **_k: "copilot")
    target = transport.SpawnTarget(cwd=str(tmp_path), local_plugin_preparation=False)
    _args, _cwd, env = await transport.resolve_local_launch(target)
    assert not any(key.upper() == CARRIER for key in env)


def test_cli_entry_drops_carrier_from_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CARRIER, CARRIER_VALUE)
    with pytest.raises(SystemExit):
        bridge_main.main(["--help"])
    assert CARRIER not in os.environ


def test_windows_receiver_script_drops_carrier_first() -> None:
    target = transport.SpawnTarget(
        type="ssh", host="example", project="example", cwd=r"C:\work", ssh_shell="pwsh",
    )
    assert transport._guarded_windows_script(target).startswith(POWERSHELL_SCRUB)


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("ssh"), reason="Win32-OpenSSH only")
def test_redirected_ssh_exits_with_scrubbed_environment() -> None:
    # With the carrier, ssh.exe never completes its first redirected stderr
    # write and never exits; scrubbing must let it fail fast and exit.
    ssh = shutil.which("ssh")
    assert ssh is not None
    env = {**os.environ, CARRIER: CARRIER_VALUE}
    scrub_posix_fd_state(env)
    proc = subprocess.Popen(
        [ssh, "-o", "ProxyCommand=none", "nonexistent.invalid", "exit"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        _out, err = proc.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail("ssh.exe with redirected stdio did not exit")
    assert proc.returncode != 0
    assert b"nonexistent.invalid" in err

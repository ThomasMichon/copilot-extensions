"""Fleet continuation retains the published startup-bound CLI contract."""

from __future__ import annotations

import shlex
import shutil
import subprocess

import pytest

from agent_dispatch import bridge


@pytest.mark.parametrize("wait,timeout", [(False, 20.0), (True, 20.0), (False, None)])
def test_fleet_continuation_uses_one_startup_bound_command_and_original_input(
    monkeypatch: pytest.MonkeyPatch, wait: bool, timeout: float | None,
) -> None:
    calls: list[tuple[list[str], str | None, float | None]] = []
    prompt = "continue\nliteral 'quote' & value"
    monkeypatch.setattr(shutil, "which", lambda name: "ssh-fixture" if name == "ssh" else None)

    def run(
        argv: list[str], *, input: str | None = None, timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((argv, input, timeout))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(bridge, "run_ssh_command", run)
    assert bridge.resume_session("session-a", prompt, host=" Pool-A ", wait=wait, timeout=timeout)
    assert len(calls) == 1
    argv, original, wall_timeout = calls[0]
    assert argv[:6] == [
        "ssh-fixture", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "pool-a",
    ]
    command = shlex.split(argv[6])
    assert command[:6] == [
        "agent-bridge", "send", "session-a", "--require-agent-hosting", "--prompt-file", "-",
    ]
    assert ("--no-wait" in command) is not wait
    assert command[-4:] == [
        "--startup-communication-timeout", str(30.0 if timeout is None else timeout),
        "--startup-process-timeout", "300.0",
    ]
    assert "--legacy-selector" not in command
    assert prompt not in argv[6] and original == prompt and wall_timeout == timeout

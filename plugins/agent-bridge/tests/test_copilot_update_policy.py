from __future__ import annotations

import base64

import pytest

from agent_bridge.transport import SpawnTarget, _unguarded_remote_cmd


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize(
    ("project", "worktree"),
    [(None, None), ("project-example", None), ("project-example", "worktree-example")],
)
@pytest.mark.parametrize("explicit_opt_out", [False, True])
def test_remote_acp_honors_native_update_policy_and_explicit_caller_opt_out(
    shell: str, project: str | None, worktree: str | None, explicit_opt_out: bool,
) -> None:
    target = SpawnTarget(
        type="ssh", host="machine-example", cwd="/workspace",
        project=project, worktree_id=worktree, ssh_shell=shell,
        copilot_args=["--no-auto-update"] if explicit_opt_out else [],
    )
    command = _unguarded_remote_cmd(target)
    if " -EncodedCommand " in command:
        command = base64.b64decode(command.rsplit(" -EncodedCommand ", 1)[1]).decode("utf-16-le")
    assert command.count("--no-auto-update") == int(explicit_opt_out)
    assert "--acp" in command and "--stdio" in command
    if project:
        assert "--no-update" in command

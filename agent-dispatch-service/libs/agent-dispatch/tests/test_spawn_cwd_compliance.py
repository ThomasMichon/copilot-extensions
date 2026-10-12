"""CWD-compliance for agent-dispatch's two detached, self-respawning spawn
seams: neither must inherit the caller's ambient cwd, since both are
fire-and-forget/detached processes whose lifetime is not bounded to
whatever repo/worktree checkout happened to be current when they fired
(service-lifecycle-supervision's "nothing pins the plugin payload" rule,
generalized to every deletable checkout)."""

from __future__ import annotations

import os
import subprocess

from agent_dispatch import coordinator_loops, supervisor_registration


class _FakeProc:
    pid = 4242


def test_spawn_self_update_successor_never_inherits_caller_cwd(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **kwargs: captured.update(cmd=cmd, kwargs=kwargs) or _FakeProc(),
    )

    supervisor_registration._spawn_self_update_successor(
        "python.exe", ["supervise", "serve", "--machine", "m"]
    )

    assert captured["kwargs"]["cwd"] == os.path.expanduser("~")


def test_spawn_self_deploy_never_inherits_caller_cwd(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **kwargs: captured.update(cmd=cmd, kwargs=kwargs) or _FakeProc(),
    )

    coordinator_loops._spawn_self_deploy("python.exe")

    assert captured["kwargs"]["cwd"] == os.path.expanduser("~")

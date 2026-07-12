"""Tests for the agent-worktrees embody spawn backend (CLI-backed autopilot)."""

from __future__ import annotations

import subprocess
import types

import pytest

from agent_dispatch import embody


def test_autopilot_prompt_mentions_task_verbs_and_deferred_completion():
    prompt = embody.autopilot_worker_prompt(
        "abc123", coordinator_url="http://c", worker_id="w9"
    )
    assert "abc123" in prompt
    assert "w9" in prompt
    assert "http://c" in prompt
    # The full deferred-completion worker loop, driven under the worktree
    # identity (owner-less claim/start/complete so the task owner stays
    # machine/worktree and live-session tracking can join it).
    assert "agent-dispatch claim --task abc123" in prompt
    assert "agent-dispatch start abc123" in prompt
    assert "agent-dispatch complete abc123" in prompt
    # The progress-beat rhythm (Phase 7 Channel B): report at transitions.
    assert "agent-dispatch progress abc123" in prompt
    assert "--summary" in prompt
    # Autopilot + the deferred-completion guarantee (do not complete early).
    assert "autopilot" in prompt.lower()
    assert "not mark it complete before" in prompt.lower()


def test_embody_available_false_without_cli(monkeypatch):
    monkeypatch.setattr(embody.shutil, "which", lambda _name: None)
    assert embody.embody_available() is False


def test_spawn_embodied_worker_unavailable_when_no_cli(monkeypatch):
    monkeypatch.setattr(embody.shutil, "which", lambda _name: None)
    with pytest.raises(embody.EmbodyUnavailable):
        embody.spawn_embodied_worker(
            "t1", coordinator_url="http://c", worker_id="w1"
        )


def test_spawn_embodied_worker_builds_embody_new_command(monkeypatch):
    captured = {}

    def fake_which(_name):
        return "/usr/bin/agent-worktrees"

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(embody.shutil, "which", fake_which)
    monkeypatch.setattr(embody.subprocess, "run", fake_run)

    embody.spawn_embodied_worker(
        "task-9", coordinator_url="http://c", worker_id="embody-1",
    )
    cmd = captured["cmd"]
    assert cmd[:2] == ["/usr/bin/agent-worktrees", "embody"]
    # A fresh parallel worktree, JSON output, and the driver banner.
    assert "--new" in cmd
    assert "--json" in cmd
    assert cmd[cmd.index("--driver") + 1] == "agent-dispatch"
    # The seed carries the autopilot worker prompt for this task/worker.
    seed = cmd[cmd.index("--seed") + 1]
    assert "task-9" in seed and "embody-1" in seed
    # No verify-timeout appended when not requested.
    assert "--verify-timeout" not in cmd


def test_spawn_embodied_worker_passes_verify_timeout(monkeypatch):
    captured = {}
    monkeypatch.setattr(embody.shutil, "which", lambda _n: "/usr/bin/agent-worktrees")
    monkeypatch.setattr(
        embody.subprocess, "run",
        lambda cmd, **kw: (captured.__setitem__("cmd", cmd)
                           or types.SimpleNamespace(returncode=0, stdout="", stderr="")),
    )
    embody.spawn_embodied_worker(
        "t", coordinator_url="http://c", worker_id="w", verify_timeout=30,
    )
    cmd = captured["cmd"]
    assert cmd[cmd.index("--verify-timeout") + 1] == "30"


def test_spawn_worker_for_uses_embody_backend(monkeypatch):
    """`create --spawn --spawn-backend embody` routes to the embody backend."""
    from agent_dispatch import __main__ as m

    calls = {}

    def fake_spawn(task_id, **kwargs):
        calls["task_id"] = task_id
        calls["driver"] = kwargs.get("coordinator_url")
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(embody, "embody_available", lambda: True)
    monkeypatch.setattr(embody, "spawn_embodied_worker", fake_spawn)
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")

    args = types.SimpleNamespace(
        spawn_backend="embody", url=None, verify_timeout=0,
        spawn_agent="task-worker", run_async=False,
    )
    m._spawn_worker_for(args, {"id": "T7"})
    assert calls["task_id"] == "T7"


def test_spawn_worker_for_embody_degrades_to_bridge(monkeypatch):
    """When agent-worktrees is absent, the embody backend falls back to bridge."""
    from agent_dispatch import __main__ as m
    from agent_dispatch import bridge

    bridge_calls = {}

    monkeypatch.setattr(embody, "embody_available", lambda: False)
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")

    def fake_bridge_spawn(task_id, **kwargs):
        bridge_calls["task_id"] = task_id
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(bridge, "spawn_worker", fake_bridge_spawn)

    args = types.SimpleNamespace(
        spawn_backend="embody", url=None, verify_timeout=0,
        spawn_agent="task-worker", run_async=False,
    )
    m._spawn_worker_for(args, {"id": "T8"})
    assert bridge_calls["task_id"] == "T8"

"""Tests for restricted-venue-targets Phase 5/7 per-task workspace lifecycle.

Verifies ``git worktree add``/``remove`` are invoked against the container's
already-materialized base checkout, scoped under ``workspace_folder/tasks/
<task_id>``, and that hostile task ids / refs are rejected before any exec
happens (no shell injection or path traversal surface).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_containers import workspace as workspace_mod
from agent_containers.config import ContainersConfig, FleetConfig
from agent_containers.resolver import LiveExecTarget


def _ok(stdout: str = ""):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _fail(stderr: str = "boom"):
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)


def _target(**overrides) -> LiveExecTarget:
    base = dict(
        name="example-1",
        container_id="a" * 64,
        config=ContainersConfig(),
        fleet=FleetConfig(devcontainer_path="D:/src/example"),
        info=None,
        actual_profile="restricted",
        user="vscode",
        workspace_folder="/workspace",
        acp_command="copilot --acp --stdio",
    )
    base.update(overrides)
    return LiveExecTarget(**base)


@pytest.fixture(autouse=True)
def _resolve_live_exec_target(monkeypatch):
    monkeypatch.setattr(
        workspace_mod, "resolve_live_exec_target", lambda _name: _target()
    )


def test_create_task_workspace_runs_git_worktree_add_under_workspace_folder():
    calls = []

    def fake_docker_exec(args, timeout=30.0):
        calls.append(args)
        return _ok()

    path = workspace_mod.create_task_workspace(
        "example-1", "task-42", "pr/some-branch", docker_exec=fake_docker_exec
    )

    assert path == "/workspace/tasks/task-42"
    assert len(calls) == 1
    argv = calls[0]
    assert argv[:3] == ["exec", "-u", "vscode"]
    assert argv[3] == "example-1"
    script = argv[-1]
    assert "cd /workspace" in script
    assert "git worktree add --force /workspace/tasks/task-42 pr/some-branch" in script


def test_create_task_workspace_raises_on_exec_failure():
    def fake_docker_exec(args, timeout=30.0):
        return _fail("fatal: not a git repository")

    with pytest.raises(workspace_mod.WorkspaceError, match="not a git repository"):
        workspace_mod.create_task_workspace(
            "example-1", "task-1", "master", docker_exec=fake_docker_exec
        )


@pytest.mark.parametrize(
    "task_id",
    ["", "-leading-dash", "has space", "has/slash", "a" * 65, "semi;colon"],
)
def test_create_task_workspace_rejects_unsafe_task_id(task_id):
    def fake_docker_exec(args, timeout=30.0):
        raise AssertionError("docker exec must not run for an unsafe task_id")

    with pytest.raises(workspace_mod.WorkspaceError, match="task_id"):
        workspace_mod.create_task_workspace(
            "example-1", task_id, "master", docker_exec=fake_docker_exec
        )


@pytest.mark.parametrize(
    "ref",
    ["", "-oPOption", "has space", "has;semicolon", "has$(cmd)", "a/../b"],
)
def test_create_task_workspace_rejects_unsafe_ref(ref):
    def fake_docker_exec(args, timeout=30.0):
        raise AssertionError("docker exec must not run for an unsafe ref")

    with pytest.raises(workspace_mod.WorkspaceError, match="ref"):
        workspace_mod.create_task_workspace(
            "example-1", "task-1", ref, docker_exec=fake_docker_exec
        )


def test_remove_task_workspace_noops_when_not_registered():
    calls = []

    def fake_docker_exec(args, timeout=30.0):
        calls.append(args)
        return _ok(stdout="worktree /workspace\nbranch refs/heads/main\n\n")

    workspace_mod.remove_task_workspace(
        "example-1", "task-42", docker_exec=fake_docker_exec
    )

    # Only the `git worktree list` probe ran -- no remove attempted.
    assert len(calls) == 1
    assert "git worktree list --porcelain" in calls[0][-1]


def test_remove_task_workspace_removes_when_registered():
    calls = []

    def fake_docker_exec(args, timeout=30.0):
        calls.append(args)
        if "git worktree list" in args[-1]:
            return _ok(
                stdout=(
                    "worktree /workspace\n"
                    "branch refs/heads/main\n"
                    "\n"
                    "worktree /workspace/tasks/task-42\n"
                    "branch refs/heads/pr/some-branch\n"
                    "\n"
                )
            )
        return _ok()

    workspace_mod.remove_task_workspace(
        "example-1", "task-42", docker_exec=fake_docker_exec
    )

    assert len(calls) == 2
    remove_script = calls[1][-1]
    assert "git worktree remove --force /workspace/tasks/task-42" in remove_script
    assert "git worktree prune" in remove_script


def test_remove_task_workspace_raises_on_list_failure():
    def fake_docker_exec(args, timeout=30.0):
        return _fail("no such container")

    with pytest.raises(workspace_mod.WorkspaceError, match="no such container"):
        workspace_mod.remove_task_workspace(
            "example-1", "task-1", docker_exec=fake_docker_exec
        )


def test_remove_task_workspace_raises_on_remove_failure():
    def fake_docker_exec(args, timeout=30.0):
        if "git worktree list" in args[-1]:
            return _ok(stdout="worktree /workspace/tasks/task-1\nbranch refs/heads/x\n\n")
        return _fail("worktree is dirty")

    with pytest.raises(workspace_mod.WorkspaceError, match="worktree is dirty"):
        workspace_mod.remove_task_workspace(
            "example-1", "task-1", docker_exec=fake_docker_exec
        )


def test_remove_task_workspace_rejects_unsafe_task_id():
    def fake_docker_exec(args, timeout=30.0):
        raise AssertionError("docker exec must not run for an unsafe task_id")

    with pytest.raises(workspace_mod.WorkspaceError, match="task_id"):
        workspace_mod.remove_task_workspace(
            "example-1", "bad id", docker_exec=fake_docker_exec
        )

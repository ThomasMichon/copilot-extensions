"""Transport-neutral names and compatibility with retained creation identities."""

from __future__ import annotations

import pytest

from agent_worktrees import tracking
from agent_worktrees.creation_tail_repair import _allocation
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity
from agent_worktrees.worktree_naming import prospective_session_worktree_id, session_worktree_id


def _record(worktree_id: str) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id=worktree_id, branch=f"worktree/{worktree_id}",
        worktree_path="/tmp/worktree", repo="project", machine="host-wsl",
        platform="wsl", started_at="", last_resumed_at="", resume_count=0,
        title=None, status="active", completed_at=None,
    )


@pytest.mark.parametrize("machine,platform,prefix", [
    ("host", "windows", "host-win"),
    ("host-win", "windows", "host-win"),
    ("host-windows", "windows", "host-windows"),
    ("host-win", "Windows", "host-win"),
    ("host-windows", "WINDOWS", "host-windows"),
    ("host-WSL", "wsl", "host-WSL"),
    ("host-WSL", "WSL", "host-WSL"),
    ("host-linux", "linux", "host-linux"),
    ("host-macos", "macos", "host-macos"),
    ("host-wsl-worker", "wsl", "host-wsl-worker-wsl"),
    ("host-wsl", "windows", "host-wsl-win"),
])
def test_session_name_includes_its_environment_once(
    machine: str, platform: str, prefix: str,
) -> None:
    assert session_worktree_id(machine, platform, "20261013-100000", "abcd") == (
        f"{prefix}-20261013-100000-abcd"
    )


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("suffix", ["abcd", "a" * 32])
def test_creation_repair_recognizes_both_name_generations(
    legacy: bool, suffix: str,
) -> None:
    identity = session_worktree_id(
        "host-wsl", "wsl", "20261013-100000", suffix, legacy=legacy,
    )
    record = _record(identity)
    assert _allocation(record) == ("20261013-100000", suffix, "wsl")


def test_creation_repair_rejects_another_machine_prefix() -> None:
    record = _record("other-wsl-20261013-100000-abcd")
    with pytest.raises(ValueError, match="verifiable native creation allocation"):
        _allocation(record)


def test_creation_repair_preserves_a_versioned_physical_name_suffix() -> None:
    record = _record("host-wsl-wsl-20261013-100000-abcd")
    record.machine_identity = WorktreeMachineIdentity("host-wsl", "wsl", "guest-space")

    assert _allocation(record) == ("20261013-100000", "abcd", "wsl")

    record.worktree_id = "host-wsl-20261013-100000-abcd"
    with pytest.raises(ValueError, match="verifiable native creation allocation"):
        _allocation(record)


@pytest.mark.parametrize("physical", [
    "rack/desk", r"rack\desk", "D:desk", "rack..desk", ".desk", "rack@{desk}",
    "rack desk", "rack\tpeer", "rack~desk", "rack^desk", "rack?desk", "rack*desk",
    "rack[desk", 'rack"desk', "rack<desk", "rack>desk", "rack|desk",
    "CON.desk", "COM1.desk", "lpt9.desk", "COM¹.desk",
])
def test_prospective_name_refuses_non_component_physical_identity(physical: str) -> None:
    identity = WorktreeMachineIdentity(physical, "wsl", "guest-space")
    with pytest.raises(ValueError, match="physical machine.*path"):
        prospective_session_worktree_id(identity, "20261021-010203", "abcd")


@pytest.mark.parametrize("physical", ["CON", "COM1", "CONSOLE", "work.station", "dèsktop"])
def test_prospective_name_preserves_valid_component_names(physical: str) -> None:
    identity = WorktreeMachineIdentity(physical, "wsl", "guest-space")
    assert prospective_session_worktree_id(identity, "20261021-010203", "abcd") == (
        f"{physical}-wsl-20261021-010203-abcd"
    )

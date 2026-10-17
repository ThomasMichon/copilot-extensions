"""Transport-neutral names and compatibility with retained creation identities."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_worktrees.creation_tail_repair import _allocation
from agent_worktrees.worktree_naming import session_worktree_id


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
    record = SimpleNamespace(machine="host-wsl", platform="wsl", worktree_id=identity)
    assert _allocation(record) == ("20261013-100000", suffix, "wsl")


def test_creation_repair_rejects_another_machine_prefix() -> None:
    record = SimpleNamespace(
        machine="host-wsl", platform="wsl",
        worktree_id="other-wsl-20261013-100000-abcd",
    )
    with pytest.raises(ValueError, match="verifiable native creation allocation"):
        _allocation(record)

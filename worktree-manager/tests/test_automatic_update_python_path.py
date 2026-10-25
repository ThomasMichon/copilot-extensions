"""Legacy automatic callers must not publish the user's persistent Python PATH."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_update_python_path import public_python as public_python
from worktree_manager import update_python_path as path_update


@pytest.mark.parametrize("arguments", [
    ["--automatic-trigger", "manager"],
    ["--automatic-trigger=manager"],
    ["--automatic-trigger", "external", "--controller", "fixture-controller"],
    ["--automatic-trigger=external", "--controller=fixture-controller"],
])
def test_legacy_automatic_completion_keeps_registry_and_environment_unchanged(
    public_python: SimpleNamespace, arguments: list[str],
) -> None:
    state = public_python
    assert path_update.finish_update(["update", *arguments], 0) == 0
    assert state.value == (state.original, 2)
    assert state.writes == [] and state.notifications == 0
    assert state.environment["PATH"] == state.original

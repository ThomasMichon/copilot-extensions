"""The source-engine compatibility bootstrap includes its identity dependency."""

from __future__ import annotations

import importlib.util

from worktree_manager.agent_worktrees_runtime import ensure_engine_runtime


def test_engine_bootstrap_includes_worktree_identity() -> None:
    ensure_engine_runtime()
    assert importlib.util.find_spec("worktree_identity") is not None

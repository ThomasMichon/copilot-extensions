"""The source-engine compatibility bootstrap includes its identity dependency."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from worktree_manager.agent_worktrees_runtime import ENGINE_SOURCE_ENV, ensure_engine_runtime


@pytest.mark.parametrize("layout", ["checkout", "payload"])
def test_engine_bootstrap_includes_worktree_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str,
) -> None:
    plugin = tmp_path / "plugins" / "agent-worktrees"
    source = plugin / "src"
    (source / "agent_worktrees").mkdir(parents=True)
    libraries = tmp_path / "libs" if layout == "checkout" else plugin / "libs"
    identity_source = libraries / "worktree-identity" / "src"
    identity_source.mkdir(parents=True)
    monkeypatch.setenv(ENGINE_SOURCE_ENV, str(source))
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert str(identity_source) not in sys.path

    assert ensure_engine_runtime() == source
    assert str(identity_source) in sys.path

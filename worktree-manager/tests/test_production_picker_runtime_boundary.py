from __future__ import annotations

import ast
from pathlib import Path

import pytest


@pytest.mark.guard
def test_production_picker_has_no_direct_agent_worktrees_imports():
    root = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "worktree_manager"
        / "production_picker"
    )
    forbidden_files = {
        "_engine_runtime.py",
        "config.py",
        "pr_ops.py",
        "reclaim.py",
        "sessions.py",
        "tracking.py",
    }

    assert not any((root / name).exists() for name in forbidden_files)

    violations: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names if alias.name.split(".", 1)[0] == "agent_worktrees"]
                if names:
                    violations.append(f"{path.name}:{node.lineno}: import {', '.join(names)}")
                compatibility = [
                    alias.name
                    for alias in node.names
                    if alias.name == "worktree_manager.agent_worktrees_runtime"
                ]
                if compatibility:
                    violations.append(
                        f"{path.name}:{node.lineno}: import {', '.join(compatibility)}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".", 1)[0] == "agent_worktrees":
                    violations.append(f"{path.name}:{node.lineno}: from {node.module} import ...")
                if node.module == "worktree_manager.agent_worktrees_runtime":
                    violations.append(
                        f"{path.name}:{node.lineno}: from {node.module} import ..."
                    )

    assert violations == []

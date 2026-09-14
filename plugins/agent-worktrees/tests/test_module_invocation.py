"""Regression test for #2650: `python -m agent_worktrees` must not crash.

The binstub (and every documented "direct" invocation) launches this CLI as
`python -m agent_worktrees`, which runs `__main__.py` under the synthetic
`"__main__"` module name -- a SEPARATE `sys.modules` entry from its real
qualified name `agent_worktrees.__main__`. `pr_cli.py` does `from . import
__main__ as core` at module scope; absent the `sys.modules.setdefault` alias
`__main__.py` installs at the top of the file, that import finds no
`agent_worktrees.__main__` entry yet and re-executes the entire module a
second time, crashing with "partially initialized module 'agent_worktrees.pr_cli'
has no attribute ..." once it reaches the `pr_cli` re-import while the first
`pr_cli` import is still in flight.

This is a real subprocess test (not a monkeypatched import) because the bug
only reproduces via genuine `-m` execution -- a plain `from agent_worktrees
import __main__` (as pytest's own collection does) never hits it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PLUGIN_SRC = str(Path(__file__).resolve().parent.parent / "src")


def _run_module(*args: str) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, "PYTHONPATH": PLUGIN_SRC}
    return subprocess.run(
        [sys.executable, "-m", "agent_worktrees", *args],
        capture_output=True, text=True, timeout=30, env=env,
    )


def test_help_does_not_crash_on_circular_import():
    result = _run_module("--help")
    assert result.returncode == 0, result.stderr
    assert "partially initialized module" not in result.stderr
    assert "agent-worktrees" in result.stdout


def test_pr_watch_usage_does_not_crash_on_circular_import():
    # pr_cli-backed verb: the exact command family whose module split
    # introduced the circular import in the first place.
    result = _run_module("pr-watch")
    assert "partially initialized module" not in result.stderr
    assert "circular import" not in result.stderr

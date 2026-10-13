"""Guard: agent-dispatch-service's bootstrap.sh must be tracked executable in
git (the +x bit), or a fresh checkout on Linux/WSL/macOS silently loses its
exec permission. Mirrors worktree-manager's own
``test_launch_scripts_tracked_executable.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


def test_posix_bootstrap_is_tracked_executable() -> None:
    path = "services/dispatch/bootstrap.sh"
    result = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--stage", "--", path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("Git index metadata is unavailable")
    if not result.stdout.strip():
        pytest.fail(f"{path} is not tracked by git")
    mode = result.stdout.split(maxsplit=1)[0]
    assert mode == "100755"

"""Guard: the POSIX interactive mux launch scripts must be tracked executable
in git (the +x bit), or a fresh checkout on Linux/WSL/macOS silently loses
their exec permission.

Migrated (new coverage, extracted from plugins/agent-worktrees/tests/
test_first_install_bootstrap.py's `test_direct_posix_payload_entrypoints_are_
tracked_executable`) as part of the Phase 3b Sub-slice 2a Step 2 cutover
(efforts/active/worktree-manager-control-plane/phase-3b-mux-relocation.md):
Worktree Manager's bin/ is now the sole copy of these scripts.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_posix_launch_scripts_are_tracked_executable() -> None:
    paths = (
        "worktree-manager/bin/launch-session.sh",
        "worktree-manager/bin/pane-wrapper.sh",
        "worktree-manager/bin/session-options.sh",
        "worktree-manager/bin/apply-mux-keybinds.sh",
    )
    result = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--stage", "--", *paths],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("Git index metadata is unavailable")
    modes = {
        line.split(maxsplit=1)[1].split("\t", maxsplit=1)[1]: line.split()[0]
        for line in result.stdout.splitlines()
    }
    assert modes == {path: "100755" for path in paths}

"""Temporary regression guards for the mux-relocation rollback path.

Phase 3b Sub-slice 2a Step 2 repoints ``cmd_launch`` to the Worktree Manager's
relocated launcher, but this PR intentionally keeps the in-plugin launcher
files deployed as a rollback path until the relocated path is proven live on
real hardware. See ``efforts/active/worktree-manager-control-plane/
phase-3b-mux-relocation.md`` for the explicit deviation. Delete or update this
guard only in the later, deliberate cleanup PR that removes that rollback.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "bin"


def test_legacy_mux_scripts_remain_deployed_for_temporary_rollback():
    for name in (
        "launch-session.cmd",
        "launch-session.ps1",
        "launch-session.sh",
        "pane-wrapper.ps1",
        "pane-wrapper.sh",
    ):
        assert (ROOT / name).is_file(), (
            f"{name} is intentionally retained as the temporary rollback path "
            "for Phase 3b mux relocation"
        )

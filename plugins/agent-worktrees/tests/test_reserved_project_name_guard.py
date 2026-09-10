"""Regression test: `agent-worktrees` must never be an auto-inferred project
name, and the reserved-name warning must fire only for a genuine explicit
mistake -- not on every routine invocation from a directory literally named
`agent-worktrees` (whose ~/.agent-worktrees/config.yaml -- the tool's OWN
runtime config -- always exists once installed, making the CWD-inference path
a guaranteed false positive before this fix).
"""

from __future__ import annotations

from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]


def test_ps1_reserved_name_excluded_from_cwd_inference() -> None:
    ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    infer = ps1.split("if (-not $ProjectName) {", 1)[1].split(
        "# Don't auto-adopt the CWD repo", 1
    )[0]
    assert "$cwdName -ne 'agent-worktrees'" in infer, (
        "cwd-inference must skip the reserved name before it can ever be "
        "assigned to $ProjectName"
    )
    guard = ps1.split("if ($ProjectName -eq 'agent-worktrees') {", 1)[1]
    assert "Ignoring reserved runtime name" in guard, (
        "the explicit-mistake warning must still exist as a safety net"
    )


def test_sh_reserved_name_excluded_from_cwd_inference() -> None:
    sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    infer = sh.split('if [[ -n "$PROJECT_NAME_ARG" ]]; then', 1)[1].split(
        'if [[ "$PROJECT_NAME" == "agent-worktrees" ]]; then', 1
    )[0]
    assert '"$_cwd_name" != "agent-worktrees"' in infer, (
        "cwd-inference must skip the reserved name before it can ever be "
        "assigned to $PROJECT_NAME"
    )
    guard = sh.split('if [[ "$PROJECT_NAME" == "agent-worktrees" ]]; then', 1)[1]
    assert "Ignoring reserved runtime name" in guard, (
        "the explicit-mistake warning must still exist as a safety net"
    )

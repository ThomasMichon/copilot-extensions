"""Anchor repo hygiene -- detect uncommitted work and stash entries.

The anchor repo (main git checkout) should stay clean between worktree
sessions.  This module detects accumulated state -- uncommitted changes
and stash entries -- and reports them so the operator can rescue or
discard the work.

Used by:
- sessionStart hook (warn only, never blocks)
- Finalization flow (blocks on dirty anchor, warns on stash)
- CLI: ``agent-worktrees anchor-check``
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import git_ops, output


@dataclass
class AnchorReport:
    """Result of an anchor hygiene check."""

    anchor_path: str
    is_clean: bool
    dirty_files: list[str] = field(default_factory=list)
    stash_entries: list[str] = field(default_factory=list)

    @property
    def has_dirty_files(self) -> bool:
        return bool(self.dirty_files)

    @property
    def has_stash(self) -> bool:
        return bool(self.stash_entries)


def _resolve_toplevel(repo_path: str | Path) -> Path:
    """Resolve the git toplevel from an arbitrary path inside a repo."""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip())
    except Exception:
        pass
    return Path(repo_path)


def check_anchor(repo_path: str | Path) -> AnchorReport:
    """Check the anchor repo for uncommitted work and stash entries.

    Args:
        repo_path: Any path inside a git repo or worktree.
            The anchor is resolved automatically.

    Returns:
        AnchorReport with the findings.
    """
    toplevel = _resolve_toplevel(repo_path)
    anchor = git_ops.resolve_to_anchor(toplevel)
    anchor_str = str(anchor)

    # Check for uncommitted changes (staged + unstaged + untracked)
    dirty_files: list[str] = []
    status_r = git_ops.git(
        "status", "--porcelain=v1", cwd=anchor_str, check=False,
    )
    if status_r.returncode == 0 and status_r.stdout.strip():
        dirty_files = [
            line for line in status_r.stdout.splitlines() if line.strip()
        ]

    # Check for stash entries
    stash_entries: list[str] = []
    stash_r = git_ops.git(
        "stash", "list", "--format=%gd: %s", cwd=anchor_str, check=False,
    )
    if stash_r.returncode == 0 and stash_r.stdout.strip():
        stash_entries = [
            line for line in stash_r.stdout.splitlines() if line.strip()
        ]

    is_clean = not dirty_files and not stash_entries
    return AnchorReport(
        anchor_path=anchor_str,
        is_clean=is_clean,
        dirty_files=dirty_files,
        stash_entries=stash_entries,
    )


def report_anchor_state(report: AnchorReport, *, quiet: bool = False) -> None:
    """Print a human-readable summary of anchor hygiene state.

    Args:
        report: The anchor hygiene report.
        quiet: If True, only print if there are issues.
    """
    if report.is_clean:
        if not quiet:
            output.ok(f"Anchor repo clean: {report.anchor_path}")
        return

    if report.has_dirty_files:
        output.warn(
            f"Anchor repo has {len(report.dirty_files)} uncommitted "
            f"file(s): {report.anchor_path}"
        )
        for f in report.dirty_files[:10]:
            print(f"       {f}")
        if len(report.dirty_files) > 10:
            print(f"       ... and {len(report.dirty_files) - 10} more")

    if report.has_stash:
        output.warn(
            f"Anchor repo has {len(report.stash_entries)} stash "
            f"entr{'y' if len(report.stash_entries) == 1 else 'ies'}: "
            f"{report.anchor_path}"
        )
        for entry in report.stash_entries[:5]:
            print(f"       {entry}")
        if len(report.stash_entries) > 5:
            print(f"       ... and {len(report.stash_entries) - 5} more")


def report_as_json(report: AnchorReport) -> dict:
    """Return the report as a JSON-serializable dict."""
    return {
        "version": 1,
        "anchor_path": report.anchor_path,
        "is_clean": report.is_clean,
        "dirty_files": report.dirty_files,
        "stash_entries": report.stash_entries,
        "dirty_file_count": len(report.dirty_files),
        "stash_entry_count": len(report.stash_entries),
    }

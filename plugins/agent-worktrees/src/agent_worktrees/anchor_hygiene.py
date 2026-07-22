"""Anchor repo hygiene -- detect uncommitted work, stash entries, and staleness.

The anchor repo (main git checkout) should stay clean AND current between
worktree sessions.  This module detects accumulated local state -- uncommitted
changes and stash entries -- and how far the anchor has fallen **behind its
upstream** (origin/<default-branch>).  A stale anchor is a silent hazard: the
Worktree Picker and status flows read config (e.g. ``machines.yaml``) from the
anchor, so when it lags upstream a machine renamed there (e.g. an SSH alias
change) can show as "fails to resolve" even though it's reachable.

Used by:
- sessionStart hook (warn only, never blocks) -- the launcher fetches the anchor
  pre-launch, so the behind-count is accurate there without a second fetch.
- Finalization flow (blocks on dirty anchor, warns on stash/behind)
- CLI: ``agent-worktrees anchor-check`` (pass ``--fetch`` for a fresh count)
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
    branch: str = ""
    tracking: str = ""
    behind_count: int = 0

    @property
    def has_dirty_files(self) -> bool:
        return bool(self.dirty_files)

    @property
    def has_stash(self) -> bool:
        return bool(self.stash_entries)

    @property
    def is_behind(self) -> bool:
        return self.behind_count > 0


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


def _behind_upstream(anchor_str: str, *, fetch: bool) -> tuple[str, str, int]:
    """Return (branch, upstream_ref, behind_count) for the anchor.

    behind_count is how many commits the anchor's HEAD is behind its upstream
    (e.g. origin/main).  Returns ("", "", 0) when the branch has no upstream,
    is detached, or git errors -- freshness is best-effort and never fatal.
    When *fetch* is True, refresh the upstream ref first (the sessionStart path
    skips this because the launcher already fetched the anchor pre-launch).
    """
    branch_r = git_ops.git(
        "rev-parse", "--abbrev-ref", "HEAD", cwd=anchor_str, check=False,
    )
    branch = branch_r.stdout.strip() if branch_r.returncode == 0 else ""
    if not branch or branch == "HEAD":
        return branch, "", 0  # detached or unknown -- no meaningful behind-count

    up_r = git_ops.git(
        "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}",
        cwd=anchor_str, check=False,
    )
    if up_r.returncode != 0 or not up_r.stdout.strip():
        return branch, "", 0  # no upstream configured
    upstream = up_r.stdout.strip()

    if fetch and "/" in upstream:
        remote = upstream.split("/", 1)[0]
        git_ops.git("fetch", "--quiet", remote, branch, cwd=anchor_str, check=False)

    count_r = git_ops.git(
        "rev-list", "--count", f"HEAD..{upstream}", cwd=anchor_str, check=False,
    )
    behind = 0
    if count_r.returncode == 0 and count_r.stdout.strip().isdigit():
        behind = int(count_r.stdout.strip())
    return branch, upstream, behind


def check_anchor(repo_path: str | Path, *, fetch: bool = False) -> AnchorReport:
    """Check the anchor repo for uncommitted work, stash entries, and staleness.

    Args:
        repo_path: Any path inside a git repo or worktree.
            The anchor is resolved automatically.
        fetch: Refresh the upstream ref before computing the behind-count.
            Default False -- the sessionStart launcher already fetches the anchor
            pre-launch, so the count is accurate there without a second fetch.
            Pass True for a standalone check (e.g. ``anchor-check --fetch``).

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
    branch, tracking, behind_count = _behind_upstream(anchor_str, fetch=fetch)
    return AnchorReport(
        anchor_path=anchor_str,
        is_clean=is_clean,
        dirty_files=dirty_files,
        stash_entries=stash_entries,
        branch=branch,
        tracking=tracking,
        behind_count=behind_count,
    )


def report_anchor_state(report: AnchorReport, *, quiet: bool = False) -> None:
    """Print a human-readable summary of anchor hygiene state.

    Args:
        report: The anchor hygiene report.
        quiet: If True, only print if there are issues.
    """
    # Staleness is independent of local cleanliness -- a clean anchor can still
    # be behind upstream, which silently staled the config the picker reads.
    if report.is_behind:
        tracking = report.tracking or "upstream"
        output.warn(
            f"Anchor repo is {report.behind_count} commit(s) behind "
            f"{tracking}: {report.anchor_path}"
        )
        print(
            "       Picker/status config here (e.g. machines.yaml) may be STALE -- a"
        )
        print(
            "       machine can show as 'fails to resolve' after an upstream alias/host"
        )
        print(
            f"       change. Fix: git -C \"{report.anchor_path}\" pull --ff-only"
        )

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
        "branch": report.branch,
        "tracking": report.tracking,
        "behind_count": report.behind_count,
        "is_behind": report.is_behind,
    }

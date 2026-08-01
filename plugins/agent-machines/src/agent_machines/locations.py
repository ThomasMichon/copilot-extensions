"""Location addressing: portable anchors, worktree-globs, and a classifier.

Permission / trusted-folder locations on a real machine are ephemeral absolute
worktree paths -- not portable. Requirement packages therefore key on **classes**,
not literals:

* ``$HOME``            -- the user home directory
* ``$REPO(<name>)``    -- a named repo's checkout root
* ``$WORKTREES/<repo>/*`` -- every worktree of a repo (declare a grant once, apply
  to every current and future worktree -- also fixing today's re-prompt-per-worktree wart)

A **portability classifier** separates a manifestable location (resolvable to a
class) from machine-junk (a bare ephemeral literal) that must never be captured.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path

_ANCHOR_RE = re.compile(r"^\$(HOME|REPO|WORKTREES)(?:\(([^)]*)\))?(.*)$")


@dataclass
class ResolvedLocation:
    """A location class resolved (or matched) against concrete paths on a machine."""

    spec: str
    kind: str  # home | repo | worktree-glob | literal
    pattern: str


def resolve(
    spec: str, home: Path, repo_paths: dict[str, Path], worktrees_root: Path
) -> ResolvedLocation:
    """Resolve a location *class* spec into a concrete path glob pattern."""
    match = _ANCHOR_RE.match(spec)
    if not match:
        return ResolvedLocation(spec=spec, kind="literal", pattern=spec)
    anchor, arg, tail = match.groups()
    tail = tail or ""
    if anchor == "HOME":
        return ResolvedLocation(spec, "home", str(home) + tail)
    if anchor == "REPO":
        base = repo_paths.get(arg or "")
        return ResolvedLocation(spec, "repo", (str(base) + tail) if base else spec)
    # WORKTREES: $WORKTREES/<repo>/*  (arg carries the repo when written $WORKTREES(repo))
    repo = arg or tail.strip("/\\").split("/", 1)[0].split("\\", 1)[0]
    return ResolvedLocation(spec, "worktree-glob", str(worktrees_root / repo / "*"))


def matches(resolved: ResolvedLocation, concrete_path: str) -> bool:
    """True when a concrete filesystem path matches a resolved location class."""
    norm = concrete_path.replace("\\", "/")
    pat = resolved.pattern.replace("\\", "/")
    if resolved.kind == "worktree-glob":
        return fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(norm, pat + "/*")
    return norm == pat or fnmatch.fnmatch(norm, pat + "/*") or norm == pat.rstrip("/*")


def is_manifestable(concrete_path: str, home: Path, known_roots: list[Path]) -> bool:
    """A location is manifestable when it resolves under a known, stable root.

    A bare ephemeral literal that lives under no known repo/worktrees root is
    machine-junk (leave it to ``prune``; never capture it into a manifest).
    """
    norm = Path(concrete_path)
    for root in [home, *known_roots]:
        try:
            norm.relative_to(root)
            return True
        except ValueError:
            continue
    return False

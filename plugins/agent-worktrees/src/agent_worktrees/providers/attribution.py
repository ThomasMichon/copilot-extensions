"""Source-worktree attribution markers embedded in PR bodies.

A PR opened by agent-worktrees carries a durable, hidden HTML-comment marker
naming the **source worktree** (+ machine / session / head SHA).  This makes
any PR traceable back to its origin without session-store archaeology -- the
gap that motivated this effort (PR #1059 had no traceable source).

The marker is a single HTML comment, invisible in rendered Markdown:

    <!-- agent-worktrees:source worktree=<id> machine=<m> session=<sid> head=<sha> -->
"""

from __future__ import annotations

import re

_MARKER_RE = re.compile(
    r"<!--\s*agent-worktrees:source\s+(?P<fields>.*?)\s*-->",
    re.DOTALL,
)
_FIELD_RE = re.compile(r"(\w+)=(\S+)")


def build_marker(
    worktree_id: str,
    *,
    machine: str = "",
    session: str = "",
    head: str = "",
) -> str:
    """Build the hidden source-attribution comment for a PR body."""
    parts = [f"worktree={worktree_id}"]
    if machine:
        parts.append(f"machine={machine}")
    if session:
        parts.append(f"session={session}")
    if head:
        parts.append(f"head={head}")
    return f"<!-- agent-worktrees:source {' '.join(parts)} -->"


def append_marker(body: str, marker: str) -> str:
    """Append *marker* to a PR *body*, replacing any existing source marker."""
    stripped = _MARKER_RE.sub("", body or "").rstrip()
    if stripped:
        return f"{stripped}\n\n{marker}\n"
    return f"{marker}\n"


def parse_marker(body: str) -> dict[str, str] | None:
    """Extract the source-attribution fields from a PR *body* (or None)."""
    m = _MARKER_RE.search(body or "")
    if not m:
        return None
    return dict(_FIELD_RE.findall(m.group("fields")))

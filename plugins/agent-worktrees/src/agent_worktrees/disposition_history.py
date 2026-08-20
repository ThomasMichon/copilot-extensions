"""Durable per-worktree DISPOSITION HISTORY -- the trajectory of a worktree's
agent-asserted summary / title / follow-up over time.

The tracking record (``<id>.yaml``) keeps only the *latest* disposition ("latest
wins"), so it answers "where does this worktree stand *now*" but not "what has
this worktree *been doing*". This module appends one durable JSONL entry per
disposition write to a sidecar alongside the record --
``~/.<project>/worktrees/<id>.history.jsonl`` -- so an agent picking up a
worktree can read the tail and immediately grok its arc (focus shifts, what got
done, when follow-ups opened/closed) without spelunking session transcripts.

Contract:

- **Append-only, one JSON object per line.** Each entry is a self-contained
  snapshot of the disposition AFTER the write, plus which fields the write
  touched::

      {"at": "<iso>", "changed": ["summary","title"], "title": "<str|null>",
       "summary": "<str>", "follow_up": <bool>}

  ``at`` mirrors the record's ``status_note_at`` stamp, so history lines
  correlate 1:1 with the record's disposition timeline.
- **Durable, not time-pruned.** Unlike the machine-wide session ``activity.jsonl``
  (7-day window), this is the worktree's own memory -- it lives and dies with the
  tracking record. It is bounded only by a generous entry **cap** (trim-oldest)
  so a pathological writer can't grow it without limit.
- **Best-effort + fail-open.** History is an aid, never load-bearing: every
  operation swallows its own errors so a history hiccup never breaks a
  disposition write, a status read, or a record removal.

Cleanup: :func:`remove` is called wherever a tracking ``<id>.yaml`` is unlinked
(full worktree removal / prune), so the sidecar never outlives its record.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config as cfg

#: Keep at most this many entries; on overflow the oldest are trimmed. A
#: disposition write is human/agent-initiated (not per-tool), so this is ample.
MAX_ENTRIES = 500

#: The disposition fields a history entry snapshots / can mark as changed.
_FIELDS = ("summary", "title", "follow_up")


def history_path(worktree_id: str) -> Path:
    """Path to a worktree's disposition-history sidecar (not created)."""
    return cfg.tracking_dir() / f"{worktree_id}.history.jsonl"


def append(
    worktree_id: str,
    *,
    at: str | None,
    summary: str,
    title: str | None,
    follow_up: bool,
    changed: list[str],
) -> None:
    """Append one disposition snapshot for *worktree_id*. Best-effort (never
    raises). Trims oldest entries past :data:`MAX_ENTRIES`.

    ``changed`` names the fields THIS write touched (a subset of :data:`_FIELDS`);
    the snapshot carries the resulting values so each line is self-contained.
    """
    try:
        entry: dict[str, Any] = {
            "at": at,
            "changed": [c for c in changed if c in _FIELDS],
            "title": title,
            "summary": summary,
            "follow_up": bool(follow_up),
        }
        path = history_path(worktree_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        if path.exists():
            try:
                lines = path.read_text("utf-8").splitlines()
            except OSError:
                lines = []
        lines.append(json.dumps(entry, ensure_ascii=False))
        if len(lines) > MAX_ENTRIES:
            lines = lines[-MAX_ENTRIES:]
        # Atomic-ish replace so a concurrent reader never sees a torn file.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception:
        # History is advisory -- a failure must never break the disposition write.
        pass


def read(worktree_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Return a worktree's disposition history oldest-first. Malformed lines are
    skipped. ``limit`` keeps only the most recent N entries. Never raises."""
    path = history_path(worktree_id)
    entries: list[dict[str, Any]] = []
    try:
        if not path.exists():
            return []
        for line in path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                entries.append(obj)
    except OSError:
        return []
    if limit is not None and limit > 0:
        return entries[-limit:]
    return entries


def remove(worktree_id: str) -> None:
    """Delete a worktree's disposition-history sidecar. Best-effort (never
    raises) -- called wherever the tracking ``<id>.yaml`` is unlinked."""
    try:
        history_path(worktree_id).unlink(missing_ok=True)
    except OSError:
        pass

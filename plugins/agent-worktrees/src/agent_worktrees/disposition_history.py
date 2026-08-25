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

#: Maximum rendered history-digest size, including any succession header added
#: by the CLI. Stored history remains untouched.
DIGEST_MAX_CHARS = 800

#: Maximum rendered summary/title size for one digest entry.
DIGEST_LABEL_MAX_CHARS = 96
DIGEST_AT_MAX_CHARS = 32
DIGEST_KIND_MAX_CHARS = 20
DIGEST_SESSION_SUFFIX_CHARS = 6
DIGEST_OMITTED = "- ... older entries omitted ..."

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
    kind: str = "status",
    session_id: str | None = None,
) -> None:
    """Append one history entry for *worktree_id*. Best-effort (never raises).
    Trims oldest entries past :data:`MAX_ENTRIES`.

    ``changed`` names the fields THIS write touched (a subset of :data:`_FIELDS`);
    the snapshot carries the resulting values so each line is self-contained.

    ``kind`` classifies the entry -- ``"status"`` (a disposition write; the
    default so every existing caller is unchanged), ``"bind"`` (a session
    declared ownership), or ``"handoff"`` (a terse handoff reference). ``kind``
    is what lets the worktree remember *what kind of thing* each session did, not
    just its latest disposition.

    ``session_id`` tags the entry with the session that wrote it, so the history
    is session-attributed ("each session contributes its own marked entries").
    Both are omitted from the line when at their neutral default (``status`` /
    ``None``) so legacy readers and byte-comparisons of status-only histories are
    unaffected.
    """
    try:
        entry: dict[str, Any] = {
            "at": at,
            "changed": [c for c in changed if c in _FIELDS],
            "title": title,
            "summary": summary,
            "follow_up": bool(follow_up),
        }
        if kind and kind != "status":
            entry["kind"] = kind
        if session_id:
            entry["session"] = session_id
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


def _digest_label(value: Any) -> str:
    """Collapse and bound one rendered digest label without changing storage."""
    text = " ".join(str(value or "").split())
    if len(text) <= DIGEST_LABEL_MAX_CHARS:
        return text
    return text[: DIGEST_LABEL_MAX_CHARS - 3].rstrip() + "..."


def _digest_field(value: Any, max_chars: int, *, default: str) -> str:
    """Collapse and bound an untrusted scalar used in digest metadata."""
    text = " ".join(str(value or "").split())
    if not text:
        return default
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _digest_session_suffix(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    return text[-DIGEST_SESSION_SUFFIX_CHARS:]


def digest(
    worktree_id: str,
    *,
    limit: int = 8,
    max_chars: int = DIGEST_MAX_CHARS,
) -> str:
    """A compact, human/agent-readable recovery digest of a worktree's recent
    history, newest last, or ``""`` when there is none. Never raises.

    This is the **record-first recovery** surface: injected into a fresh or
    successor session at start so it inherits "what this worktree has recently
    been doing" straight from the worktree's own memory -- no second service
    reachable, no successful live handoff required. Deliberately terse (one line
    per entry) so it is cheap to carry as session context.
    """
    try:
        all_entries = read(worktree_id)
        entries = all_entries[-limit:] if limit > 0 else all_entries
        if not entries:
            return ""
        heading = "This worktree's recent history (most recent last):"
        if max_chars < len(heading):
            return ""
        rendered: list[str] = []
        for e in entries:
            at = _digest_field(e.get("at"), DIGEST_AT_MAX_CHARS, default="?")
            kind = _digest_field(
                e.get("kind"), DIGEST_KIND_MAX_CHARS, default="status"
            )
            sess = _digest_session_suffix(e.get("session"))
            sess_tag = f" {sess}" if sess else ""
            flag = " !" if e.get("follow_up") else ""
            title = e.get("title")
            summary = _digest_label(e.get("summary"))
            label = summary or (title or "")
            if kind != "status" and not label:
                label = f"({kind})"
            label = _digest_label(label)
            line = f"- {at} [{kind}{sess_tag}]{flag} {label}".rstrip()
            rendered.append(line)

        selected: list[str] = []
        used = len(heading)
        omitted = len(all_entries) - len(entries)
        marker_cost = 1 + len(DIGEST_OMITTED)
        for line in reversed(rendered):
            added = 1 + len(line)
            needs_marker = omitted > 0 or len(selected) < len(rendered) - 1
            if used + added + (marker_cost if needs_marker else 0) > max_chars:
                omitted += 1
                break
            selected.append(line)
            used += added
        selected.reverse()
        parts = [heading]
        if omitted and len(heading) + marker_cost <= max_chars:
            parts.append(DIGEST_OMITTED)
        parts.extend(selected)
        return "\n".join(parts)
    except Exception:
        return ""

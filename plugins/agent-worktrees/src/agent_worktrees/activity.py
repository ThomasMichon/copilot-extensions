"""Append-only worktree activity log -- high-level lifecycle events.

Records the high-level lifecycle of worktrees and their Copilot/mux
sessions to a machine-global JSONL file at
``~/.agent-worktrees/logs/activity.jsonl``.  Unlike the per-PID launcher
setup logs under ``$TMPDIR/worktree-setup-logs`` (capped at the 10 newest
and wiped on reboot), this log persists across reboots and accumulates a
rolling window of history (default 7 days), so session-lifecycle anomalies
-- e.g. a finalized worktree whose tmux/Copilot session is never reaped --
can be reconstructed after the fact.

Both the Python lifecycle code and the bash launcher append here (the
launcher via ``agent-worktrees activity-log``), so a single file captures
the full picture across processes.

Events are intentionally high-level:

  worktree_created          a new worktree + branch was created
  worktree_resumed          an existing worktree was resumed via the picker
  session_started           a Copilot session registered against a worktree
  session_ended             a Copilot session deregistered
  copilot_exited            the Copilot process exited (launcher)
  mux_attached              a tmux/psmux session was attached/joined (launcher)
  mux_detached              the attach returned -- user detached or session ended
  changes_pushed            worktree content was pushed to the default branch
  worktree_finalized        finalize completed (content on upstream)
  finalize_skipped_removal  finalize left the worktree/branch/session in place
                            (running inside it, or a live session was detected)
  worktree_reaped           cleanup removed a worktree's dir/branch/session

Every record carries ``worktree_id`` and (where known) ``session_id``.

Logging must never break the worktree lifecycle: every public function
swallows its own exceptions.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as cfg

# Rolling retention window. Lines older than this are dropped on prune.
RETENTION_DAYS = 7

# Prune is only attempted once the file grows past this size, keeping the
# common append path cheap. Events are small and infrequent, so this
# triggers rarely (hundreds of sessions).
_PRUNE_SIZE_BYTES = 512 * 1024

_HOSTNAME = socket.gethostname()


def log_path() -> Path:
    """Path to the machine-global activity log."""
    return cfg.install_dir() / "logs" / "activity.jsonl"


def log_event(
    event: str,
    *,
    worktree_id: str | None = None,
    session_id: str | None = None,
    source: str = "python",
    **fields: object,
) -> None:
    """Append a single high-level lifecycle event. Never raises.

    Args:
        event: One of the documented event names (see module docstring).
        worktree_id: The worktree this event concerns.
        session_id: The Copilot session id, if known.
        source: Originating component ("python" or "launcher").
        **fields: Extra context (branch, reason, exit_code, ...). ``None``
            values are dropped.
    """
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "worktree_id": worktree_id,
            "session_id": session_id,
            "pid": os.getpid(),
            "host": _HOSTNAME,
            "source": source,
        }
        for key, value in fields.items():
            if value is not None:
                record[key] = value
        line = json.dumps(record, ensure_ascii=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        _maybe_prune(path)
    except Exception:
        # A diagnostic log must never interfere with the operation it
        # observes -- fail silently.
        pass


def _maybe_prune(path: Path) -> None:
    """Prune lines older than the retention window if the file is large."""
    try:
        if path.stat().st_size < _PRUNE_SIZE_BYTES:
            return
    except OSError:
        return
    _prune(path, RETENTION_DAYS)


def _prune(path: Path, retention_days: int) -> int:
    """Rewrite the log keeping only lines within the retention window.

    Returns the number of lines kept. Best-effort: a concurrent append
    during the rewrite could be lost, which is acceptable for a
    diagnostic log. Unparseable lines are kept.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept: list[str] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.rstrip("\n")
                if not line:
                    continue
                ts = _parse_ts(line)
                if ts is None or ts >= cutoff:
                    kept.append(line)
    except OSError:
        return 0

    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(
            ("\n".join(kept) + "\n") if kept else "", encoding="utf-8"
        )
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
    return len(kept)


def _parse_ts(line: str) -> datetime | None:
    """Extract the UTC timestamp from a log line, or None if unparseable."""
    try:
        ts = datetime.fromisoformat(json.loads(line)["ts"])
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


# ── Reader / viewer ────────────────────────────────────────────────────

_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_since(value: str) -> datetime | None:
    """Parse a --since value into a UTC cutoff datetime.

    Accepts relative durations like ``2d``, ``12h``, ``30m``, ``1w`` or an
    ISO date/datetime (``2026-06-09`` or ``2026-06-09T11:00``). Returns
    None if the value cannot be parsed.
    """
    value = value.strip()
    if not value:
        return None
    if value[-1].lower() in _DURATION_UNITS and value[:-1].isdigit():
        seconds = int(value[:-1]) * _DURATION_UNITS[value[-1].lower()]
        return datetime.now(timezone.utc) - timedelta(seconds=seconds)
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def read_events(
    *,
    since: datetime | None = None,
    worktree_id: str | None = None,
    event: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Return matching events, oldest first."""
    path = log_path()
    out: list[dict] = []
    if not path.exists():
        return out
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    continue
                if worktree_id and rec.get("worktree_id") != worktree_id:
                    continue
                if event and rec.get("event") != event:
                    continue
                if since is not None:
                    ts = _parse_ts(raw)
                    if ts is not None and ts < since:
                        continue
                out.append(rec)
    except OSError:
        return out
    if limit is not None and limit > 0:
        out = out[-limit:]
    return out


def _fmt_local(ts_iso: str) -> str:
    """Render a UTC ISO timestamp in local time for display."""
    try:
        dt = datetime.fromisoformat(ts_iso)
    except ValueError:
        return ts_iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


# Context fields worth surfacing in the human-readable table, in order.
_EXTRA_KEYS = ("reason", "branch", "exit_code", "resume_count", "state", "mux")


def render_events(events: list[dict]) -> str:
    """Format events as an aligned, human-readable table (oldest first)."""
    if not events:
        return "No activity recorded."
    rows: list[tuple[str, str, str, str, str]] = []
    for rec in events:
        when = _fmt_local(str(rec.get("ts", "")))
        event = str(rec.get("event", ""))
        wt = rec.get("worktree_id") or "-"
        sess = rec.get("session_id")
        sess = sess[:8] if isinstance(sess, str) else "-"
        extras = [
            f"{k}={rec[k]}" for k in _EXTRA_KEYS if rec.get(k) is not None
        ]
        rows.append((when, event, str(wt), sess, " ".join(extras)))

    w_event = max(len(r[1]) for r in rows)
    w_wt = max(len(r[2]) for r in rows)
    lines = []
    for when, event, wt, sess, extra in rows:
        lines.append(
            f"{when}  {event:<{w_event}}  {wt:<{w_wt}}  {sess:<8}  {extra}".rstrip()
        )
    return "\n".join(lines)


def cmd_activity(args) -> int:
    """``agent-worktrees activity`` -- view the lifecycle log."""
    since = None
    raw_since = getattr(args, "since", None)
    if raw_since:
        since = parse_since(raw_since)
        if since is None:
            print(f"Invalid --since value: {raw_since!r}", file=sys.stderr)
            return 1
    events = read_events(
        since=since,
        worktree_id=getattr(args, "worktree_id", None),
        event=getattr(args, "event", None),
        limit=getattr(args, "lines", None),
    )
    if getattr(args, "json", False):
        for rec in events:
            print(json.dumps(rec, ensure_ascii=True))
        return 0
    print(render_events(events))
    return 0


def cmd_activity_log(args) -> int:
    """``agent-worktrees activity-log`` -- append one event (launcher hook).

    Extra context is passed as repeatable ``--field key=value`` args.
    """
    event = getattr(args, "event", None)
    if not event:
        print("Usage: activity-log EVENT [--worktree-id ID] ...", file=sys.stderr)
        return 1
    fields: dict[str, object] = {}
    for item in getattr(args, "field", None) or []:
        if "=" in item:
            key, _, value = item.partition("=")
            key = key.strip()
            if key:
                fields[key] = value
    log_event(
        event,
        worktree_id=getattr(args, "worktree_id", None),
        session_id=getattr(args, "session_id", None),
        source=getattr(args, "source", None) or "launcher",
        **fields,
    )
    return 0

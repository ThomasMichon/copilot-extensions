"""Worktree tracking YAML -- read, write, and update operations.

Each worktree gets a YAML file at ~/.{project}/worktrees/{id}.yaml
tracking its lifecycle state.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml

from . import config as cfg

WorktreeStatus = Literal["active", "complete", "pushed", "finalized", "orphaned"]


@dataclass
class SessionEntry:
    """A Copilot session associated with a worktree."""

    session_id: str
    started_at: str
    pid: int | None = None
    ended_at: str | None = None


@dataclass
class WorktreeRecord:
    """Parsed worktree tracking record."""

    worktree_id: str
    branch: str
    worktree_path: str
    repo: str
    machine: str
    platform: str
    started_at: str
    last_resumed_at: str
    resume_count: int
    title: str | None
    status: WorktreeStatus
    completed_at: str | None
    handoff_prompt: str | None
    sessions: list[SessionEntry] | None = field(default=None)

    @property
    def yaml_path(self) -> Path:
        """Path to this record's YAML file in the tracking directory."""
        from . import config as cfg

        return cfg.tracking_dir() / f"{self.worktree_id}.yaml"


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _atomic_write(path: Path, content: str) -> None:
    """Write content to a file atomically via temp + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        # On Windows, can't rename over existing -- remove first
        if path.exists():
            path.unlink()
        os.rename(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_record(path: Path) -> WorktreeRecord:
    """Load a worktree tracking record from a YAML file."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    title = data.get("title")
    if title == "null" or title is None:
        title = None

    started_at_raw = data.get("started_at", "")
    if hasattr(started_at_raw, "isoformat"):
        started_at_raw = started_at_raw.isoformat()

    last_resumed_raw = data.get("last_resumed_at", "")
    if hasattr(last_resumed_raw, "isoformat"):
        last_resumed_raw = last_resumed_raw.isoformat()

    completed_raw = data.get("completed_at")
    if completed_raw == "null" or completed_raw is None:
        completed_raw = None
    elif hasattr(completed_raw, "isoformat"):
        completed_raw = completed_raw.isoformat()

    # Parse sessions list -- None means "not yet indexed" (pre-registry),
    # [] means "indexed, no sessions recorded".  This distinction drives
    # fallback: None -> full scan, [] -> skip scan.
    raw_sessions = data.get("sessions")
    sessions_list: list[SessionEntry] | None = None
    if raw_sessions is not None:
        sessions_list = []
        if isinstance(raw_sessions, list):
            for entry in raw_sessions:
                if isinstance(entry, dict) and "session_id" in entry:
                    sa = entry.get("started_at", "")
                    if hasattr(sa, "isoformat"):
                        sa = sa.isoformat()
                    ea = entry.get("ended_at")
                    if ea and hasattr(ea, "isoformat"):
                        ea = ea.isoformat()
                    elif ea == "null" or ea is None:
                        ea = None
                    sessions_list.append(SessionEntry(
                        session_id=str(entry["session_id"]),
                        started_at=str(sa),
                        pid=int(entry["pid"]) if entry.get("pid") else None,
                        ended_at=str(ea) if ea else None,
                    ))

    return WorktreeRecord(
        worktree_id=data["worktree_id"],
        branch=data["branch"],
        worktree_path=data.get("worktree_path", ""),
        repo=data.get("repo") or cfg.project_name(),
        machine=data.get("machine", ""),
        platform=data.get("platform", ""),
        started_at=str(started_at_raw),
        last_resumed_at=str(last_resumed_raw),
        resume_count=int(data.get("resume_count", 0)),
        title=title,
        status=data.get("status", "active"),
        completed_at=str(completed_raw) if completed_raw else None,
        handoff_prompt=data.get("handoff_prompt") or None,
        sessions=sessions_list,
    )


def save_record(record: WorktreeRecord, path: Path | None = None) -> None:
    """Write a worktree tracking record to YAML (atomic)."""
    if path is None:
        path = record.yaml_path

    title_val = record.title or "null"
    # Quote titles that contain YAML-special characters (colons, etc.)
    if title_val != "null" and any(ch in title_val for ch in ":{}[]#&*!|>',\""):
        safe_title = title_val.replace("'", "''")
        title_val = f"'{safe_title}'"

    content = (
        f"worktree_id: {record.worktree_id}\n"
        f"branch: {record.branch}\n"
        f"worktree_path: {record.worktree_path}\n"
        f"repo: {record.repo}\n"
        f"machine: {record.machine}\n"
        f"platform: {record.platform}\n"
        f"started_at: {record.started_at}\n"
        f"last_resumed_at: {record.last_resumed_at}\n"
        f"resume_count: {record.resume_count}\n"
        f"title: {title_val}\n"
        f"status: {record.status}\n"
        f"completed_at: {record.completed_at or 'null'}\n"
    )
    if record.handoff_prompt:
        content += f"handoff_prompt: {record.handoff_prompt}\n"

    # Serialize sessions list -- None omitted (not yet indexed),
    # [] written as empty list (indexed, no sessions).
    if record.sessions is not None:
        entries = [
            {
                "session_id": s.session_id,
                "started_at": s.started_at,
                **({"pid": s.pid} if s.pid else {}),
                **({"ended_at": s.ended_at} if s.ended_at else {}),
            }
            for s in record.sessions
        ]
        content += yaml.safe_dump(
            {"sessions": entries},
            default_flow_style=False,
            sort_keys=False,
        )

    _atomic_write(path, content)


def list_records(
    tracking_path: Path,
    *,
    status_filter: WorktreeStatus | None = None,
    platform_filter: str | None = None,
    repo_filter: str | None = None,
) -> list[WorktreeRecord]:
    """List all worktree records, optionally filtered by status/platform/repo."""
    records: list[WorktreeRecord] = []
    if not tracking_path.exists():
        return records

    for yaml_file in sorted(tracking_path.glob("*.yaml")):
        try:
            rec = load_record(yaml_file)
        except Exception:
            continue
        if status_filter and rec.status != status_filter:
            continue
        if platform_filter and rec.platform != platform_filter:
            continue
        if repo_filter and rec.repo != repo_filter:
            continue
        records.append(rec)

    return records


def update_status(record: WorktreeRecord, new_status: WorktreeStatus) -> None:
    """Update a record's status and save it."""
    record.status = new_status
    if new_status in ("finalized", "orphaned", "complete", "pushed"):
        if record.completed_at is None:
            record.completed_at = _now_iso()
    save_record(record)


def mark_resumed(record: WorktreeRecord) -> None:
    """Increment resume count and update last_resumed_at."""
    record.resume_count += 1
    record.last_resumed_at = _now_iso()
    save_record(record)


def set_handoff(worktree_id: str, prompt_path: str) -> None:
    """Set the handoff_prompt field on a worktree record."""
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"Worktree record not found: {yaml_path}")
    record = load_record(yaml_path)
    record.handoff_prompt = prompt_path
    save_record(record)


def consume_handoff(worktree_id: str) -> str | None:
    """Atomically read and clear the handoff_prompt field.

    Returns the prompt path if one was set, or None.
    Only active worktrees can consume handoffs -- finalized, complete,
    or orphaned worktrees return None (and clear any stale prompt).
    """
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return None
    record = load_record(yaml_path)
    # Only active worktrees should relaunch via handoff
    if record.status != "active":
        if record.handoff_prompt:
            record.handoff_prompt = None
            save_record(record)
        return None
    prompt_path = record.handoff_prompt
    if prompt_path:
        record.handoff_prompt = None
        save_record(record)
    return prompt_path


def create_new_record(
    worktree_id: str,
    branch: str,
    worktree_path: str,
    repo: str,
    machine: str,
    platform_name: str,
    tracking_path: Path,
) -> WorktreeRecord:
    """Create and save a new worktree tracking record."""
    now = _now_iso()
    record = WorktreeRecord(
        worktree_id=worktree_id,
        branch=branch,
        worktree_path=worktree_path,
        repo=repo,
        machine=machine,
        platform=platform_name,
        started_at=now,
        last_resumed_at=now,
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        handoff_prompt=None,
        sessions=[],
    )
    path = tracking_path / f"{worktree_id}.yaml"
    save_record(record, path)
    return record


# ---------------------------------------------------------------------------
# Session registry -- per-worktree session tracking via hooks
# ---------------------------------------------------------------------------

class _RecordLock:
    """Short-lived file lock for read-modify-write on a tracking YAML.

    Uses fcntl advisory locks on Unix.  Falls back to no-op on platforms
    where fcntl is unavailable (Windows) -- the atomic-write pattern still
    prevents torn files, and concurrent sessions in the same worktree are
    rare enough that lost updates are acceptable there.
    """

    def __init__(self, yaml_path: Path, timeout: float = 2.0):
        self._lock_path = yaml_path.with_suffix(".lock")
        self._timeout = timeout
        self._fd: int | None = None

    def __enter__(self) -> "_RecordLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR)
        try:
            import fcntl as _fcntl
        except ImportError:
            # Windows -- no fcntl; proceed unlocked
            return self
        import time
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                _fcntl.flock(self._fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                return self
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    # Timeout -- proceed unlocked rather than stall launch
                    return self
                time.sleep(0.05)

    def __exit__(self, *_: object) -> None:
        if self._fd is not None:
            try:
                import fcntl as _fcntl
                _fcntl.flock(self._fd, _fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            os.close(self._fd)
            self._fd = None


def register_session(
    worktree_id: str,
    session_id: str,
    pid: int | None = None,
) -> None:
    """Register a Copilot session against a worktree (called from sessionStart hook)."""
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return

    with _RecordLock(yaml_path):
        record = load_record(yaml_path)
        if record.sessions is None:
            record.sessions = []

        # Dedupe -- update existing entry instead of appending
        for entry in record.sessions:
            if entry.session_id == session_id:
                entry.started_at = _now_iso()
                entry.pid = pid
                entry.ended_at = None
                save_record(record)
                return

        record.sessions.append(SessionEntry(
            session_id=session_id,
            started_at=_now_iso(),
            pid=pid,
        ))
        save_record(record)


def deregister_session(
    worktree_id: str,
    session_id: str,
) -> None:
    """Mark a session as ended on a worktree (called from sessionEnd hook)."""
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return

    with _RecordLock(yaml_path):
        record = load_record(yaml_path)
        if record.sessions is None:
            return

        for entry in record.sessions:
            if entry.session_id == session_id:
                entry.ended_at = _now_iso()
                save_record(record)
                return

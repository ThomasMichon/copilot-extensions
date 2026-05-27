"""Worktree tracking YAML — read, write, and update operations.

Each worktree gets a YAML file at ~/.{project}/worktrees/{id}.yaml
tracking its lifecycle state.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml

from . import config as cfg

WorktreeStatus = Literal["active", "complete", "finalized", "orphaned"]


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
        # On Windows, can't rename over existing — remove first
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
    if new_status in ("finalized", "orphaned", "complete"):
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
    Only active worktrees can consume handoffs — finalized, complete,
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
    )
    path = tracking_path / f"{worktree_id}.yaml"
    save_record(record, path)
    return record

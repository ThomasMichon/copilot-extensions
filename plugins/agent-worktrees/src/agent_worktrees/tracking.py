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

# A worktree's owner class. "session" = an interactive agent session (the
# default, shown in the launch Picker). "system" = a daemon-owned worktree
# created per work-session by a background service; hidden from the Picker and
# exempt from routine cleanup. See the agent-worktrees docs and the
# aperture-labs system-worktrees effort.
WorktreeKind = Literal["session", "system"]


@dataclass
class SessionEntry:
    """A Copilot session associated with a worktree."""

    session_id: str
    started_at: str
    pid: int | None = None
    ended_at: str | None = None


@dataclass
class PRRecord:
    """Pull-request metadata nested under a worktree record (PR mode).

    Present only when the worktree has entered the PR workflow.  ``state``
    tracks the PR lifecycle; ``branch`` is the pushed feature branch.  A
    worktree may carry several of these over its life (serial re-PRs) or at
    once (parallel PRs) -- see ``WorktreeRecord.prs``.
    """

    state: str = ""          # creating | open | merged | closed
    branch: str = ""
    base_sha: str = ""
    head_sha: str = ""
    url: str = ""
    number: int | None = None
    provider: str = ""
    repo: str = ""           # target repo "owner/name"; default = worktree repo
    opened_at: str = ""      # ISO timestamp the PR record was opened
    closed_at: str = ""      # ISO timestamp the PR reached a terminal state


# PR lifecycle states that are still live (the PR can still receive pushes).
# Anything else (merged/closed) is terminal.
_PR_NON_TERMINAL = ("", "creating", "open")


def _pr_is_terminal(pr: PRRecord) -> bool:
    """Return True when a PR has reached a terminal (merged/closed) state."""
    return pr.state not in _PR_NON_TERMINAL


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
    handoff_prompt: str | None  # deprecated, kept for YAML compat
    sessions: list[SessionEntry] | None = field(default=None)
    # PR records (PR mode).  A worktree can track multiple PRs -- serially
    # (re-PR after a merge) or in parallel -- each self-describing (including
    # its target ``repo``).  Empty when the worktree has not entered the PR
    # workflow.  The legacy single ``pr:`` YAML block loads as a one-element
    # list; the ``pr`` property below preserves the old single-PR accessor.
    prs: list[PRRecord] = field(default_factory=list)
    kind: WorktreeKind = "session"
    owner: str | None = None  # owning service name, for system worktrees

    def active_pr(self) -> PRRecord | None:
        """Return the PR a no-selector command should target.

        Rule (see the multi-PR effort): the most recent **non-terminal**
        (creating/open) PR; if none are live, the most recent overall.
        "Most recent" is by ``opened_at`` then list order, so a record with
        no timestamps resolves deterministically to the last-appended PR.
        """
        if not self.prs:
            return None
        pool = [p for p in self.prs if not _pr_is_terminal(p)] or self.prs
        return max(pool, key=lambda p: (p.opened_at or "", self.prs.index(p)))

    def has_live_pr(self) -> bool:
        """Return True if any tracked PR is still non-terminal (open/creating).

        A worktree with a live PR must not be reaped by cleanup -- the PR is
        still in review and its feature branch is the recovery source.
        """
        return any(not _pr_is_terminal(p) for p in self.prs)

    @property
    def pr(self) -> PRRecord | None:
        """Back-compat accessor: the active PR (see :meth:`active_pr`)."""
        return self.active_pr()

    @pr.setter
    def pr(self, value: PRRecord | None) -> None:
        """Back-compat mutator: replace the active PR, or append/clear.

        Mirrors the old single-slot semantics for call sites that still do
        ``record.pr = PRRecord(...)``: with an active PR present the value
        replaces it in place (preserving list position); with none, the value
        is appended.  Assigning ``None`` drops the active PR from the list.
        Write sites that intend a *new* PR (serial/parallel) mutate ``prs``
        directly instead.
        """
        active = self.active_pr()
        if value is None:
            if active is not None:
                self.prs = [p for p in self.prs if p is not active]
            return
        if active is not None:
            self.prs[self.prs.index(active)] = value
        else:
            self.prs.append(value)

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


def _parse_pr_mapping(raw: dict, default_repo: str) -> PRRecord:
    """Parse one PR mapping (from a ``prs:`` item or legacy ``pr:`` block)."""
    num = raw.get("number")
    if num in (None, "", "null"):
        num_val: int | None = None
    else:
        try:
            num_val = int(num)
        except (TypeError, ValueError):
            num_val = None
    return PRRecord(
        state=str(raw.get("state", "")),
        branch=str(raw.get("branch", "")),
        base_sha=str(raw.get("base_sha", "")),
        head_sha=str(raw.get("head_sha", "")),
        url=str(raw.get("url", "")),
        number=num_val,
        provider=str(raw.get("provider", "")),
        # A legacy record without a per-PR repo targets the worktree's repo.
        repo=str(raw.get("repo", "")) or default_repo,
        opened_at=str(raw.get("opened_at", "")),
        closed_at=str(raw.get("closed_at", "")),
    )


def _pr_to_yaml_dict(pr: PRRecord) -> dict[str, object]:
    """Serialize a PRRecord to a YAML-friendly mapping (lean: omit empties)."""
    d: dict[str, object] = {
        "state": pr.state,
        "branch": pr.branch,
        "base_sha": pr.base_sha,
        "head_sha": pr.head_sha,
        "url": pr.url,
    }
    if pr.number is not None:
        d["number"] = pr.number
    d["provider"] = pr.provider
    if pr.repo:
        d["repo"] = pr.repo
    if pr.opened_at:
        d["opened_at"] = pr.opened_at
    if pr.closed_at:
        d["closed_at"] = pr.closed_at
    return d


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

    # Parse PR records -- the multi-PR ``prs:`` list (preferred) or a legacy
    # single ``pr:`` mapping (loaded as a one-element list).  Absent in
    # non-PR worktrees.
    default_repo = data.get("repo") or cfg.project_name()
    prs_list: list[PRRecord] = []
    raw_prs = data.get("prs")
    if isinstance(raw_prs, list):
        for raw in raw_prs:
            if isinstance(raw, dict):
                prs_list.append(_parse_pr_mapping(raw, default_repo))
    elif isinstance(data.get("pr"), dict):
        prs_list.append(_parse_pr_mapping(data["pr"], default_repo))

    # Owner class -- absent (legacy records) defaults to "session".
    kind_raw = data.get("kind")
    kind_val: WorktreeKind = "system" if kind_raw == "system" else "session"
    owner_raw = data.get("owner")
    if owner_raw in (None, "", "null"):
        owner_raw = None

    return WorktreeRecord(
        worktree_id=data["worktree_id"],
        branch=data["branch"],
        worktree_path=data.get("worktree_path", ""),
        repo=default_repo,
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
        prs=prs_list,
        kind=kind_val,
        owner=str(owner_raw) if owner_raw else None,
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
        f"handoff_prompt: {record.handoff_prompt or 'null'}\n"
    )

    # Owner class -- only emit for system worktrees so existing session-record
    # YAMLs stay byte-identical (no churn for the common case).
    if record.kind == "system":
        content += "kind: system\n"
        if record.owner:
            content += f"owner: {record.owner}\n"

    # Serialize PR records.  Emit the multi-PR ``prs:`` list and mirror the
    # active PR to a legacy ``pr:`` block for one release, so a same-machine
    # tool *downgrade* still finds the active PR.  Zero-PR worktrees emit
    # neither, keeping the common-case YAML byte-identical.
    if record.prs:
        content += yaml.safe_dump(
            {"prs": [_pr_to_yaml_dict(p) for p in record.prs]},
            default_flow_style=False,
            sort_keys=False,
        )
        active = record.active_pr()
        if active is not None:
            content += yaml.safe_dump(
                {"pr": _pr_to_yaml_dict(active)},
                default_flow_style=False,
                sort_keys=False,
            )

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
    kind_filter: WorktreeKind | None = None,
) -> list[WorktreeRecord]:
    """List all worktree records, optionally filtered by status/platform/repo/kind."""
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
        if kind_filter and rec.kind != kind_filter:
            continue
        records.append(rec)

    return records


def find_worktree_id_by_cwd(cwd: str) -> str | None:
    """Resolve a worktree_id from a session cwd.

    Matches *cwd* (or any worktree root that is an ancestor of it) against
    the tracked ``worktree_path`` values.  Used by the sessionStart hook to
    associate a session with its worktree when the ``WORKTREE_ID`` env var
    is not present in the hook environment -- the Copilot CLI delivers the
    cwd via the hook's stdin payload instead.

    When several worktree roots match (nested trees), the deepest
    (longest) match wins.  Returns None if no worktree contains *cwd*.
    """
    if not cwd:
        return None
    tracking_path = cfg.tracking_dir()
    if not tracking_path.exists():
        return None

    norm = os.path.normcase(os.path.normpath(cwd)).rstrip("/\\")
    best_id: str | None = None
    best_len = -1
    for rec in list_records(tracking_path):
        wp = rec.worktree_path
        if not wp:
            continue
        wnorm = os.path.normcase(os.path.normpath(wp)).rstrip("/\\")
        if norm == wnorm or norm.startswith(wnorm + os.sep):
            if len(wnorm) > best_len:
                best_len = len(wnorm)
                best_id = rec.worktree_id
    return best_id


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


def create_new_record(
    worktree_id: str,
    branch: str,
    worktree_path: str,
    repo: str,
    machine: str,
    platform_name: str,
    tracking_path: Path,
    *,
    kind: WorktreeKind = "session",
    owner: str | None = None,
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
        kind=kind,
        owner=owner,
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

    def __enter__(self) -> _RecordLock:
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

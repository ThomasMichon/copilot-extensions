"""Shared test fixtures for agent-worktrees tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent_worktrees import tracking

# ---------------------------------------------------------------------------
# Path fixtures — redirect config helpers to tmp dirs
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_tracking_dir(tmp_path: Path) -> Path:
    """Temporary tracking directory for worktree YAMLs."""
    d = tmp_path / "worktrees"
    d.mkdir()
    return d


@pytest.fixture
def tmp_session_state_dir(tmp_path: Path) -> Path:
    """Temporary ~/.copilot/session-state/ equivalent."""
    d = tmp_path / "session-state"
    d.mkdir()
    return d


@pytest.fixture
def monkeypatch_config(monkeypatch, tmp_path: Path, tmp_tracking_dir: Path):
    """Patch config helpers to use tmp dirs."""
    monkeypatch.setenv("WORKTREE_PROJECT", "test-project")
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tmp_tracking_dir)
    monkeypatch.setattr("agent_worktrees.config.project_dir", lambda: tmp_path / ".test-project")
    monkeypatch.setattr(
        "agent_worktrees.config.install_dir", lambda: tmp_path / ".agent-worktrees"
    )


@pytest.fixture
def sample_record(tmp_tracking_dir: Path) -> tracking.WorktreeRecord:
    """A pre-built WorktreeRecord saved to the tracking dir."""
    rec = tracking.WorktreeRecord(
        worktree_id="test-wt-001",
        branch="worktree/test-wt-001",
        worktree_path="/tmp/test-worktree",
        repo="test-repo",
        machine="test-machine",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        handoff_prompt=None,
        sessions=[],
    )
    tracking.save_record(rec, tmp_tracking_dir / f"{rec.worktree_id}.yaml")
    return rec


def make_session_dir(
    session_state_dir: Path,
    session_id: str,
    cwd: str,
    *,
    summary: str = "",
    updated_at: str = "2026-06-01T10:00:00.000Z",
    events_lines: list[str] | None = None,
    lock_pid: int | None = None,
    has_events_file: bool = True,
) -> Path:
    """Create a mock session directory with workspace.yaml and optional files."""
    sdir = session_state_dir / session_id
    sdir.mkdir(parents=True, exist_ok=True)

    ws_content = textwrap.dedent(f"""\
        id: {session_id}
        cwd: {cwd}
        git_root: {cwd}
        branch: main
        name: Test Session
        summary: {summary or 'Test Session'}
        created_at: {updated_at}
        updated_at: {updated_at}
    """)
    (sdir / "workspace.yaml").write_text(ws_content)

    if has_events_file:
        lines = events_lines or []
        (sdir / "events.jsonl").write_text("\n".join(lines) + "\n" if lines else "")

    if lock_pid is not None:
        (sdir / f"inuse.{lock_pid}.lock").write_text("")

    return sdir

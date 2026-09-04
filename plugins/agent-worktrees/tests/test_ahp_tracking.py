from __future__ import annotations

from pathlib import Path

import yaml

from agent_worktrees import tracking


def record(tmp_path: Path) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id="host-win-20260903-abcd",
        branch="worktree/host-win-20260903-abcd",
        worktree_path=str(tmp_path / "worktree"),
        repo="example",
        machine="host",
        platform="windows",
        started_at="2026-09-03T00:00:00+00:00",
        last_resumed_at="2026-09-03T00:00:00+00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
    )


def binding() -> tracking.SessionBackendBinding:
    return tracking.SessionBackendBinding(
        kind="ahp",
        endpoint_url="ws://127.0.0.1:8765",
        session_id="11111111-1111-1111-1111-111111111111",
        protocol_version="0.7.0",
        auth_account="octocat",
        created_at="2026-09-03T00:00:01+00:00",
        last_seen_at="2026-09-03T00:00:02+00:00",
    )


def test_session_backend_round_trips(tmp_path: Path):
    path = tmp_path / "record.yaml"
    original = record(tmp_path)
    original.session_backend = binding()
    tracking.save_record(original, path)

    loaded = tracking.load_record(path)
    assert loaded.session_backend == original.session_backend


def test_newer_session_backend_schema_is_preserved(tmp_path: Path):
    path = tmp_path / "record.yaml"
    original = record(tmp_path)
    tracking.save_record(original, path)
    data = yaml.safe_load(path.read_text())
    data["session_backend"] = {
        "version": 2,
        "kind": "future",
        "opaque": {"value": True},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False))

    loaded = tracking.load_record(path)
    assert loaded.session_backend is None
    assert loaded.session_backend_opaque is True
    tracking.save_record(loaded, path)
    assert yaml.safe_load(path.read_text())["session_backend"] == data[
        "session_backend"
    ]


def test_invalid_current_session_backend_is_preserved_opaquely(tmp_path: Path):
    path = tmp_path / "record.yaml"
    original = record(tmp_path)
    tracking.save_record(original, path)
    data = yaml.safe_load(path.read_text())
    data["session_backend"] = {
        "version": 1,
        "kind": "ahp",
        "session_id": "missing-required-fields",
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False))

    loaded = tracking.load_record(path)
    assert loaded.session_backend is None
    assert loaded.session_backend_opaque is True
    tracking.save_record(loaded, path)
    assert yaml.safe_load(path.read_text())["session_backend"] == data[
        "session_backend"
    ]

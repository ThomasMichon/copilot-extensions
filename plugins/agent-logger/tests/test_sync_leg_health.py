"""Independent transfer-leg recovery and sustained-partial classification."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_logger.sync import meta
from agent_logger.sync.health import classify_sync_health
from agent_logger.sync.targets.base import SyncStatus
from agent_logger.sync.targets.filesystem import LocalTarget

pytestmark = pytest.mark.contract("agent_logger.sync.leg_health")


def test_log_retry_recovers_without_session_transfer(tmp_path: Path) -> None:
    target = LocalTarget({"path": str(tmp_path / "dest")})
    root = tmp_path / "dest" / "machine"
    meta.write_sync_meta(
        root,
        "machine",
        "local",
        "ok",
        7,
        excluded_roots=["session-state/example/browser"],
        excluded_file_count=12,
        excluded_byte_count=345,
        excluded_measurement_complete=False,
    )
    original = meta.read_sync_meta(root)
    logs = tmp_path / "logs"
    assert target.push_process_logs(logs, "machine").ok
    assert meta.read_sync_meta(root)["status"] == "partial"
    logs.mkdir()
    (logs / "process-123-456.log").write_text("evidence\n", encoding="utf-8")
    assert target.push_process_logs(logs, "machine").ok
    recovered = meta.read_sync_meta(root)
    assert recovered["status"] == "ok"
    assert recovered["consecutive_partial_count"] == 0
    assert recovered["deferred_file_count"] == 0
    assert recovered["session_count"] == original["session_count"]
    for key in original:
        if key.startswith("excluded_"):
            assert recovered[key] == original[key]
    assert recovered["sync_legs"]["session-state"] == original["sync_legs"]["session-state"]


def test_repeated_log_partial_survives_successful_session_pushes(tmp_path: Path) -> None:
    for attempt in range(1, 4):
        meta.write_sync_meta(tmp_path, "machine", "local", "ok", 4)
        meta.write_process_log_meta(tmp_path, "partial", ["process-example.log"])
        current = meta.read_sync_meta(tmp_path)
        assert current["consecutive_partial_count"] == attempt
        classified = classify_sync_health(
            "machine",
            SyncStatus(supported=True, metadata=current),
            max_age_hours=12,
            partial_threshold=3,
        )
        assert classified.health == ("unhealthy" if attempt == 3 else "degraded")
    meta.heartbeat_sync_meta(tmp_path, "machine", "local", 4)
    assert meta.read_sync_meta(tmp_path)["consecutive_partial_count"] == 3
    meta.write_process_log_meta(tmp_path, "ok")
    assert meta.read_sync_meta(tmp_path)["status"] == "ok"


def test_log_success_preserves_session_partial_and_exact_counts(tmp_path: Path) -> None:
    session_paths = [f"session-state/example/file-{i}" for i in range(20)]
    log_paths = [f"process-{i}-example.log" for i in range(30)]
    meta.write_sync_meta(
        tmp_path,
        "machine",
        "local",
        "partial",
        deferred_files=session_paths,
    )
    meta.write_process_log_meta(tmp_path, "partial", log_paths)
    combined = meta.read_sync_meta(tmp_path)
    assert combined["deferred_file_count"] == 50
    assert len(combined["deferred_files"]) == meta.MAX_DEFERRED_FILE_SAMPLES
    assert combined["sync_legs"]["process-logs"]["deferred_file_count"] == 30
    meta.write_process_log_meta(tmp_path, "ok")
    recovered_logs = meta.read_sync_meta(tmp_path)
    assert recovered_logs["status"] == "partial"
    assert recovered_logs["deferred_file_count"] == 20
    assert recovered_logs["deferred_files"] == session_paths[: meta.MAX_DEFERRED_FILE_SAMPLES]
    meta.write_sync_meta(tmp_path, "machine", "local", "ok")
    assert meta.read_sync_meta(tmp_path)["status"] == "ok"


def test_legacy_partial_needs_session_retry(tmp_path: Path) -> None:
    meta.write_sync_meta(tmp_path, "machine", "local", "partial", deferred_files=["old"])
    previous = meta.read_sync_meta(tmp_path)
    del previous["sync_legs"]
    (tmp_path / "sync-meta.json").write_text(json.dumps(previous), encoding="utf-8")
    meta.write_process_log_meta(tmp_path, "ok")
    assert meta.read_sync_meta(tmp_path)["status"] == "partial"
    meta.write_sync_meta(tmp_path, "machine", "local", "ok")
    assert meta.read_sync_meta(tmp_path)["status"] == "ok"


def test_log_retry_does_not_refresh_stale_session_health(tmp_path: Path) -> None:
    meta.write_sync_meta(tmp_path, "machine", "local", "ok")
    previous = meta.read_sync_meta(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    previous["sync_legs"]["session-state"]["last_checked_utc"] = old
    (tmp_path / "sync-meta.json").write_text(json.dumps(previous), encoding="utf-8")
    meta.write_process_log_meta(tmp_path, "ok")
    classified = classify_sync_health(
        "machine",
        SyncStatus(supported=True, metadata=meta.read_sync_meta(tmp_path)),
        max_age_hours=12,
        partial_threshold=3,
    )
    assert classified.reason == "stale"
    meta.heartbeat_sync_meta(tmp_path, "machine", "local", 0)
    classified = classify_sync_health(
        "machine",
        SyncStatus(supported=True, metadata=meta.read_sync_meta(tmp_path)),
        max_age_hours=12,
        partial_threshold=3,
    )
    assert classified.health == "healthy"


def test_invalid_recorded_status_cannot_be_cleared_by_other_leg(tmp_path: Path) -> None:
    meta.write_sync_meta(tmp_path, "machine", "local", "unknown")
    meta.write_process_log_meta(tmp_path, "ok")
    classified = classify_sync_health(
        "machine",
        SyncStatus(supported=True, metadata=meta.read_sync_meta(tmp_path)),
        max_age_hours=12,
        partial_threshold=3,
    )
    assert classified.reason == "invalid_status"


@pytest.mark.parametrize("payload", ["invalid json", '{"sync_legs":{"unexpected":{}}}'])
def test_invalid_metadata_is_preserved_and_reported(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    payload: str,
) -> None:
    path = tmp_path / "sync-meta.json"
    path.write_text(payload, encoding="utf-8")
    meta.write_process_log_meta(tmp_path, "ok")
    assert path.read_text(encoding="utf-8") == payload
    assert "cannot update process-log sync health" in caplog.text


def test_deferred_log_retry_clears_only_log_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger.sync.targets import filesystem

    target = LocalTarget({"path": str(tmp_path / "dest")})
    root = tmp_path / "dest" / "machine"
    meta.write_sync_meta(root, "machine", "local", "ok")
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(
        filesystem,
        "_copy_process_logs",
        lambda *_args: (0, 0, [logs / "process-123-456.log"]),
    )
    assert target.push_process_logs(logs, "machine").ok
    partial = meta.read_sync_meta(root)
    assert partial["status"] == "partial"
    assert partial["deferred_file_count"] == 1
    monkeypatch.setattr(filesystem, "_copy_process_logs", lambda *_args: (0, 0, []))
    assert target.push_process_logs(logs, "machine").ok
    assert meta.read_sync_meta(root)["status"] == "ok"

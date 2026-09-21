from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from agent_mcp import mcp_health
from agent_mcp.__main__ import _cmd_mcp_health


def _log_line(ts: datetime, message: str) -> str:
    stamp = ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return f"{stamp} [WARNING] [rust:copilot_runtime::session::mcp::tool_snapshot_cache] {message}\n"


def test_resolve_log_dir_honors_explicit_override(tmp_path):
    override = tmp_path / "custom-logs"
    assert mcp_health.resolve_log_dir(str(override)) == override


def test_resolve_log_dir_honors_copilot_home_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path))
    assert mcp_health.resolve_log_dir() == tmp_path / "logs"


def test_sweep_counts_stale_schema_cache_entries(tmp_path):
    now = datetime.now(timezone.utc)
    log = tmp_path / "process-1.log"
    log.write_text(
        _log_line(
            now,
            "Skipping invalid MCP tool cache entry /x/y.json: "
            "Unsupported MCP tool cache schema version: 1",
        ),
        encoding="utf-8",
    )

    result = mcp_health.sweep(tmp_path, since_hours=None)

    assert result.signal_counts.get("stale_schema_cache_entry") == 1
    assert result.files_scanned == 1


def test_sweep_counts_cache_hydration_timeout(tmp_path):
    now = datetime.now(timezone.utc)
    log = tmp_path / "process-1.log"
    log.write_text(_log_line(now, "Timed out loading persisted MCP tool cache"), encoding="utf-8")

    result = mcp_health.sweep(tmp_path, since_hours=None)

    assert result.signal_counts.get("cache_hydration_timeout") == 1


def test_sweep_counts_pending_snapshot(tmp_path):
    now = datetime.now(timezone.utc)
    log = tmp_path / "process-1.log"
    log.write_text(
        _log_line(now, 'mcp unchanged reload reuses live graph {"connected_count":0,'
                       '"pending_count":1,"failed_retry_count":0}'),
        encoding="utf-8",
    )

    result = mcp_health.sweep(tmp_path, since_hours=None)

    assert result.signal_counts.get("pending_snapshot") == 1
    # connected_count > 0 must NOT count as a pending snapshot.
    log.write_text(
        _log_line(now, 'mcp unchanged reload reuses live graph {"connected_count":1,'
                       '"pending_count":0,"failed_retry_count":0}'),
        encoding="utf-8",
    )
    result2 = mcp_health.sweep(tmp_path, since_hours=None)
    assert result2.signal_counts.get("pending_snapshot", 0) == 0


def test_sweep_counts_explicit_failed_retry(tmp_path):
    now = datetime.now(timezone.utc)
    log = tmp_path / "process-1.log"
    log.write_text(
        _log_line(now, 'mcp unchanged reload reuses live graph {"connected_count":0,'
                       '"pending_count":0,"failed_retry_count":2}'),
        encoding="utf-8",
    )

    result = mcp_health.sweep(tmp_path, since_hours=None)

    assert result.signal_counts.get("explicit_failed_retry") == 1


def test_sweep_respects_since_hours_window(tmp_path):
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    log = tmp_path / "process-1.log"
    log.write_text(
        _log_line(old, "Timed out loading persisted MCP tool cache")
        + _log_line(recent, "Timed out loading persisted MCP tool cache"),
        encoding="utf-8",
    )

    result = mcp_health.sweep(tmp_path, since_hours=24.0)

    assert result.signal_counts.get("cache_hydration_timeout") == 1


def test_sweep_missing_log_dir_reports_zero_files(tmp_path):
    result = mcp_health.sweep(tmp_path / "does-not-exist", since_hours=None)
    assert result.files_scanned == 0
    assert result.signal_counts == {}


def test_health_report_combines_log_sweep_and_tool_cache(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "entry.json").write_text(
        json.dumps({"schemaVersion": 3, "serverName": "demo"}), encoding="utf-8"
    )

    report = mcp_health.health_report(
        log_dir_override=str(log_dir), cache_dir_override=str(cache_dir), since_hours=None
    )

    assert report["log_sweep"]["files_scanned"] == 0
    assert report["tool_cache"]["found"] is True
    assert report["tool_cache"]["total_entries"] == 1
    assert report["tool_cache"]["stale_entries"] == 0


def test_cmd_mcp_health_missing_dirs_is_not_an_error(tmp_path, capsys):
    args = argparse.Namespace(
        since_hours=24.0,
        log_dir=str(tmp_path / "no-logs"),
        cache_dir=str(tmp_path / "no-cache"),
        json=False,
        quiet=False,
    )

    rc = _cmd_mcp_health(args)

    output = capsys.readouterr().out
    assert rc == 0
    assert "nothing to sweep" in output
    assert "No tool cache directory found" in output


def test_cmd_mcp_health_json_output(tmp_path, capsys):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    args = argparse.Namespace(
        since_hours=24.0, log_dir=str(log_dir), cache_dir=str(tmp_path / "no-cache"),
        json=True, quiet=True,
    )

    rc = _cmd_mcp_health(args)

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["log_sweep"]["files_scanned"] == 0
    assert payload["tool_cache"]["found"] is False

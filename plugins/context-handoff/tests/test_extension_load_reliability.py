"""Tests for the extension-load reliability report."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parents[1]
_SCRIPT = _PLUGIN / "scripts" / "extension_load_reliability.py"
_SPEC = importlib.util.spec_from_file_location(
    "extension_load_reliability_under_test", _SCRIPT
)
assert _SPEC and _SPEC.loader
reliability = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = reliability
_SPEC.loader.exec_module(reliability)


def _write_log(
    log_dir: Path,
    plugin: str,
    *,
    launch_ms: int,
    pid: int,
    terminal_marker: str | None,
    disposition: str | None = None,
    cli_version: str = "1.0.87-0",
) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"plugin-{plugin}_{plugin}-{launch_ms}-{pid}.log"
    module = (
        rf"C:\Users\x\.copilot\installed-plugins\copilot-extensions\{plugin}"
        rf"\extensions\{plugin}\extension.mjs"
    )
    lines = [
        f"=== launch pid={pid} module={module} ===",
        "[extension-fork] resolveBootstrapPath: "
        rf"__dir=C:\Users\x\AppData\Local\copilot\pkg\win32-arm64\{cli_version}",
    ]
    if terminal_marker:
        lines.append(f"=== {terminal_marker} ===")
    disposition_suffix = f" disposition={disposition}" if disposition else ""
    lines.append(f"=== exit code=1{disposition_suffix} ===")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_parse_log_classifies_ready(tmp_path):
    path = _write_log(
        tmp_path, "context-handoff", launch_ms=1_700_000_000_000, pid=1,
        terminal_marker="ready", disposition="stopped-normally",
    )
    record = reliability.parse_log(path)
    assert record.outcome == "ready"
    assert record.cli_version == "1.0.87-0"
    assert record.launch_time == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)


def test_parse_log_classifies_ready_timeout(tmp_path):
    path = _write_log(
        tmp_path, "context-handoff", launch_ms=1_700_000_000_000, pid=2,
        terminal_marker="ready-timeout",
    )
    assert reliability.parse_log(path).outcome == "ready-timeout"


def test_parse_log_classifies_peer_closed_before_ready(tmp_path):
    path = _write_log(
        tmp_path, "context-handoff", launch_ms=1_700_000_000_000, pid=3,
        terminal_marker="peer-closed-before-ready", disposition="startup-failure",
    )
    assert reliability.parse_log(path).outcome == "peer-closed-before-ready"


def test_parse_log_classifies_crash(tmp_path):
    path = _write_log(
        tmp_path, "context-handoff", launch_ms=1_700_000_000_000, pid=4,
        terminal_marker=None, disposition="crash",
    )
    assert reliability.parse_log(path).outcome == "crash"


def test_parse_log_older_format_is_unknown_not_failure(tmp_path):
    # Pre-disposition-tracking CLI versions only wrote "=== exit <ts> code=N
    # signal=null ===" with no ready/ready-timeout/disposition marker at all.
    # These must be bucketed separately, never silently counted as a failure.
    log_dir = tmp_path
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "plugin-context-handoff_context-handoff-1700000000000-5.log"
    path.write_text(
        "=== launch 2026-01-01T00:00:00.000Z pid=5 module=x ===\n"
        "=== exit 2026-01-01T00:00:00.200Z code=1 signal=null ===\n",
        encoding="utf-8",
    )
    assert reliability.parse_log(path).outcome == "unknown"


def test_iter_plugin_logs_filters_to_matching_plugin_prefix(tmp_path):
    _write_log(tmp_path, "context-handoff", launch_ms=1_700_000_000_000, pid=1, terminal_marker="ready")
    _write_log(tmp_path, "agent-bridge", launch_ms=1_700_000_000_000, pid=2, terminal_marker="ready")
    paths = list(reliability.iter_plugin_logs(tmp_path, "context-handoff"))
    assert len(paths) == 1
    assert "context-handoff" in paths[0].name


def test_summarize_counts_outcomes_and_versions(tmp_path):
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    _write_log(tmp_path, "p", launch_ms=now_ms, pid=1, terminal_marker="ready", cli_version="1.0.87-0")
    _write_log(tmp_path, "p", launch_ms=now_ms, pid=2, terminal_marker="ready-timeout", cli_version="1.0.86")
    records = reliability.load_records(tmp_path, "p")
    summary = reliability.summarize(records, label="w")
    assert summary.total == 2
    assert summary.outcomes == {"ready": 1, "ready-timeout": 1}
    assert summary.cli_versions == {"1.0.87-0": 1, "1.0.86": 1}
    assert summary.ready_timeout_rate == 0.5


def test_summarize_ready_timeout_rate_is_none_when_empty():
    summary = reliability.summarize([], label="w")
    assert summary.total == 0
    assert summary.ready_timeout_rate is None


def test_load_records_excludes_logs_before_since(tmp_path):
    now = datetime.now(timezone.utc)
    old_ms = int((now - timedelta(days=30)).timestamp() * 1000)
    new_ms = int(now.timestamp() * 1000)
    _write_log(tmp_path, "p", launch_ms=old_ms, pid=1, terminal_marker="ready")
    _write_log(tmp_path, "p", launch_ms=new_ms, pid=2, terminal_marker="ready")
    records = reliability.load_records(tmp_path, "p", since=now - timedelta(days=7))
    assert len(records) == 1
    assert records[0].launch_time == datetime.fromtimestamp(new_ms / 1000, tz=timezone.utc)


def test_report_without_split_returns_single_window(tmp_path):
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    _write_log(tmp_path, "p", launch_ms=now_ms, pid=1, terminal_marker="ready")
    summaries = reliability.report(tmp_path, "p", days=7)
    assert len(summaries) == 1
    assert summaries[0].label == "window"
    assert summaries[0].total == 1


def test_report_with_split_at_partitions_before_and_after(tmp_path):
    now = datetime.now(timezone.utc)
    split_at = now - timedelta(hours=1)
    before_ms = int((split_at - timedelta(minutes=30)).timestamp() * 1000)
    after_ms = int((split_at + timedelta(minutes=30)).timestamp() * 1000)
    _write_log(tmp_path, "p", launch_ms=before_ms, pid=1, terminal_marker="ready-timeout")
    _write_log(tmp_path, "p", launch_ms=after_ms, pid=2, terminal_marker="ready")
    _write_log(tmp_path, "p", launch_ms=after_ms, pid=3, terminal_marker="ready")

    summaries = reliability.report(tmp_path, "p", days=7, split_at=split_at)
    assert [s.label for s in summaries] == ["before", "after"]
    before, after = summaries
    assert before.total == 1
    assert before.outcomes == {"ready-timeout": 1}
    assert after.total == 2
    assert after.outcomes == {"ready": 2}


def test_main_json_output_is_parseable(tmp_path, capsys):
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    _write_log(tmp_path, "p", launch_ms=now_ms, pid=1, terminal_marker="ready")
    exit_code = reliability.main(["--plugin", "p", "--log-dir", str(tmp_path), "--json"])
    assert exit_code == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["total"] == 1


def test_main_missing_log_dir_reports_zero_not_error(tmp_path, capsys):
    missing = tmp_path / "does-not-exist"
    exit_code = reliability.main(["--plugin", "p", "--log-dir", str(missing), "--json"])
    assert exit_code == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["total"] == 0

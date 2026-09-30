"""Guard: `agent-bridge service status` surfaces reconcile staleness plainly
(agent-bridge-unified-zdd-cutover Phase 0's observability item) -- an
operator should never have to reconstruct "how long since the last
successful reconcile, is background reconcile enabled" from a raw log
file. Background reconcile is now always-on (the opt-in gate was removed;
see test_bootstrap_check_reconcile_always_on.py), so "enabled" is a
constant; what genuinely varies is staleness.
"""

from __future__ import annotations

import datetime
import json

import pytest

from agent_bridge import service_process_cli as spc


def test_format_reconcile_age_days():
    now = datetime.datetime.now(datetime.timezone.utc)
    at = (now - datetime.timedelta(days=5, minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert spc._format_reconcile_age(at) == "5 days ago"


def test_format_reconcile_age_singular_day():
    now = datetime.datetime.now(datetime.timezone.utc)
    at = (now - datetime.timedelta(days=1, minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert spc._format_reconcile_age(at) == "1 day ago"


def test_format_reconcile_age_unparseable_returns_none():
    assert spc._format_reconcile_age("not-a-timestamp") is None
    assert spc._format_reconcile_age(None) is None


def test_print_reconcile_status_always_enabled(tmp_path, monkeypatch, capsys):
    core = spc._core()
    monkeypatch.setattr(core, "_INSTALL_DIR", str(tmp_path))
    spc._print_reconcile_status()
    out = capsys.readouterr().out
    assert "Background reconcile: enabled" in out
    assert "none recorded yet" in out


def test_print_reconcile_status_reports_staleness(tmp_path, monkeypatch, capsys):
    core = spc._core()
    monkeypatch.setattr(core, "_INSTALL_DIR", str(tmp_path))
    status_path = tmp_path / "reconcile-status.json"
    status_path.write_text(
        json.dumps(
            {
                "at": "2020-01-01T00:00:00Z",
                "from": "1.0.0",
                "to": "1.0.1",
                "launched_pid": 123,
                "log": str(tmp_path / "reconcile.log"),
            }
        ),
        encoding="utf-8",
    )
    spc._print_reconcile_status()
    out = capsys.readouterr().out
    assert "Background reconcile: enabled" in out
    assert "days ago" in out
    assert "1.0.0 -> 1.0.1" in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

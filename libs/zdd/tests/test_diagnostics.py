"""Tests for the shared daemon-health audit + repair helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zdd import breadcrumb, diagnostics, routing


def _ctx(
    tmp_path: Path,
    *,
    state: dict[str, object],
    health_check=None,
    make_client=None,
    is_superseded=None,
) -> diagnostics.DiagnosticContext:
    def _candidates() -> list[diagnostics.DaemonCandidate]:
        return [
            diagnostics.DaemonCandidate(pid=pid, start_time=start_time)
            for pid, start_time in sorted(state["live"].items())
        ]

    def _terminate(pid: int, expected_start_time: str | None) -> dict:
        if state["live"].get(pid) != expected_start_time:
            return {
                "killed": False,
                "identity_verified": False,
                "method": "identity-mismatch",
            }
        state["terminated"].append(pid)
        state["live"].pop(pid, None)
        return {"killed": True, "identity_verified": True, "method": "fake"}

    return diagnostics.DiagnosticContext(
        service="test-daemon",
        config_dir=tmp_path,
        read_lock=lambda: state["lock"],
        lock_is_live=lambda data: data == state["lock"],
        list_candidates=_candidates,
        is_superseded=is_superseded or (lambda pid, generation: False),
        terminate_pid_if_identity=_terminate,
        make_client=make_client,
        health_check=health_check,
        abandoned_passive_grace_seconds=0.0,
    )


def _set_breadcrumb_age(tmp_path: Path, record: dict, *, seconds: float) -> None:
    record = dict(record)
    record["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat()
    (tmp_path / "cutover.json").write_text(json.dumps(record), encoding="utf-8")


def test_audit_reports_duplicate_resident_without_side_effects(tmp_path: Path):
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=101, version="1.0.0")
    state = {
        "lock": {"pid": 101, "start_time": "owner"},
        "live": {101: "owner", 202: "duplicate"},
        "terminated": [],
    }

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state=state))

    assert report["counts"]["duplicate_resident"] == 1
    finding = report["findings"][0]
    assert finding["kind"] == "duplicate_resident"
    assert finding["owner"]["pid"] == 101
    assert [item["pid"] for item in finding["targets"]] == [202]
    assert state["terminated"] == []


def test_apply_repairs_same_version_duplicate_without_touching_owner(tmp_path: Path):
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=101, version="1.0.0")
    state = {
        "lock": {"pid": 101, "start_time": "owner"},
        "live": {101: "owner", 202: "duplicate"},
        "terminated": [],
    }

    result = diagnostics.apply_daemon_health(_ctx(tmp_path, state=state))

    assert state["live"] == {101: "owner"}
    assert state["terminated"] == [202]
    assert result["before"]["counts"]["duplicate_resident"] == 1
    assert result["after"]["counts"]["total"] == 0
    assert result["actions"][0]["termination"]["killed"] is True


def test_audit_and_apply_report_abandoned_passive_without_duplicate_noise(tmp_path: Path):
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=101, version="1.0.0")
    record = breadcrumb.write_breadcrumb(
        tmp_path,
        state="started",
        old={"bind": "127.0.0.1", "port": 9281},
        new_port=9282,
        new_pid=303,
    )
    _set_breadcrumb_age(tmp_path, record, seconds=9999)
    state = {
        "lock": {"pid": 101, "start_time": "owner"},
        "live": {101: "owner", 303: "passive"},
        "terminated": [],
    }

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state=state))
    assert report["counts"]["abandoned_passive"] == 1
    assert report["counts"]["total"] == 1

    result = diagnostics.apply_daemon_health(_ctx(tmp_path, state=state))
    assert state["live"] == {101: "owner"}
    assert state["terminated"] == [303]
    assert result["after"]["counts"]["total"] == 0


def test_audit_and_apply_recover_stranded_survivor(tmp_path: Path):
    record = breadcrumb.write_breadcrumb(
        tmp_path,
        state="draining",
        old={"bind": "127.0.0.1", "port": 9281},
        new_port=9282,
        new_pid=303,
    )
    _set_breadcrumb_age(tmp_path, record, seconds=9999)
    undrained: list[str] = []
    state = {
        "lock": {"pid": 101, "start_time": "owner"},
        "live": {101: "owner"},
        "terminated": [],
    }

    class _Client:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url

        def undrain(self) -> dict:
            undrained.append(self.base_url)
            return {"draining": False}

    ctx = _ctx(
        tmp_path,
        state=state,
        make_client=_Client,
        health_check=lambda host, port: (host, port) == ("127.0.0.1", 9281),
    )
    report = diagnostics.audit_daemon_health(ctx)
    assert report["counts"]["stranded_survivor"] == 1

    result = diagnostics.apply_daemon_health(ctx)
    assert undrained == ["http://127.0.0.1:9281"]
    assert result["actions"][0]["result"]["recovered"] is True
    assert result["after"]["counts"]["total"] == 0


def test_audit_and_apply_reap_superseded_generation_without_touching_owner(tmp_path: Path):
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=202, version="1.0.0")
    routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=9282,
        pid=101,
        version="1.0.0",
        demote_existing=True,
    )
    state = {
        "lock": {"pid": 101, "start_time": "owner"},
        "live": {101: "owner", 202: "old"},
        "terminated": [],
    }
    ctx = _ctx(
        tmp_path,
        state=state,
        is_superseded=lambda pid, generation: pid == 202 and generation == 1,
    )

    report = diagnostics.audit_daemon_health(ctx)
    assert report["counts"]["superseded_generation"] == 1
    assert report["counts"]["total"] == 1

    result = diagnostics.apply_daemon_health(ctx)
    assert state["live"] == {101: "owner"}
    assert state["terminated"] == [202]
    assert result["after"]["counts"]["total"] == 0


def test_apply_blocks_duplicate_repair_without_validated_owner(tmp_path: Path):
    state = {
        "lock": {"pid": 999, "start_time": "missing-owner"},
        "live": {101: "one", 202: "two"},
        "terminated": [],
    }

    result = diagnostics.apply_daemon_health(_ctx(tmp_path, state=state))

    assert state["terminated"] == []
    assert result["actions"][0]["blocked"] is True
    assert result["actions"][0]["reason"] == "no validated live owner"

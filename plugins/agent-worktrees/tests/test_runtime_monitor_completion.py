from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from agent_worktrees import runtime_monitor_completion as completion
from zdd import breadcrumb, routing


@pytest.mark.parametrize("phase", ["started", "flipped", "committed", "publication-uncertain"])
@pytest.mark.parametrize("grant_fails", [False, True])
def test_existing_resident_completes_retained_witness_after_owner_loss(
    tmp_path, monkeypatch, caplog, phase, grant_fails,
):
    endpoint = routing.Endpoint("127.0.0.1", 61001, os.getpid(), "new", 2, "2002")
    record = {
        "state": phase, "new_pid": os.getpid(), "new_process_start_time": "2002",
        "new_port": endpoint.port, "self_retirement": True,
        "old": routing.Endpoint("127.0.0.1", 61000, 101, "old", 1, "1001").to_dict(),
    }
    state = {"record": record, "released": False, "promoted": False, "republished": False}
    monkeypatch.setattr(completion.locks, "process_start_time", lambda pid: "2002")
    monkeypatch.setattr(
        completion.cutover, "_acquire_cutover_lock",
        lambda *a, **k: SimpleNamespace(release=lambda: state.update(released=True)),
    )
    monkeypatch.setattr(breadcrumb, "read_breadcrumb", lambda path: state["record"])
    def clear(path):
        state["record"] = None
        return True

    monkeypatch.setattr(breadcrumb, "clear_breadcrumb", clear)
    monkeypatch.setattr(breadcrumb, "write_breadcrumb", lambda path, **kwargs: state.update(record=kwargs))
    monkeypatch.setattr(routing, "read_table", lambda *a, **k: {"active": endpoint.to_dict()})
    monkeypatch.setattr(routing, "confirm_active", lambda *a, **k: state.update(republished=True))
    monkeypatch.setattr(completion.retirements, "_records", lambda root: [(tmp_path / "old.json", {
        "pid": 101, "start": "1001", "slot": None, "release_claim": None, "state": "prepared",
    })])
    events = []

    def commit(*args, **kwargs):
        events.append("grant")
        if grant_fails:
            raise OSError("retirement publication unavailable")
        return True

    def promote():
        events.append("promote")
        state["promoted"] = True
        return {"adopted": True}

    monkeypatch.setattr(completion.retirements, "commit", commit)
    completion.recover(tmp_path, promote=promote, ready=lambda: state["promoted"])
    assert state["released"]
    assert state["republished"] is (phase != "committed")
    if grant_fails:
        assert state["record"] is not None and not state["promoted"]
        assert "completion remains pending" in caplog.text
    else:
        assert events == ["grant", "promote"] and state["record"] is None


@pytest.mark.parametrize("marker", [None, False, 1, "true"])
def test_resident_does_not_complete_synchronous_or_malformed_witness(tmp_path, monkeypatch, marker):
    monkeypatch.setattr(breadcrumb, "read_breadcrumb", lambda path: {
        "state": "flipped", "new_pid": os.getpid(), "self_retirement": marker,
    })
    monkeypatch.setattr(
        completion.cutover, "_acquire_cutover_lock",
        lambda *a, **k: pytest.fail("non-asynchronous witness must not acquire custody"),
    )
    completion.recover(
        tmp_path, promote=lambda: pytest.fail("synchronous candidate must not be promoted"),
        ready=lambda: False,
    )


@pytest.mark.parametrize("failure", ["promotion", "cleanup", "route", "missing-route"])
def test_completion_retains_recovery_evidence_on_failure(tmp_path, monkeypatch, caplog, failure):
    directory = tmp_path / "status-monitor-routing"
    old = routing.Endpoint("127.0.0.1", 61000, 101, "old", 1, "1001")
    new = routing.Endpoint("127.0.0.1", 61001, os.getpid(), "new", 2, "2002")
    monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda pid: "1001" if pid == 101 else "2002")
    routing.publish_active(directory, **old.to_dict())
    routing.publish_active(directory, **new.to_dict(), demote_existing=True)
    breadcrumb.write_breadcrumb(
        directory, state="started", old=old.to_dict(), new_port=new.port,
        new_pid=new.pid, new_process_start_time=new.process_start_time,
        self_retirement=True,
    )
    monkeypatch.setattr(completion.locks, "process_start_time", lambda pid: "2002")
    monkeypatch.setattr(
        completion.cutover, "_acquire_cutover_lock",
        lambda *a, **k: SimpleNamespace(release=lambda: None),
    )
    monkeypatch.setattr(completion.retirements, "_records", lambda root: [])
    if failure == "cleanup":
        monkeypatch.setattr(breadcrumb, "clear_breadcrumb", lambda path: False)
    ready = {"value": False}

    def promote():
        if failure == "promotion":
            raise RuntimeError("promotion failed")
        ready["value"] = True
        if failure == "route":
            routing.publish_active(directory, **old.to_dict())
        elif failure == "missing-route":
            routing.routing_table_path(directory).write_text("{}", encoding="utf-8")
        return {"adopted": True}

    completion.recover(tmp_path, promote=promote, ready=lambda: ready["value"])
    assert breadcrumb.read_breadcrumb(directory)["state"] == "committed"
    assert "completion remains pending" in caplog.text
    if failure not in {"route", "missing-route"}:
        table = routing.read_table(directory, strict=True)
        assert table["active"] == new.to_dict()
        assert table["previous"] == old.to_dict()

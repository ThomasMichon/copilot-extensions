from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from agent_worktrees import runtime_monitor_completion as completion
from zdd import breadcrumb, routing


@pytest.mark.parametrize("phase", ["flipped", "committed", "publication-uncertain"])
@pytest.mark.parametrize("grant_fails", [False, True])
def test_existing_resident_completes_retained_witness_after_owner_loss(
    tmp_path, monkeypatch, caplog, phase, grant_fails,
):
    endpoint = routing.Endpoint("127.0.0.1", 61001, os.getpid(), "new", 2, "2002")
    record = {
        "state": phase, "new_pid": os.getpid(), "new_process_start_time": "2002",
        "old": {"pid": 101, "process_start_time": "1001"},
    }
    state = {"record": record, "released": False, "promoted": False, "republished": False}
    monkeypatch.setattr(completion.locks, "process_start_time", lambda pid: "2002")
    monkeypatch.setattr(
        completion.cutover, "_acquire_cutover_lock",
        lambda *a, **k: SimpleNamespace(release=lambda: state.update(released=True)),
    )
    monkeypatch.setattr(breadcrumb, "read_breadcrumb", lambda path: state["record"])
    monkeypatch.setattr(breadcrumb, "clear_breadcrumb", lambda path: state.update(record=None))
    monkeypatch.setattr(breadcrumb, "write_breadcrumb", lambda path, **kwargs: state.update(record=kwargs))
    monkeypatch.setattr(routing, "read_table", lambda *a, **k: {"active": endpoint.to_dict()})
    monkeypatch.setattr(routing, "publish_active", lambda *a, **k: state.update(republished=True))
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

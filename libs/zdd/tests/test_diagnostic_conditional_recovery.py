"""Generic diagnostics cannot discard retained conditional-generation work."""

from __future__ import annotations

import pytest
import time

from zdd import breadcrumb, diagnostics, routing
from zdd.cutover_lock import CutoverLock

from .test_diagnostics import _ctx


@pytest.mark.parametrize("reachable", [False, True])
def test_retained_promoted_successor_requires_owner_recovery(tmp_path, reachable):
    routing.publish_active(tmp_path, bind="127.0.0.1", port=61002, pid=202, version="new")
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=61001, pid=101,
        version="old", demote_existing=True,
    )
    breadcrumb.write_breadcrumb(
        tmp_path, state="conditional-flipped",
        old={"bind": "127.0.0.1", "port": 61001}, new_pid=202, new_port=61002,
    )
    before = breadcrumb.breadcrumb_path(tmp_path).read_bytes()
    state = {
        "lock": {"pid": 101, "start_time": "old"},
        "live": {101: "old", 202: "new"}, "terminated": [],
    }
    probes = []
    ctx = _ctx(
        tmp_path, state=state,
        reachability_check=lambda *_: probes.append(True) or reachable,
    )
    report = diagnostics.audit_daemon_health(ctx)
    result = diagnostics.apply_daemon_health(ctx)
    assert report["findings"][0]["repairable"] is False
    assert result["actions"][0]["blocked"] is True
    assert result["counts"]["conditional_recovery"] == 1
    assert state["terminated"] == [] and probes == []
    assert breadcrumb.breadcrumb_path(tmp_path).read_bytes() == before


def test_diagnostics_requires_native_authority_even_without_consumer_guard(tmp_path):
    state = {
        "lock": {"pid": 101, "start_time": "old"},
        "live": {101: "old", 202: "new"}, "terminated": [],
    }
    with CutoverLock(tmp_path):
        result = diagnostics.apply_daemon_health(_ctx(tmp_path, state=state))
    assert result["actions"][0]["blocked"]
    assert state["terminated"] == []


@pytest.mark.parametrize("content", ["{", "{}", '{"state":"unknown"}'])
def test_diagnostics_refuses_unverifiable_recovery_state(tmp_path, content):
    state = {
        "lock": {"pid": 101, "start_time": "old"},
        "live": {101: "old", 202: "new"}, "terminated": [],
    }
    breadcrumb.breadcrumb_path(tmp_path).write_text(content)
    result = diagnostics.apply_daemon_health(_ctx(tmp_path, state=state))
    assert result["actions"][0]["blocked"]
    assert state["terminated"] == []
    assert breadcrumb.breadcrumb_path(tmp_path).read_text() == content


def test_generic_diagnostics_retains_displaced_promoted_candidate(tmp_path):
    witness = routing.publication_witness(tmp_path, deadline=time.monotonic() + 1)
    for pid, port in ((202, 61002), (303, 61003), (404, 61004)):
        routing.publish_active(
            tmp_path, bind="127.0.0.1", port=port, pid=pid,
            version="fixture", demote_existing=True,
        )
    breadcrumb.write_breadcrumb(
        tmp_path, state="started", new_pid=202, new_port=61002,
        new_process_start_time="new", publication_witness=witness,
    )
    state = {
        "lock": {"pid": 404, "start_time": "winner"},
        "live": {202: "new", 303: "middle", 404: "winner"}, "terminated": [],
    }
    result = diagnostics.apply_daemon_health(_ctx(tmp_path, state=state))
    assert state["terminated"] == []
    action = next(action for action in result["actions"] if action.get("pid") == 202)
    assert action["termination"]["blocked"]

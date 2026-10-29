"""Generic diagnostics cannot discard retained conditional-generation work."""

from __future__ import annotations

import pytest
import time
import threading
from contextlib import ExitStack
from types import SimpleNamespace

from zdd import breadcrumb, diagnostics, routing
from zdd.cutover_lock import CutoverBorrowError, CutoverLock, CutoverLockedError

from .test_diagnostics import _ctx


@pytest.mark.parametrize("invalid", ["closed", "nested", "thread", "process"])
def test_diagnostic_apply_refuses_invalid_native_borrow_before_mutation(
    tmp_path, monkeypatch, invalid,
):
    from zdd import cutover_lock

    state = {"lock": None, "live": {}, "terminated": []}
    lease = CutoverLock(tmp_path)
    lease.acquire()
    mutations, results, errors = [], [], []
    monkeypatch.setattr(
        diagnostics, "_apply_daemon_health_locked",
        lambda *args, **kwargs: mutations.append(True) or {"mutated": True},
    )
    ctx = _ctx(tmp_path, state=state, acquire_cutover_guard=lambda _: lease)

    def apply():
        try:
            results.append(diagnostics.apply_daemon_health(ctx))
        except Exception as exc:
            errors.append(exc)

    try:
        with ExitStack() as scope:
            if invalid == "closed":
                lease._fh.close()
            elif invalid == "nested":
                scope.enter_context(lease.borrow())
            elif invalid == "process":
                pid = cutover_lock.os.getpid()
                patch = scope.enter_context(monkeypatch.context())
                patch.setattr(cutover_lock, "os", SimpleNamespace(getpid=lambda: pid + 1))
            if invalid == "thread":
                thread = threading.Thread(target=apply)
                thread.start()
                thread.join(timeout=3)
                assert not thread.is_alive()
            else:
                apply()
            assert mutations == []
            assert errors == []
            assert results[0]["actions"][0]["blocked"] is True
            assert results[0]["actions"][0]["kind"] == "cutover_guard"
            assert lease.held is (invalid != "closed")
            assert lease._borrowed is (invalid == "nested")
        if lease.held:
            with pytest.raises(CutoverLockedError):
                CutoverLock(tmp_path).acquire()
    finally:
        lease.release()


@pytest.mark.parametrize("same_path", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_diagnostic_apply_borrows_and_releases_its_acquired_guard(
    tmp_path, monkeypatch, same_path, fails,
):
    state = {"lock": None, "live": {}, "terminated": []}
    lease = CutoverLock(tmp_path if same_path else tmp_path / "consumer")

    def acquire(_):
        lease.acquire()
        return lease

    def mutate(*args, **kwargs):
        assert lease._borrowed and lease.held
        with pytest.raises(CutoverLockedError):
            CutoverLock(tmp_path).acquire()
        with pytest.raises(CutoverBorrowError):
            lease.release()
        if fails:
            raise RuntimeError("fixture mutation failure")
        return {"completed": True}

    monkeypatch.setattr(diagnostics, "_apply_daemon_health_locked", mutate)
    ctx = _ctx(tmp_path, state=state, acquire_cutover_guard=acquire)
    try:
        if fails:
            with pytest.raises(RuntimeError, match="fixture mutation failure"):
                diagnostics.apply_daemon_health(ctx)
        else:
            assert diagnostics.apply_daemon_health(ctx) == {"completed": True}
        assert not lease.held and not lease._borrowed
        with CutoverLock(tmp_path):
            pass
    finally:
        lease.release()


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

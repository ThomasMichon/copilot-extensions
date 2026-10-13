from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from zdd import breadcrumb, conditional, routing
from zdd.conditional import ConditionalCutover, PreparedCutover, RuntimeAttestation, RuntimeSnapshot
from zdd.cutover import CutoverOrchestrator
from zdd.cutover_lock import CutoverLock


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda pid: str(pid * 10))
    old = routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=61001,
        pid=101,
        version="1.0.0",
    )
    events = []

    class Handle:
        pid = 202
        exited = False

        def terminate(self):
            events.append("terminate")
            self.exited = True

        def poll(self):
            return 0 if self.exited else None

    class Client:
        def drain(self, **kwargs):
            events.append("drain")
            return {"drained": True, "clean": True, "forced": False}

        def undrain(self):
            events.append("undrain")
            return {}

        def shutdown(self):
            events.append("shutdown")
            return {}

        def adopt_relay(self):
            return {}

    handle = Handle()

    def terminate_identity(pid, start, *, exit_timeout):
        assert pid == handle.pid and start == str(pid * 10)
        assert exit_timeout == 5.0
        handle.terminate()
        return {"killed": True, "identity_verified": True, "exit_confirmed": True}

    monkeypatch.setattr("zdd.diagnostics.terminate_pid_if_identity", terminate_identity)

    def spawn(port):
        events.append("spawn")
        return handle

    def health(endpoint):
        return RuntimeAttestation(
            endpoint.pid,
            endpoint.process_start_time,
            endpoint.version,
            "effective-config-v1",
            True,
        )

    snapshot = RuntimeSnapshot(
        "effective-config-v1",
        spawn_passive=spawn,
        attest=health,
        make_client=lambda _: Client(),
        retire=lambda endpoint, successor: Client().shutdown(),
        confirm_retired=lambda endpoint, timeout: True,
        drain_predecessor=lambda old, new, timeout: Client().drain(
            timeout=timeout, poll=1.0, force=False
        ),
        undrain_predecessor=lambda old: Client().undrain(),
        adopt_successor=lambda new: Client().adopt_relay(),
    )
    request = ConditionalCutover(
        expected_active=old,
        selected_is_newer=lambda selected, serving: (
            tuple(map(int, selected.split("."))) > tuple(map(int, serving.split(".")))
        ),
        capture_runtime=lambda _: snapshot,
        routing_lock_timeout=0.05,
    )
    orch = CutoverOrchestrator(
        tmp_path,
        bind="127.0.0.1",
        version="2.0.0",
        spawn_passive=lambda _: pytest.fail("ambient spawn must never be used"),
        health_check=lambda *_: pytest.fail("ambient health must never be used"),
        make_client=lambda _: pytest.fail("ambient credentials must never be used"),
        pick_free_port=lambda: 61002,
    )
    return tmp_path, old, events, handle, snapshot, request, orch


def test_routing_budget_is_fresh_after_runtime_capture(case, monkeypatch):
    root, _, _, _, snapshot, request, orch = case
    now = [100.0]
    deadlines = []
    real_validate = routing.validate_active

    def capture(_owner):
        now[0] += 30.0
        return snapshot

    def validate(*args, **kwargs):
        deadlines.append(kwargs["lock_deadline"])
        return real_validate(*args, **kwargs)

    monkeypatch.setattr(conditional, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(routing, "validate_active", validate)
    with CutoverLock(root):
        prepared = PreparedCutover(orch, replace(request, capture_runtime=capture))
    assert deadlines == pytest.approx([
        100.0 + request.routing_lock_timeout,
        130.0 + request.routing_lock_timeout,
    ])
    assert prepared.routing_deadline() == pytest.approx(130.0 + request.routing_lock_timeout)


@pytest.mark.parametrize("change", ["pid", "generation", "start", "missing-start", "newer"])
def test_conditional_refuses_changed_or_unverified_owner_before_mutation(case, change):
    root, old, events, _, _, request, orch = case
    if change == "missing-start":
        expected = replace(old, process_start_time=None)
        routing._atomic_write(root / "active.json", {"active": expected.to_dict()})
        request = replace(request, expected_active=expected)
    else:
        altered = {
            "pid": replace(old, pid=999),
            "generation": replace(old, generation=old.generation + 1),
            "start": replace(old, process_start_time="9999"),
            "newer": replace(old, version="3.0.0"),
        }[change]
        routing._atomic_write(root / "active.json", {"active": altered.to_dict()})
        if change == "newer":
            request = replace(request, expected_active=altered)
    before = (root / "active.json").read_bytes()
    result = orch.run(conditional=request)
    assert not result.ok and not result.committed
    assert result.error
    assert events == []
    assert (root / "active.json").read_bytes() == before
    assert breadcrumb.read_breadcrumb(root) is None


def test_conditional_revalidates_after_native_cutover_lock_wait(case):
    root, old, events, _, _, request, orch = case
    with CutoverLock(root):
        routing._atomic_write(
            root / "active.json",
            {"active": replace(old, generation=9).to_dict()},
        )
        result = orch.run(conditional=request, lock_timeout=0)
    assert not result.ok and events == []
    result = orch.run(conditional=request)
    assert not result.ok and "changed" in result.error and events == []


@pytest.mark.parametrize("snapshot_kind", ["missing", "mismatch", "identity-reused"])
def test_conditional_snapshot_refusal_is_pre_spawn(case, monkeypatch, snapshot_kind):
    root, _, events, _, snapshot, request, orch = case
    if snapshot_kind == "missing":
        request = replace(request, capture_runtime=lambda _: None)
    elif snapshot_kind == "mismatch":
        snapshot = replace(snapshot, identity="not-the-serving-config")
        request = replace(request, capture_runtime=lambda _: snapshot)
    else:
        monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda _: "wrong")
    before = (root / "active.json").read_bytes()
    result = orch.run(conditional=request)
    assert not result.ok and result.error
    assert events == []
    assert (root / "active.json").read_bytes() == before


def test_conditional_success_uses_captured_context_and_attests_retirement(case, monkeypatch):
    _, old, events, _, snapshot, request, orch = case
    secret = "fixture-only-control-token"
    child_environment = {"DATABASE": "captured.db", "CONTROL_TOKEN": secret}
    seen = []

    def spawn(port):
        assert child_environment["DATABASE"] == "captured.db"
        assert child_environment["CONTROL_TOKEN"] == secret
        return snapshot.spawn_passive(port)

    def attest(endpoint):
        seen.append(endpoint)
        return snapshot.attest(endpoint)

    request = replace(
        request,
        capture_runtime=lambda _: replace(snapshot, spawn_passive=spawn, attest=attest),
    )
    monkeypatch.setenv("DATABASE", "unrelated-caller.db")
    monkeypatch.setenv("COPILOT_TEST_SECRET", "must-not-be-copied")
    result = orch.run(conditional=request)
    assert result.ok and result.committed
    assert events == ["spawn", "drain", "shutdown"]
    assert seen[0] == old
    assert len([ep for ep in seen if ep.pid == 202]) >= 2
    assert result.serving_endpoint.pid == 202
    assert result.retirement_requested is True
    assert secret not in repr(request) + repr(result.to_dict())


def test_candidate_promoted_then_displaced_twice_is_not_disposed(case):
    root, _, events, handle, snapshot, request, orch = case

    def attest(endpoint):
        attested = snapshot.attest(endpoint)
        if endpoint.pid == 202:
            for pid, port in ((202, 61002), (303, 61003), (404, 61004)):
                routing.publish_active(
                    root, bind="127.0.0.1", port=port, pid=pid,
                    version="2.0.0", demote_existing=True,
                )
            return replace(attested, ready=False)
        return attested

    request = replace(request, capture_runtime=lambda _: replace(snapshot, attest=attest))
    result = orch.run(conditional=request, health_timeout=0)
    assert not result.ok and not result.rolled_back
    assert handle.poll() is None and events == ["spawn"]
    assert any("candidate disposal refused" in step for step in result.steps)


@pytest.mark.parametrize("exit_confirmed", [False, True])
def test_disposal_confirms_native_exit_before_releasing_publication_fence(
    case, monkeypatch, exit_confirmed,
):
    root, _, events, handle, snapshot, request, orch = case
    outcomes = []

    def publish_competitor():
        try:
            with routing.routing_lock(root, deadline=time.monotonic()):
                outcomes.append("admitted")
        except routing.RoutingLockTimeout:
            outcomes.append("blocked")

    def terminate(pid, start, *, exit_timeout):
        assert pid == handle.pid and start == str(pid * 10) and exit_timeout == 5.0
        competitor = threading.Thread(target=publish_competitor)
        competitor.start()
        competitor.join(timeout=2)
        assert not competitor.is_alive()
        if exit_confirmed:
            handle.terminate()
        return {
            "killed": True, "identity_verified": True, "exit_confirmed": exit_confirmed,
        }

    monkeypatch.setattr("zdd.diagnostics.terminate_pid_if_identity", terminate)
    monkeypatch.setattr(
        orch, "_await_exit",
        lambda _: pytest.fail("candidate exit checked outside publication fence"),
    )
    attest = snapshot.attest
    request = replace(request, capture_runtime=lambda _: replace(
        snapshot, attest=lambda endpoint: replace(
            attest(endpoint), ready=endpoint.pid != handle.pid,
        ),
    ))
    result = orch.run(conditional=request, health_timeout=0)
    assert not result.ok and result.rolled_back is exit_confirmed
    assert outcomes == ["blocked"]
    assert events == (["spawn", "terminate"] if exit_confirmed else ["spawn"])
    if exit_confirmed:
        assert breadcrumb.read_breadcrumb(root) is None
    else:
        assert breadcrumb.read_breadcrumb(root)["state"] == "conditional-started"


@pytest.mark.parametrize("content", ["{", "{}", '{"state":"unknown"}'])
def test_ordinary_cutover_refuses_unverifiable_breadcrumb(case, content):
    root, _, events, _, _, _, orch = case
    breadcrumb.breadcrumb_path(root).write_text(content)
    result = orch.run()
    assert not result.ok and "owner-led recovery" in result.error
    assert events == []
    assert breadcrumb.breadcrumb_path(root).read_text() == content


def test_ordinary_cutover_returns_failure_on_unverifiable_route(case):
    root, _, events, _, _, _, orch = case
    routing.routing_table_path(root).write_text("{corrupt")
    result = orch.run()
    assert not result.ok and "publication custody unavailable" in result.error
    assert events == [] and breadcrumb.read_breadcrumb(root) is None
    assert routing.routing_table_path(root).read_text() == "{corrupt"


def test_saturated_history_does_not_block_verified_owner_handoff(case, monkeypatch):
    from zdd import publication_history

    root, _, events, _, _, request, orch = case
    monkeypatch.setattr(publication_history, "MAX_ENTRIES", 1)
    result = orch.run(conditional=request)
    assert result.ok and events == ["spawn", "drain", "shutdown"]
    assert routing.read_table(root)[publication_history.KEY]["saturated"]


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux directory fsync")
def test_post_rename_fsync_failure_retains_routable_successor(case, monkeypatch):
    from zdd import routing_persistence
    import stat

    root, _, events, handle, snapshot, _, orch = case
    orch.spawn_passive = snapshot.spawn_passive
    orch.health_check = lambda *_: True
    orch.make_client = snapshot.make_client
    monkeypatch.setattr(routing, "_pid_alive", lambda _: True)
    monkeypatch.setattr(routing, "_listening", lambda *_a, **_k: True)
    real_fsync = routing_persistence.os.fsync
    calls = []

    def fsync(descriptor):
        calls.append(descriptor)
        table = routing.read_table(root) or {}
        if (
            stat.S_ISDIR(os.fstat(descriptor).st_mode)
            and (table.get("active") or {}).get("pid") == 202
        ):
            raise OSError("fixture post-rename directory flush failure")
        real_fsync(descriptor)

    monkeypatch.setattr(routing_persistence.os, "fsync", fsync)
    result = orch.run()
    assert not result.ok and not result.rolled_back and result.publication_uncertain
    assert events == ["spawn"] and handle.poll() is None
    assert routing.read_table(root)["active"]["pid"] == 202
    assert breadcrumb.read_breadcrumb(root)["state"] == "publication-uncertain"
    assert result.to_dict()["publication_uncertain"] is True
    refused = orch.run()
    assert not refused.ok and "owner-led recovery" in refused.error


def test_conditional_pre_adoption_combination_refuses_before_mutation(case):
    root, _, events, _, _, request, orch = case
    before = (root / "active.json").read_bytes()
    orch.drain_before_publish = True
    result = orch.run(conditional=request)
    assert not result.ok and result.conditional
    assert "pre-publication drain" in result.error
    assert events == [] and breadcrumb.read_breadcrumb(root) is None
    assert (root / "active.json").read_bytes() == before


def test_conditional_can_borrow_caller_guard_without_bypassing_capture(case):
    root, _, events, _, _, request, orch = case
    with CutoverLock(root) as lease:
        result = orch.run(conditional=request, held_lock=lease)
        assert result.ok and result.retirement_confirmed
        assert lease.held
        assert events == ["spawn", "drain", "shutdown"]
    assert breadcrumb.read_breadcrumb(root) is None


@pytest.mark.parametrize("conditional_request", [False, True])
def test_borrowed_guard_refuses_corrupt_recovery_before_any_capture(
    case, conditional_request,
):
    root, _, events, _, _, request, orch = case
    path = root / "cutover.json"
    path.write_text("broken", encoding="utf-8")
    before = (root / "active.json").read_bytes()
    with CutoverLock(root) as lease:
        result = orch.run(
            conditional=request if conditional_request else None,
            held_lock=lease,
        )
        assert lease.held
    assert not result.ok and "owner-led recovery" in result.error
    assert events == [] and path.read_text() == "broken"
    assert (root / "active.json").read_bytes() == before


@pytest.mark.parametrize("after_flip", [False, True])
def test_conditional_config_mismatch_rolls_back_before_retiring(case, after_flip):
    root, old, events, _, snapshot, request, orch = case

    def attest(endpoint):
        health = snapshot.attest(endpoint)
        if endpoint.pid == 202 and (not after_flip or "drain" in events):
            return replace(health, config_identity="wrong")
        return health

    request = replace(request, capture_runtime=lambda _: replace(snapshot, attest=attest))
    result = orch.run(conditional=request, health_timeout=0)
    assert not result.ok and not result.committed
    assert result.rolled_back == (not after_flip)
    assert "shutdown" not in events
    assert ("terminate" in events) == (not after_flip)
    assert routing.read_table(root)["active"]["pid"] == old.pid
    assert ("undrain" in events) == after_flip


def test_conditional_route_winner_during_health_is_not_overwritten(case):
    root, old, events, _, snapshot, request, orch = case
    winner = replace(old, pid=303, process_start_time="3030", generation=20, version="3.0.0")

    def attest(endpoint):
        if endpoint.pid == 202:
            routing._atomic_write(root / "active.json", {"active": winner.to_dict()})
        return snapshot.attest(endpoint)

    request = replace(request, capture_runtime=lambda _: replace(snapshot, attest=attest))
    result = orch.run(conditional=request)
    assert not result.ok and not result.committed
    assert events == ["spawn", "terminate"]
    assert routing.read_table(root)["active"] == winner.to_dict()


def test_conditional_capture_cannot_change_owner_before_spawn(case):
    root, old, events, _, snapshot, request, orch = case
    winner = replace(old, generation=20)

    def capture(endpoint):
        routing._atomic_write(root / "active.json", {"active": winner.to_dict()})
        return snapshot

    result = orch.run(conditional=replace(request, capture_runtime=capture))
    assert not result.ok and "changed" in result.error and not events
    assert breadcrumb.read_breadcrumb(root) is None


def test_conditional_refuses_unfinished_transition_without_recovery(case):
    root, old, events, _, _, request, orch = case
    breadcrumb.write_breadcrumb(root, state="started", new_port=61002, new_pid=999)
    before = (root / "active.json").read_bytes()
    crumb = breadcrumb.read_breadcrumb(root)
    result = orch.run(conditional=request)
    assert not result.ok and "owner-led recovery" in result.error
    assert not events
    assert (root / "active.json").read_bytes() == before
    assert breadcrumb.read_breadcrumb(root) == crumb


@pytest.mark.parametrize("content", ["{", "{}", '{"state":"unknown"}'])
def test_conditional_refuses_unverifiable_transition_state(case, content):
    root, _, events, _, _, request, orch = case
    path = breadcrumb.breadcrumb_path(root)
    path.write_text(content)
    result = orch.run(conditional=request)
    assert not result.ok and "owner-led recovery" in result.error
    assert not events and path.read_text() == content


def test_conditional_drained_predecessor_can_be_restored(case):
    root, old, events, _, snapshot, request, orch = case

    def attest(endpoint):
        response = snapshot.attest(endpoint)
        if "drain" in events:
            return replace(response, ready=False)
        return response

    result = orch.run(
        conditional=replace(request, capture_runtime=lambda _: replace(snapshot, attest=attest)),
        health_timeout=0,
    )
    assert not result.ok and not result.rolled_back
    assert result.serving_endpoint is None
    assert events == ["spawn", "drain", "undrain"]
    assert routing.read_table(root)["active"]["pid"] == old.pid
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_conditional_post_flip_route_winner_prevents_rollback_and_retirement(case):
    root, old, events, _, snapshot, request, orch = case
    winner = replace(old, pid=303, process_start_time="3030", generation=20, version="3.0.0")
    base_client = snapshot.make_client("")

    class Client:
        def drain(self, **kwargs):
            response = base_client.drain(**kwargs)
            routing._atomic_write(root / "active.json", {"active": winner.to_dict()})
            return response

    result = orch.run(
        conditional=replace(
            request,
            capture_runtime=lambda _: replace(
                snapshot, make_client=lambda _: Client(),
                drain_predecessor=lambda old, new, timeout: Client().drain(timeout=timeout),
            ),
        )
    )
    assert not result.ok and not result.committed and not result.rolled_back
    assert result.serving_endpoint is None
    assert events == ["spawn", "drain"]
    assert routing.read_table(root)["active"] == winner.to_dict()
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_conditional_post_flip_lock_contention_retains_generations_and_recovery(case):
    root, _, events, _, snapshot, request, orch = case
    acquired, release = threading.Event(), threading.Event()

    def hold():
        with routing._routing_lock(root):
            acquired.set()
            assert release.wait(5)

    thread = threading.Thread(target=hold)
    base_client = snapshot.make_client("")

    class Client:
        def drain(self, **kwargs):
            response = base_client.drain(**kwargs)
            thread.start()
            assert acquired.wait(2)
            return response

    try:
        started = time.monotonic()
        result = orch.run(
            conditional=replace(
                request,
                capture_runtime=lambda _: replace(
                    snapshot, make_client=lambda _: Client(),
                    drain_predecessor=lambda old, new, timeout: Client().drain(timeout=timeout),
                ),
            )
        )
        assert time.monotonic() - started < 1
    finally:
        release.set()
        thread.join(2)
    assert not result.ok and not result.committed and not result.rolled_back
    assert events == ["spawn", "drain"]
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


@pytest.mark.parametrize("post_spawn", [False, True])
def test_native_routing_thread_contention_has_bounded_owner_outcome(case, post_spawn):
    root, old, events, _, snapshot, request, orch = case
    acquired = threading.Event()
    release = threading.Event()

    def hold():
        with routing._routing_lock(root):
            acquired.set()
            assert release.wait(5)

    thread = threading.Thread(target=hold)
    if post_spawn:

        def spawn(port):
            thread.start()
            assert acquired.wait(2)
            return snapshot.spawn_passive(port)

        request = replace(
            request, capture_runtime=lambda _: replace(snapshot, spawn_passive=spawn)
        )
    else:
        thread.start()
        assert acquired.wait(2)
    started = time.monotonic()
    try:
        result = orch.run(conditional=request, health_timeout=0)
        assert time.monotonic() - started < 1
    finally:
        release.set()
        thread.join(2)
    assert not result.ok and not result.committed
    assert events == (["spawn"] if post_spawn else [])
    assert routing.read_table(root)["active"] == old.to_dict()
    if post_spawn:
        assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


@pytest.mark.skipif(
    os.name == "nt", reason="Linux flock path; Windows has a native byte-lock test"
)
def test_native_file_lock_deadline_does_not_publish(tmp_path):
    import fcntl

    old = routing.publish_active(tmp_path, bind="127.0.0.1", port=61001)
    before = (tmp_path / "active.json").read_bytes()
    with (tmp_path / "active.lock").open("a+b") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        started = time.monotonic()
        with pytest.raises(routing.RoutingLockTimeout):
            routing.publish_active_with_previous_guarded(
                tmp_path,
                bind="127.0.0.1",
                port=61002,
                expected_active=old,
                lock_deadline=time.monotonic() + 0.05,
            )
        assert time.monotonic() - started < 1
    assert (tmp_path / "active.json").read_bytes() == before


def test_native_routing_does_not_treat_io_error_as_contention(monkeypatch):
    import errno

    def denied():
        raise OSError(errno.EIO, "fixture I/O failure")

    with pytest.raises(OSError) as exc:
        routing._wait_for_lock(denied, time.monotonic() + 10)
    assert exc.value.errno == errno.EIO


def test_conditional_never_disposes_a_candidate_published_by_another_owner(case):
    root, _, events, _, snapshot, request, orch = case

    def attest(endpoint):
        if endpoint.pid == 202:
            routing.publish_active(
                root,
                bind=endpoint.bind,
                port=endpoint.port,
                pid=endpoint.pid,
                version=endpoint.version,
                process_start_time=endpoint.process_start_time,
            )
        return snapshot.attest(endpoint)

    result = orch.run(
        conditional=replace(
            request,
            capture_runtime=lambda _: replace(snapshot, attest=attest),
        )
    )
    assert not result.ok and not result.rolled_back and not result.committed
    assert events == ["spawn"]
    assert routing.read_table(root)["active"]["pid"] == 202
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_conditional_never_replaces_or_terminates_predecessor_as_candidate(case):
    root, old, events, handle, _, request, orch = case
    handle.pid = old.pid
    result = orch.run(conditional=request, health_timeout=0)
    assert not result.ok and not result.committed
    assert events == ["spawn"]
    assert routing.read_table(root)["active"] == old.to_dict()


def test_windows_native_lock_adapter_uses_only_bounded_nonblocking_acquisition(
    tmp_path, monkeypatch
):
    import errno
    import sys
    from types import SimpleNamespace

    calls = []

    def locking(fd, mode, count):
        calls.append(mode)
        assert mode == 2 and count == 1
        raise OSError(errno.EACCES, "fixture mandatory byte lock held")

    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        SimpleNamespace(
            locking=locking,
            LK_LOCK=1,
            LK_NBLCK=2,
            LK_UNLCK=3,
        ),
    )
    monkeypatch.setattr(routing, "sys", SimpleNamespace(platform="win32"))
    started = time.monotonic()
    with pytest.raises(routing.RoutingLockTimeout):
        with routing._file_routing_lock(tmp_path, deadline=started + 0.02):
            pytest.fail("contended acquisition must not enter the mutation body")
    assert calls and set(calls) == {2}
    assert time.monotonic() - started < 1

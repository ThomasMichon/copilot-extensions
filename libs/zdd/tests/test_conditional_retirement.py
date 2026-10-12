from __future__ import annotations

from dataclasses import replace

import pytest

from zdd import breadcrumb, routing
from zdd.cutover import CutoverError

from . import test_conditional_cutover

case = test_conditional_cutover.case


def test_failed_retirement_retains_recovery_and_blocks_next_attempt(case):
    root, _, events, _, snapshot, request, orch = case

    def retire(endpoint):
        raise OSError("fixture transport failed")

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, retire=retire)
        )
    )
    assert not result.ok and result.committed and not result.retirement_requested
    assert events == ["spawn", "drain"]
    assert routing.read_table(root)["active"]["pid"] == 202
    record = breadcrumb.read_breadcrumb(root)
    assert breadcrumb.is_stale(record)
    assert record["old"]["pid"] == 101
    assert record["new_pid"] == 202
    events.clear()
    again = orch.run(conditional=request)
    assert not again.ok and "owner-led recovery" in again.error
    assert not events


def test_restored_ready_predecessor_does_not_dispose_promoted_work(case):
    root, old, events, handle, snapshot, request, orch = case

    def attest(endpoint):
        response = snapshot.attest(endpoint)
        if endpoint.pid == handle.pid and "drain" in events:
            return replace(response, ready=False)
        return response

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, attest=attest)
        )
    )
    assert not result.ok and not result.committed and not result.rolled_back
    assert result.serving_endpoint.pid == old.pid
    assert events == ["spawn", "drain", "undrain"]
    assert handle.poll() is None
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_receiver_identity_refusal_does_not_shutdown_replacement(case):
    root, old, events, _, snapshot, request, orch = case
    expected = []

    def retire(endpoint):
        expected.append(endpoint)
        raise CutoverError("receiver identity no longer matches")

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, retire=retire)
        )
    )
    assert expected == [old]
    assert not result.ok and not result.retirement_requested
    assert events == ["spawn", "drain"]
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_route_change_during_undrain_returns_unresolved_result(case):
    root, old, events, _, snapshot, request, orch = case
    winner = replace(old, pid=303, generation=30, process_start_time="3030")
    base_client = snapshot.make_client("")

    class Client:
        def drain(self, **kwargs):
            base_client.drain(**kwargs)
            return {"drained": False}

        def undrain(self):
            base_client.undrain()
            routing._atomic_write(root / "active.json", {"active": winner.to_dict()})
            return {}

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, make_client=lambda _: Client())
        )
    )
    assert not result.ok and not result.rolled_back and not result.committed
    assert result.serving_endpoint is None
    assert events == ["spawn", "drain", "undrain"]
    assert routing.read_table(root)["active"] == winner.to_dict()
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_accepted_shutdown_without_exit_remains_unresolved(case):
    root, _, events, _, snapshot, request, orch = case
    confirmations = []

    def confirm(endpoint, timeout):
        confirmations.append((endpoint.pid, timeout))
        return False

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, confirm_retired=confirm)
        )
    )
    assert not result.ok and result.committed
    assert result.retirement_requested and not result.retirement_confirmed
    assert confirmations == [(101, 30.0)]
    assert events == ["spawn", "drain", "shutdown"]
    assert routing.read_table(root)["active"]["pid"] == 202
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))
    assert result.to_dict()["retirement_confirmed"] is False


@pytest.mark.parametrize("state", ["conditional-started", "conditional-flipped", "conditional-draining"])
def test_legacy_recovery_and_reap_refuse_conditional_state(case, state):
    root, old, events, _, _, _, orch = case
    breadcrumb.write_breadcrumb(
        root, state=state, old=old.to_dict(), new_pid=202, new_port=61002
    )
    before = breadcrumb.breadcrumb_path(root).read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("legacy recovery must not probe, undrain or terminate")

    recovery = breadcrumb.recover_stale_cutover(root, forbidden, health_check=forbidden)
    reap = breadcrumb.reap_abandoned_passive(
        root, pid_alive=forbidden, terminate=forbidden, grace_seconds=0
    )
    assert recovery["refused"] and reap["refused"]
    result = orch.run()
    assert not result.ok and "owner-led recovery" in result.error
    assert events == []
    assert breadcrumb.breadcrumb_path(root).read_bytes() == before


def test_legacy_recovery_cannot_race_native_transition(case):
    from zdd.cutover_lock import CutoverLock

    root, old, _, _, _, _, _ = case
    stale = breadcrumb.write_breadcrumb(
        root, state="draining", old=old.to_dict(), new_pid=202
    )
    before = breadcrumb.breadcrumb_path(root).read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("contended native authority must prevent legacy side effects")

    with CutoverLock(root):
        recovery = breadcrumb.recover_stale_cutover(root, forbidden)
        reap = breadcrumb.reap_abandoned_passive(
            root, pid_alive=forbidden, terminate=forbidden, record=stale, grace_seconds=0
        )
    assert recovery["refused"] and reap["refused"]
    assert breadcrumb.breadcrumb_path(root).read_bytes() == before
    breadcrumb.write_breadcrumb(root, state="conditional-draining", old=old.to_dict())
    reap = breadcrumb.reap_abandoned_passive(
        root, pid_alive=forbidden, terminate=forbidden, record=stale, grace_seconds=0
    )
    assert reap["refused"]


@pytest.mark.parametrize("bind", ["0.0.0.0", "::", "[::]"])
def test_free_port_probe_uses_successor_bind_scope(case, monkeypatch, bind):
    import socket

    from zdd.conditional import run_conditional_cutover
    from zdd.cutover import CutoverOrchestrator, CutoverResult

    root, old, _, _, _, request, _ = case
    expected_host = "::" if bind == "[::]" else bind
    observed = []

    class Socket:
        def __init__(self, family, kind):
            assert family == (socket.AF_INET6 if ":" in bind else socket.AF_INET)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def bind(self, address):
            observed.append(address)

        def getsockname(self):
            return (expected_host, 61002)

    def run(self, **kwargs):
        assert self.pick_free_port() == 61002
        return CutoverResult(ok=False)

    monkeypatch.setattr("zdd.conditional.socket.socket", Socket)
    monkeypatch.setattr(CutoverOrchestrator, "run", run)
    run_conditional_cutover(
        root, version="2.0.0", request=replace(request, expected_active=replace(old, bind=bind))
    )
    assert observed == [(expected_host, 0)]


@pytest.mark.parametrize("pid", [None, False, "202", -1])
def test_invalid_candidate_pid_retains_unresolved_recovery(case, pid):
    root, _, events, handle, _, request, orch = case
    handle.pid = pid
    result = orch.run(conditional=request, health_timeout=0)
    assert not result.ok and not result.committed and not result.rolled_back
    assert events == ["spawn"]
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


@pytest.mark.parametrize("reason", [None, "", 1])
def test_malformed_refusal_reason_is_explicit_failure(reason, caplog):
    message = breadcrumb.refusal_reason({"refused": True, "reason": reason})
    assert "outcome contract invalid" in message
    assert message in caplog.text


def test_conditional_spawn_failure_is_redacted_and_preserves_owner_state(case):
    root, old, _, _, snapshot, request, orch = case
    secret = "fixture-sensitive-spawn-detail"

    def spawn(port):
        raise OSError(secret)

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, spawn_passive=spawn)
        )
    )
    assert not result.ok and not result.committed
    assert result.error == "conditional spawn failed (OSError)"
    record = breadcrumb.read_breadcrumb(root)
    assert record["state"] == "conditional-started"
    assert record["error"] == result.error
    assert routing.read_table(root)["active"] == old.to_dict()
    assert secret not in repr(result.to_dict()) + repr(record)


@pytest.mark.parametrize("publication", ["active", "previous", "changed-transition"])
def test_passive_reap_revalidates_fresh_native_owner(case, publication):
    root, old, _, _, _, _, _ = case
    record = breadcrumb.write_breadcrumb(
        root, state="started", old=old.to_dict(), new_pid=202, new_port=61002
    )
    if publication == "changed-transition":
        breadcrumb.write_breadcrumb(root, state="started", new_pid=303, new_port=61003)
    else:
        candidate = replace(old, pid=202, process_start_time="2020", generation=2)
        table = {"active": candidate.to_dict()} if publication == "active" else {
            "active": old.to_dict(), "previous": candidate.to_dict()
        }
        routing._atomic_write(root / "active.json", table)
    before = breadcrumb.breadcrumb_path(root).read_bytes()
    calls = []
    result = breadcrumb.reap_abandoned_passive(
        root, record=record, active_pid=101, grace_seconds=0,
        pid_alive=lambda pid: True, terminate=lambda pid: calls.append(pid) or True,
    )
    assert not result["reaped"] and calls == []
    assert breadcrumb.breadcrumb_path(root).read_bytes() == before

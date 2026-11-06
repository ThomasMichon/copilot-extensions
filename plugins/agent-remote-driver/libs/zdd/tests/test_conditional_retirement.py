from __future__ import annotations

from dataclasses import replace

import pytest

from zdd import breadcrumb, routing
from zdd.cutover import CutoverError

from . import test_conditional_cutover

case = test_conditional_cutover.case


def test_failed_retirement_retains_recovery_and_blocks_next_attempt(case):
    root, _, events, _, snapshot, request, orch = case

    def retire(endpoint, successor):
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

    def retire(endpoint, successor):
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
            return {"drained": False, "clean": False, "forced": False}

        def undrain(self):
            base_client.undrain()
            routing._atomic_write(root / "active.json", {"active": winner.to_dict()})
            return {}

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(
                snapshot, make_client=lambda _: Client(),
                drain_predecessor=lambda old, new, timeout: Client().drain(timeout=timeout),
                undrain_predecessor=lambda old: Client().undrain(),
            )
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


@pytest.mark.parametrize("bind", ["", "0.0.0.0", "::", "[::]"])
def test_free_port_probe_uses_successor_bind_scope(case, monkeypatch, bind):
    import socket

    from zdd.conditional import run_conditional_cutover
    from zdd.cutover import CutoverOrchestrator, CutoverResult

    root, old, _, _, _, request, _ = case
    expected_host = "::" if bind == "[::]" else bind or "0.0.0.0"
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
@pytest.mark.parametrize("published_pid", [202, None, 999])
def test_passive_reap_revalidates_fresh_native_owner(case, publication, published_pid):
    root, old, _, _, _, _, _ = case
    record = breadcrumb.write_breadcrumb(
        root, state="started", old=old.to_dict(), new_pid=202, new_port=61002
    )
    if publication == "changed-transition":
        breadcrumb.write_breadcrumb(root, state="started", new_pid=303, new_port=61003)
    else:
        candidate = replace(
            old, pid=published_pid, port=61002,
            process_start_time="2020", generation=2,
        )
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


def test_recovery_lock_io_failure_is_explicit_refusal(case, monkeypatch):
    root, _, _, _, _, _, _ = case

    def unavailable(*args, **kwargs):
        raise PermissionError("fixture private path")

    monkeypatch.setattr("zdd.cutover_lock.CutoverLock.acquire", unavailable)
    result = breadcrumb.recover_stale_cutover(root, lambda _: pytest.fail("must not control"))
    assert result["refused"] and not result["recovered"]
    assert "PermissionError" in result["reason"]
    assert "fixture private path" not in result["reason"]


@pytest.mark.parametrize("published_pid", [None, 999])
def test_rollback_retains_address_only_published_candidate(case, published_pid):
    root, _, events, _, snapshot, request, orch = case

    def attest(endpoint):
        if endpoint.pid == 202:
            routing._atomic_write(
                root / "active.json",
                {"active": replace(endpoint, pid=published_pid, generation=9).to_dict()},
            )
        return snapshot.attest(endpoint)

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, attest=attest)
        )
    )
    assert not result.ok and not result.rolled_back
    assert events == ["spawn"]
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_route_winner_during_exit_confirmation_cannot_report_success(case):
    root, old, events, _, snapshot, request, orch = case
    winner = replace(old, pid=303, generation=30, process_start_time="3030")

    def confirm(endpoint, timeout):
        routing._atomic_write(root / "active.json", {"active": winner.to_dict()})
        return True

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, confirm_retired=confirm)
        )
    )
    assert not result.ok and result.committed and result.retirement_confirmed
    assert result.serving_endpoint is None
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))
    assert routing.read_table(root)["active"] == winner.to_dict()
    assert events == ["spawn", "drain", "shutdown"]


def test_receiver_bound_drain_refuses_route_changed_after_caller_check(case, monkeypatch):
    import time

    from zdd.conditional import PreparedCutover

    root, old, events, _, snapshot, request, orch = case
    winner = replace(old, pid=303, generation=30, process_start_time="3030")
    verify = PreparedCutover.verify_before_retire
    captured = []

    def change_after_check(self):
        verify(self)
        routing._atomic_write(root / "active.json", {"active": winner.to_dict()})

    def drain(predecessor, successor, timeout):
        captured.append((predecessor, successor, timeout))
        with routing.routing_lock(root, deadline=time.monotonic() + 1):
            active = routing.Endpoint.from_dict(routing.read_table(root)["active"])
            if active != successor:
                raise CutoverError("receiver refuses changed successor ownership")
            pytest.fail("the receiver must not open its drain gate")

    monkeypatch.setattr(PreparedCutover, "verify_before_retire", change_after_check)
    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, drain_predecessor=drain)
        )
    )
    assert not result.ok and not result.committed
    assert captured[0][0] == old
    assert captured[0][1].pid == 202
    assert captured[0][1].generation > old.generation
    assert events == ["spawn"]
    assert routing.read_table(root)["active"] == winner.to_dict()
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


@pytest.mark.parametrize("phase", ["client", "adopt", "response"])
def test_relay_adoption_never_serializes_captured_secrets(case, caplog, phase):
    _, _, _, _, snapshot, request, orch = case
    secret = "fixture-sensitive-relay-error"

    class Client:
        def adopt_relay(self):
            if phase == "adopt":
                raise OSError(secret)
            return {"adopted": secret}

    def client(url):
        if phase == "client":
            raise OSError(secret)
        return Client()

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(
                snapshot, make_client=client,
                adopt_successor=lambda endpoint: client(endpoint.base_url).adopt_relay(),
            )
        )
    )
    assert secret not in repr(result.to_dict())
    assert secret not in caplog.text
    if phase != "client":
        assert any("relay adopt failed" in step for step in result.steps)


def test_receiver_bound_retirement_refuses_repromoted_predecessor(case):
    import time

    root, old, events, _, snapshot, request, orch = case
    captured = []

    def retire(predecessor, successor):
        captured.append((predecessor, successor))
        routing._atomic_write(
            root / "active.json",
            {"active": replace(old, generation=30).to_dict()},
        )
        with routing.routing_lock(root, deadline=time.monotonic() + 1):
            active = routing.Endpoint.from_dict(routing.read_table(root)["active"])
            if active != successor:
                raise CutoverError("receiver refuses changed successor ownership")
            pytest.fail("receiver must not shut down the repromoted predecessor")

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, retire=retire)
        )
    )
    assert captured[0][0] == old and captured[0][1].pid == 202
    assert not result.ok and result.committed and not result.retirement_requested
    assert result.serving_endpoint is None
    assert events == ["spawn", "drain"]
    assert routing.read_table(root)["active"]["pid"] == old.pid
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_identity_bound_candidate_disposal_refusal_retains_recovery(case, monkeypatch):
    root, _, events, _, snapshot, request, orch = case

    def attest(endpoint):
        response = snapshot.attest(endpoint)
        return replace(response, ready=False) if endpoint.pid == 202 else response

    monkeypatch.setattr(
        "zdd.diagnostics.terminate_pid_if_identity",
        lambda *_args, **_kwargs: {
            "killed": False, "identity_verified": False, "exit_confirmed": False,
        },
    )
    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, attest=attest)
        ),
        health_timeout=0,
    )
    assert not result.ok and not result.rolled_back
    assert events == ["spawn"]
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


@pytest.mark.parametrize("phase", ["capture", "attest"])
def test_preparation_callback_errors_are_redacted_failed_results(case, caplog, phase):
    root, old, events, _, snapshot, request, orch = case
    secret = "fixture-sensitive-preparation-error"

    def fail(*args):
        raise OSError(secret)

    capture = fail if phase == "capture" else lambda _: replace(snapshot, attest=fail)
    result = orch.run(conditional=replace(request, capture_runtime=capture))
    assert not result.ok and not result.committed
    assert result.error == f"captured runtime {phase} failed (OSError)"
    assert events == []
    assert routing.read_table(root)["active"] == old.to_dict()
    assert breadcrumb.read_breadcrumb(root) is None
    assert secret not in repr(result.to_dict()) + caplog.text


def test_drain_cutover_errors_cannot_leak_captured_credentials(case, caplog):
    root, _, _, _, snapshot, request, orch = case
    secret = "fixture-sensitive-drain-error"

    def fail(*args):
        raise CutoverError(secret)

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, drain_predecessor=fail)
        )
    )
    assert not result.ok and not result.committed
    assert result.error == "captured runtime drain failed (CutoverError)"
    assert secret not in repr(result.to_dict()) + caplog.text
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_conditional_drain_sanitizes_response_and_reports_only_busy_count(case):
    _, _, _, _, snapshot, request, orch = case
    secret = "fixture-sensitive-response-value"
    response = {
        "drained": False, "clean": False, "forced": False,
        "busy_sessions": [secret], "credential": secret,
    }
    result = orch.run(
        conditional=replace(
            request,
            capture_runtime=lambda _: replace(snapshot, drain_predecessor=lambda *_: response),
        )
    )
    assert not result.ok
    assert result.drain == {
        "drained": False, "clean": False, "forced": False, "busy_sessions_count": 1,
    }
    assert secret not in repr(result.to_dict())


@pytest.mark.parametrize("invalid", [
    {"forced": True}, {"clean": None}, {"clean": False}, {"drained": 1},
    {"mode": "incompatible-contract-cold"},
])
def test_conditional_drain_requires_unforced_boolean_evidence(case, invalid):
    root, _, events, _, snapshot, request, orch = case
    response = {"drained": True, "clean": True, "forced": False, **invalid}
    result = orch.run(
        conditional=replace(
            request,
            capture_runtime=lambda _: replace(snapshot, drain_predecessor=lambda *_: response),
        )
    )
    assert not result.ok and not result.committed
    assert "shutdown" not in events
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_receiver_bound_undrain_refuses_a_later_route_owner(case):
    import time

    root, old, events, _, snapshot, request, orch = case
    winner = replace(old, pid=303, generation=30, process_start_time="3030")
    captured = []

    def attest(endpoint):
        response = snapshot.attest(endpoint)
        return replace(response, ready=False) if endpoint.pid == 202 and "drain" in events else response

    def undrain(restored):
        captured.append(restored)
        routing._atomic_write(root / "active.json", {"active": winner.to_dict()})
        with routing.routing_lock(root, deadline=time.monotonic() + 1):
            active = routing.Endpoint.from_dict(routing.read_table(root)["active"])
            if active != restored:
                raise CutoverError("receiver refuses lost restoration ownership")
            pytest.fail("receiver must not reopen the replaced predecessor")

    result = orch.run(
        conditional=replace(
            request,
            capture_runtime=lambda _: replace(
                snapshot, attest=attest, undrain_predecessor=undrain,
            ),
        )
    )
    assert captured[0].pid == old.pid and captured[0].generation > old.generation
    assert not result.ok and not result.rolled_back
    assert events == ["spawn", "drain"]
    assert routing.read_table(root)["active"] == winner.to_dict()
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_receiver_bound_adoption_refuses_a_later_route_owner(case):
    import time

    root, old, events, _, snapshot, request, orch = case
    winner = replace(old, pid=303, generation=30, process_start_time="3030")
    captured = []

    def adopt(successor):
        captured.append(successor)
        routing._atomic_write(root / "active.json", {"active": winner.to_dict()})
        with routing.routing_lock(root, deadline=time.monotonic() + 1):
            active = routing.Endpoint.from_dict(routing.read_table(root)["active"])
            if active != successor:
                raise CutoverError("receiver refuses changed adoption ownership")
            pytest.fail("receiver must not adopt relay for a replaced successor")

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, adopt_successor=adopt),
        )
    )
    assert captured[0].pid == 202
    assert not result.ok and result.committed
    assert events == ["spawn", "drain", "shutdown"]
    assert result.serving_endpoint is None
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_rollback_rechecks_predecessor_identity_under_publication_lock(case, monkeypatch):
    root, _, events, _, snapshot, request, orch = case
    publish = routing.publish_active_with_previous_guarded

    def attest(endpoint):
        response = snapshot.attest(endpoint)
        return replace(response, ready=False) if endpoint.pid == 202 and "drain" in events else response

    def change_before_restore(*args, **kwargs):
        expected = kwargs.get("expected_active")
        if expected is not None and expected.pid == 202:
            monkeypatch.setattr(
                "zdd.diagnostics.process_start_time",
                lambda pid: "reused" if pid == 101 else str(pid * 10),
            )
        return publish(*args, **kwargs)

    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", change_before_restore)
    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, attest=attest)
        )
    )
    assert not result.ok and not result.rolled_back
    assert events == ["spawn", "drain"]
    assert routing.read_table(root)["active"]["pid"] == 202
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_rollback_restoration_io_failure_retains_recovery(case, monkeypatch):
    root, _, events, _, snapshot, request, orch = case
    publish = routing.publish_active_with_previous_guarded

    def attest(endpoint):
        response = snapshot.attest(endpoint)
        return replace(response, ready=False) if endpoint.pid == 202 and "drain" in events else response

    def fail_restore(*args, **kwargs):
        expected = kwargs.get("expected_active")
        if expected is not None and expected.pid == 202:
            raise PermissionError("fixture routing write unavailable")
        return publish(*args, **kwargs)

    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", fail_restore)
    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, attest=attest)
        )
    )
    assert not result.ok and not result.rolled_back
    assert events == ["spawn", "drain"]
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))


def test_corrupt_routing_during_candidate_health_retains_candidate(case):
    root, _, events, _, snapshot, request, orch = case

    def attest(endpoint):
        response = snapshot.attest(endpoint)
        if endpoint.pid == 202:
            (root / "active.json").write_text("{")
            return replace(response, ready=False)
        return response

    result = orch.run(
        conditional=replace(
            request, capture_runtime=lambda _: replace(snapshot, attest=attest)
        ),
        health_timeout=0,
    )
    assert not result.ok and not result.rolled_back
    assert events == ["spawn"]
    assert (root / "active.json").read_text() == "{"
    assert breadcrumb.is_stale(breadcrumb.read_breadcrumb(root))

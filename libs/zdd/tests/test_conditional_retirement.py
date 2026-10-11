from __future__ import annotations

from dataclasses import replace

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

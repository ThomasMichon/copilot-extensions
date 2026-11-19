"""Original subscriptions stay owned after native retirement and uncertain cancellation."""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent_dispatch.queue_records import TaskError
from agent_dispatch.repository_feed_registration import RepositoryFeedRegistrar
from tests import test_repository_feed_preparation as preparation
from tests import test_repository_feed_registration as registration
from tests._helpers import RepoDefaultingQueue as TaskQueue

pytestmark = pytest.mark.contract("agent_dispatch.repository_feed.cancellation")


def retired(
    tmp_path: Path,
    *,
    register: bool = True,
) -> tuple[TaskQueue, str, dict[str, Any], registration.Client, RepositoryFeedRegistrar]:
    queue, task_id, spec, client, registrar = registration.prepared(tmp_path)
    if register:
        registration.reconcile(registrar, task_id, spec)
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None
    result = queue.expire_repository_feed_waiter(waiter, now=spec["feed"]["deadline_at"])
    assert result is not None
    return queue, task_id, spec, client, registrar


def cancel(
    registrar: RepositoryFeedRegistrar,
    task_id: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    return asyncio.run(registrar.cancel_registration(task_id, spec["preparation_id"]))


def receipt(client: registration.Client, spec: dict[str, Any]) -> dict[str, Any]:
    return client.subscriptions.execute(
        client.identity.key,
        client.binding["consumer"],
        "inspect-request",
        {"request_id": spec["feed"]["request_id"]},
        expires_at=int(client.now) + 60,
    )


def selected(
    queue: TaskQueue,
    client: registration.Client,
    binding: dict[str, Any],
    *,
    checkpoint: Callable[[], None] = lambda: None,
) -> RepositoryFeedRegistrar:
    return RepositoryFeedRegistrar(
        queue,
        binding=binding,
        client=client,
        lane="harness",
        repository_target=preparation.TARGET,
        land="author",
        checkpoint=checkpoint,
    )


def test_actual_retirement_cancels_only_original_receipt_without_ack_or_settlement(
    tmp_path: Path,
) -> None:
    queue, task_id, spec, client, registrar = retired(tmp_path)
    native = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    task, wakes = queue.get(task_id), queue.list_run_waiter_wakes(task_id)
    before = receipt(client, spec)["subscription"]
    other = client.subscriptions.execute(
        client.identity.key,
        client.binding["consumer"],
        "subscribe",
        {"request_id": "another-request", "after": 0, "ttl": 30},
        expires_at=int(client.now) + 60,
    )["subscription"]
    client.after_receipt = lambda result: result.update(future="not returned")
    result = cancel(registrar, task_id, spec)
    assert result == {"status": "cancelled", "registration_id": before["registration_id"]}
    after = receipt(client, spec)["subscription"]
    assert after == {**before, "cancelled": True}
    assert not client.subscriptions.execute(
        client.identity.key,
        client.binding["consumer"],
        "inspect",
        {"registration_id": other["registration_id"]},
        expires_at=int(client.now) + 60,
    )["subscription"]["cancelled"]
    assert queue.find_delegated_preparation(task_id, spec["preparation_id"]) == native
    assert native is not None and not native["replay_settled"]
    assert queue.get(task_id) == task and queue.list_run_waiter_wakes(task_id) == wakes
    assert "events" not in client.calls and "acknowledge" not in client.calls
    assert client.deadline is None and not registrar._running
    original = client.store.path.read_bytes()
    assert cancel(registrar, task_id, spec) == result
    assert client.store.path.read_bytes() == original
    assert registration.reconcile(registrar, task_id, spec)["status"] == "cancelled"
    assert client.calls.count("subscribe") == 1


@pytest.mark.parametrize("during", [False, True])
def test_prior_or_racing_native_settlement_does_not_repeat_service_cancellation(
    tmp_path: Path,
    during: bool,
) -> None:
    queue, task_id, spec, client, registrar = retired(tmp_path)
    original = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert original is not None

    def settled_elsewhere() -> None:
        client.subscriptions.execute(
            client.identity.key,
            client.binding["consumer"],
            "cancel",
            {"registration_id": original["replay_registration_id"]},
            expires_at=int(client.now) + 60,
        )
        queue.replay_waiter_progress(original, settled=True)

    if during:
        client.during = settled_elsewhere
    else:
        settled_elsewhere()
    before = list(client.calls)
    assert cancel(registrar, task_id, spec) == {"status": "settled"}
    assert client.calls == before + (["capabilities"] if during else [])


def test_live_or_unknown_registration_refuses_before_service_io(tmp_path: Path) -> None:
    queue, task_id, spec, client, registrar = registration.prepared(tmp_path)
    before = list(client.calls)
    with pytest.raises(TaskError, match="retired"):
        cancel(registrar, task_id, spec)
    assert client.calls == before
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None
    queue.expire_repository_feed_waiter(waiter, now=spec["feed"]["deadline_at"])
    with pytest.raises(TaskError, match="inspect its request"):
        cancel(registrar, task_id, spec)
    assert client.calls == before


@pytest.mark.parametrize("committed", [False, True])
def test_uncertain_registration_must_be_inspected_before_cancellation(
    tmp_path: Path,
    committed: bool,
) -> None:
    queue, task_id, spec, client, registrar = registration.prepared(tmp_path)
    client.lost_reply, client.before_commit = committed, not committed
    with pytest.raises(TimeoutError):
        registration.reconcile(registrar, task_id, spec)
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None
    queue.expire_repository_feed_waiter(waiter, now=spec["feed"]["deadline_at"])
    before = list(client.calls)
    with pytest.raises(TaskError, match="inspect its request"):
        cancel(registrar, task_id, spec)
    assert client.calls == before
    recovered = registration.reconcile(registrar, task_id, spec)
    if committed:
        assert cancel(registrar, task_id, spec)["status"] == "cancelled"
        assert (
            receipt(client, spec)["subscription"]["registration_id"]
            == recovered["registration_id"]
        )
    else:
        assert recovered["status"] == "unresolved"
        assert "cancel" not in client.calls
    assert client.calls.count("subscribe") == 1


def test_lost_committed_cancel_reply_recovers_original_identity_after_reopen(
    tmp_path: Path,
) -> None:
    queue, task_id, spec, client, registrar = retired(tmp_path)
    client.lost_reply = True
    with pytest.raises(TimeoutError):
        cancel(registrar, task_id, spec)
    native = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert native is not None and not native["replay_settled"]
    registrar = selected(TaskQueue(tmp_path / "tasks.db"), client, registrar.binding)
    result = registration.reconcile(registrar, task_id, spec)
    assert result["status"] == "cancelled"
    assert result["registration_id"] == native["replay_registration_id"]
    assert client.calls.count("subscribe") == client.calls.count("cancel") == 1
    assert client.calls[-1] == "inspect-request"
    retained = registrar.queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert retained is not None and not retained["replay_settled"]


def test_cancel_preserves_real_delivered_unacknowledged_event(tmp_path: Path) -> None:
    queue, task_id, spec, client, registrar = retired(tmp_path)
    key = client.identity.key
    client.subscriptions.projection.apply_facts(
        {key: {"numbers": [7], "outcome": "complete", "error": "", "pages": 1}},
        {(key, client.identity.repo, 7): None},
        expires_at=int(client.now) + 60,
    )
    original = receipt(client, spec)["subscription"]
    payload = {"registration_id": original["registration_id"], "limit": 1}
    delivered = client.subscriptions.execute(
        key,
        client.binding["consumer"],
        "events",
        payload,
        expires_at=int(client.now) + 60,
    )
    assert delivered["events"]
    before = receipt(client, spec)["subscription"]
    assert before["delivered"] > before["acknowledged"]
    wakes = queue.list_run_waiter_wakes(task_id)
    cancel(registrar, task_id, spec)
    assert receipt(client, spec)["subscription"] == {**before, "cancelled": True}
    replay = client.subscriptions.execute(
        key,
        client.binding["consumer"],
        "events",
        payload,
        expires_at=int(client.now) + 60,
    )
    assert replay["events"] == delivered["events"]
    assert queue.list_run_waiter_wakes(task_id) == wakes
    native = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert native is not None and not native["replay_settled"]


@pytest.mark.parametrize("field", ["consumer", "reviewer", "namespace"])
def test_foreign_binding_cannot_cancel_retained_registration(tmp_path: Path, field: str) -> None:
    queue, task_id, spec, client, registrar = retired(tmp_path)
    binding = copy.deepcopy(registrar.binding)
    if field == "namespace":
        binding["identity"]["namespace"] += "-foreign"
    else:
        binding[field] += "-foreign"
    foreign = selected(queue, client, binding)
    before = list(client.calls)
    with pytest.raises(TaskError, match="preparation unavailable"):
        cancel(foreign, task_id, spec)
    assert client.calls == before and not receipt(client, spec)["subscription"]["cancelled"]


@pytest.mark.parametrize("feature", ["native", "service"])
def test_cancellation_requires_running_recovery_support(tmp_path: Path, feature: str) -> None:
    queue, task_id, spec, client, registrar = retired(tmp_path)
    if feature == "native":
        queue.repository_feed_receipt_protocol = "unsupported"
    else:
        client.capabilities.pop("request_recovery")
    before = list(client.calls)
    with pytest.raises((ValueError, TaskError), match="unsupported"):
        cancel(registrar, task_id, spec)
    assert "cancel" not in client.calls
    if feature == "native":
        assert client.calls == before
    assert not receipt(client, spec)["subscription"]["cancelled"]


def test_drain_after_negotiation_prevents_transmission(tmp_path: Path) -> None:
    queue, task_id, spec, client, original = retired(tmp_path)
    draining = False

    def checkpoint() -> None:
        if draining:
            raise TaskError("owner draining")

    def drain() -> None:
        nonlocal draining
        draining = True

    registrar = selected(queue, client, original.binding, checkpoint=checkpoint)
    client.during = drain
    with pytest.raises(TaskError, match="owner draining"):
        cancel(registrar, task_id, spec)
    assert "cancel" not in client.calls
    assert not receipt(client, spec)["subscription"]["cancelled"]
    assert client.deadline is None and not registrar._running


@pytest.mark.parametrize("operation,read", [("cancel", 1), ("cancel", 2), ("inspect", 1)])
def test_native_read_cannot_cross_owner_closure_into_service_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    read: int,
) -> None:
    queue, task_id, spec, client, original = retired(tmp_path)
    closed, reads = False, 0
    progress = queue.replay_waiter_progress

    def checkpoint() -> None:
        if closed:
            raise TaskError("owner closed during native read")

    def read_progress(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal closed, reads
        result = progress(*args, **kwargs)
        reads += 1
        closed = reads >= read
        return result

    registrar = selected(queue, client, original.binding, checkpoint=checkpoint)
    monkeypatch.setattr(queue, "replay_waiter_progress", read_progress)
    before = list(client.calls)
    with pytest.raises(TaskError, match="owner closed during native read"):
        if operation == "cancel":
            cancel(registrar, task_id, spec)
        else:
            registration.reconcile(registrar, task_id, spec)
    assert client.calls == before + (["capabilities"] if read == 2 else [])
    assert not receipt(client, spec)["subscription"]["cancelled"]
    assert client.deadline is None and not registrar._running


@pytest.mark.parametrize("operation", ["inspect", "cancel"])
@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), -1.0])
def test_invalid_or_elapsed_caller_deadline_refuses_before_service_io(
    tmp_path: Path,
    operation: str,
    deadline: float,
) -> None:
    _, task_id, spec, client, registrar = retired(tmp_path)
    client.deadline = deadline
    before = list(client.calls)
    with pytest.raises((ValueError, TaskError), match="IO deadline"):
        if operation == "inspect":
            registration.reconcile(registrar, task_id, spec)
        else:
            cancel(registrar, task_id, spec)
    assert client.calls == before and not registrar._running


def test_cancel_restores_caller_deadline_and_retains_unknown_cancelled_coroutine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task_id, spec, client, registrar = retired(tmp_path)
    original_request = client.request
    prior = time.monotonic() + 10
    client.deadline = prior

    async def scenario() -> None:
        committed = asyncio.Event()

        async def request(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
            assert client.deadline is not None and client.deadline <= prior
            result = await original_request(operation, payload)
            if operation == "cancel":
                committed.set()
                await asyncio.Event().wait()
            return result

        monkeypatch.setattr(client, "request", request)
        pending = asyncio.create_task(
            registrar.cancel_registration(task_id, spec["preparation_id"])
        )
        await asyncio.wait_for(committed.wait(), 2)
        with pytest.raises(TaskError, match="already in flight"):
            await registrar.cancel_registration(task_id, spec["preparation_id"])
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(scenario())
    assert client.deadline == prior and not registrar._running
    monkeypatch.setattr(client, "request", original_request)
    assert registration.reconcile(registrar, task_id, spec)["status"] == "cancelled"
    native = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert native is not None and not native["replay_settled"]


@pytest.mark.parametrize("field", ["status", "registration_id"])
def test_invalid_cancel_response_keeps_original_unsettled_custody(
    tmp_path: Path, field: str
) -> None:
    queue, task_id, spec, client, registrar = retired(tmp_path)
    before = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    client.after_receipt = lambda result: result.update({field: "foreign"})
    with pytest.raises(ValueError, match="cancellation receipt differs"):
        cancel(registrar, task_id, spec)
    assert queue.find_delegated_preparation(task_id, spec["preparation_id"]) == before
    client.after_receipt = lambda _result: None
    assert registration.reconcile(registrar, task_id, spec)["status"] == "cancelled"


def test_missing_historical_admission_and_expiry_do_not_strand_known_cancellation(
    tmp_path: Path,
) -> None:
    queue, task_id, spec, _client, registrar = retired(tmp_path)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE run_waiters SET feed_admission_json=NULL, replay_registration_expires_at=NULL "
            "WHERE task_id=?",
            (task_id,),
        )
    assert cancel(registrar, task_id, spec)["status"] == "cancelled"
    native = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert native is not None and native["feed_admission"] is None
    assert native["replay_registration_expires_at"] is None and not native["replay_settled"]

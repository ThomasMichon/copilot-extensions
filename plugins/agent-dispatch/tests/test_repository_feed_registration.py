"""Actual native waiters and repository receipts, without a consumer or task wake."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import copy
import importlib
from pathlib import Path
import sys
import time
from typing import Any

import pytest

from agent_dispatch.queue_records import TaskError
from agent_dispatch.repository_feed_registration import RepositoryFeedRegistrar
from agent_pull_requests.repository_contract import RepositoryIdentity
from agent_pull_requests.repository_projection import RepositoryProjection
from agent_pull_requests.repository_subscriptions import RepositorySubscriptions, contract
from agent_pull_requests.watch_replay_store import ReplayStore
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests import test_repository_feed_preparation as preparation

pytestmark = pytest.mark.contract("agent_dispatch.repository_feed.registration")


class Client(preparation.Client):
    def __init__(self, binding: dict[str, Any], root: Path) -> None:
        super().__init__(binding)
        self.capabilities.update(contract(reviewer_actions=True))
        self.now = time.time()
        self.store = ReplayStore(
            root, binding["identity"]["namespace"], clock=lambda: self.now,
            repository_cache=True, repository_continuing=True, repository_activity=True,
        )
        self.store.provision()
        self.identity = RepositoryIdentity(**binding["identity"])
        self.subscriptions = RepositorySubscriptions(RepositoryProjection(self.store, (self.identity,)))
        self.lost_reply = False
        self.before_commit = False
        self.after_receipt: Callable[[dict[str, Any]], None] = lambda _result: None
        self.payloads: list[dict[str, Any]] = []

    async def request(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation == "next-actions":
            return await super().request(operation, payload)
        self.calls.append(operation)
        self.payloads.append(copy.deepcopy(payload))
        if self.before_commit:
            self.before_commit = False
            raise TimeoutError("request outcome unknown")
        result = self.subscriptions.execute(
            self.identity.key, self.binding["consumer"], operation, payload,
            expires_at=int(self.now) + 60,
        )
        if self.lost_reply:
            self.lost_reply = False
            raise TimeoutError("committed reply lost")
        self.after_receipt(result)
        return result


def setup(
    tmp_path: Path,
) -> tuple[TaskQueue, str, dict[str, Any], Client, RepositoryFeedRegistrar]:
    queue, task_id, spec, _, _ = preparation.setup(tmp_path)
    spec["feed"]["baseline"] = {"event_cursor": 0, "action_key": None}
    client = Client(spec["feed"]["binding"], tmp_path / "cache")
    registrar = RepositoryFeedRegistrar(
        queue, binding=spec["feed"]["binding"], client=client, lane="harness",
        repository_target=preparation.TARGET, land="author", checkpoint=lambda: None,
    )
    return queue, task_id, spec, client, registrar


def prepared(
    tmp_path: Path,
) -> tuple[TaskQueue, str, dict[str, Any], Client, RepositoryFeedRegistrar]:
    selected = setup(tmp_path)
    asyncio.run(preparation.prepare(selected[-1], selected[1], selected[2]))
    return selected


def reconcile(registrar: RepositoryFeedRegistrar, task_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(registrar.reconcile_registration(task_id, spec["preparation_id"]))


def test_real_receipt_reopens_without_new_subscription_delivery_or_wake(tmp_path: Path) -> None:
    queue, task_id, spec, client, registrar = prepared(tmp_path)
    first = reconcile(registrar, task_id, spec)
    assert first["status"] == first["waiter_state"] == "active"
    assert client.payloads == [{
        "request_id": spec["feed"]["request_id"], "ttl": spec["timeout"],
        "after": 0, "not_after": int(spec["feed"]["deadline_at"]),
    }]
    before = client.store.path.read_bytes()
    queue = TaskQueue(tmp_path / "tasks.db")
    registrar.queue = queue
    again = reconcile(registrar, task_id, spec)
    assert again == first and client.calls[-1] == "inspect-request"
    assert client.store.path.read_bytes() == before and not queue.list_run_waiter_wakes(task_id)
    receipt = client.subscriptions.execute(
        client.identity.key, client.binding["consumer"], "inspect-request",
        {"request_id": spec["feed"]["request_id"]}, expires_at=int(client.now) + 60,
    )["subscription"]
    assert receipt["expires_at"] == int(spec["feed"]["deadline_at"])
    assert receipt["acknowledged"] == receipt["delivered"] == 0
    assert client.deadline is None


@pytest.mark.parametrize("committed", [False, True])
def test_lost_reply_uses_original_inspection_without_creation_replay(
    tmp_path: Path, committed: bool,
) -> None:
    queue, task_id, spec, client, registrar = prepared(tmp_path)
    client.lost_reply, client.before_commit = committed, not committed
    with pytest.raises(TimeoutError):
        reconcile(registrar, task_id, spec)
    registrar.queue = TaskQueue(tmp_path / "tasks.db")
    result = reconcile(registrar, task_id, spec)
    assert result["status"] == ("active" if committed else "unresolved")
    assert client.calls.count("subscribe") == 1 and client.calls[-1] == "inspect-request"
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None and waiter["replay_attempted"] and not waiter["replay_settled"]
    assert not queue.list_run_waiter_wakes(task_id)


@pytest.mark.parametrize("feature", ["service", "native"])
def test_unsupported_recovery_refuses_before_suspension(tmp_path: Path, feature: str) -> None:
    queue, task_id, spec, client, registrar = setup(tmp_path)
    if feature == "service":
        client.capabilities.pop("request_recovery")
    else:
        queue.repository_feed_attempt_protocol = "unsupported"
    with pytest.raises((ValueError, TaskError), match="unsupported"):
        asyncio.run(preparation.prepare(registrar, task_id, spec))
    task = queue.get(task_id)
    assert task is not None and task.status == "started"
    assert queue.find_delegated_preparation(task_id, spec["preparation_id"]) is None
    assert "subscribe" not in client.calls and not queue.list_run_waiter_wakes(task_id)


def test_changed_task_retains_late_receipt_but_cannot_arm(tmp_path: Path) -> None:
    queue, task_id, spec, client, registrar = prepared(tmp_path)

    def hold(_result: dict[str, Any]) -> None:
        with queue._connect() as conn:
            conn.execute("UPDATE tasks SET hold_reason='operator hold' WHERE id=?", (task_id,))

    client.after_receipt = hold
    with pytest.raises(TaskError):
        reconcile(registrar, task_id, spec)
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None and waiter["replay_registration_id"] and waiter["state"] == "preparing"
    assert not waiter["replay_settled"] and not queue.list_run_waiter_wakes(task_id)


@pytest.mark.parametrize("field,value", [
    ("request_id", "foreign"), ("consumer", "foreign"), ("target", "foreign"),
    ("request", {"after": 0, "ttl": 1, "not_after": 1}), ("expires_at", True),
])
def test_foreign_or_changed_receipt_stays_unresolved(
    tmp_path: Path, field: str, value: object,
) -> None:
    queue, task_id, spec, client, registrar = prepared(tmp_path)
    client.after_receipt = lambda result: result["subscription"].update({field: value})
    with pytest.raises(ValueError, match="receipt differs"):
        reconcile(registrar, task_id, spec)
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None and waiter["replay_attempted"]
    assert waiter["replay_registration_id"] is None and waiter["state"] == "preparing"
    assert not queue.list_run_waiter_wakes(task_id)


def test_absence_after_original_cutoff_does_not_fabricate_native_completion(tmp_path: Path) -> None:
    queue, task_id, spec, client, registrar = prepared(tmp_path)
    client.before_commit = True
    with pytest.raises(TimeoutError):
        reconcile(registrar, task_id, spec)
    client.now = spec["feed"]["deadline_at"] + 1
    result = reconcile(registrar, task_id, spec)
    assert result == {"status": "unresolved", "provider_now": int(client.now)}
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None and not waiter["replay_settled"] and waiter["state"] == "preparing"
    assert client.calls.count("subscribe") == 1 and not queue.list_run_waiter_wakes(task_id)


@pytest.mark.parametrize("reason", ["baseline", "cutoff"])
def test_unqualified_original_intent_never_records_a_transmission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str,
) -> None:
    queue, task_id, spec, client, registrar = setup(tmp_path)
    if reason == "baseline":
        spec["feed"]["baseline"] = None
    asyncio.run(preparation.prepare(registrar, task_id, spec))
    if reason == "cutoff":
        monkeypatch.setattr("time.time", lambda: int(spec["feed"]["deadline_at"]))
    with pytest.raises((ValueError, TaskError), match="baseline|cutoff"):
        reconcile(registrar, task_id, spec)
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None and not waiter["replay_attempted"] and waiter["state"] == "preparing"
    assert "subscribe" not in client.calls and not queue.list_run_waiter_wakes(task_id)


@pytest.mark.parametrize("status", ["cancelled", "expired"])
def test_cancelled_or_expired_recovered_receipt_cannot_arm_or_complete(
    tmp_path: Path, status: str,
) -> None:
    queue, task_id, spec, client, registrar = prepared(tmp_path)
    client.lost_reply = True
    with pytest.raises(TimeoutError):
        reconcile(registrar, task_id, spec)
    if status == "cancelled":
        found = client.subscriptions.execute(
            client.identity.key, client.binding["consumer"], "inspect-request",
            {"request_id": spec["feed"]["request_id"]}, expires_at=int(client.now) + 60,
        )["subscription"]
        client.subscriptions.execute(
            client.identity.key, client.binding["consumer"], "cancel",
            {"registration_id": found["registration_id"]}, expires_at=int(client.now) + 60,
        )
    else:
        client.now = spec["feed"]["deadline_at"] + 1
    result = reconcile(registrar, task_id, spec)
    assert result["status"] == status and result["waiter_state"] == "preparing"
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None and waiter["replay_registration_id"] and not waiter["replay_settled"]
    assert not queue.list_run_waiter_wakes(task_id)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux private Unix registration")
def test_actual_async_unix_registration_and_original_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "services/pr/src"))
    monkeypatch.syspath_prepend(str(root / "services/pr/tests"))
    host_fixture = importlib.import_module("test_bound_local_client")
    from agent_dispatch.repository_feed_preparation import create_repository_feed_preparer

    async def scenario() -> None:
        async with host_fixture.running(tmp_path) as host:
            host.store.clock = time.time
            target = host.identity.endpoint + "/" + host.identity.repo
            queue, task_id, spec, _, _ = preparation.setup(tmp_path, target=target)
            spec["repo"] = host.identity.repo
            spec["feed"]["baseline"] = {"event_cursor": 0, "action_key": None}
            spec["feed"]["binding"].update(
                socket=str(host.path), target="repository", consumer="consumer",
                identity=host.identity.document(),
            )
            selected = create_repository_feed_preparer(
                queue, binding=spec["feed"]["binding"], lane="harness",
                repository_target=target, land="author", checkpoint=lambda: None,
            )
            registrar = RepositoryFeedRegistrar(
                queue, binding=selected.binding, client=selected.client, lane="harness",
                repository_target=target, land="author", checkpoint=lambda: None,
            )
            await preparation.prepare(registrar, task_id, spec)
            first = await registrar.reconcile_registration(task_id, spec["preparation_id"])
            before = host.store.path.read_bytes()
            again = await registrar.reconcile_registration(task_id, spec["preparation_id"])
            assert first == again and first["waiter_state"] == "active"
            assert [call["operation"] for call in host.calls] == [
                "capabilities", "next-actions", "capabilities", "capabilities", "subscribe",
                "capabilities", "capabilities", "inspect-request",
            ]
            assert all(call["body"].get("expected_consumer") == "consumer"
                       for call in host.calls if call["operation"] != "capabilities")
            assert host.store.path.read_bytes() == before and not queue.list_run_waiter_wakes(task_id)

    asyncio.run(scenario())

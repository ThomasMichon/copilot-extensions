"""Ordinary allocation evidence belongs to the placed reservation, not an attachment."""

import json
import sqlite3
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from agent_dispatch import embody, ordinary_bridge
from agent_dispatch.client import DispatchError
from tests._hosting_owner import admission_owner as admission_owner
from tests.test_ordinary_pool_startup import api as api
from tests.test_ordinary_pool_startup import launch
from tests.test_ordinary_pool_startup import ordinary as ordinary
from tests.test_ordinary_pool_startup import q as q


def original_owner_nonce(o: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    create = embody.create_worktree

    def owner(**kwargs: Any) -> dict[str, Any]:
        return {**create(**kwargs), "creation_nonce": o.ground["creation_nonce"]}

    monkeypatch.setattr(embody, "create_worktree", owner)


def test_original_pool_allocation_reaches_both_storage_transactions(
    ordinary: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary
    original_owner_nonce(o, monkeypatch)
    assert launch(o)["native_session_id"] == "native-a"
    [prepare] = [r for r in o.requests if r.url.path.endswith("/worktree")]
    publish = next(r for r in o.requests if r.url.path.endswith("/created-agent"))
    body = json.loads(prepare.content)
    assert (
        body["pool_creation_nonce"] == o.ground["creation_nonce"] and "creation_nonce" not in body
    )
    assert o.requests.index(prepare) < o.requests.index(publish)
    reopened = type(o.q)(o.q.db_path)
    row = reopened.get_reservation(o.placed["assignment"]["reservation_key"])
    owner = json.loads(publish.content)["worktree_record"]
    assert (row.task_id, row.key, row.attempt, row.reserved_by, row.driver) == tuple(
        owner["dispatch_attempt"][k]
        for k in ("task_id", "reservation_key", "attempt", "supervisor", "driver")
    )
    assert row.allocation_cohort["pool"] == o.placed["assignment"]
    assert (row.worktree, row.allocation_project, row.worktree_path, row.creation_nonce) == tuple(
        owner[k] for k in ("id", "repo", "path", "creation_nonce")
    )
    assert row.agent_info["connection"]["native_session_id"] == "native-a"
    assert not row.task_relationship_required and row.task_relationship_event_id is None
    assert reopened.task_relationship(o.task["id"]) is None
    before = reopened.get(o.task["id"])
    assert before.status == "claimed" and before.owner is before.owner_session_id is None
    o.client.start_pool_task(
        before.id,
        "host-a/worktree-a",
        owner_session_id="native-a",
        expected_generation=o.placed["assignment"]["task_generation"],
    )
    started = reopened.get(before.id)
    assert (started.status, started.owner, started.owner_session_id) == (
        "started",
        "host-a/worktree-a",
        "native-a",
    )
    counts = reopened.reconcile_liveness(resolver=lambda *_owner: "gone")
    suspended = reopened.get(before.id)
    assert counts["suspended"] == 1 and counts["requeued"] == 0
    assert (suspended.status, suspended.owner, suspended.owner_session_id) == (
        "suspended", started.owner, started.owner_session_id,
    )
    assert reopened.get_reservation(row.key) == row


@pytest.mark.parametrize("changed", ["reservation", "placement"])
def test_gone_pool_probe_cannot_suspend_changed_custody(
    ordinary: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    o = ordinary
    original_owner_nonce(o, monkeypatch)
    launch(o)
    o.client.start_pool_task(
        o.task["id"], "host-a/worktree-a", owner_session_id="native-a",
        expected_generation=o.placed["assignment"]["task_generation"],
    )
    before = o.q.get(o.task["id"])

    def replace_during_probe(*_owner: str | None) -> str:
        with o.q._connect() as conn:
            if changed == "reservation":
                conn.execute(
                    "UPDATE spawn_reservations SET session_handle=? WHERE key=?",
                    ("local-body:replacement", o.placed["reservation"]["key"]),
                )
            else:
                conn.execute(
                    "UPDATE task_pool_assignments SET catalog_revision=catalog_revision+1 "
                    "WHERE task_id=?",
                    (before.id,),
                )
        return "gone"

    counts = o.q.reconcile_liveness(resolver=replace_during_probe)

    assert counts["gone"] == 1 and counts["suspended"] == counts["requeued"] == 0
    assert o.q.get(before.id) == before


def test_keyed_owner_return_and_agent_publish_or_roll_back_together(
    ordinary: SimpleNamespace,
) -> None:
    # Older preparation omitted the nonce. Only Bridge's original keyed owner
    # return (not a later workspace lookup) can still prove it at publication.
    o = ordinary
    with o.q._connect() as conn:
        conn.execute(
            "CREATE TRIGGER reject_native AFTER UPDATE OF agent_info ON spawn_reservations"
            " WHEN NEW.agent_info IS NOT NULL AND NEW.creation_nonce='allocation-a'"
            " BEGIN SELECT RAISE(ABORT,'publication interrupted'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="publication interrupted"):
        launch(o)
    key = o.placed["reservation"]["key"]
    row = o.q.get_reservation(key)
    assert row.creation_nonce is row.agent_info is row.session_handle is None
    assert not any(argv[2] == "send" for argv, _ in o.calls)
    with o.q._connect() as conn:
        conn.execute("DROP TRIGGER reject_native")
    assert launch(o)["native_session_id"] == "native-a"
    assert len(o.allocations) == 1
    row = o.q.get_reservation(key)
    assert row.creation_nonce == o.creation_records[0]["creation_nonce"]
    assert row.agent_info["creation"]["worktree"] == o.creation_records[0]


@pytest.mark.parametrize("change", ["nonce", "reservation", "stale"])
def test_failed_agent_retarget_cannot_publish_or_send(
    ordinary: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    o = ordinary
    original_owner_nonce(o, monkeypatch)
    publish = o.client.record_created_agent

    def corrupt(key: str, **body: Any) -> object:
        changed = deepcopy(body)
        if change == "stale":
            with o.q._connect() as conn:
                conn.execute(
                    "UPDATE task_pool_assignments SET task_generation=task_generation+1"
                    " WHERE task_id=?",
                    (o.task["id"],),
                )
        else:
            owner = changed["worktree_record"]
            if change == "reservation":
                owner["dispatch_attempt"]["reservation_key"] = "other"
            else:
                owner["creation_nonce"] = "other"
                changed["startup_invocation"]["binding"]["creation_nonce"] = "other"
            changed["agent_info"]["creation"]["worktree"] = owner
        return publish(key, **changed)

    monkeypatch.setattr(o.client, "record_created_agent", corrupt)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
        launch(o)
    row = o.q.get_reservation(o.placed["reservation"]["key"])
    assert row.creation_nonce == "allocation-a" and row.agent_info is row.session_handle is None
    assert not any(argv[2] == "send" for argv, _ in o.calls)


def test_unsupported_provider_cannot_allocate_or_silently_drop_nonce(
    ordinary: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary
    supported = o.client.pool_placement_contract()
    supported.pop("ordinary_allocation_nonce_versions")
    monkeypatch.setattr(o.client, "pool_placement_contract", lambda: supported)
    with pytest.raises(
        ordinary_bridge.BridgeCarriedSessionBusy, match="original ordinary allocation nonce"
    ):
        launch(o)
    assert not o.allocations and not o.calls


def test_missing_prepare_ack_retains_allocation_before_bridge(
    ordinary: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary
    original_owner_nonce(o, monkeypatch)
    publish = o.client.record_spawn_worktree

    def missing_ack(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = dict(publish(*args, **kwargs))
        del result["creation_nonce"]
        return result

    monkeypatch.setattr(o.client, "record_spawn_worktree", missing_ack)
    with pytest.raises(
        ordinary_bridge.CreatedAgentUnrecorded, match="did not retain exact workspace"
    ):
        launch(o)
    assert not o.calls
    key = o.placed["reservation"]["key"]
    assert o.q.get_reservation(key).creation_nonce == "allocation-a"
    from agent_dispatch import acquisition_receipts

    receipt = {
        "reservation_key": key,
        "task_id": o.task["id"],
        "task_generation": o.task["generation"],
        "worktree_id": "worktree-a",
        "session_id": "bridge-a",
        "native_session_id": "native-a",
        "mode": "acp",
        "delivery_id": "delivery",
    }
    evidence = {
        "delivery_id": "delivery",
        "stage": "delivered",
        "state": "completed",
        "delivery_admitted": True,
        "recorded_result": receipt,
    }
    monkeypatch.setattr(acquisition_receipts, "bridge_evidence", lambda _: evidence)
    acquisition_receipts.bind(o.q, receipt, evidence=evidence)
    with pytest.raises(DispatchError, match="requires its published agent allocation"):
        o.client.start_pool_task(
            o.task["id"],
            "host-a/worktree-a",
            owner_session_id="native-a",
            expected_generation=o.task["generation"],
        )


def test_stale_reservation_cannot_bind_prepared_incarnation(ordinary: SimpleNamespace) -> None:
    o = ordinary
    key = o.placed["reservation"]["key"]
    o.client.record_spawn_allocation(key, "example")
    with o.q._connect() as conn:
        conn.execute(
            "INSERT INTO pool_reservation_bindings"
            " SELECT 'other',registration_id,machine,env,capabilities"
            " FROM pool_reservation_bindings WHERE reservation_key=?",
            (key,),
        )
        conn.execute(
            "UPDATE task_pool_assignments SET reservation_key='other' WHERE task_id=?",
            (o.task["id"],),
        )
    with pytest.raises(DispatchError, match="original current pool placement"):
        o.client.record_spawn_worktree(
            key,
            "worktree-a",
            ownership="created",
            driver="ordinary-bridge",
            creating_host="host-a",
            execution_machine="host-a",
            allocation_project="example",
            worktree_path=o.ground["path"],
            pool_creation_nonce="allocation-a",
        )
    row = o.q.get_reservation(key)
    assert row.worktree is row.worktree_path is row.creation_nonce is None

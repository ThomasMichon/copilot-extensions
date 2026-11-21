"""Body-free command/API controls; no native SDK or installed service fixture."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge import (
    compatible_inbox,
    delivery_telemetry,
    pending_queue,
    pending_queue_cli,
    pending_queue_notice,
)
from agent_bridge.client import BridgeClientError
from agent_bridge.db import Database
from agent_bridge.inbox_custody import InboxConsumer
from agent_bridge.live_delivery_state import DeliveryStateObservation, LiveDeliveryState
from agent_bridge.routes.pending_queue import router

QueueAPI = tuple[FastAPI, TestClient, Database]


@pytest.fixture
def queue_api(tmp_db: Database, monkeypatch: pytest.MonkeyPatch) -> Iterator[QueueAPI]:
    monkeypatch.setattr(
        pending_queue_notice, "observations", pending_queue_notice.QueueNoticeObservations()
    )
    app = FastAPI()
    app.state.db = tmp_db
    app.state.config = SimpleNamespace(bind="127.0.0.1")
    app.state.ready = True
    app.state.live_delivery_state = LiveDeliveryState()
    app.include_router(router, prefix="/api/v1/live-sessions")
    tmp_db.register_live_session(
        "native",
        machine=None,
        cwd=None,
        worktree_id="worktree",
        repo=None,
        branch=None,
        pid=123,
        process_started_at=100.0,
        role=None,
        now=time.time(),
    )
    with TestClient(app, client=("127.0.0.1", 1234)) as client:
        yield app, client, tmp_db


PATH = "/api/v1/live-sessions/native/pending-queue"
PARAMS: dict[str, str | int] = {"contract_version": 1, "worktree_id": "worktree"}


def _enroll(db: Database) -> InboxConsumer:
    consumer = InboxConsumer("native", "receiver")
    compatible_inbox.enroll(db, consumer, expected_incarnation=None, now=time.time())
    return consumer


def test_inspection_is_body_free_cursor_neutral_and_pages_originals(queue_api: QueueAPI) -> None:
    _, api, db = queue_api
    ids = [
        db.enqueue_live_message("native", "sender", "PRIVATE BODY", time.time()) for _ in range(3)
    ]
    before = [dict(row) for row in db.execute_read("SELECT * FROM live_messages")]
    first = api.get(PATH, params={**PARAMS, "limit": 2})
    assert first.status_code == 200, first.text
    result = first.json()
    assert [m["message_id"] for m in result["messages"]] == ids[:2]
    assert result["page"] == {"after": 0, "limit": 2, "next_after": ids[1], "complete": False}
    assert result["coverage"]["receiver_pending_notice"] == "unavailable"
    assert result["coverage"]["native_sdk_queue"] == "unavailable"
    assert "PRIVATE BODY" not in first.text and "sender" not in first.text
    second = api.get(PATH, params={**PARAMS, "after": ids[1], "limit": 2}).json()
    assert [m["message_id"] for m in second["messages"]] == ids[2:]
    assert second["page"]["complete"] is True
    assert [dict(row) for row in db.execute_read("SELECT * FROM live_messages")] == before


def test_real_cli_client_contract_and_parent_router_reach_metadata_read(
    queue_api: QueueAPI,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_bridge import __main__ as cli
    from agent_bridge.routes.live_sessions import router as parent_router

    app, _, db = queue_api
    app.router.routes.clear()
    app.include_router(parent_router)
    mid = db.enqueue_live_message("native", "sender", "never expose this", time.time())

    class Provider:
        _base = "http://testserver"

        def _request(
            self,
            method: str,
            path: str,
            body: Any = None,
            *,
            params: Any = None,
            **_kwargs: Any,
        ) -> Any:
            with TestClient(app, client=("127.0.0.1", 1234)) as api:
                reply = api.request(method, path, params=params, json=body)
                assert reply.status_code == 200, reply.text
                return reply.json()

    def get_client(*, ensure: bool = True) -> Provider:
        assert ensure is False
        return Provider()

    monkeypatch.setattr(cli, "_get_client", get_client)
    cli.main(["pending-queue", "native", "--worktree-id", "worktree", "--limit", "1", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert [row["message_id"] for row in result["messages"]] == [mid]
    assert result["messages"][0]["stage"] == "admitted"
    assert db.execute_read("SELECT handoff_json FROM live_messages")[0]["handoff_json"] is None


@pytest.mark.parametrize("race_with_poll", [False, True])
def test_reconcile_prepares_originals_once_without_claim_ack_or_native_submission(
    queue_api: QueueAPI,
    race_with_poll: bool,
) -> None:
    _, api, db = queue_api
    ids = [db.enqueue_live_message("native", "sender", "original", time.time()) for _ in range(2)]
    consumer = _enroll(db)
    body = {**PARAMS, "consumer_incarnation": consumer.incarnation}

    def other_operation() -> Any:
        if race_with_poll:
            return compatible_inbox.poll(db, consumer, now=time.time(), limit=10)
        return api.post(PATH + "/reconcile", json=body)

    jobs: list[Callable[[], Any]] = [
        lambda: api.post(PATH + "/reconcile", json=body),
        other_operation,
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda run: run(), jobs))
    assert replies[0].status_code == 200, replies[0].text
    if race_with_poll:
        assert [row["id"] for row in replies[1]] == ids
        assert len(replies[0].json()["reconciled"]["prepared_message_ids"]) in {0, 2}
    else:
        assert replies[1].status_code == 200, replies[1].text
        assert sorted(len(r.json()["reconciled"]["prepared_message_ids"]) for r in replies) == [
            0,
            2,
        ]
    stored = [dict(row) for row in db.execute_read("SELECT * FROM live_messages ORDER BY id")]
    assert [row["id"] for row in stored] == ids
    assert all(row["body"] == "original" for row in stored)
    assert all(
        row[key] is None
        for row in stored
        for key in (
            "claimed_at",
            "delivered_at",
            "handoff_started_at",
            "consumed_at",
            "outcome",
        )
    )
    handles = [json.loads(row["handoff_json"]) for row in stored]
    polled = compatible_inbox.poll(db, consumer, now=time.time(), limit=10)
    assert [row["id"] for row in polled] == ids
    assert [row["handoff"] for row in polled] == handles
    # The ordinary consumer still owns the one-shot handoff grant.
    assert compatible_inbox.begin(db, consumer, ids[0], handles[0]["id"], now=time.time())
    assert not compatible_inbox.begin(db, consumer, ids[0], handles[0]["id"], now=time.time())


def test_unknown_started_and_accepted_handoffs_are_never_replayed(queue_api: QueueAPI) -> None:
    _, api, db = queue_api
    ids = [db.enqueue_live_message("native", "sender", "original", time.time()) for _ in range(3)]
    consumer = _enroll(db)
    offered = compatible_inbox.poll(db, consumer, now=time.time(), limit=10)
    for row in offered:
        compatible_inbox.begin(db, consumer, row["id"], row["handoff"]["id"], now=time.time())
    db.record_live_message_handoff(
        consumer,
        ids[1],
        offered[1]["handoff"]["id"],
        outcome="unknown",
        now=time.time(),
        compatible_consumer=True,
    )
    db.record_live_message_handoff(
        consumer,
        ids[2],
        offered[2]["handoff"]["id"],
        outcome="sdk_accepted",
        now=time.time(),
        compatible_consumer=True,
    )
    before = [dict(row) for row in db.execute_read("SELECT * FROM live_messages")]
    response = api.post(PATH + "/reconcile", json={**PARAMS, "consumer_incarnation": "receiver"})
    assert response.status_code == 200, response.text
    assert response.json()["reconciled"]["prepared_message_ids"] == []
    assert [m["stage"] for m in response.json()["messages"]] == [
        "sending",
        "unknown",
        "sdk_accepted",
    ]
    assert [dict(row) for row in db.execute_read("SELECT * FROM live_messages")] == before


def test_exact_binding_current_receiver_and_serving_refusals(queue_api: QueueAPI) -> None:
    app, api, db = queue_api
    db.enqueue_live_message("native", "sender", "original", time.time())
    _enroll(db)
    body = {**PARAMS, "consumer_incarnation": "receiver"}
    for change in ({"worktree_id": "other"}, {"consumer_incarnation": "retired"}):
        assert api.post(PATH + "/reconcile", json={**body, **change}).status_code == 409
    assert api.get(PATH.replace("native", "missing"), params=PARAMS).status_code == 404
    app.state.shutting_down = True
    assert api.post(PATH + "/reconcile", json=body).status_code == 503
    app.state.shutting_down = False
    db.execute_write("UPDATE live_sessions SET updated_at=1 WHERE session_id='native'")
    assert api.get(PATH, params=PARAMS).json()["target"]["freshness"] == "stale-or-retired"
    assert api.post(PATH + "/reconcile", json=body).status_code == 409
    assert db.execute_read("SELECT handoff_json FROM live_messages")[0]["handoff_json"] is None


def test_unenrolled_or_wrong_ingress_cannot_prepare_offers(queue_api: QueueAPI) -> None:
    _, api, db = queue_api
    db.enqueue_live_message("native", "sender", "original", time.time())
    assert api.get(PATH, params=PARAMS).json()["reconcile_supported"] is False
    body = {**PARAMS, "consumer_incarnation": "missing"}
    assert api.post(PATH + "/reconcile", json=body).status_code == 409
    _enroll(db)
    assert (
        api.post(
            PATH + "/reconcile",
            json={**body, "consumer_incarnation": "receiver"},
            headers={"X-Forwarded-For": "127.0.0.1"},
        ).status_code
        == 409
    )
    assert (
        api.post(
            PATH + "/reconcile",
            json={**body, "consumer_incarnation": "receiver", "contract_version": True},
        ).status_code
        == 422
    )
    assert db.execute_read("SELECT handoff_json FROM live_messages")[0]["handoff_json"] is None


def test_muted_empty_and_missing_observations_do_not_claim_native_emptiness(
    queue_api: QueueAPI,
) -> None:
    app, api, db = queue_api
    empty = api.get(PATH, params=PARAMS).json()
    assert empty["messages"] == [] and empty["coverage"]["native_sdk_queue"] == "unavailable"
    assert empty["attention"]["state"] == "unknown"
    mid = db.enqueue_live_message("native", "sender", "original", time.time())
    _enroll(db)
    app.state.live_delivery_state.publish(
        "native",
        DeliveryStateObservation(
            pid=123,
            process_started_at=100,
            generation="g",
            muted=True,
            complete=True,
        ),
    )
    result = api.post(
        PATH + "/reconcile", json={**PARAMS, "consumer_incarnation": "receiver"}
    ).json()
    assert result["attention"]["reasons"] == ["muted"]
    assert result["reconciled"]["prepared_message_ids"] == [mid]
    assert result["reconciled"]["sdk_submission"] == "not-requested"
    assert db.execute_read("SELECT delivered_at FROM live_messages")[0]["delivered_at"] is None


def test_existing_hook_notice_metadata_stays_distinct_from_empty_backend(
    queue_api: QueueAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, api, db = queue_api
    _enroll(db)
    monkeypatch.setattr(delivery_telemetry, "_buffer", lambda: None)
    event = {
        "session_id": "native",
        "stage": "hook_notice",
        "source": "pending-input-set-v1",
        "pending_count": 1,
        "incomplete": False,
        "delivery_path": "retained",
        "consumer_incarnation": "receiver",
    }
    delivery_telemetry.represented("other", event)
    assert api.get(PATH, params=PARAMS).json()["receiver_notice"]["availability"] == "unavailable"
    delivery_telemetry.represented("native", event)
    result = api.get(PATH, params=PARAMS).json()
    assert result["messages"] == []
    assert result["receiver_notice"]["pending_count"] == 1
    assert result["receiver_notice"]["receiver_binding"] == "matched"
    assert result["coverage"]["native_sdk_queue"] == "unavailable"
    assert (
        pending_queue_notice.observations.inspect("native", "successor")["availability"]
        == "unavailable"
    )
    clock = time.monotonic()
    monkeypatch.setattr(
        "agent_bridge.pending_queue_notice.time.monotonic",
        lambda: clock + pending_queue_notice.TTL + 1,
    )
    assert api.get(PATH, params=PARAMS).json()["receiver_notice"]["availability"] == "unavailable"


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"after": -1}, {"contract_version": 2}]
)
def test_http_bounds_and_version_refuse_before_effects(
    queue_api: QueueAPI,
    params: dict[str, int],
) -> None:
    _, api, _ = queue_api
    assert api.get(PATH, params={**PARAMS, **params}).status_code == 422


@pytest.mark.parametrize("declaration", [None, {}, {**pending_queue.CONTRACT, "version": True}])
def test_unsupported_provider_is_never_sent_a_reconciliation(declaration: Any) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    class Provider:
        _base = "http://example.invalid"

        def _request(self, method: str, path: str, *args: Any, **kwargs: Any) -> Any:
            calls.append((method, path, kwargs))
            return declaration

    args = argparse.Namespace(
        session_id="native",
        worktree_id="worktree",
        after=0,
        limit=50,
        reconcile=True,
        consumer_incarnation="receiver",
    )
    with pytest.raises(BridgeClientError, match="does not support"):
        pending_queue_cli.execute(Provider(), args)
    assert len(calls) == 1 and calls[0][0] == "GET"
    assert calls[0][2]["allow_retry"] is False


def test_client_mutation_is_pinned_and_never_replayed_after_unknown_outcome() -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    class Provider:
        _base = "http://example.invalid"

        def _request(self, method: str, path: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append((method, path, kwargs))
            if len(calls) == 1:
                return pending_queue.CONTRACT
            raise BridgeClientError(503, "outcome unavailable")

    args = argparse.Namespace(
        session_id="native",
        worktree_id="worktree",
        after=0,
        limit=50,
        reconcile=True,
        consumer_incarnation="receiver",
    )
    with pytest.raises(BridgeClientError, match="outcome unavailable"):
        pending_queue_cli.execute(Provider(), args)
    assert [call[0] for call in calls] == ["GET", "POST"]
    assert all(
        call[2]["_endpoint"] == Provider._base and not call[2]["allow_retry"] for call in calls
    )


@pytest.mark.parametrize("tail", [["--limit", "101"], ["--after", "-1"], ["--lim", "2"]])
def test_strict_cli_bounds_before_client_creation(tail: list[str]) -> None:
    parser = argparse.ArgumentParser()
    pending_queue_cli.register_commands(parser.add_subparsers(dest="command"))
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["pending-queue", "native", "--worktree-id", "worktree", *tail])
    assert exc.value.code == 2


@pytest.mark.parametrize("turn_state", ["running", "idle"])
def test_resumed_receiver_keeps_original_unknown_handoff_and_rejects_old_incarnation(
    queue_api: QueueAPI,
    turn_state: str,
) -> None:
    _, api, db = queue_api
    old = _enroll(db)
    mid = db.enqueue_live_message("native", "sender", "original", time.time())
    offer = compatible_inbox.poll(db, old, now=time.time(), limit=1)[0]
    assert compatible_inbox.begin(db, old, mid, offer["handoff"]["id"], now=time.time())
    db.record_live_message_handoff(
        old,
        mid,
        offer["handoff"]["id"],
        outcome="unknown",
        now=time.time(),
        compatible_consumer=True,
    )
    resumed = InboxConsumer("native", "resumed-receiver")
    compatible_inbox.enroll(
        db,
        resumed,
        expected_incarnation=old.incarnation,
        now=time.time(),
    )
    db.execute_write(
        "UPDATE live_sessions SET turn_state=? WHERE session_id='native'",
        (turn_state,),
    )
    before = [dict(row) for row in db.execute_read("SELECT * FROM live_messages")]
    view = api.get(PATH, params=PARAMS).json()
    assert view["target"]["consumer_incarnation"] == resumed.incarnation
    assert view["target"]["turn_state"] == turn_state
    assert view["messages"][0]["stage"] == "unknown"
    assert (
        api.post(
            PATH + "/reconcile",
            json={**PARAMS, "consumer_incarnation": old.incarnation},
        ).status_code
        == 409
    )
    reconciled = api.post(
        PATH + "/reconcile",
        json={**PARAMS, "consumer_incarnation": resumed.incarnation},
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["reconciled"]["prepared_message_ids"] == []
    assert compatible_inbox.poll(db, resumed, now=time.time(), limit=1) == []
    assert [dict(row) for row in db.execute_read("SELECT * FROM live_messages")] == before


def test_worktree_successor_cannot_retarget_an_exact_original_queue(queue_api: QueueAPI) -> None:
    _, api, db = queue_api
    _enroll(db)
    mid, reason = db.enqueue_live_message_if_fresh(
        "native",
        sender="sender",
        body="exact original",
        now=time.time(),
        expected_session_id="native",
        enrolled_inbox=True,
        exact_session=True,
    )
    assert mid is not None and reason is None
    db.register_live_session(
        "successor",
        machine=None,
        cwd=None,
        worktree_id="worktree",
        repo=None,
        branch=None,
        pid=124,
        process_started_at=101,
        role=None,
        now=time.time() + 1,
    )
    old = api.get(PATH, params=PARAMS)
    assert old.status_code in {200, 404, 409}
    if old.status_code == 200:
        assert old.json()["target"]["session_id"] == "native"
        assert old.json()["target"]["freshness"] == "stale-or-retired"
    assert (
        api.post(
            PATH + "/reconcile",
            json={**PARAMS, "consumer_incarnation": "receiver"},
        ).status_code
        == 409
    )
    current = api.get(PATH.replace("native", "successor"), params=PARAMS)
    assert current.status_code == 200, current.text
    assert current.json()["target"]["session_id"] == "successor"
    assert current.json()["messages"] == []
    original = db.execute_read(
        "SELECT session_id,body,delivered_at FROM live_messages WHERE id=?", (mid,)
    )[0]
    assert original["session_id"] == "native" and original["body"] == "exact original"
    assert original["delivered_at"] is None

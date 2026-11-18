"""A lifecycle writer can commit its terminal state and its idle notice together."""

import sqlite3

import pytest

from agent_bridge.db import Database
from agent_bridge.status_store import SelectedAgent, Selection, StatusStore


def test_status_and_subscription_follow_outer_transaction(tmp_db: Database) -> None:
    store = StatusStore(tmp_db)
    store.subscribe("watch", Selection(watcher="reader", agents=[SelectedAgent(session_id="worker")], events=["idle"]))
    with pytest.raises(RuntimeError, match="abort settlement"):
        with tmp_db._runtime_transaction(write=True) as conn:
            store.publish("worker", "idle", "turn-1", transaction=conn)
            raise RuntimeError("abort settlement")
    page = store.page(after=0, epoch=None, subscription_id="watch")
    assert not page["events"] and page["subscription"]["unread"] == page["subscription"]["acked"]
    with tmp_db._runtime_transaction(write=True) as conn:
        event_id = store.publish("worker", "idle", "turn-1", transaction=conn)
        assert event_id is not None and conn.in_transaction
    with tmp_db._runtime_transaction(write=True) as conn:
        assert store.publish("worker", "idle", "turn-1", transaction=conn) is None
    page = store.page(after=0, epoch=None, subscription_id="watch")
    assert [event["id"] for event in page["events"]] == [event_id]
    assert page["subscription"]["unread"] == event_id > page["subscription"]["acked"]
    assert page["subscription"]["notice_epoch"] == 1


def test_status_rejects_foreign_or_inactive_transaction(tmp_db: Database) -> None:
    store = StatusStore(tmp_db)
    with pytest.raises(ValueError, match="active transaction"):
        store.publish("worker", "idle", "inactive", transaction=tmp_db._get_conn())
    other = sqlite3.connect(":memory:")
    try:
        other.execute("BEGIN")
        with pytest.raises(ValueError, match="active transaction"):
            store.publish("worker", "idle", "foreign", transaction=other)
        assert other.in_transaction
    finally:
        other.close()
    assert not store.page(after=0, epoch=None)["events"]

"""Inbox projection work is bounded independently of unrelated retained history."""

from __future__ import annotations

from dataclasses import replace
import json

import pytest

from agent_bridge.runtime_custody import RuntimeCustodyError
from test_inbox_custody import inbox as inbox


@pytest.mark.parametrize("history_size", [0, 20_000])
def test_empty_inbox_does_not_scan_unrelated_retained_history(inbox, history_size) -> None:
    db, _, _, admission, consumer = inbox
    with db._runtime_transaction(write=True) as conn:
        conn.executemany(
            "INSERT INTO live_messages "
            "(session_id,sender,body,created_at,runtime_custody_json) VALUES (?,?,?,?,?)",
            (("unrelated-native", "sender", "retained history", 10.0, admission.document())
             for _ in range(history_size)),
        )
    ticks = []
    conn = db._get_conn()

    def progress() -> int:
        ticks.append(True)
        return 0

    conn.set_progress_handler(progress, 100)
    try:
        assert db.inspect_live_inbox(consumer.session_id, 10) == []
    finally:
        conn.set_progress_handler(None, 0)
    # Count actual SQLite VM work, not a timing proxy or a prescribed plan name.
    assert len(ticks) * 100 <= 1_000


@pytest.mark.parametrize("history_size", [0, 20_000])
@pytest.mark.parametrize(("target", "history_session"), [
    ("enrolled", "unrelated-native"), ("retired", "unrelated-native"),
    ("legacy", "unrelated-native"), ("enrolled", "native-a"), ("retired", "native-a"),
])
def test_ordinary_admission_is_bounded_by_consumer_history_indexes(
    inbox, history_size, target, history_session,
) -> None:
    db, _, _, admission, consumer = inbox
    original = dict(db.execute_read("SELECT * FROM bridge_inbox_consumers")[0])
    with db._runtime_transaction(write=True) as conn:
        conn.executemany(
            "INSERT INTO bridge_inbox_consumers "
            "(incarnation,installation,epoch,session_id,consumer_json,predecessor,current,enrolled_at) "
            "VALUES (?,?,?,?,?,NULL,0,10)",
            ((f"retired-{i}", original["installation"], original["epoch"], history_session,
              json.dumps(replace(
                  consumer, session_id=history_session, incarnation=f"retired-{i}",
              ).document(), sort_keys=True))
             for i in range(history_size)),
        )
    sid = consumer.session_id
    if target == "retired":
        db.execute_write("UPDATE bridge_inbox_consumers SET current=0 WHERE incarnation=?",
                         (consumer.incarnation,))
    elif target == "legacy":
        sid = "never-enrolled"
        assert db.register_live_session(
            sid, machine="machine-a", cwd=".", worktree_id=None, repo="repo-a",
            branch=None, pid=103, role=None, now=10.0, process_started_at=1.0,
        ) == "live"
    ticks = []
    conn = db._get_conn()

    def progress() -> int:
        ticks.append(True)
        return 0

    conn.set_progress_handler(progress, 100)
    try:
        def send():
            return db.enqueue_live_message_if_fresh(
                sid, sender="sender", body="new input", now=11.0,
                idempotency_key="new-operation", enrolled_inbox=True, runtime_admission=admission,
            )

        if target == "retired":
            with pytest.raises(RuntimeCustodyError, match="current frontend and consumer enrollment"):
                send()
            assert db.execute_read("SELECT * FROM live_messages") == []
        else:
            mid, reason = send()
            assert mid is not None and reason is None
    finally:
        conn.set_progress_handler(None, 0)
    assert len(ticks) * 100 <= 10_000

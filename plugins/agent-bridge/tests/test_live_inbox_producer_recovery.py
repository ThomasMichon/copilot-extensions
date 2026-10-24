"""Real SQLite ordering and process-loss controls for producer admission."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import subprocess
import sys
import threading

import pytest

from agent_bridge.db import Database
from agent_bridge.runtime_custody import RuntimeCustodyError
from test_inbox_custody import close, inbox as inbox


@pytest.mark.parametrize("winner", ["send", "closure"])
def test_enrolled_producer_and_closure_commit_in_one_serial_order(inbox, winner: str) -> None:
    db, owner, _, admission, consumer = inbox
    other = Database(db.db_path)
    entered, release, competing = threading.Event(), threading.Event(), threading.Event()
    timeouts = []

    def operate(selected: Database, action: str, pause: bool = False):
        conn = selected._get_conn()
        effect = False

        def trace(statement: str) -> None:
            nonlocal effect
            if not pause and statement == "BEGIN IMMEDIATE":
                competing.set()
            if statement.startswith(("INSERT OR IGNORE INTO live_messages",
                                     "UPDATE bridge_runtime_epoch SET epoch=")):
                effect = True
            if pause and effect and statement == "COMMIT":
                entered.set()
                if not release.wait(10):
                    timeouts.append(True)

        conn.set_trace_callback(trace)
        try:
            if action == "closure":
                return close(inbox, db=selected)
            return selected.enqueue_live_message_if_fresh(
                consumer.session_id, sender="sender", body="original body", now=11.0,
                idempotency_key="operation-a", runtime_admission=admission, enrolled_inbox=True,
            )
        finally:
            conn.set_trace_callback(None)
            conn.close()
            selected._local.conn = None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(operate, db, winner, True)
            assert entered.wait(10)
            second = pool.submit(operate, other, "closure" if winner == "send" else "send")
            assert competing.wait(10)
            release.set()
            first.result(timeout=10)
            if winner == "closure":
                with pytest.raises(RuntimeCustodyError, match="original open epoch"):
                    second.result(timeout=10)
            else:
                # The competing closure observed its revision before the send
                # committed. Its failed CAS must reconcile, not ignore that send.
                with pytest.raises(RuntimeCustodyError, match="changed before closure"):
                    second.result(timeout=10)
                close(inbox)
            assert not timeouts
        rows = db.runtime_custody_inventory(owner)["rows"]["live_messages"]
        assert len(rows) == (1 if winner == "send" else 0)
        if rows:
            retained = db.execute_read("SELECT * FROM live_messages")[0]
            assert retained["body"] == "original body"
            assert retained["runtime_custody_json"] == admission.document()
            assert retained["handoff_json"] is None
    finally:
        release.set()
        other.close()


def test_producer_process_exits_before_reply_and_replay_recovers_identical_original(inbox) -> None:
    db, owner, _, admission, consumer = inbox
    child = """
import json, os, sys
from agent_bridge.db import Database
from agent_bridge.runtime_custody import RuntimeAdmission, RuntimeInstallation
owner = RuntimeInstallation(**json.loads(sys.argv[1]))
admission = RuntimeAdmission(**json.loads(sys.argv[2]))
db = Database(owner.database)
mid, reason = db.enqueue_live_message_if_fresh(
    "native-a", sender="sender", body="original body", now=11.0,
    idempotency_key="operation-a", runtime_admission=admission, enrolled_inbox=True,
)
assert mid and reason is None
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", child, json.dumps(asdict(owner)), admission.document()],
        check=True, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    original = dict(db.execute_read("SELECT * FROM live_messages")[0])
    close(inbox)
    db.deregister_live_session(consumer.session_id)
    reopened = Database(db.db_path)
    try:
        mid, reason = reopened.enqueue_live_message_if_fresh(
            consumer.session_id, sender="sender", body="original body", now=12.0,
            idempotency_key="operation-a", report_duplicate=True, enrolled_inbox=True,
            inbox_refusal="replacement frontend is not enrolled",
        )
        assert mid == original["id"] and reason == "duplicate"
        assert dict(reopened.execute_read("SELECT * FROM live_messages")[0]) == original
    finally:
        reopened.close()

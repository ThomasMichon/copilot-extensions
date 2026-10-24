"""Inbox projection work is bounded independently of unrelated retained history."""

from __future__ import annotations

import pytest

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

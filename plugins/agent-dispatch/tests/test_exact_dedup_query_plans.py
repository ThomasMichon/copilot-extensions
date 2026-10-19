"""Live dedup queries use existing indexes without scanning completed history."""

from __future__ import annotations

import pytest

from agent_dispatch.queue import Status, TaskQueue
from agent_dispatch.queue_dedup import find_live_dedup_task

REPO = "github.com/example/project"
ACTIVE_KEY = "recipe:reviewer:target=github.com/example/project#41"
PENDING_KEY = "recipe:reviewer:target=github.com/example/project#42"


@pytest.fixture
def history_queue(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    active = queue.create(
        "active review",
        repo=REPO,
        dedup_key=ACTIVE_KEY,
        source="recipe",
        origin_ref="reviewer",
    )
    pending = queue.create(
        "pending review",
        repo=REPO,
        dedup_key=PENDING_KEY,
        source="recipe",
        origin_ref="reviewer",
        require_verification=True,
    )
    queue.claim_one("review-worker", repo=REPO, task_id=pending.id)
    queue.start(pending.id, "review-worker")
    pending = queue.complete(pending.id, "review-worker")
    assert pending.status == Status.SUBMITTED
    with queue._connect() as conn:
        conn.executemany(
            "INSERT INTO tasks (id, title, repo, dedup_key, status, created_at, updated_at) "
            "VALUES (?, 'completed history', ?, ?, 'completed', 1, 1)",
            ((f"history-{i}", REPO, f"history:{i}") for i in range(10000)),
        )
    return queue, active, pending


@pytest.mark.parametrize("reviewer_aliases", [False, True])
def test_exact_dedup_uses_active_and_pending_indexes_on_completed_history(
    history_queue, reviewer_aliases
):
    queue, active, pending = history_queue
    with queue._connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'completed'").fetchone()[0]
            == 10000
        )
        schema_before = list(conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name"))
        conn.execute("PRAGMA query_only = ON")
        for expected in (active, pending):
            statements = []
            conn.set_trace_callback(statements.append)
            try:
                result = find_live_dedup_task(
                    conn,
                    REPO,
                    expected.dedup_key,
                    reviewer_target_for_key=(queue._reviewer_target if reviewer_aliases else None),
                )
            finally:
                conn.set_trace_callback(None)
            assert result == expected
            assert len(statements) == (2 if reviewer_aliases else 1)
            for statement in statements:
                plan = [row["detail"] for row in conn.execute("EXPLAIN QUERY PLAN " + statement)]
                assert any("USING INDEX idx_tasks_dedup" in detail for detail in plan), plan
                assert any(
                    "USING INDEX idx_tasks_status (status=?)" in detail for detail in plan
                ), plan
                assert not any(
                    "idx_tasks_repo" in detail or detail.startswith("SCAN tasks")
                    for detail in plan
                ), plan
                assert "UNION ALL" in statement
                if not reviewer_aliases or statement == statements[0]:
                    assert any(
                        "idx_tasks_dedup (repo=? AND dedup_key=?)" in detail for detail in plan
                    ), plan
        assert (
            list(conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name"))
            == schema_before
        )

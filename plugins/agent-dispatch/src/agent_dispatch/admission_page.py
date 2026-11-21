"""One bounded keyset page supplements saturated supervisor discovery windows."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from fastapi import FastAPI, Query

from .client import DispatchClient
from .client_errors import DispatchError
from .queue import TaskQueue
from .queue_common import Task, _TASK_BULK_SELECT

PAGE_SIZE = 200


def register(app: FastAPI, queue: TaskQueue, bulk: Callable[[Iterable[Task]], list[dict[str, Any]]]) -> None:
    @app.get("/worker-admission/task-page")
    def page(after: str = Query("", max_length=256), repo: str | None = None) -> list[dict[str, Any]]:
        lane = queue._canonical_repo(repo)
        repo_clause = " AND repo=?" if lane is not None else ""
        # Without ANALYZE, SQLite can prefer idx_tasks_status + a full sort.
        index = "idx_queued_finite_repo_id" if lane is not None else "idx_queued_finite_id"
        with queue._connect() as conn:
            rows = conn.execute(
                f"SELECT {_TASK_BULK_SELECT} FROM tasks INDEXED BY {index} WHERE status='queued' AND kind='finite' "
                f"AND id>?{repo_clause} ORDER BY id LIMIT ?",  # noqa: S608
                (after, lane, PAGE_SIZE) if lane is not None else (after, PAGE_SIZE),
            ).fetchall()
        return bulk(Task._from_row(row) for row in rows)


def next_page(client: DispatchClient, repo: str | None, after: str) -> tuple[list[dict[str, Any]], str]:
    result = client._unwrap(client._http.get(
        "/worker-admission/task-page", params={"after": after, **({"repo": repo} if repo is not None else {})},
    ))
    if not isinstance(result, list) or len(result) > PAGE_SIZE:
        raise DispatchError(502, "invalid admission candidate page")
    last = after
    for row in result:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] <= last or (
            row.get("status") != "queued" or row.get("kind") != "finite"
        ):
            raise DispatchError(502, "invalid admission candidate or nonadvancing cursor")
        last = row["id"]
    return result, last if len(result) == PAGE_SIZE else ""

"""Bounded relevance transport; each decision retains its own atomic receipt."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
import json
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .coordinator_relevance import RelevanceBody
from .queue import TaskError, TaskQueue
from .queue_relevance_registration import RelevanceAdmission, RelevanceRegistration
from .relevance_source import RelevanceSource
from .task_relevance_contract import RELEVANCE_PATH


class ReportItem(RelevanceBody):
    task_id: str = Field(min_length=1, max_length=4096)


class ReportBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reports: list[ReportItem] = Field(min_length=1, max_length=100)


def register_batch_routes(
    app: FastAPI, queue: TaskQueue, *, dependencies: list[Any], source: RelevanceSource,
    decide: Callable[[str, RelevanceBody, Request, RelevanceAdmission], dict | JSONResponse],
    envelope: Callable[[dict], dict],
    policy: Callable[[], AbstractContextManager[tuple[str, str, frozenset[str]]]],
) -> None:
    @app.get(f"{RELEVANCE_PATH}/snapshots", dependencies=dependencies)
    def snapshots(
        registration_id: str = Query(min_length=1, max_length=4096),
        task_ids: list[Annotated[str, Field(min_length=1, max_length=4096)]] = Query(
            min_length=1, max_length=100,
        ),
    ) -> dict:
        admission = source.prepare()
        results = []
        for task_id in task_ids:
            try:
                with policy() as (machine, env, disabled):
                    result = queue.relevance_snapshot(
                        task_id,
                        registration=RelevanceRegistration(registration_id, machine, env),
                        disabled_ids=disabled, registration_admission=admission,
                    )
                results.append({"task_id": task_id, "result": result})
            except (TaskError, OSError, ValueError, TimeoutError) as exc:
                results.append({"task_id": task_id, "error": str(exc)})
        return envelope({"items": results})

    @app.post(f"{RELEVANCE_PATH}/reports", dependencies=dependencies)
    def reports(body: ReportBatch, request: Request) -> dict:
        admission = source.prepare()
        results = []
        for row in body.reports:
            values = row.model_dump(exclude={"task_id"})
            try:
                result = decide(row.task_id, RelevanceBody(**values), request, admission)
            except Exception:
                # The decision may have committed; no rejection proof, so retain custody.
                item = {"error": "relevance outcome unknown; recover the original receipt"}
            else:
                if isinstance(result, JSONResponse):
                    item = json.loads(result.body)
                else:
                    item = {"result": result["result"]}
            results.append({"task_id": row.task_id, "request_id": row.request_id, **item})
        return envelope({"items": results})

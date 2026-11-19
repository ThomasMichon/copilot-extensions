from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.producers.emitter import _author_tasks
from agent_dispatch.queue import TaskQueue


def test_source_poll_and_actual_create_disposition_join_without_task_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    spec = {"id": "test-feed", "source": "recipe"}
    row = {
        "title": "not-loggable-title", "repo": "example/project", "dedup_key": "not-loggable-key",
        "payload_inline": json.dumps({"kind": "pr-review", "pr_number": 7, "head_sha": "a" * 40,
                                      "body": "not-loggable-content"}),
    }

    with TestClient(create_app(queue)) as api:
        class LocalClient:
            def create(self, title: str, **fields: Any) -> dict[str, Any]:
                result = api.post("/tasks", json={"title": title, **fields})
                result.raise_for_status()
                return cast(dict[str, Any], result.json())

        client = cast(DispatchClient, LocalClient())
        _author_tasks(client, spec, "[]")
        first, = _author_tasks(client, spec, json.dumps([row]))
        second, = _author_tasks(client, spec, json.dumps([row]))
    assert first["id"] == second["id"]
    capture = capsys.readouterr()
    assert not capture.out and "not-loggable" not in capture.err
    traces = [json.loads(line) for line in capture.err.splitlines() if line.startswith('{"event":')]
    assert [r["candidate_count"] for r in traces if r["event"] == "emitter.source_read"] == [0, 1, 1]
    writes = [r for r in traces if r["event"] == "dispatch.create_result"]
    assert [r["outcome"] for r in writes] == ["created", "deduplicated"]
    linked = [r for r in traces if "key_ref" in r]
    assert len({(r["key_ref"], r["emitter_ref"], r["pr_number"], r["observed_head"]) for r in linked}) == 1
    assert [r["task_id"] for r in linked if r["event"] != "emitter.candidate"] == [first["id"]] * 4

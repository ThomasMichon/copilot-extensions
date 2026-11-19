from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from process_telemetry.buffer import BufferedSink

from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.producers.emitter import _author_tasks
from agent_dispatch.queue import TaskQueue
from agent_dispatch import emission_trace


def test_source_poll_and_actual_create_disposition_join_without_task_content(
    tmp_path: Path, capfd: pytest.CaptureFixture[str],
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
        unkeyed = dict(row)
        del unkeyed["dedup_key"]
        third, = _author_tasks(client, spec, json.dumps([unkeyed]))
    assert first["id"] == second["id"]
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        counts = emission_trace._writer.stats()
        if counts["delivered"] >= counts["accepted"]:
            break
        time.sleep(0.005)
    capture = capfd.readouterr()
    assert not capture.out and "not-loggable" not in capture.err
    traces = [json.loads(line) for line in capture.err.splitlines() if line.startswith('{"event":')]
    assert [r["candidate_count"] for r in traces if r["event"] == "emitter.source_read"] == [0, 1, 1, 1]
    writes = [r for r in traces if r["event"] == "dispatch.create_result"]
    assert [r["outcome"] for r in writes] == ["created", "deduplicated", "created"]
    assert writes[-1]["task_id"] == third["id"] and "key_ref" not in writes[-1]
    linked = [r for r in traces if "key_ref" in r]
    assert len({(r["key_ref"], r["emitter_ref"], r["pr_number"], r["observed_head"]) for r in linked}) == 1
    assert [r["task_id"] for r in linked if r["event"] != "emitter.candidate"] == [first["id"]] * 4


def test_blocked_diagnostics_do_not_block_or_replay_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = threading.Event(), threading.Event()

    def blocked(_: dict[str, Any]) -> None:
        entered.set()
        assert release.wait(5)

    writer = BufferedSink(blocked, capacity=1, batch_size=1).start()
    try:
        monkeypatch.setattr(emission_trace, "_writer", writer)
        deadline = time.monotonic() + 2
        while not entered.is_set() and time.monotonic() < deadline:
            writer({"event": "block"})
            entered.wait(0.025)
        assert entered.is_set(), writer.stats()
        writer({"event": "fill"})
        with TestClient(create_app(TaskQueue(tmp_path / "tasks.db"))) as api:
            response = api.post("/tasks", json={"title": "work", "repo": "example/project", "origin_ref": "emitter"})
            assert response.status_code == 200 and response.json()["status"] == "queued"
        assert writer.stats()["full"] > 0 and writer.stats()["sink_failed"] == 0
    finally:
        release.set()
        writer.close()
        writer.join(1)

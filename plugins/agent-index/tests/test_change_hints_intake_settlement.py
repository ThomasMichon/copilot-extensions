"""Contended SQLite admission leaves the loop responsive and survives cancellation."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from types import SimpleNamespace

import httpx
import pytest

from agent_index import server
from agent_index.indexing.change_hints import ChangeHints
from agent_index.indexing.task_store import TaskStore


class _ObservedStore(TaskStore):
    def __init__(self, path):
        self.watch = False
        self.entered = threading.Event()
        super().__init__(path)

    def _connect(self):
        connection = super()._connect()

        def trace(statement):
            if self.watch and statement == "BEGIN IMMEDIATE":
                self.entered.set()

        connection.set_trace_callback(trace)
        return connection


async def _until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), timeout=2)


@pytest.mark.parametrize("cancelled", [False, True])
def test_contended_hint_enqueue_retains_drain_admission_until_settled(tmp_path, monkeypatch, cancelled):
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(server, "_missing_indexing_dependencies", lambda: [])

    async def exercise():
        store = _ObservedStore(tmp_path / "tasks.db")
        store.watch = True
        app = server.build_app()
        notifications = []

        async def drain(**kwargs):
            return True

        app.state.task_store = store
        app.state.task_runner = SimpleNamespace(
            notify=lambda: notifications.append("notify"), drain=drain,
        )
        writer = sqlite3.connect(store._db_path)
        writer.execute("BEGIN IMMEDIATE")
        posting = draining = None
        hints = ChangeHints(
            source="git:sample", canonical_family="git", account_id="account-a", item_ids=("a",),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture",
        ) as client:
            try:
                posting = asyncio.create_task(client.post("/reindex/hints", json={
                    "source": "git:sample", "change_hints": hints.to_dict(),
                }))
                await _until(store.entered.is_set)
                assert not posting.done()
                health = await asyncio.wait_for(client.get("/health"), timeout=2)
                assert health.status_code == 200
                assert app.state.drain_gate.reads == 1
                identity = health.json()
                draining = asyncio.create_task(client.post("/drain", json={
                    "timeout": 3, "poll": 0.01,
                }, headers={
                    server.INSTALLATION_HEADER: identity["installationId"],
                    server.INSTANCE_HEADER: identity["instanceToken"],
                }))
                await _until(lambda: app.state.drain_gate.draining)
                if cancelled:
                    posting.cancel()
                    await asyncio.sleep(0.03)
                    posting.cancel()
                    await asyncio.sleep(0.03)
                assert not posting.done()
                assert not draining.done()
                assert app.state.drain_gate.reads == 1
                assert store.get_pending_count() == 0
                writer.rollback()
                writer.close()
                writer = None
                if cancelled:
                    with pytest.raises(asyncio.CancelledError):
                        await posting
                else:
                    accepted = await posting
                    assert accepted.json()["accepted"] is True
                drained = await draining
                assert drained.json()["clean"] is True
                assert drained.json()["busy_reads"] == 0
                assert notifications == ["notify"]
                assert store.get_pending_count() == 1
                assert store.get_queue()[0].change_hints == hints
                assert app.state.drain_gate.reads == 0
            finally:
                if writer is not None:
                    writer.rollback()
                    writer.close()
                for task in (posting, draining):
                    if task is not None:
                        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())

"""Actual Textual graph activation keeps optional history reads off startup."""

import asyncio
from threading import Event

import pytest
from textual.widgets import Static, TabbedContent

from worktree_manager import mux_companion as companion
from test_companion_observations import envelope


@pytest.fixture(autouse=True)
def no_ambient_attention(monkeypatch):
    monkeypatch.setattr(companion.ca, "read_session_attention", lambda *a, **kw: {
        "attention": None, "error": "attention-unavailable",
    })


def data():
    return companion._CompanionData(
        worktree={"id": "selected", "path": "fixture-path"},
        sessions=[{"id": "session-a", "is_head": True}],
        bundle={"worktree_id": "selected", "facts": {}},
    )


def test_graph_reads_only_after_activation_and_refreshes(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: data())

    def read(project, session_id, **kwargs):
        calls.append((project, session_id))
        return {"observation": envelope(), "error": None}

    monkeypatch.setattr(companion.co, "read_session_observations", read)

    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test(size=(80, 30)) as pilot:
            await app.workers.wait_for_complete()
            assert not calls
            assert app.query_one("#views", TabbedContent).active == "summary-tab"
            app.query_one("#views", TabbedContent).active = "topology-tab"
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert calls == [(None, "session-a")]
            assert app._data.session_observation == envelope()
            assert "Selected worktree" in app.query_one("#topology", Static).content.label.plain
            app.action_refresh()
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert len(calls) == 2
            assert app.query_one("#views", TabbedContent).active == "topology-tab"

    asyncio.run(check())


def test_ambiguous_head_does_not_guess_or_read_another_session(monkeypatch):
    current = data()
    current.sessions.append({"id": "other", "is_head": True})
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: current)

    def forbidden(*a, **kw):
        raise AssertionError("ambiguous lineage must not select a session")

    monkeypatch.setattr(companion.co, "read_session_observations", forbidden)

    async def check():
        app = companion.MuxCompanionApp()
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            app.query_one("#views", TabbedContent).active = "topology-tab"
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app._data.observation_error == "head-session-unavailable"
            assert app._data.session_observation is None

    asyncio.run(check())


def test_optional_provider_failure_preserves_summary_and_recovery(monkeypatch):
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: data())
    monkeypatch.setattr(companion.co, "read_session_observations", lambda *a, **kw: {
        "observation": None, "error": "observation-read-failed",
    })

    async def check():
        app = companion.MuxCompanionApp()
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            tabs = app.query_one("#views", TabbedContent)
            tabs.active = "topology-tab"
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app._data.observation_error == "observation-read-failed"
            tabs.active = "recovery-tab"
            await pilot.pause()
            assert app.query_one("#cutover-btn").disabled
            tabs.active = "summary-tab"
            await pilot.pause()
            assert app._data.error is None

    asyncio.run(check())


def test_escape_cancels_blocked_graph_read(monkeypatch):
    entered, stopped = Event(), Event()
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: data())
    monkeypatch.setattr(companion.cc, "cancellable_read_runner", lambda cancelled, **kw: cancelled)

    def blocked(*a, runner, **kw):
        entered.set()
        assert runner.wait(5)
        stopped.set()
        return {"observation": None, "error": "cancelled"}

    monkeypatch.setattr(companion.co, "read_session_observations", blocked)

    async def check():
        app = companion.MuxCompanionApp()
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            app.query_one("#views", TabbedContent).active = "topology-tab"
            assert await asyncio.to_thread(entered.wait, 2)
            await pilot.press("escape")
        assert await asyncio.to_thread(stopped.wait, 2)

    asyncio.run(check())


def test_late_graph_read_cannot_replace_refreshed_snapshot(monkeypatch):
    entered, release, finished = Event(), Event(), Event()
    reads = []
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: data())

    def read(*a, **kw):
        reads.append(1)
        value = envelope()
        if len(reads) == 1:
            entered.set()
            assert release.wait(5)
            finished.set()
        else:
            value["as_of"] = "2026-10-25T08:00:00Z"
        return {"observation": value, "error": None}

    monkeypatch.setattr(companion.co, "read_session_observations", read)

    async def check():
        app = companion.MuxCompanionApp()
        try:
            async with app.run_test() as pilot:
                await app.workers.wait_for_complete()
                app.query_one("#views", TabbedContent).active = "topology-tab"
                assert await asyncio.to_thread(entered.wait, 2)
                original = app._data
                app.action_refresh()
                await pilot.pause()
                await app.workers.wait_for_complete()
                assert app._data is not original
                assert app._data.session_observation["as_of"] == "2026-10-25T08:00:00Z"
                release.set()
                assert await asyncio.to_thread(finished.wait, 2)
                await pilot.pause()
                assert app._data.session_observation["as_of"] == "2026-10-25T08:00:00Z"
        finally:
            release.set()

    asyncio.run(check())

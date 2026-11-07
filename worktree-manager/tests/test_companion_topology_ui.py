"""Actual Textual graph activation keeps optional history reads off startup."""

import asyncio
import io
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
            await pilot.wait_for_animation()
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


def graph_text(app):
    from rich.console import Console

    console = Console(width=200, record=True, file=io.StringIO())
    console.print(app.query_one("#topology", Static).content)
    return console.export_text()


def pr_data():
    current = data()
    current.bundle["companion_pr_observations"] = dict(companion.cpr.DECLARATION)
    return current


def test_pr_point_read_only_on_selection_and_late_reply_is_rejected(monkeypatch):
    from test_companion_pr_state import fact, row, snapshot

    entered, release = Event(), Event()
    reads = []
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: pr_data())
    monkeypatch.setattr(companion.co, "read_session_observations",
                        lambda *a, **kw: {"observation": None, "error": "none"})
    monkeypatch.setattr(companion.cpr, "read_associations", lambda wt, **kw: fact(row()))

    def point(association, **kw):
        reads.append(association.pr_id)
        if len(reads) == 2:
            entered.set()
            assert release.wait(5)
        return companion.cpr.decode_snapshot(
            snapshot(pr_state="closed", merged=True), association, observed_at=1.0)

    monkeypatch.setattr(companion.cpr, "point_read", point)

    async def check():
        app = companion.MuxCompanionApp()
        try:
            async with app.run_test(size=(100, 40)) as pilot:
                await app.workers.wait_for_complete()
                app.query_one("#views", TabbedContent).active = "topology-tab"
                await pilot.pause()
                await app.workers.wait_for_complete()
                assert not reads
                graph = graph_text(app)
                assert "recorded lifecycle: active" in graph and "Not read" in graph
                app.query_one("#pr-association").value = "pr-1"
                await pilot.pause()
                await app.workers.wait_for_complete()
                assert reads == ["pr-1"]
                assert "Point read: merged" in graph_text(app)
                app.query_one("#pr-read-btn").press()
                assert await asyncio.to_thread(entered.wait, 2)
                generation = app._pr_generation
                app.action_refresh()
                for _ in range(100):
                    if app._pr_generation != generation:
                        break
                    await pilot.pause(0.01)
                assert app._pr_generation != generation
                release.set()
                await pilot.pause()
                await app.workers.wait_for_complete()
                assert app._pr_selected is None and app._pr_read is None
                assert "Point read" not in graph_text(app)
        finally:
            release.set()

    asyncio.run(check())


def _pr_app(monkeypatch, *rows):
    from test_companion_pr_state import fact

    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: pr_data())
    monkeypatch.setattr(companion.co, "read_session_observations",
                        lambda *a, **kw: {"observation": None, "error": "none"})
    monkeypatch.setattr(companion.cpr, "read_associations", lambda wt, **kw: fact(*rows))


async def _open_graph(app, pilot):
    await app.workers.wait_for_complete()
    app.query_one("#views", TabbedContent).active = "topology-tab"
    await pilot.pause()
    await app.workers.wait_for_complete()


def test_pr_selection_stays_visible_and_deselection_cancels_the_read(monkeypatch):
    from textual.widgets import Select

    from test_companion_pr_state import row, snapshot

    _pr_app(monkeypatch, row())
    entered, release = Event(), Event()
    cancels = []

    def point(association, *, runner=None, **kw):
        cancels.append(runner)
        entered.set()
        assert release.wait(5)
        return companion.cpr.decode_snapshot(snapshot(), association, observed_at=1.0)

    monkeypatch.setattr(companion.cpr, "point_read", point)
    monkeypatch.setattr(companion.MuxCompanionApp, "_owned_read_runner",
                        lambda self, cancelled: cancelled)

    async def check():
        app = companion.MuxCompanionApp()
        try:
            async with app.run_test(size=(100, 40)) as pilot:
                await _open_graph(app, pilot)
                select = app.query_one("#pr-association", Select)
                select.value = "pr-1"
                assert await asyncio.to_thread(entered.wait, 2)
                await pilot.pause()
                assert select.value == "pr-1" and app._pr_busy
                select.clear()
                await pilot.pause()
                assert cancels[0].is_set()
                assert app._pr_selected is None and app.query_one("#pr-read-btn").disabled
                release.set()
                await pilot.pause()
                await app.workers.wait_for_complete()
                assert app._pr_read is None and "Point read" not in graph_text(app)
                entered.clear()
                select.value = "pr-1"
                await pilot.pause()
                await app.workers.wait_for_complete()
                assert select.value == "pr-1" and "Point read: open" in graph_text(app)
                assert not app.query_one("#pr-read-btn").disabled
        finally:
            release.set()

    asyncio.run(check())


def test_switching_pr_waits_for_the_previous_read_to_retire(monkeypatch):
    from textual.widgets import Select

    from test_companion_pr_state import row, snapshot

    _pr_app(monkeypatch, row(), row("pr-2", number=8))
    entered, release = Event(), Event()
    reads, active, peak = [], [0], [0]

    def point(association, **kw):
        reads.append(association.pr_id)
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        try:
            if association.pr_id == "pr-1":
                entered.set()
                assert release.wait(5)
            return companion.cpr.decode_snapshot(
                snapshot(pr=association.number), association, observed_at=1.0)
        finally:
            active[0] -= 1

    monkeypatch.setattr(companion.cpr, "point_read", point)

    async def check():
        app = companion.MuxCompanionApp()
        try:
            async with app.run_test(size=(100, 40)) as pilot:
                await _open_graph(app, pilot)
                select = app.query_one("#pr-association", Select)
                select.value = "pr-1"
                assert await asyncio.to_thread(entered.wait, 2)
                select.value = "pr-2"
                for _ in range(20):
                    await pilot.pause(0.01)
                assert reads == ["pr-1"] and select.value == "pr-2"
                release.set()
                await pilot.pause()
                await app.workers.wait_for_complete()
                assert reads == ["pr-1", "pr-2"] and peak[0] == 1
                assert app._pr_read.association.pr_id == "pr-2"
                assert "Point read: open" in graph_text(app)
        finally:
            release.set()

    asyncio.run(check())


def test_undeclared_engine_keeps_claim_only_view(monkeypatch):
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *a, **kw: data())
    monkeypatch.setattr(companion.co, "read_session_observations",
                        lambda *a, **kw: {"observation": None, "error": "none"})

    def forbidden(*a, **kw):
        raise AssertionError("undeclared engines must not receive the optional request")

    monkeypatch.setattr(companion.cpr, "read_associations", forbidden)

    async def check():
        app = companion.MuxCompanionApp()
        async with app.run_test(size=(100, 40)) as pilot:
            await app.workers.wait_for_complete()
            app.query_one("#views", TabbedContent).active = "topology-tab"
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert "Not declared by this engine" in graph_text(app)
            assert app.query_one("#pr-association").disabled

    asyncio.run(check())

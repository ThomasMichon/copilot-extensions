"""Headless launch-history privacy, read budgets, navigation and cancellation."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from textual.app import App
from textual.widgets import Button, DataTable, Input, Static

from worktree_manager import launch_attempts, launch_history_cli, launch_history_screen
from worktree_manager.launch_attempts import LaunchAttempts
from worktree_manager.launch_history_screen import LaunchHistoryScreen

pytestmark = pytest.mark.contract("worktree_manager.launch_history_screen")


async def _ready(pilot, screen):
    async with asyncio.timeout(10):
        while screen._busy or not screen.is_mounted:
            await pilot.pause(0.01)
        await pilot.pause()


@pytest.fixture
def journal(tmp_path, monkeypatch):
    root = tmp_path / "manager"
    monkeypatch.setattr(launch_attempts, "default_root", lambda: root)
    store = LaunchAttempts(root)
    for index in range(3):
        store.record_intent(
            f"{index + 1:032x}", action="new", project="demo", surface="test",
            is_local=True, prompt=f"PRIVATE INPUT {index}\n[red]literal $text",
        )
    return store


def test_metadata_pagination_detail_and_explicit_input_are_read_only(journal, monkeypatch):
    monkeypatch.setattr(launch_history_screen, "_PAGE_SIZE", 2)
    calls = []

    def evidence(project, attempt, **kwargs):
        calls.append((project, attempt, kwargs))
        return {
            "scope": "local-project", "receipts": [], "errors": [{"source": "native", "code": "missing"}],
            "admissions": [], "has_more": True, "next_after": "retained-cursor",
            "prompt_acceptance": "unknown",
        }

    monkeypatch.setattr(launch_history_cli, "engine_evidence", evidence)
    before = journal.path.read_bytes()

    async def run():
        app = App()
        screen = LaunchHistoryScreen("demo")
        async with app.run_test(size=(130, 40)) as pilot:
            await app.push_screen(screen)
            await _ready(pilot, screen)
            assert [row["attempt_id"] for row in screen._rows] == [f"{n:032x}" for n in (3, 2)]
            assert calls == []
            assert "PRIVATE INPUT" not in app.export_screenshot()
            await pilot.click("#history-older")
            await _ready(pilot, screen)
            assert [row["attempt_id"] for row in screen._rows] == [f"{1:032x}"]
            await pilot.click("#history-newer")
            await _ready(pilot, screen)
            table = screen.query_one("#history-list", DataTable)
            table.focus()
            await pilot.press("enter")
            await _ready(pilot, screen)
            assert len(calls) == 1 and calls[0][2] == {"limit": 10, "after": None, "request_ids": []}
            assert screen._detail["attempt"]["attempt_id"] == f"{3:032x}"
            assert "input" not in screen._detail["attempt"]
            assert screen._detail["attempt"]["events_has_more"] is False
            assert screen._detail["engine"]["has_more"] is True
            assert screen._detail["engine"]["errors"] == [{"source": "native", "code": "missing"}]
            assert "PRIVATE INPUT" not in app.export_screenshot()
            await pilot.click("#history-input")
            await _ready(pilot, screen)
            assert screen._detail["attempt"]["input"]["prompt"] == "PRIVATE INPUT 2\n[red]literal $text"
            assert len(calls) == 1
            await pilot.click("#history-back")
            assert screen._detail is None
            assert "PRIVATE INPUT" not in app.export_screenshot()
            await pilot.press("escape")
            assert app.screen is not screen

    asyncio.run(run())
    assert journal.path.read_bytes() == before


def test_input_expiry_and_storage_errors_remain_sanitized(journal, monkeypatch, caplog):
    def unavailable(*args, **kwargs):
        raise launch_history_cli.engine_client.EngineError("PRIVATE ENGINE ERROR")

    monkeypatch.setattr(launch_history_cli, "engine_evidence", unavailable)

    async def run():
        app = App()
        screen = LaunchHistoryScreen("demo")
        async with app.run_test(size=(130, 40)) as pilot:
            await app.push_screen(screen)
            await _ready(pilot, screen)
            screen.query_one("#history-list", DataTable).focus()
            await pilot.press("enter")
            await _ready(pilot, screen)
            assert screen._detail["engine"]["status"] == "unavailable"
            assert "PRIVATE ENGINE ERROR" not in app.export_screenshot()
            confirmed = screen._detail["attempt"]["submitted_at"]
            monkeypatch.setattr(
                launch_attempts, "_timestamp", lambda: confirmed + launch_attempts.RETENTION_SECONDS,
            )
            await pilot.click("#history-input")
            await _ready(pilot, screen)
            assert "input" not in screen._detail["attempt"]
            assert "expired" in str(screen.query_one("#history-status", Static).render())
            assert "PRIVATE INPUT" not in app.export_screenshot()
            await pilot.click("#history-back")

            def broken(**kwargs):
                raise launch_attempts.LaunchAttemptError("PRIVATE STORAGE ERROR")

            monkeypatch.setattr(screen._store, "entries", broken)
            await pilot.click("#history-refresh")
            await _ready(pilot, screen)
            assert "PRIVATE STORAGE ERROR" not in app.export_screenshot()
            assert "unavailable" in str(screen.query_one("#history-status", Static).render())
            assert screen._rows == [] and screen.query_one("#history-list", DataTable).row_count == 0
            assert screen.query_one("#history-close", Button).disabled is False

    asyncio.run(run())
    assert "PRIVATE" not in caplog.text


def test_default_page_contains_at_most_twenty_five_actual_metadata_rows(journal, monkeypatch):
    for index in range(3, 27):
        journal.record_intent(
            f"{index + 1:032x}", action="open", project="demo", surface="test", is_local=True,
        )
    monkeypatch.setattr(
        launch_history_cli, "engine_evidence",
        lambda *args, **kwargs: pytest.fail("metadata paging must not query an engine"),
    )

    async def run():
        app = App()
        screen = LaunchHistoryScreen("demo")
        async with app.run_test(size=(100, 40)) as pilot:
            await app.push_screen(screen)
            await _ready(pilot, screen)
            assert len(screen._rows) == 25 and screen._has_more
            assert screen.query_one("#history-list", DataTable).row_count == 25
            first = {row["attempt_id"] for row in screen._rows}
            await pilot.click("#history-older")
            await _ready(pilot, screen)
            assert len(screen._rows) == 2 and not screen._has_more
            assert first.isdisjoint(row["attempt_id"] for row in screen._rows)
            assert len(first | {row["attempt_id"] for row in screen._rows}) == 27

    asyncio.run(run())


def test_close_during_slow_metadata_read_drops_late_result(journal, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow(**kwargs):
        entered.set()
        assert release.wait(5)
        return []

    monkeypatch.setattr(LaunchAttempts, "entries", lambda self, **kwargs: slow(**kwargs))

    async def run():
        app = App()
        screen = LaunchHistoryScreen("demo")
        async with app.run_test(size=(130, 40)) as pilot:
            try:
                await app.push_screen(screen)
                async with asyncio.timeout(5):
                    while not entered.is_set():
                        await pilot.pause(0.01)
                assert screen._busy
                await pilot.press("escape")
                assert app.screen is not screen
                threads = tuple(screen._threads)
                release.set()
                for thread in threads:
                    await asyncio.to_thread(thread.join, 2)
                    assert not thread.is_alive()
                assert screen._detail is None and screen._rows == []
            finally:
                release.set()

    asyncio.run(run())


@pytest.mark.parametrize("direction", ["older", "newer"])
def test_changed_filters_reset_every_paging_cursor(journal, monkeypatch, direction):
    monkeypatch.setattr(launch_history_screen, "_PAGE_SIZE", 1)
    for index in range(3, 6):
        journal.record_intent(
            f"{index + 1:032x}", action="resume" if index == 5 else "open",
            project="other", surface="test", is_local=True,
        )

    async def run():
        app = App()
        screen = LaunchHistoryScreen("demo")
        async with app.run_test(size=(100, 40)) as pilot:
            await app.push_screen(screen)
            await _ready(pilot, screen)
            if direction == "newer":
                for _ in range(2):
                    await pilot.click("#history-older")
                    await _ready(pilot, screen)
                assert len(screen._previous) == 2
            screen.query_one("#history-project", Input).value = "other"
            await pilot.click("#history-" + direction)
            await _ready(pilot, screen)
            assert screen._before is None and screen._previous == []
            assert [row["attempt_id"] for row in screen._rows] == [f"{6:032x}"]
            screen.query_one("#history-action", Input).value = "resume"
            await pilot.click("#history-older")
            await _ready(pilot, screen)
            assert screen._before is None and screen._previous == []
            assert [row["attempt_id"] for row in screen._rows] == [f"{6:032x}"]
            assert screen._has_more is False

    asyncio.run(run())


@pytest.mark.parametrize("include_input", [False, True])
def test_preview_budget_is_explicit_and_counts_unicode_characters(journal, monkeypatch, include_input):
    async def run():
        app = App()
        screen = LaunchHistoryScreen("demo")
        async with app.run_test(size=(80, 24)) as pilot:
            await app.push_screen(screen)
            await _ready(pilot, screen)
            rendered = []
            monkeypatch.setattr(
                screen.query_one("#history-detail", Static), "update", rendered.append,
            )
            screen._detail = {"attempt": {"attempt_id": "a" * 32}}
            if include_input:
                screen._detail["attempt"]["input"] = {"prompt": "\u03bb" * 40000}
            else:
                screen._detail["engine"] = {"evidence": "\u03bb" * 40000}
            screen._render_detail()
            prefix, notice = rendered[-1].split("\n[Preview truncated;", 1)
            assert len(prefix) == 32768
            assert prefix == json.dumps(screen._detail, ensure_ascii=False, indent=2)[:32768]
            assert f"launch-attempts {'a' * 32} --json" in notice
            assert ("--include-input" in notice) == include_input
            screen._detail = None

    asyncio.run(run())


def test_compact_history_row_messages_never_reach_resume_even_while_busy(journal, monkeypatch):
    from worktree_manager.picker_app import WorktreeManagerApp

    entered, release = threading.Event(), threading.Event()
    leaked, launches = [], []

    class CompactApp(WorktreeManagerApp):
        def on_data_table_row_selected(self, event):
            leaked.append(event)
            super().on_data_table_row_selected(event)

    def evidence(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {"receipts": [], "errors": [], "prompt_acceptance": "unknown"}

    monkeypatch.setattr(launch_history_cli, "engine_evidence", evidence)

    async def run():
        app = CompactApp(lambda: [], project="demo")
        monkeypatch.setattr(app, "_request_launch", launches.append)
        async with app.run_test(size=(100, 40)) as pilot:
            try:
                await pilot.press("ctrl+shift+h")
                screen = app.screen
                await _ready(pilot, screen)
                table = screen.query_one("#history-list", DataTable)
                table.focus()
                await pilot.press("enter")
                async with asyncio.timeout(5):
                    while not entered.is_set():
                        await pilot.pause(0.01)
                assert screen._busy and launches == [] and leaked == []
                table.post_message(DataTable.RowSelected(table, 0, table.ordered_rows[0].key))
                await pilot.pause()
                assert launches == [] and leaked == []
                release.set()
                await _ready(pilot, screen)
                assert screen._detail is not None and app.screen is screen
                assert launches == [] and leaked == []
            finally:
                release.set()

    asyncio.run(run())


@pytest.mark.parametrize("surface", ["production", "compact"])
def test_history_shortcut_works_on_both_surfaces_without_launching(journal, monkeypatch, surface):
    from test_picker_tui import _fixture_source

    from worktree_manager.picker_app import WorktreeManagerApp
    from worktree_manager.production_picker import context
    from worktree_manager.production_picker.picker_tui.engine import PickerApp

    monkeypatch.setattr(context, "project", lambda: "demo")
    monkeypatch.setattr(
        launch_history_cli, "engine_evidence",
        lambda *args, **kwargs: pytest.fail("opening metadata must not query an engine"),
    )
    entries, limits = LaunchAttempts.entries, []

    def bounded_entries(self, **kwargs):
        limits.append(kwargs["limit"])
        return entries(self, **kwargs)

    monkeypatch.setattr(LaunchAttempts, "entries", bounded_entries)

    async def run():
        app = (PickerApp(_fixture_source(), live=False) if surface == "production"
               else WorktreeManagerApp(lambda: [], project="demo"))
        if surface == "compact":
            monkeypatch.setattr(
                app, "_request_launch", lambda *args: pytest.fail("history leaked a launch shortcut"),
            )
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+shift+h")
            assert isinstance(app.screen, LaunchHistoryScreen)
            screen = app.screen
            await _ready(pilot, screen)
            assert len(screen._rows) == 3
            assert limits == [26]
            screen.query_one("#history-close", Button).focus()
            assert screen.query_one("#history-close", Button).region.right <= 80
            await pilot.press("n")
            assert app.screen is screen
            await pilot.press("escape")
            assert app.screen is not screen

    asyncio.run(run())

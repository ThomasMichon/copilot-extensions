"""Composer caches cannot extend private history's original deadline."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from textual.app import App

from worktree_manager import seed_history
from worktree_manager.production_picker import context
from worktree_manager.production_picker.picker_tui.engine_dialogs import ScopeDlgScreen, SubMenuScreen
from worktree_manager.production_picker.picker_tui.seed_prompt_field import SeedPromptField


@pytest.mark.parametrize("surface", ["new", "resume"])
def test_actual_composer_navigation_discards_expired_cached_text_without_changing_draft(
    tmp_path, monkeypatch, surface,
):
    clock = [2_000_000_000.0 - seed_history.RETENTION_SECONDS + 1]
    monkeypatch.setattr(seed_history.time, "time", lambda: clock[0])
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    context.set_project("example")
    history = seed_history.SeedHistory("example")
    history.remember("older-expiring-input")
    clock[0] = 2_000_000_000.0
    history.remember("newer-unexpired-input")

    async def run():
        app = App()
        callbacks = []
        def background(_label, work, done, **kwargs):
            callbacks.append(lambda: done(work()))
        engine = SimpleNamespace(real_ops=True, src=SimpleNamespace(), app=app, _run_bg=background)
        dialog = (
            ScopeDlgScreen({"opts": [], "section": 1}, show_prompt=True)
            if surface == "new" else SubMenuScreen(
                {"is_local": True, "raw": {"id": "selected-worktree"}}, ["Resume"], engine=engine,
            )
        )
        async with app.run_test() as pilot:
            app.push_screen(dialog)
            await pilot.pause()
            if surface == "resume":
                assert len(callbacks) == 1
                callbacks.pop()()
            field = dialog.query_one(SeedPromptField)
            assert field.seed_cursor.entries == ["newer-unexpired-input", "older-expiring-input"]
            field.load_text("unsent draft with edits\n")
            field.focus()
            await pilot.press("ctrl+up", "ctrl+up")
            assert field.text == "older-expiring-input"
            await pilot.press("ctrl+down", "ctrl+down")
            assert field.text == "unsent draft with edits\n"
            assert 1 in field.seed_cursor.edits
            clock[0] += 1
            assert history.expire() == 1
            await pilot.press("ctrl+up", "ctrl+up")
            assert field.text == "newer-unexpired-input"
            assert field.seed_cursor.entries == ["newer-unexpired-input", ""]
            assert 1 not in field.seed_cursor.edits
            await pilot.press("ctrl+down")
            assert field.text == "unsent draft with edits\n"
    try:
        asyncio.run(run())
    finally:
        context.reset()


def test_cursor_hides_expired_cache_even_without_physical_maintenance(tmp_path, monkeypatch):
    clock = [2_000_000_000.0]
    monkeypatch.setattr(seed_history.time, "time", lambda: clock[0])
    history = seed_history.SeedHistory("example", tmp_path)
    history.remember("private cached input")
    cursor = history.cursor()
    assert cursor.move("original draft", 1) == "private cached input"
    clock[0] += seed_history.RETENTION_SECONDS
    assert cursor.move("currently edited text", 1) is None
    assert cursor.entries == [""]
    assert cursor.move("currently edited text", -1) == "original draft"
    assert cursor.move("original draft", 1) is None
    assert b"private cached input" in history.path.read_bytes()
    clock[0] -= 1
    assert cursor.move("original draft", 1) is None


def test_invalid_clock_reports_without_changing_composer_draft(tmp_path, monkeypatch):
    async def run():
        app = App()
        field = SeedPromptField(history_on_mount=False)
        field.seed_cursor = seed_history.HistoryCursor(["private"], expires_at=[2_000_000_001])
        async with app.run_test() as pilot:
            await app.mount(field)
            field.load_text("unchanged draft")
            field.focus()
            notices = []
            monkeypatch.setattr(field, "notify", lambda message, **_kwargs: notices.append(message))
            monkeypatch.setattr(seed_history.time, "time", lambda: float("nan"))
            await pilot.press("ctrl+up")
            assert field.text == "unchanged draft"
            assert notices == ["Local seed history clock is invalid."]
    asyncio.run(run())

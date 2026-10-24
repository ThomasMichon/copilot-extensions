"""Simulated authorization exercises the actual shared modal and its rendering."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rich.style import Style
from textual.app import App
from textual.widgets import Static

from worktree_manager.picker_button import PickerButton
from worktree_manager.pivot_progress import PivotProgressScreen, progress_text
from worktree_manager.production_picker.picker_tui.styles import C_BTN, C_BTN_SEL


MESSAGE = (
    "Authorize account example-user on this device.\n"
    "Open https://localhost/device\nEnter code: DEMO-CODE\n"
    "[bold red]literal provider text[/bold red]"
)


def _contrast(foreground, background):
    def luminance(color):
        channels = [channel / 255 for channel in color]
        linear = [value / 12.92 if value <= .04045 else
                  ((value + .055) / 1.055) ** 2.4 for value in channels]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))

    values = sorted((luminance(foreground), luminance(background)))
    return (values[1] + .05) / (values[0] + .05)


@pytest.mark.parametrize("theme,size", [
    ("textual-dark", (100, 32)), ("textual-dark", (48, 24)),
    ("textual-light", (100, 32)), ("textual-light", (48, 24)),
])
def test_modal_render_focus_hover_completion(theme, size, tmp_path):
    release = threading.Event()
    completed = Mock()

    class Runtime:
        def run_action_stream(self, _action, _context, progress, should_cancel):
            progress(None, MESSAGE)
            while not release.wait(.01):
                if should_cancel():
                    return False, "cancelled"
            return True, "Authorized example-user"

    async def check():
        app = App()
        app.theme = theme
        screen = PivotProgressScreen(
            Runtime(), SimpleNamespace(label="Authorize"), {}, completed=completed,
        )
        async with app.run_test(size=size) as pilot:
            app.push_screen(screen)
            for _ in range(30):
                await pilot.pause()
                if "DEMO-CODE" in screen.query_one("#view-progress", Static).render().plain:
                    break
            body = screen.query_one("#view-progress", Static)
            button = screen.query_one("#view-cancel", PickerButton)
            text = body.render()
            assert text.plain == MESSAGE
            assert text.spans and all(span.style.link is None for span in text.spans)
            url_start = MESSAGE.index("https://")
            link_style = text.get_style_at_offset(url_start)
            assert link_style.underline and link_style.bold
            assert link_style.foreground != body.styles.color
            assert _contrast(link_style.foreground.rgb, body.background_colors[1].rgb) >= 4.5
            assert not text.get_style_at_offset(MESSAGE.index("[bold")).bold
            assert app.focused is button and button.region.height == 1
            focused = Style.parse(C_BTN_SEL)
            assert button.styles.background.rgb == focused.bgcolor.get_truecolor()
            assert button.styles.color.rgb == focused.color.get_truecolor()
            assert button.region.right <= size[0] and button.region.bottom <= size[1]
            assert body.region.width <= size[0]
            app.save_screenshot(str(tmp_path / f"{theme}-{size[0]}-working.svg"))
            button.blur()
            await pilot.hover("#view-title")
            await pilot.pause()
            assert button.styles.background.rgb == Style.parse(C_BTN).bgcolor.get_truecolor()
            await pilot.hover("#view-cancel")
            await pilot.pause()
            assert button.styles.background.rgb == focused.bgcolor.get_truecolor()
            button.focus()
            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert screen.finished and str(button.label) == "Close"
            completed.assert_called_once_with()
            assert "Succeeded: Authorized example-user" in body.render().plain
            app.save_screenshot(str(tmp_path / f"{theme}-{size[0]}-complete.svg"))
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is not screen

    try:
        asyncio.run(check())
    finally:
        release.set()


@pytest.mark.parametrize("cancel", ["escape", "button"])
def test_cancel_and_error_remain_literal(cancel):
    completed = Mock()

    class Runtime:
        def run_action_stream(self, _action, _context, progress, should_cancel):
            progress(None, MESSAGE)
            while not should_cancel():
                threading.Event().wait(.01)
            return False, "[bold]not success[/bold]"

    async def check():
        app = App()
        screen = PivotProgressScreen(
            Runtime(), SimpleNamespace(label="Authorize"), {}, completed=completed,
        )
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            if cancel == "escape":
                await pilot.press("escape")
            else:
                await pilot.click("#view-cancel")
            await app.workers.wait_for_complete()
            assert screen.finished and screen.cancelled.is_set()
            assert screen.query_one("#view-progress").render().plain == "Cancelled."
            completed.assert_not_called()
            screen._message("Failed: [bold]denied[/bold]")
            assert screen.query_one("#view-progress").render().plain == "Failed: [bold]denied[/bold]"
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is not screen

    asyncio.run(check())


@pytest.mark.parametrize("message,styled", [
    ("Open https://localhost/device.", True),
    ("Open http://localhost:8080/device", True),
    ("https://user:secret@localhost/device", False),
    ("https://localhost:bad/device", False),
    ("javascript:alert(1) [@click=app.quit]quit[/]", False),
    ("[link=javascript:alert]DEMO-CODE[/link]", False),
])
def test_safe_url_style_only(message, styled):
    text = progress_text(message, "#3366ff")
    assert text.plain == message
    assert bool(text.spans) == styled
    assert all(span.style.link is None and not span.style.meta for span in text.spans)

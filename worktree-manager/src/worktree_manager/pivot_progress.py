"""Shared nonblocking progress modal for explicitly activated view actions."""
from __future__ import annotations

import asyncio
import re
import threading
from urllib.parse import urlsplit

from rich.style import Style
from rich.text import Text
from rich.color import Color as RichColor
from textual.app import ComposeResult
from textual.color import Color
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from .picker_button import PickerButton


def _luminance(color: Color) -> float:
    channels = [value / 255 for value in color.rgb]
    return sum(
        (value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4) * weight
        for value, weight in zip(channels, (.2126, .7152, .0722))
    )


def _link_color(body: Static) -> RichColor:
    background = body.background_colors[1]
    color = body.styles.link_color
    target = background.get_contrast_text(1)
    for step in range(21):
        candidate = color.blend(target, step / 20)
        dark, light = sorted((_luminance(candidate), _luminance(background)))
        if (light + .05) / (dark + .05) >= 4.5:
            return candidate.rich_color
    return target.rich_color


def progress_text(message: str, link_color: str | RichColor) -> Text:
    """Style web URLs without interpreting provider markup or enabling actions."""
    text = Text(message)
    for match in re.finditer(r"https?://[^\s<>\"'\[\]]+", message):
        url = match.group().rstrip(".,;:!?)")
        try:
            parsed = urlsplit(url)
            valid = parsed.hostname and not parsed.username and not parsed.password
            parsed.port
        except ValueError:
            continue
        if valid:
            text.stylize(Style(color=link_color, bold=True, underline=True),
                         match.start(), match.start() + len(url))
    return text


class PivotProgressScreen(ModalScreen):
    CSS = """
    PivotProgressScreen { align: center middle; background: $background 55%; }
    PivotProgressScreen > Vertical {
        width: 76; max-width: 100%; height: auto; max-height: 90%; padding: 1 2;
        border: round $accent; background: $surface;
    }
    PivotProgressScreen Static { height: auto; }
    PivotProgressScreen #view-title { text-style: bold; margin-bottom: 1; }
    PivotProgressScreen VerticalScroll { height: auto; max-height: 1fr; }
    PivotProgressScreen #view-progress {
        padding-bottom: 1; link-color: $text-accent;
    }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, runtime, action, context, *, completed, close_runtime=False):
        super().__init__()
        self.runtime = runtime
        self.action = action
        self.context = dict(context)
        self.completed = completed
        self.close_runtime = close_runtime
        self.cancelled = threading.Event()
        self.finished = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.action.label, id="view-title", markup=False)
            with VerticalScroll():
                yield Static("Starting...", id="view-progress", markup=False)
            yield PickerButton("Cancel", id="view-cancel")

    def on_mount(self):
        self.query_one("#view-cancel", Button).focus()
        self.run_worker(self._run())

    async def _run(self):
        loop = asyncio.get_running_loop()

        def progress(_pct, message):
            loop.call_soon_threadsafe(self._message, message)

        try:
            ok, message = await asyncio.to_thread(
                self.runtime.run_action_stream, self.action, self.context, progress,
                should_cancel=self.cancelled.is_set,
            )
        except (OSError, RuntimeError) as exc:
            ok, message = False, f"Action failed: {exc}"
        if not self.is_mounted:
            return
        self.finished = True
        self._message(
            ("Cancelled." if self.cancelled.is_set()
             else ("Succeeded: " if ok else "Failed: ") + message)
        )
        self.query_one("#view-cancel", Button).label = "Close"
        if ok and not self.cancelled.is_set():
            self.completed()

    def _message(self, message):
        if self.is_mounted:
            body = self.query_one("#view-progress", Static)
            body.update(progress_text(message, _link_color(body)))

    def action_cancel(self):
        if self.finished:
            self.dismiss()
        else:
            self.cancelled.set()
            self._message("Cancelling authorization...")

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.action_cancel()

    def on_unmount(self):
        self.cancelled.set()
        if self.close_runtime:
            self.runtime.close()

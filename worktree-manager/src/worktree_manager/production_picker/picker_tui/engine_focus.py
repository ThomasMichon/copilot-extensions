#!/usr/bin/env python3
"""Shared focus widgets extracted from ``engine.py``."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import SelectionList, Static

from .engine_helpers import C_BTN, C_BTN_SEL

class FocusGroup(Widget):
    """A single focusable tab-stop containing several button-like choices; the
    arrow keys move an internal highlight and Enter/Space activate the
    highlighted one (#88 NF).

    The native answer to the tab-group requirement -- "one entry in a group
    receives tab-focus, you arrow to the rest" -- for the *heterogeneous* rows
    Textual's list widgets (`OptionList` / `SelectionList` / `RadioSet`) don't
    cover, e.g. a `Confirm` / `Cancel` button pair. The framework owns focus
    (this is one tab-stop); the widget owns only the intra-group highlight, and
    posts a :class:`FocusGroup.Activated` message on Enter/Space.
    """

    can_focus = True
    BINDINGS = [
        Binding("left", "move(-1)", show=False),
        Binding("up", "move(-1)", show=False),
        Binding("right", "move(1)", show=False),
        Binding("down", "move(1)", show=False),
        Binding("enter", "activate", show=False),
        Binding("space", "activate", show=False),
    ]

    class Activated(Message):
        """Posted when a choice is activated (Enter/Space on the highlight)."""

        def __init__(self, group: FocusGroup, index: int, value: str) -> None:
            super().__init__()
            self.group = group
            self.index = index
            self.value = value

    def __init__(self, options, *, initial: int = 0, **kw) -> None:
        # options: list of (value, label)
        super().__init__(**kw)
        self._options = list(options)
        self._idx = max(0, min(initial, max(0, len(self._options) - 1)))
        self._focused = False

    @property
    def value(self) -> str:
        return self._options[self._idx][0]

    def compose(self) -> ComposeResult:
        # A child Static measures its own content width (a bare Widget.render()
        # does not, and Textual then clips the row) -- so the button row renders
        # in full.
        yield Static(self._row(), id="fg-row")

    def _row(self) -> Text:
        t = Text()
        for i, (_v, label) in enumerate(self._options):
            if i:
                t.append("   ")
            focused = self._focused and i == self._idx
            t.append(f" {label} ", style=C_BTN_SEL if focused else C_BTN)
        return t

    def _refresh_row(self) -> None:
        self.query_one("#fg-row", Static).update(self._row())

    def action_move(self, delta: int) -> None:
        if self._options:
            self._idx = (self._idx + delta) % len(self._options)
            self._refresh_row()

    def action_activate(self) -> None:
        if self._options:
            v, _label = self._options[self._idx]
            self.post_message(self.Activated(self, self._idx, v))

    def on_focus(self) -> None:
        self._focused = True
        self._refresh_row()

    def on_blur(self) -> None:
        self._focused = False
        self._refresh_row()

class _ScopeSelectionList(SelectionList):
    """SelectionList that toggles the highlighted option directly."""

    def action_select(self) -> None:
        highlighted = self.highlighted
        if highlighted is not None:
            self.toggle(self.get_option_at_index(highlighted))

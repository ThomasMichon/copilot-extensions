#!/usr/bin/env python3
"""Shared confirm-before-run gate for a ``confirm: true`` contributed action
(#6044).

Split into its own module (rather than living in ``engine_dialogs.py``
alongside its sibling modal screens) purely to keep every touched module
under ``tools/check-module-size.py``'s cap -- ``engine_dialogs.py`` and
``engine_worktree_actions.py`` were already at or near their 1000-line
ceiling. ``ActionConfirmScreen`` is a plain Textual ``ModalScreen`` like any
other in ``engine_dialogs.py``; it has no dependency of its own on living
here.

Three independent dispatch paths each accept a manifest-declared ``confirm``
field -- :class:`~picker_tui.pivot_manifest.PivotAction` (a registered
pivot's Enter sub-menu task action, run via ``engine_pivot_actions``'s
``_run_task_action``), :class:`~picker_tui.pivot_actions.WorktreeAction` (a
worktree row's contributed Enter sub-menu verb, run via
``engine_worktree_actions``'s ``_run_wt_action``), and
:class:`~picker_tui.pivot_actions.ConfigSection` (a ⚙ Configuration menu
entry, run via ``engine_pivot_actions``'s ``_run_config_section``). All three
dataclasses parse and schema-validate ``confirm``, but historically no
dispatch path actually enforced it before running the action -- a plugin
author declaring ``confirm: true`` on a destructive verb (e.g.
``plugins/agent-dispatch/pivots/agent-dispatch.json``'s own
pause/unpause/force-stop/abandon actions) got silent, false protection; the
action ran immediately on Enter. :func:`confirm_then` is the single shared
enforcement point all three call sites funnel through.
"""
from __future__ import annotations

from collections.abc import Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from .engine_focus import FocusGroup
from .engine_helpers import C_HEADER


class ActionConfirmScreen(ModalScreen[bool]):
    """Native modal are-you-sure for a ``confirm: true`` contributed action.

    Returns ``dismiss(True)`` to proceed, ``dismiss(False)``/Esc/q to cancel.
    Deliberately NOT ``CreateActionScreen._show_confirm_prompt``'s inline
    shape (that composes a prompt onto an already-open fields screen); this
    is a standalone modal for callers with no existing screen to compose
    onto (a worktree/task Enter sub-menu dismiss, or a Configuration section
    selection).

    **Native-focus internals (#88 NF1):** the ``[Confirm] [Cancel]`` row is a
    :class:`FocusGroup` (one tab-stop; ◀▶ to choose, Enter/Space to
    activate). *Cancel* is the group's initial choice, so a reflexive Enter
    never runs the action. Esc/q cancel via ``BINDINGS``.
    """

    CSS = """
    ActionConfirmScreen { align: center middle; background: $background 55%; }
    ActionConfirmScreen > #action-confirm-frame {
        width: 56; height: auto; border: round #ffaf00;
        background: $surface; padding: 1 2;
    }
    ActionConfirmScreen #action-confirm-prompt { height: auto; padding: 0 0 1 0; }
    ActionConfirmScreen FocusGroup { height: auto; }
    """
    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("q", "cancel", show=False),
    ]

    def __init__(self, label: str) -> None:
        super().__init__()
        self._label = label

    def compose(self) -> ComposeResult:
        with Vertical(id="action-confirm-frame"):
            yield Static(Text(f"Proceed with {self._label!r}?", style=C_HEADER),
                         id="action-confirm-prompt")
            yield FocusGroup([("confirm", "Confirm"), ("cancel", "Cancel")],
                             initial=1, id="action-confirm-buttons")

    def on_mount(self) -> None:
        self.query_one("#action-confirm-frame", Vertical).border_title = "Confirm"
        # The FocusGroup composes its own child, so it may not be mounted yet at
        # screen on_mount; defer the focus until the layout settles.
        self.call_after_refresh(
            lambda: self.query_one("#action-confirm-buttons", FocusGroup).focus())

    def on_focus_group_activated(self, event: FocusGroup.Activated) -> None:
        self.dismiss(event.value == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


def confirm_then(app: App, action, run: Callable[[], None]) -> None:
    """Run ``run()`` immediately, unless ``action.confirm`` is set -- then
    push :class:`ActionConfirmScreen` first and only run it when confirmed.

    The one shared enforcement point for every ``confirm: true`` dispatch
    path (see the module docstring); callers never duplicate the gate logic.
    """
    if not getattr(action, "confirm", False):
        run()
        return

    def _confirmed(ok: bool) -> None:
        if ok:
            run()

    app.push_screen(ActionConfirmScreen(action.label), _confirmed)

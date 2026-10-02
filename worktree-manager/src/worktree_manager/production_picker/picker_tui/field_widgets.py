"""Submit-semantics-agnostic field-spec -> widget rendering.

A dependency-free base layer (no import of ``.steering`` or
``.steering_form``, which both import *from* here) -- ``PivotFormScreen``
(the Steer surface) and any field-spec-driven form (e.g. a plain
creation-prompt screen that has no Confirm/Save/Reset/draft semantics at
all) both call :func:`compose_field` to turn one field spec (``{"name",
"type", "options", "allow_other", ...}``) into the widget(s) a caller yields
from its own ``compose``, plus a lightweight ``rec`` dict of widget
references the caller uses afterward for value collection/restore. This
module knows nothing about submission, drafts, or conditional visibility --
that bookkeeping stays with each caller.
"""

from __future__ import annotations

from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Input, RadioButton, RadioSet, SelectionList, TextArea

#: Internal sentinels for the "Other…" affordance (never a real option value).
_OTHER_SENTINEL = "\x00other"
_OTHER_LABEL = "Other…"


class _AutoExpandTextArea(TextArea):
    """A docked steer input that grows with its content and follows the
    Copilot-CLI editing mechanic.

    * Height is CSS ``auto`` (bounded by ``min_height``/``max_height``), so the
      box grows one row per line of content and caps out (then scrolls) -- no
      manual line math, no off-by-one.
    * **Enter accepts + advances** focus to the next field (or the button row on
      the last one); **Shift+Enter inserts a newline** (grow the box) -- matching
      the operator's Copilot-CLI muscle memory. Windows Terminal emits ``ESC``+
      ``CR`` for Shift+Enter (not the Kitty ``\\x1b[13;2u`` form), which Textual
      would otherwise collapse to a plain ``enter``; ``_register_shift_enter_key``
      (module scope, ``engine_helpers.py``) restores the distinct
      ``shift+enter`` key. **Alt+Enter** and **Ctrl+J** remain wired as newline
      fallbacks for terminals that report those distinctly instead.
    * **Ctrl+Left / Ctrl+Right switch tabs** even while the box has focus: a
      ``TextArea`` natively binds these to cursor word-movement, which would
      otherwise swallow them before the screen's tab bindings fire, so we forward
      them to the screen's ``next_tab`` / ``prev_tab`` actions here."""

    def __init__(self, *args, min_height: int = 3, max_height: int = 12, **kw) -> None:
        super().__init__(*args, **kw)
        self._min_height = min_height
        self._max_height = max_height

    def on_mount(self) -> None:
        self.styles.height = "auto"
        self.styles.min_height = self._min_height
        self.styles.max_height = self._max_height

    def autosize(self) -> None:
        # Back-compat no-op: CSS ``height: auto`` now does the growing.
        return

    def on_key(self, event) -> None:
        key = event.key
        if key in ("ctrl+left", "ctrl+right"):
            # Tab-switching wins over the TextArea's native word-movement while a
            # box is focused (operator request): the TextArea would otherwise
            # consume these as cursor_word_left/right and the screen's ctrl+←/→
            # tab bindings would never fire. Forward to the screen's tab actions.
            event.prevent_default()
            event.stop()
            action = "action_next_tab" if key == "ctrl+right" else "action_prev_tab"
            fn = getattr(self.screen, action, None)
            if callable(fn):
                fn()
        elif key in ("shift+enter", "alt+enter", "ctrl+j"):
            # Insert a newline (grow the box). ``shift+enter`` is the primary
            # mechanic, but a mux (psmux) or a terminal without the enhanced
            # keyboard protocol collapses it to a bare ``enter`` -- so ``alt+enter``
            # and ``ctrl+j`` (LF, distinct from CR/enter in Textual) are wired as
            # always-distinguishable, mux-safe fallbacks.
            event.prevent_default()
            event.stop()
            self.insert("\n")
        elif key == "enter":
            # Accept + advance (Copilot-CLI mechanic) -- do NOT insert a newline.
            # Using the public on_key handler (not the private _on_key) keeps this
            # robust across Textual upgrades.
            event.prevent_default()
            event.stop()
            adv = getattr(self.screen, "_advance_focus", None)
            if callable(adv):
                adv(self)


class _AdvancingInput(Input):
    """A single-line ``text`` field with the same Enter-to-advance /
    Ctrl+Left/Right-tab-switch keyboard flow ``_AutoExpandTextArea`` gives
    multi-line fields. Textual's plain ``Input`` natively binds Ctrl+Left/
    Right to word-cursor movement and Enter only to its own ``Submitted``
    message, neither forwarded to the screen -- without this, a ``text``
    field could not follow a multi-field form's documented keyboard flow."""

    def on_key(self, event) -> None:
        key = event.key
        if key in ("ctrl+left", "ctrl+right"):
            event.prevent_default()
            event.stop()
            action = "action_next_tab" if key == "ctrl+right" else "action_prev_tab"
            fn = getattr(self.screen, action, None)
            if callable(fn):
                fn()
        elif key == "enter":
            event.prevent_default()
            event.stop()
            adv = getattr(self.screen, "_advance_focus", None)
            if callable(adv):
                adv(self)


class _SteerRadioSet(RadioSet):
    """A single-select that keeps ``Space`` = toggle-and-stay but makes ``Enter``
    = toggle-**and-advance** (the steer form's keyboard flow). Enter that lands on
    "Other…" focuses the revealed free-text box instead of advancing."""

    BINDINGS = [
        Binding("enter", "toggle_and_advance", show=False),
        Binding("space", "toggle_button", show=False),
    ]

    def action_toggle_and_advance(self) -> None:
        self.action_toggle_button()
        # The selection settles asynchronously (pressed_index updates after the
        # button's Changed message), so defer the advance until after refresh.
        self.call_after_refresh(self._notify_advance)

    def _notify_advance(self) -> None:
        adv = getattr(self.screen, "_advance_after_choice", None)
        if callable(adv):
            adv(self)


class _SteerSelectionList(SelectionList):
    """A multi-select where ``Space`` toggles-and-stays (pick several) and
    ``Enter`` toggles the highlighted option **and advances**. Enter that toggles
    "Other…" on focuses the revealed free-text box."""

    BINDINGS = [
        Binding("enter", "toggle_and_advance", show=False),
    ]

    def action_toggle_and_advance(self) -> None:
        self.action_select()
        self.call_after_refresh(self._notify_advance)

    def _notify_advance(self) -> None:
        adv = getattr(self.screen, "_advance_after_choice", None)
        if callable(adv):
            adv(self)


def field_label(f: dict) -> str:
    """Humanized field name, e.g. ``"prompt"`` -> ``"Prompt"``."""
    return str(f["name"]).replace("_", " ").capitalize()


def compose_field(f: dict, i: int) -> tuple[list[Widget], dict]:
    """Render one field spec's input widget(s) (never its question label --
    callers own their own label styling/text).

    Returns ``(widgets, rec)``: ``widgets`` is the ordered list to ``yield``
    from the caller's ``compose``; ``rec`` is ``{"name", "type", "options",
    "allow_other", "primary", "other"}`` -- the same shape
    ``PivotFormScreen._q`` has always stored, minus the caller-owned
    ``show_when``/``visible`` keys a conditional-fields caller adds itself.
    ``rec["other"]`` is the optional "Other…" free-text box for a
    ``choice``/``multichoice`` field with ``allow_other`` set, else ``None``.
    """
    name, ftype = f["name"], f["type"]
    options = list(f.get("options", []))
    allow_other = bool(f.get("allow_other"))
    rec: dict = {
        "name": name,
        "type": ftype,
        "options": options,
        "allow_other": allow_other,
        "primary": None,
        "other": None,
    }
    widgets: list[Widget] = []
    if ftype == "choice":
        btns = [RadioButton(o, value=(j == 0)) for j, o in enumerate(options)]
        if allow_other:
            btns.append(RadioButton(_OTHER_LABEL))
        rs = _SteerRadioSet(*btns, id=f"q-{i}")
        rec["primary"] = rs
        widgets.append(rs)
        if allow_other:
            other = _AutoExpandTextArea(id=f"other-{i}")
            other.display = False
            rec["other"] = other
            widgets.append(other)
    elif ftype == "multichoice":
        sels = [(o, o) for o in options]
        if allow_other:
            sels.append((_OTHER_LABEL, _OTHER_SENTINEL))
        sl = _SteerSelectionList(*sels, id=f"q-{i}")
        rec["primary"] = sl
        widgets.append(sl)
        if allow_other:
            other = _AutoExpandTextArea(id=f"other-{i}")
            other.display = False
            rec["other"] = other
            widgets.append(other)
    else:  # text -> single-line Input; textarea -> free-form auto-expand box
        if ftype == "text":
            w: Widget = _AdvancingInput(id=f"q-{i}")
        else:
            w = _AutoExpandTextArea(id=f"q-{i}")
        rec["primary"] = w
        widgets.append(w)
    return widgets, rec

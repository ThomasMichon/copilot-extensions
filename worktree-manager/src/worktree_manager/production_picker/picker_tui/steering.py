"""Steering/card picker surfaces mechanically extracted from ``engine.py``.

This module exists only to control ``engine.py`` module size. The extracted
code was moved verbatim with no behavior change.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    RadioSet,
    SelectionList,
    Static,
    TextArea,
)

from .engine_focus import FocusGroup
from .engine_helpers import C_BTN, C_BTN_SEL, C_HEADER


def _normalize_form_fields(raw: object) -> list[dict]:
    """Normalize a request-input spec into a clean field list for the form.

    Accepts the ``[{name, type, options?, allow_other?}]`` shape
    ``steering.parse_request_input`` produces (surfaced on a task's
    ``card.request_input``). Drops non-dict / un-named entries, defaults an
    unknown/absent type to ``text``, keeps only a non-empty ``options`` list for
    a choice/multichoice (an empty one degrades to a free-text field), and
    carries ``allow_other`` (the "Other…" affordance) and a valid
    ``show_when={"field", "equals"}`` choice predicate. Never raises -- a
    malformed spec degrades to a shorter (or empty) form, so the modal always
    renders."""
    if not isinstance(raw, list):
        return []
    valid_types = {"text", "textarea", "choice", "multichoice"}
    choice_types = {"choice", "multichoice"}
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        ftype = item.get("type")
        ftype = ftype if isinstance(ftype, str) and ftype in valid_types else "text"
        field: dict = {"name": name.strip(), "type": ftype}
        if ftype in choice_types:
            opts = item.get("options")
            options = [str(o) for o in opts if str(o).strip()] if isinstance(opts, list) else []
            if not options:
                # A choice with no options can't be answered -- treat as text.
                field["type"] = "text"
            else:
                field["options"] = options
                if item.get("allow_other"):
                    field["allow_other"] = True
        condition = item.get("show_when")
        if isinstance(condition, dict):
            source = condition.get("field")
            expected = condition.get("equals")
            if (
                isinstance(source, str)
                and source.strip()
                and isinstance(expected, str)
                and expected.strip()
            ):
                field["show_when"] = {
                    "field": source.strip(),
                    "equals": expected.strip(),
                }
        out.append(field)
    by_name = {field["name"]: field for field in out}
    for field in out:
        condition = field.get("show_when")
        if not condition:
            continue
        controller = by_name.get(condition["field"])
        if (
            controller is None
            or controller is field
            or controller.get("type") != "choice"
            or controller.get("show_when")
            or condition["equals"] not in (controller.get("options") or [])
        ):
            field.pop("show_when", None)
    return out


def _escape_markdown_inline(value: object) -> str:
    """Escape card metadata before embedding it in the Markdown wrapper."""
    return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", str(value))


def _card_markdown(card: dict, fallback_title: str = "") -> str:
    """Compose card metadata and its Markdown body into one document."""
    parts: list[str] = []
    title = card.get("title") or fallback_title
    if title:
        parts.append(f"# {_escape_markdown_inline(title)}")
    status = card.get("status")
    if status:
        parts.append(f"*Status:* **{status}**")
    link = card.get("link")
    if link:
        parts.append(f"*Link:* <{str(link).strip()}>")
    body = card.get("body")
    if body:
        if parts:
            parts.append("---")
        parts.append(str(body))
    if not parts:
        parts.append("*(empty card)*")
    return "\n\n".join(parts)


class PivotCardScreen(ModalScreen[None]):
    """Read-only scrollable card-detail modal (A5 -- the DISPATCH pivot 'card').

    Renders the card a blocked worker posted -- its title, one-line status, an
    optional link to the rich artifact, and a scrollable body -- so the operator
    can read the full brief before steering. Purely informational: no field
    entry, no subprocess. Esc/q/Enter close; ↑/↓/PgUp/PgDn scroll the body (the
    native ``VerticalScroll`` owns scrolling once focused)."""

    CSS = """
    PivotCardScreen { align: center middle; background: $background 55%; }
    PivotCardScreen > #card-frame {
        width: 92; height: auto; max-height: 90%;
        border: round #ffaf00; background: $surface; padding: 0 1;
    }
    PivotCardScreen #card-scroll { height: auto; max-height: 24; }
    PivotCardScreen Markdown { background: $surface; padding: 0 1; }
    PivotCardScreen MarkdownH1 {
        content-align: left middle; color: #ffaf00; background: $surface;
    }
    PivotCardScreen MarkdownH2, PivotCardScreen MarkdownH3 { color: #4aa3ff; }
    PivotCardScreen MarkdownBlock > .strong { color: #ffaf00; text-style: bold; }
    PivotCardScreen MarkdownBlock > .em { color: #a3a3a3; text-style: italic; }
    PivotCardScreen MarkdownBlockQuote {
        background: #17212b; border-left: outer #4aa3ff;
    }
    PivotCardScreen #card-foot { color: grey; height: auto; padding: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("q", "close", show=False),
        Binding("enter", "close", show=False),
    ]

    def __init__(self, row_title: str, card: dict) -> None:
        super().__init__()
        self._row_title = row_title
        self._card = card or {}

    def compose(self) -> ComposeResult:
        # Deferred (picker-startup-latency follow-up): Textual's Markdown
        # widget pulls in the markdown-it-py parser, a ~100ms import cost this
        # module previously paid on EVERY picker start even though a card is
        # only ever composed when the operator explicitly opens one. Local
        # import defers that cost to the first actual card open.
        from textual.widgets import Markdown

        with Vertical(id="card-frame"):
            with VerticalScroll(id="card-scroll"):
                yield Markdown(
                    _card_markdown(self._card, self._row_title),
                    id="card-body",
                )
            yield Static("↑/↓ scroll · Esc close", id="card-foot")

    def on_mount(self) -> None:
        self.query_one("#card-frame", Vertical).border_title = "Card"
        self.query_one("#card-scroll", VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)


#: Env override for the steer-draft directory (tests / operator escape hatch).
_STEER_DRAFTS_ENV = "AGENT_WORKTREES_STEER_DRAFTS"
#: Internal sentinels for the "Other…" affordance (never a real option value).
_OTHER_SENTINEL = "\x00other"
_OTHER_LABEL = "Other…"


def _steer_drafts_dir() -> Path:
    """Directory holding saved steer drafts (partial answers). Overridable via
    ``AGENT_WORKTREES_STEER_DRAFTS``; defaults to ``~/.agent-worktrees/steer-drafts``."""
    override = os.environ.get(_STEER_DRAFTS_ENV)
    return Path(override) if override else Path.home() / ".agent-worktrees" / "steer-drafts"


def _steer_draft_path(task_id: str) -> Path | None:
    """Per-task draft file path (``<drafts>/<sanitized-task-id>.json``), or
    ``None`` when the task id has no filesystem-safe characters."""
    tid = "".join(c for c in str(task_id) if c.isalnum() or c in "-_")
    return _steer_drafts_dir() / f"{tid}.json" if tid else None


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
      (module scope) restores the distinct ``shift+enter`` key. **Alt+Enter** and
      **Ctrl+J** remain wired as newline fallbacks for terminals that report those
      distinctly instead.
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


class SteerButtonRow(Widget):
    """A single-line row of Picker-style buttons (Confirm/Save/Cancel), focusable
    as one region: ←/→ move the cursor, Enter/Space press. Matches the picker's
    ``C_BTN``/``C_BTN_SEL`` button aesthetic rather than the heavier Textual
    ``Button`` widget. Calls ``on_press(key)`` when a button is pressed."""

    can_focus = True

    def __init__(self, buttons: list[tuple[str, str]], on_press, **kw) -> None:
        super().__init__(**kw)
        self._buttons = list(buttons)  # [(key, label), ...]
        self._on_press = on_press
        self._idx = 0

    def render(self):
        t = Text()
        for i, (_key, label) in enumerate(self._buttons):
            if i:
                t.append("  ")
            focused = self.has_focus and i == self._idx
            t.append(f" {label} ", style=C_BTN_SEL if focused else C_BTN)
        return t

    def on_key(self, event) -> None:
        if event.key == "left":
            self._idx = (self._idx - 1) % len(self._buttons)
            self.refresh()
            event.stop()
        elif event.key == "right":
            self._idx = (self._idx + 1) % len(self._buttons)
            self.refresh()
            event.stop()
        elif event.key in ("enter", "space"):
            self._on_press(self._buttons[self._idx][0])
            event.stop()

    def press(self, key: str) -> None:
        """Programmatic press (used by tests / click)."""
        self._on_press(key)

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()

    def on_click(self, event) -> None:
        event.stop()
        self.focus()
        # Hit-test the click against each button's rendered span so a mouse
        # press acts on the button under the cursor. Any exact-span miss (an
        # off-by-one in the computed span vs. the actual rendered position,
        # a click landing in the inter-button gap, or past the last button)
        # falls back to the *nearest* button rather than silently doing
        # nothing -- a click inside this row must always press exactly one
        # button; a coordinate near-miss must never be indistinguishable
        # from "nothing was clicked" (see the Picker steer Confirm/Save
        # unreliability report).
        x = int(getattr(event, "x", 0))
        spans: list[tuple[int, int]] = []
        pos = 0
        for i, (_key, label) in enumerate(self._buttons):
            if i:
                pos += 2  # the "  " separator between buttons
            width = len(label) + 2  # the " label " span
            spans.append((pos, pos + width))
            pos += width
        chosen = 0
        for i, (start, end) in enumerate(spans):
            if start <= x < end:
                chosen = i
                break
        else:
            def _distance(span: tuple[int, int]) -> int:
                start, end = span
                if x < start:
                    return start - x
                if x >= end:
                    return x - end + 1
                return 0

            chosen = min(range(len(spans)), key=lambda i: _distance(spans[i]))
        self._idx = chosen
        self.refresh()
        self._on_press(self._buttons[chosen][0])


class ResetConfirmScreen(ModalScreen[bool]):
    """Are-you-sure gate for the steer form's Reset button.

    Mirrors :class:`QuitConfirmScreen`'s FocusGroup pattern: returns its
    verdict via ``dismiss(bool)`` (``True`` reset, ``False`` stays), with
    *Stay* as the initial choice so a reflexive Enter never wipes the form.
    """

    CSS = """
    ResetConfirmScreen { align: center middle; background: $background 55%; }
    ResetConfirmScreen > #reset-frame {
        width: 52; height: auto; border: round #ffaf00;
        background: $surface; padding: 1 2;
    }
    ResetConfirmScreen #reset-prompt { height: auto; padding: 0 0 1 0; }
    ResetConfirmScreen FocusGroup { height: auto; }
    ResetConfirmScreen #reset-hint { color: grey; height: auto; padding: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("y", "reset", show=False),
        Binding("n", "stay", show=False),
        Binding("escape", "stay", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="reset-frame"):
            yield Static(
                Text(" Clear every field on this steer form?", style=C_HEADER),
                id="reset-prompt",
            )
            yield FocusGroup([("reset", "Reset"), ("stay", "Stay")], initial=1,
                             id="reset-buttons")
            yield Static("y reset · n/Esc stay · ←/→ choose", id="reset-hint")

    def on_mount(self) -> None:
        self.query_one("#reset-frame", Vertical).border_title = "Reset the form?"
        self.call_after_refresh(
            lambda: self.query_one("#reset-buttons", FocusGroup).focus())

    def on_focus_group_activated(self, event: FocusGroup.Activated) -> None:
        self.dismiss(event.value == "reset")

    def action_reset(self) -> None:
        self.dismiss(True)

    def action_stay(self) -> None:
        self.dismiss(False)


class SubmitErrorScreen(ModalScreen[None]):
    """Fail-fast blocking error surface for a form action's async submission
    (e.g. the Steer form's Confirm).

    A form action dismisses its input modal immediately and submits
    off-thread (see ``_run_pivot_form_submit``); if that submission cannot be
    *delivered* -- the command was not found, exited non-zero, raised, or the
    coordinator round-trip otherwise failed -- that must never be reducible to
    an easily-missed footer status-line message the very next background
    refresh can silently overwrite (see
    ThomasMichon/copilot-extensions#2453). This modal blocks until explicitly
    acknowledged, so a delivery failure cannot pass unnoticed, and it always
    names where the operator's answer is still safely recoverable from -- the
    on-disk steer draft, which Confirm/Save/Esc all persist *before* the
    submission ever runs and which a failed submission never clears."""

    CSS = """
    SubmitErrorScreen { align: center middle; background: $background 55%; }
    SubmitErrorScreen > #submit-error-frame {
        width: 68; height: auto; border: round #ff5f5f;
        background: $surface; padding: 1 2;
    }
    SubmitErrorScreen #submit-error-title { height: auto; padding: 0 0 1 0; }
    SubmitErrorScreen #submit-error-body { height: auto; padding: 0 0 1 0; }
    SubmitErrorScreen #submit-error-hint { color: grey; height: auto; }
    """
    BINDINGS = [
        Binding("enter", "ack", show=False),
        Binding("escape", "ack", show=False),
        Binding("space", "ack", show=False),
    ]

    def __init__(self, label: str, detail: str, draft_path: Path | None) -> None:
        super().__init__()
        self._label = label
        self._detail = detail or "see command output"
        self._draft_path = draft_path

    def compose(self) -> ComposeResult:
        with Vertical(id="submit-error-frame"):
            yield Static(
                Text(f" {self._label} could not be delivered", style=C_HEADER),
                id="submit-error-title",
            )
            body = self._detail
            if self._draft_path is not None:
                body += (
                    f"\n\nYour answer was NOT lost -- it was saved to:\n"
                    f"{self._draft_path}\n"
                    "It will be restored the next time this card is opened, "
                    "so it is safe to retry."
                )
            yield Static(body, id="submit-error-body")
            yield Static("Enter / Esc / Space to dismiss", id="submit-error-hint")

    def on_mount(self) -> None:
        self.query_one("#submit-error-frame", Vertical).border_title = "Delivery failed"
        self.focus()

    def action_ack(self) -> None:
        self.dismiss(None)



from .steering_form import PivotFormScreen  # noqa: F401,E402 -- re-export for engine.py


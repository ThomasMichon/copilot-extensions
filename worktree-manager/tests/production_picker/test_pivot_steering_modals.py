"""Headless Pilot tests for the DISPATCH-pivot steering modals.

Covers the read-only ``PivotCardScreen`` and the redesigned ``PivotFormScreen``
-- the docked card + tabbed-elicitation surface: card prose on top, a docked
question section (single/multi-select with an "Other…" reveal, free-form
auto-expanding boxes), single-line Confirm/Save/Cancel buttons, and resumable
Save drafts. The screen only gathers the operator's answer (returned via
``dismiss``); it never runs a command and never carries a verdict.
"""
from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("textual", reason="textual not installed (optional TUI dep)")

from textual.widgets import Markdown, RadioButton, RadioSet, SelectionList  # noqa: E402
from textual.app import App  # noqa: E402

from worktree_manager.production_picker.picker_tui.engine import (  # noqa: E402
    PivotCardScreen,
    PivotFormScreen,
    _AutoExpandTextArea,
    _normalize_form_fields,
    _steer_draft_path,
)


class _Host(App):
    """A bare host that pushes one modal and records its dismiss result."""

    def __init__(self, modal) -> None:
        super().__init__()
        self._modal = modal
        self.result = "UNSET"

    def on_mount(self) -> None:
        self.push_screen(self._modal, self._done)

    def _done(self, r) -> None:
        self.result = r


def _drafts(monkeypatch, tmp_path):
    d = tmp_path / "steer-drafts"
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(d))
    return d


def _card():
    return {
        "title": "Review PR 123",
        "status": "recommend post-approved",
        "link": "https://example.test/pr/123",
        "body": (
            "> *Recommended verdict:* **APPROVE**\n\n"
            "> The exact postable comment.\n\n"
            "*Rationale: this follows the local contract.*"
        ),
    }


def _fields():
    return [
        {"name": "feedback", "type": "textarea"},
        {"name": "decision", "type": "choice",
         "options": ["revise", "post-approved", "hold"]},
        {"name": "severity", "type": "choice", "options": ["low", "high"],
         "allow_other": True},
        {"name": "tags", "type": "multichoice", "options": ["perf", "api", "ux"],
         "allow_other": True},
    ]


# ---- PivotCardScreen (read-only) --------------------------------------------


def test_form_text_field_is_single_line_input(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    from textual.widgets import Input
    scr = PivotFormScreen(_card(), [{"name": "ref", "type": "text"}],
                          "Steer", task_id="t-text")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            inp = scr.query_one("#q-0", Input)   # text -> single-line Input
            inp.value = "PR 42"
            scr._confirm()
            await pilot.pause()

    asyncio.run(run())
    assert app.result == {"ref": "PR 42"}


def test_card_renders_parts_and_closes():
    scr = PivotCardScreen("Row Title", _card())
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, PivotCardScreen)
            markdown = scr.query_one("#card-body", Markdown)
            assert "Review PR 123" in markdown.source
            assert "*Recommended verdict:* **APPROVE**" in markdown.source
            assert scr.query("MarkdownBlockQuote")
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, PivotCardScreen)

    asyncio.run(run())
    assert app.result is None


# ---- PivotFormScreen: layout + collection -----------------------------------


def test_form_shows_card_prose_and_tabs(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), _fields(), "Steer", task_id="t-layout")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            markdown = scr.query_one("#steer-card-body", Markdown)
            assert "Review PR 123" in markdown.source
            assert "*Recommended verdict:* **APPROVE**" in markdown.source
            assert scr.query("MarkdownBlockQuote")
            from textual.widgets import TabbedContent
            assert scr.query(TabbedContent)   # >1 question -> a tab bar exists

    asyncio.run(run())


def test_form_single_question_has_no_tabs(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), [{"name": "feedback", "type": "textarea"}],
                          "Steer", task_id="t-single")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            from textual.widgets import TabbedContent
            assert not scr.query(TabbedContent)
            assert scr.query_one("#q-0", _AutoExpandTextArea) is not None

    asyncio.run(run())


def test_form_collect_all_types_on_confirm(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), _fields(), "Steer", task_id="t-collect")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            scr.query_one("#q-0", _AutoExpandTextArea).text = "looks good"
            # decision default is index 0 -> "revise"
            sev = scr.query_one("#q-2", RadioSet)
            list(sev.query(RadioButton))[2].value = True  # the "Other…" radio
            await pilot.pause()
            assert scr.query_one("#other-2", _AutoExpandTextArea).display is True
            scr.query_one("#other-2", _AutoExpandTextArea).text = "in between"
            tags = scr.query_one("#q-3", SelectionList)
            tags.select(tags.get_option_at_index(0))   # perf
            tags.select(tags.get_option_at_index(1))   # api
            await pilot.pause()
            scr._confirm()
            await pilot.pause()

    asyncio.run(run())
    assert app.result == {
        "feedback": "looks good",
        "decision": "revise",
        "severity": "in between",
        "tags": ["perf", "api"],
    }


def test_choice_gated_followup_and_separate_verdict(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    fields = [
        {"name": "comments", "type": "choice", "options": ["Accept", "Reject"]},
        {
            "name": "reason",
            "type": "textarea",
            "show_when": {"field": "comments", "equals": "Reject"},
        },
        {
            "name": "verdict",
            "type": "choice",
            "options": ["Approve", "Waiting for author", "Reject"],
            "show_when": {"field": "comments", "equals": "Accept"},
        },
    ]
    scr = PivotFormScreen(_card(), fields, "Steer", task_id="t-conditional")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            from textual.widgets import TabbedContent

            tabs = scr.query_one("#steer-tabs", TabbedContent)
            assert tabs.get_tab("tab-1").display is False
            assert scr._collect() == {"comments": "Accept", "verdict": "Approve"}

            feedback = scr.query_one("#q-0", RadioSet)
            list(feedback.query(RadioButton))[1].value = True
            await pilot.pause()
            await pilot.pause()
            assert tabs.get_tab("tab-1").display is True
            scr.query_one("#q-1", _AutoExpandTextArea).text = "Drop comment 2."
            scr._confirm()
            await pilot.pause()

    asyncio.run(run())
    assert app.result == {
        "comments": "Reject",
        "reason": "Drop comment 2.",
    }


def test_choice_gated_followup_keyboard_skips_hidden_tab(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    fields = [
        {"name": "comments", "type": "choice", "options": ["Accept", "Reject"]},
        {
            "name": "reason",
            "type": "textarea",
            "show_when": {"field": "comments", "equals": "Reject"},
        },
        {
            "name": "verdict",
            "type": "choice",
            "options": ["Approve", "Reject"],
            "show_when": {"field": "comments", "equals": "Accept"},
        },
    ]
    scr = PivotFormScreen(_card(), fields, "Steer", task_id="t-conditional-flow")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            scr.query_one("#q-0", RadioSet).focus()
            await pilot.press("enter")
            await pilot.pause()
            assert app.focused.id == "q-2"

    asyncio.run(run())


def test_invalid_condition_degrades_to_visible_field():
    fields = _normalize_form_fields([
        {
            "name": "reason",
            "type": "textarea",
            "show_when": {"field": "missing", "equals": "Reject"},
        },
    ])
    assert fields == [{"name": "reason", "type": "textarea"}]


def test_form_multichoice_other_free_member(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    fields = [{"name": "tags", "type": "multichoice", "options": ["perf", "api"],
               "allow_other": True}]
    scr = PivotFormScreen(_card(), fields, "Steer", task_id="t-multi-other")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            sl = scr.query_one("#q-0", SelectionList)
            sl.select(sl.get_option_at_index(0))          # perf
            sl.select(sl.get_option_at_index(2))          # the "Other…" sentinel
            await pilot.pause()
            assert scr.query_one("#other-0", _AutoExpandTextArea).display is True
            scr.query_one("#other-0", _AutoExpandTextArea).text = "custom tag"
            scr._confirm()
            await pilot.pause()

    asyncio.run(run())
    assert app.result == {"tags": ["perf", "custom tag"]}


# ---- Save / restore / cancel / escape ---------------------------------------


def test_form_save_writes_draft_and_restores(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), _fields(), "Steer", task_id="t-draft")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            scr.query_one("#q-0", _AutoExpandTextArea).text = "partial answer"
            list(scr.query_one("#q-1", RadioSet).query(RadioButton))[1].value = True
            await pilot.pause()
            scr.action_save()
            await pilot.pause()

    asyncio.run(run())
    assert app.result is None  # Save does not submit
    assert _steer_draft_path("t-draft").exists()

    scr2 = PivotFormScreen(_card(), _fields(), "Steer", task_id="t-draft")
    app2 = _Host(scr2)

    async def run2():
        async with app2.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            assert scr2.query_one("#q-0", _AutoExpandTextArea).text == "partial answer"
            assert scr2.query_one("#q-1", RadioSet).pressed_index == 1

    asyncio.run(run2())


def test_form_confirm_clears_draft(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), [{"name": "feedback", "type": "textarea"}],
                          "Steer", task_id="t-clear")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            scr._write_draft()  # persist a draft without closing
            await pilot.pause()
            assert _steer_draft_path("t-clear").exists()
            scr._confirm()
            await pilot.pause()

    asyncio.run(run())
    assert isinstance(app.result, dict)
    assert not _steer_draft_path("t-clear").exists()


def test_form_cancel_discards_draft(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), [{"name": "feedback", "type": "textarea"}],
                          "Steer", task_id="t-cancel")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            scr._write_draft()  # persist a draft without closing
            await pilot.pause()
            assert _steer_draft_path("t-cancel").exists()
            scr._cancel()
            await pilot.pause()

    asyncio.run(run())
    assert app.result is None
    assert not _steer_draft_path("t-cancel").exists()


def test_form_escape_preserves_draft(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), [{"name": "feedback", "type": "textarea"}],
                          "Steer", task_id="t-esc")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            scr.query_one("#q-0", _AutoExpandTextArea).text = "unsaved but escaped"
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(run())
    assert app.result is None
    path = _steer_draft_path("t-esc")
    assert path.exists()   # Escape auto-saves so nothing is lost
    import json
    assert json.loads(path.read_text())["values"]["feedback"] == "unsaved but escaped"


def test_form_save_preserves_hidden_conditional_text(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    fields = [
        {"name": "comments", "type": "choice", "options": ["Accept", "Reject"]},
        {
            "name": "reason",
            "type": "textarea",
            "show_when": {"field": "comments", "equals": "Reject"},
        },
    ]
    scr = PivotFormScreen(_card(), fields, "Steer", task_id="t-hidden-draft")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            comments = scr.query_one("#q-0", RadioSet)
            buttons = list(comments.query(RadioButton))
            buttons[1].value = True
            await pilot.pause()
            scr.query_one("#q-1", _AutoExpandTextArea).text = "Keep this draft."
            buttons[0].value = True
            await pilot.pause()
            scr.action_save()
            await pilot.pause()

    asyncio.run(run())
    values = json.loads(
        _steer_draft_path("t-hidden-draft").read_text(encoding="utf-8")
    )["values"]
    assert values == {"comments": "Accept", "reason": "Keep this draft."}


# ---- button row + auto-expand -----------------------------------------------


def test_button_row_confirm_via_row(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    from worktree_manager.production_picker.picker_tui.engine import SteerButtonRow
    scr = PivotFormScreen(_card(), [{"name": "feedback", "type": "textarea"}],
                          "Steer", task_id="t-btn")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            scr.query_one("#q-0", _AutoExpandTextArea).text = "x"
            scr.query_one(SteerButtonRow).press("confirm")
            await pilot.pause()

    asyncio.run(run())
    assert app.result == {"feedback": "x"}


def test_textarea_auto_height(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), [{"name": "notes", "type": "textarea"}],
                          "Steer", task_id="t-grow")
    app = _Host(scr)
    heights = {}

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            ta = scr.query_one("#q-0", _AutoExpandTextArea)
            # CSS height:auto -> the widget sizes to content, bounded by
            # min/max-height (no manual line math, no off-by-one).
            assert str(ta.styles.height) == "auto"
            heights["one"] = ta.outer_size.height
            ta.text = "\n".join(f"line {i}" for i in range(30))
            await pilot.pause()
            heights["many"] = ta.outer_size.height

    asyncio.run(run())
    assert heights["many"] > heights["one"]     # grew with content
    assert heights["many"] <= 12                # capped by max-height


# ---- keyboard flow (Copilot-CLI mechanics) ----------------------------------


def _flow_fields():
    return [
        {"name": "decision", "type": "choice", "options": ["revise", "post-approved"]},
        {"name": "severity", "type": "choice", "options": ["low", "high"],
         "allow_other": True},
        {"name": "tags", "type": "multichoice", "options": ["perf", "api"],
         "allow_other": True},
    ]


def test_enter_advances_shift_enter_newlines(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), [{"name": "feedback", "type": "textarea"},
                                    {"name": "decision", "type": "choice",
                                     "options": ["a", "b"]}],
                          "Steer", task_id="t-flow1")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            ta = scr.query_one("#q-0", _AutoExpandTextArea)
            ta.focus()
            await pilot.pause()
            ta.text = "one"
            await pilot.press("shift+enter")     # inserts a newline (grow)
            await pilot.pause()
            assert "\n" in ta.text
            await pilot.press("enter")           # accept + advance to q-1
            await pilot.pause()
            assert app.focused.id == "q-1"

    asyncio.run(run())


def test_space_stays_enter_advances_on_radio(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), _flow_fields(), "Steer", task_id="t-flow2")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            scr.query_one("#q-0", RadioSet).focus()
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("space")           # toggle without advancing
            await pilot.pause()
            assert app.focused.id == "q-0"        # stayed
            assert scr.query_one("#q-0", RadioSet).pressed_index == 1
            await pilot.press("enter")            # toggle + advance
            await pilot.pause()
            assert app.focused.id == "q-1"

    asyncio.run(run())


def test_enter_on_other_focuses_box_then_advances(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), _flow_fields(), "Steer", task_id="t-flow3")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            sev = scr.query_one("#q-1", RadioSet)
            sev.focus()
            await pilot.pause()
            # highlight "Other…" (index 2 = after low/high), Enter -> focus the box
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert app.focused.id == "other-1"
            assert scr.query_one("#other-1", _AutoExpandTextArea).display is True
            # typing + Enter from the Other box advances to the next question
            await pilot.press("h")
            await pilot.press("enter")
            await pilot.pause()
            assert app.focused.id == "q-2"

    asyncio.run(run())


def test_newline_fallbacks_alt_enter_and_ctrl_j(monkeypatch, tmp_path):
    # A mux (psmux) / non-enhanced terminal collapses shift+enter to bare enter,
    # so alt+enter and ctrl+j must ALSO insert a newline (and never advance).
    _drafts(monkeypatch, tmp_path)
    for nl_key in ("alt+enter", "ctrl+j"):
        scr = PivotFormScreen(_card(), [{"name": "feedback", "type": "textarea"},
                                        {"name": "decision", "type": "choice",
                                         "options": ["a", "b"]}],
                              "Steer", task_id=f"t-nl-{nl_key}")
        app = _Host(scr)

        async def run(key=nl_key):
            async with app.run_test(size=(120, 45)) as pilot:
                await pilot.pause()
                ta = scr.query_one("#q-0", _AutoExpandTextArea)
                ta.focus()
                await pilot.pause()
                ta.text = "one"
                await pilot.press(key)           # inserts a newline (grow)
                await pilot.pause()
                assert "\n" in ta.text, key
                assert app.focused is ta, f"{key} must not advance focus"

        asyncio.run(run())


def test_shift_enter_esc_cr_parses_as_shift_enter():
    # Windows Terminal emits ESC+CR (\x1b\r) for Shift+Enter, which Textual would
    # otherwise collapse to a plain "enter" (indistinguishable from Enter). The
    # engine registers \x1b\r so the parser surfaces a distinct shift+enter key
    # (which _AutoExpandTextArea.on_key binds to newline). Importing the engine
    # (done at module top) applies the registration.
    from textual._xterm_parser import XTermParser

    def key_names(seq):
        p = XTermParser()
        names = []
        for msg in list(p.feed(seq)) + list(p.feed("")):
            names.append(getattr(msg, "key", type(msg).__name__))
        return names

    assert key_names("\x1b\r") == ["shift+enter"]   # Shift+Enter -> distinct key
    assert key_names("\r") == ["enter"]             # plain Enter unchanged
    assert key_names("\x1b") == ["escape"]          # lone Esc still save+close


def test_ctrl_arrows_cycle_tabs_while_textarea_focused(monkeypatch, tmp_path):
    # The real-world bug: while a text box has focus the TextArea natively
    # consumes ctrl+←/→ (word-move), so the screen's tab bindings never fire.
    # The box must forward them to the tab actions instead.
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), _fields(), "Steer", task_id="t-tabs-ta")
    app = _Host(scr)
    from textual.widgets import TabbedContent

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            tabs = scr.query_one("#steer-tabs", TabbedContent)
            scr.query_one("#q-0", _AutoExpandTextArea).focus()
            await pilot.pause()
            assert tabs.active == "tab-0"
            await pilot.press("ctrl+right")      # forwarded -> next tab
            await pilot.pause()
            assert tabs.active == "tab-1"
            await pilot.press("ctrl+left")       # forwarded -> prev tab
            await pilot.pause()
            assert tabs.active == "tab-0"

    asyncio.run(run())


def test_ctrl_arrows_cycle_tabs(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), _flow_fields(), "Steer", task_id="t-tabs")
    app = _Host(scr)
    from textual.widgets import TabbedContent

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            tabs = scr.query_one("#steer-tabs", TabbedContent)
            assert tabs.active == "tab-0"
            await pilot.press("ctrl+right")
            await pilot.pause()
            assert tabs.active == "tab-1"
            await pilot.press("ctrl+left")
            await pilot.press("ctrl+left")       # wrap backwards 0 -> 2
            await pilot.pause()
            assert tabs.active == "tab-2"

    asyncio.run(run())


def test_last_field_enter_focuses_confirm(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), [{"name": "feedback", "type": "textarea"}],
                          "Steer", task_id="t-last")
    app = _Host(scr)
    from worktree_manager.production_picker.picker_tui.engine import SteerButtonRow

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            scr.query_one("#q-0", _AutoExpandTextArea).focus()
            await pilot.pause()
            await pilot.press("enter")            # last field -> button row
            await pilot.pause()
            assert isinstance(app.focused, SteerButtonRow)

    asyncio.run(run())


def test_multichoice_space_stays_enter_advances(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    fields = [
        {"name": "tags", "type": "multichoice", "options": ["perf", "api"],
         "allow_other": True},
        {"name": "note", "type": "textarea"},
    ]
    scr = PivotFormScreen(_card(), fields, "Steer", task_id="t-mflow")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            sl = scr.query_one("#q-0", SelectionList)
            sl.focus()
            await pilot.pause()
            await pilot.press("space")            # toggle perf, stay
            await pilot.pause()
            assert list(sl.selected) == ["perf"]
            assert app.focused.id == "q-0"
            await pilot.press("down")
            await pilot.press("enter")            # toggle api + advance
            await pilot.pause()
            assert "api" in list(sl.selected)
            assert app.focused.id == "q-1"

    asyncio.run(run())


def test_multichoice_enter_on_other_focuses_box(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    fields = [{"name": "tags", "type": "multichoice", "options": ["perf", "api"],
               "allow_other": True}]
    scr = PivotFormScreen(_card(), fields, "Steer", task_id="t-mother")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            sl = scr.query_one("#q-0", SelectionList)
            sl.focus()
            await pilot.pause()
            # highlight "Other…" (index 2 = after perf/api), Enter -> focus its box
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert app.focused.id == "other-0"
            assert scr.query_one("#other-0", _AutoExpandTextArea).display is True

    asyncio.run(run())


def test_card_prose_linkifies_urls(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    # URL ends the sentence -> the trailing period must NOT be part of the link.
    card = {"title": "T", "body": "See https://ado/pr/2312460."}
    scr = PivotFormScreen(card, [{"name": "feedback", "type": "textarea"}],
                          "Steer", task_id="t-link")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            markdown = scr.query_one("#steer-card-body", Markdown)
            assert "https://ado/pr/2312460." in markdown.source
            await pilot.pause()
            # The GFM parser recognizes the URL while keeping sentence
            # punctuation outside the clickable span.
            actions = []
            for block in markdown.query("MarkdownBlock"):
                content = getattr(block, "_content", None)
                for span in getattr(content, "spans", []):
                    meta = getattr(span.style, "meta", None) or {}
                    actions.extend(str(value) for value in meta.values())
            assert any("https://ado/pr/2312460" in action for action in actions)
            assert not any("https://ado/pr/2312460." in action for action in actions)

    asyncio.run(run())

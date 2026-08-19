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

import pytest

pytest.importorskip("textual", reason="textual not installed (optional TUI dep)")

from textual.widgets import RadioButton, RadioSet, SelectionList  # noqa: E402
from textual.app import App  # noqa: E402

from agent_worktrees.picker_tui.engine import (  # noqa: E402
    PivotCardScreen,
    PivotFormScreen,
    _AutoExpandTextArea,
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
    return {"title": "Review PR 123", "status": "recommend post-approved",
            "link": "https://ado/pr/123", "body": "The full review body.\nLine two."}


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
            text = getattr(scr.query_one("#card-body").render(), "plain", "")
            assert "Review PR 123" in text and "The full review body." in text
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
            body = getattr(scr.query_one("#steer-card-body").render(), "plain", "")
            assert "Review PR 123" in body and "The full review body." in body
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


# ---- button row + auto-expand -----------------------------------------------


def test_button_row_confirm_via_row(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    from agent_worktrees.picker_tui.engine import SteerButtonRow
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


def test_auto_expand_grows_with_lines(monkeypatch, tmp_path):
    _drafts(monkeypatch, tmp_path)
    scr = PivotFormScreen(_card(), [{"name": "notes", "type": "textarea"}],
                          "Steer", task_id="t-grow")
    app = _Host(scr)
    heights = {}

    async def run():
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            ta = scr.query_one("#q-0", _AutoExpandTextArea)
            heights["min"] = ta.styles.height.value
            ta.text = "\n".join(f"line {i}" for i in range(20))
            ta.autosize()
            await pilot.pause()
            heights["max"] = ta.styles.height.value

    asyncio.run(run())
    assert heights["min"] == 3            # min_lines floor
    assert heights["max"] == 10           # capped at max_lines

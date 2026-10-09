"""Conditional field synchronization waits for real tab mount readiness."""

from types import SimpleNamespace

import pytest
from textual.css.query import NoMatches

from worktree_manager.production_picker.picker_tui.field_questions import FieldQuestionsMixin


@pytest.mark.parametrize("missing", [False, True])
def test_conditional_sync_defers_until_tabs_mount(missing):
    callbacks = []
    events = []

    class Tabs:
        active = ""
        ready = False

        def get_tab(self, name):
            if missing and not self.ready:
                raise NoMatches(name)
            return SimpleNamespace(is_mounted=self.ready)

        def show_tab(self, name):
            assert self.ready
            events.append(name)

    tabs = Tabs()

    class Screen(FieldQuestionsMixin):
        _q = [{"name": "first", "visible": True}]

        def query(self, widget_type):
            return [tabs]

        def call_after_refresh(self, callback):
            callbacks.append(callback)

    screen = Screen()
    screen._sync_conditional_fields()
    assert not events and tabs.active == ""
    assert len(callbacks) == 1
    tabs.ready = True
    callbacks.pop()()
    assert events == ["tab-0"]
    assert tabs.active == "tab-0"

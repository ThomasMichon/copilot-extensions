from agent_worktrees.picker_tui.engine import PickerScreen


def test_paused_update_indicator_is_visible_and_not_actionable():
    screen = object.__new__(PickerScreen)
    screen.update_state = "paused"

    assert not screen._update_actionable()
    assert "\u2016" in screen._update_seg(False).plain

"""Pure line-index geometry without Textual renderable materialization."""

from rich.cells import cell_len

from worktree_manager.companion_timeline_contract import MAX_DETAIL_LINES, ViewportAnchor
from worktree_manager.companion_timeline_layout import TimelineLayout, wrap_lines
from worktree_manager.companion_timeline_model import TimelineWindow
from _companion_timeline_fixtures import event, snapshot, token


def test_wide_combining_and_literal_controls_wrap_by_cells():
    value = "界e\u0301 **literal** \x1b[31m \x9btest"
    lines = list(wrap_lines(value, 8))
    assert all(cell_len(line) <= 8 for line, _ in lines)
    rendered = "".join(line for line, _ in lines)
    assert "\x1b" not in rendered and "\x9b" not in rendered
    assert "\\x1b" in rendered and "**literal**" in rendered
    assert list(wrap_lines("界", 1))[0][0] == "?"


def test_height_index_materializes_requested_rows_only_and_detail_bounds():
    source = token()
    model = TimelineWindow()
    model.bind(source)
    rows = tuple(event(source, number, details="long detail\n" * 100) for number in range(64))
    model.apply_snapshot(snapshot(source, rows))
    model.toggle_expansion(rows[1].ref)
    layout = TimelineLayout()
    layout.rebuild(model, 60)
    assert layout.materialized_rows == 0
    assert layout.blocks[1].detail_rows <= MAX_DETAIL_LINES
    assert layout.blocks[1].detail_clipped
    for index in range(min(12, layout.height)):
        assert layout.row(index, model) is not None
    assert layout.materialized_rows <= 12


def test_logical_anchor_survives_width_reflow_and_collapsing_details():
    source = token()
    model = TimelineWindow()
    model.bind(source)
    item = event(source, 0, details="first logical line\nsecond logical line")
    model.apply_snapshot(snapshot(source, (item,)))
    model.toggle_expansion(item.ref)
    layout = TimelineLayout()
    layout.rebuild(model, 30)
    anchor = ViewportAnchor(item.ref, 1001, 0)
    assert layout.anchor_row(anchor) is not None
    layout.rebuild(model, 80)
    assert layout.anchor_row(anchor) is not None
    model.toggle_expansion(item.ref)
    layout.rebuild(model, 80)
    assert layout.anchor_row(anchor) == 0


def test_wrapped_character_anchor_preserves_the_exact_row_until_width_changes():
    source = token()
    model = TimelineWindow()
    model.bind(source)
    item = event(source, 0, summary="long wrapped header " * 10)
    model.apply_snapshot(snapshot(source, (item,)))
    layout = TimelineLayout()
    layout.rebuild(model, 30)
    row = layout.row(1, model)
    assert row is not None and row.character_offset > 0
    anchor = ViewportAnchor(row.event, row.logical_line, 0)
    assert layout.anchor_row(anchor, character_offset=row.character_offset) == 1
    layout.rebuild(model, 60)
    restored = layout.anchor_row(anchor, character_offset=row.character_offset)
    assert restored is not None
    recovered = layout.row(restored, model)
    assert recovered is not None and recovered.character_offset <= row.character_offset

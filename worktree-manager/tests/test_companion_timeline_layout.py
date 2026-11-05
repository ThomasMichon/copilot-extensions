"""Pure line-index geometry without Textual renderable materialization."""

from dataclasses import replace
from datetime import datetime, timezone

from rich.cells import cell_len
import pytest

from worktree_manager.companion_timeline_contract import (
    EventRef, LiveDelta, MAX_DETAIL_LINES, Qualification, RelationKind,
    TimelineRelation, ViewportAnchor,
)
from worktree_manager.companion_timeline_layout import TimelineLayout, wrap_lines
from worktree_manager.companion_timeline_model import TimelineWindow
from _companion_timeline_fixtures import event, position, snapshot, token


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


def test_live_append_reuses_unchanged_geometry_without_revalidating_immutable_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = token()
    model = TimelineWindow()
    model.bind(source)
    rows = tuple(event(source, number, actor="same-actor") for number in range(64))
    model.apply_snapshot(snapshot(source, rows))
    layout = TimelineLayout()
    layout.rebuild(model, 80)
    incoming = event(source, 64, actor="same-actor")
    assert model.apply_delta(LiveDelta(
        source, position(source, "live-0"), position(source, "live-1"), (incoming,),
    )).applied
    counts = {"wraps": 0, "references": 0}
    original_count, original_reference = TimelineLayout._count, EventRef.__post_init__

    def count(text: str, width: int, limit: int) -> tuple[int, bool]:
        counts["wraps"] += 1
        return original_count(text, width, limit)

    def reference(value: EventRef) -> None:
        counts["references"] += 1
        original_reference(value)

    monkeypatch.setattr(TimelineLayout, "_count", staticmethod(count))
    monkeypatch.setattr(EventRef, "__post_init__", reference)
    layout.rebuild(model, 80)
    assert counts == {"wraps": 1, "references": 0}
    counts["wraps"] = 0
    layout.rebuild(model, 80)
    for index in range(min(12, layout.height)):
        assert layout.row(index, model) is not None
    assert counts == {"wraps": 0, "references": 0}
    layout.rebuild(model, 80, now=datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert counts["wraps"] == len(model.events)
    counts["wraps"] = 0
    layout.rebuild(model, 40, now=datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert counts == {"wraps": len(model.events), "references": 0}


def test_reused_geometry_updates_expansion_and_newly_loaded_relationship_details() -> None:
    source = token()
    model = TimelineWindow()
    model.bind(source)
    parent = event(source, 0)
    model.apply_snapshot(snapshot(source, (parent,)))
    layout = TimelineLayout()
    layout.rebuild(model, 80)
    model.toggle_expansion(parent.ref)
    layout.rebuild(model, 80)
    original = layout._refs[parent.ref]
    assert original.detail_rows > 0 and "Linked child event" not in original.detail_text
    child = replace(event(source, 1), relations=(
        TimelineRelation(RelationKind.PARENT, Qualification.REPORTED, parent.ref),
    ))
    assert model.apply_delta(LiveDelta(
        source, position(source, "live-0"), position(source, "live-1"), (child,),
    )).applied
    layout.rebuild(model, 80)
    assert "Linked child event" in layout._refs[parent.ref].detail_text
    assert child.event_id in layout._refs[parent.ref].detail_text
    model.toggle_expansion(parent.ref)
    layout.rebuild(model, 80)
    assert layout._refs[parent.ref].detail_rows == 0
    assert layout._refs[parent.ref].detail_text == ""

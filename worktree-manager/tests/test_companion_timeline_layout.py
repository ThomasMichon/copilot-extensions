"""Pure line-index geometry without Textual renderable materialization."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from rich.cells import cell_len
import pytest

from worktree_manager.companion_timeline_contract import (
    Completeness, EventRef, HistoryPage, LiveDelta, MAX_DETAIL_LINES, MAX_EXPANDED_GROUPS,
    MAX_PAGE_EVENTS, Provider, Qualification, RelationKind, TimelineKind, TimelineRelation, ViewportAnchor,
)
from worktree_manager.companion_timeline_layout import OUTPUT_GROUP_LINE, TimelineLayout, output_runs, wrap_lines
from worktree_manager.companion_timeline_model import TimelineWindow
from worktree_manager.companion_worktree_codec import stamp
from _companion_timeline_fixtures import event, position, snapshot, token


def test_wide_combining_and_literal_controls_wrap_by_cells():
    value = "界e\u0301 **literal** \x1b[31m \x9btest"
    lines = list(wrap_lines(value, 8))
    assert all(cell_len(line) <= 8 for line, _ in lines)
    rendered = "".join(line for line, _ in lines)
    assert "\x1b" not in rendered and "\x9b" not in rendered
    assert "\\x1b" in rendered and "**literal**" in rendered
    assert list(wrap_lines("界", 1))[0][0] == "?"


def test_sparse_heading_keeps_exact_clock_actor_and_qualification_in_details() -> None:
    source = token()
    model = TimelineWindow()
    model.bind(source)
    item = event(source, 0, summary="Check the accepted design")
    model.apply_snapshot(snapshot(source, (item,)))
    layout = TimelineLayout()
    layout.rebuild(model, 80)
    block = layout.blocks[0]
    assert block.heading_rows == 1
    assert item.summary in block.heading_text
    assert item.kind.value not in block.heading_text
    assert item.actor.label not in block.heading_text
    assert item.observed_at.isoformat() not in block.heading_text
    assert block.heading_text.startswith("~")
    model.toggle_expansion(item.ref)
    layout.rebuild(model, 80)
    details = layout.blocks[0].detail_text
    assert str(item.observed_at) in details and "observed, not occurrence" in details
    assert item.kind.value in details and item.actor.label in details
    assert item.actor.qualification.value in details


@pytest.mark.parametrize("value,offset", [
    ("0001-01-01T00:00:00+00:00", -8),
    ("9999-12-31T23:59:59+00:00", 8),
])
def test_codec_accepted_boundary_time_cannot_abort_local_clock_reflow(value: str, offset: int) -> None:
    source = token()
    model = TimelineWindow()
    model.bind(source)
    item = replace(event(source, 0), observed_at=stamp(value))
    assert item.observed_at is not None
    assert model.apply_snapshot(snapshot(source, (item,))).applied
    model.toggle_expansion(item.ref)
    layout = TimelineLayout()
    layout.timezone = timezone(timedelta(hours=offset))
    layout.rebuild(model, 80)
    assert layout.blocks[0].heading_text.startswith("--:--  ")
    assert "Local clock unavailable" in layout.blocks[0].detail_text
    assert str(item.observed_at) in layout.blocks[0].detail_text
    assert model.events == (item,)


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


@pytest.mark.parametrize("width", [80, 18])
def test_output_group_aliases_survive_append_prepend_expansion_and_eviction(width: int) -> None:
    source = token(key="bridge")
    source = replace(source, identity=replace(source.identity, provider=Provider.BRIDGE))
    model = TimelineWindow(max_events=6)
    model.bind(source)
    rows = tuple(replace(event(source, n), kind=TimelineKind.SOURCE_OUTPUT) for n in range(4))
    assert model.apply_snapshot(snapshot(source, rows)).applied
    raw_content = tuple(row.content() for row in rows)
    layout = TimelineLayout()
    layout.rebuild(model, width)
    assert len(layout.blocks) == 1
    assert layout.blocks[0].members == tuple(row.ref for row in rows)
    assert "4 loaded records" in layout.blocks[0].heading_text
    anchor = ViewportAnchor(rows[2].ref, 0, 0)
    assert layout.anchor_row(anchor) == 0
    assert layout.visible_ref(rows[2].ref) == rows[0].ref
    assert model.request_history(source, position(source, "older-0")).applied
    older = replace(event(source, -1), kind=TimelineKind.SOURCE_OUTPUT)
    assert model.prepend_page(HistoryPage(
        source, position(source, "older-0"), None, (older,), Completeness.PARTIAL,
    )).applied
    layout.rebuild(model, width)
    assert layout.anchor_row(anchor) == 0
    assert layout.visible_ref(rows[2].ref) == older.ref
    assert layout.toggle_output(rows[2].ref).applied
    layout.rebuild(model, width)
    assert len(layout.blocks) == 6 and len(model.events) == 5
    assert layout.visible_ref(rows[2].ref) == rows[2].ref
    assert layout.anchor_row(anchor) == layout._refs[rows[2].ref].start
    assert model.toggle_expansion(rows[2].ref).applied
    layout.rebuild(model, width)
    assert rows[2].position.cursor in layout._refs[rows[2].ref].detail_text
    assert layout._refs[rows[2].ref].detail_rows <= MAX_DETAIL_LINES
    assert layout.toggle_output(rows[2].ref).applied
    layout.rebuild(model, width)
    row = layout.row(min(1, layout.height - 1), model)
    assert row is not None and row.logical_line == OUTPUT_GROUP_LINE
    header_anchor = ViewportAnchor(row.event, row.logical_line, 0)
    assert layout.anchor_row(header_anchor, character_offset=row.character_offset) == min(1, layout.height - 1)
    added = tuple(replace(event(source, n), kind=TimelineKind.SOURCE_OUTPUT) for n in (4, 5))
    assert model.apply_delta(LiveDelta(
        source, position(source, "live-0"), position(source, "live-1"), added,
    )).applied
    layout.rebuild(model, width, anchors=(older.ref,))
    assert len(model.events) == 6 and model.evicted == 1 and model.event(older.ref) is None
    assert len(layout.blocks) == 1 and layout.anchor_row(anchor) == 0
    assert layout.anchor_row(ViewportAnchor(older.ref, OUTPUT_GROUP_LINE, 0)) == 0
    assert layout.visible_ref(older.ref, group=True) == rows[0].ref
    assert tuple(model.event(row.ref).content() for row in rows) == raw_content
    assert len(model._proofs) == 7


def test_output_run_boundaries_do_not_group_prose_unknown_actors_or_foreign_sources() -> None:
    source = token(key="bridge")
    source = replace(source, identity=replace(source.identity, provider=Provider.BRIDGE))
    first = replace(event(source, 0), kind=TimelineKind.SOURCE_OUTPUT)
    other_actor = replace(event(source, 1, actor="another"), kind=TimelineKind.SOURCE_OUTPUT)
    foreign = replace(source, identity=replace(source.identity, generation="another-generation"))
    other_generation = replace(event(foreign, 2), kind=TimelineKind.SOURCE_OUTPUT)
    other_source = replace(source, identity=replace(source.identity, source_key="another-source"))
    same_label = replace(event(other_source, 3), kind=TimelineKind.SOURCE_OUTPUT)
    unknown_actor = replace(event(source, 4, actor=None), kind=TimelineKind.SOURCE_OUTPUT)
    lookalike = event(source, 5, summary="agent_message Thought output")
    diagnostic = replace(event(source, 6), kind=TimelineKind.SOURCE_GAP)
    rows = (first, other_actor, other_generation, same_label, unknown_actor, lookalike, diagnostic, first)
    assert tuple(output_runs(rows)) == tuple((row,) for row in rows)
    non_bridge = replace(first, source=token().identity, position=position(token(), "one"))
    assert tuple(output_runs((non_bridge, non_bridge))) == ((non_bridge,), (non_bridge,))


def test_output_groups_and_expansion_have_fixed_bounds() -> None:
    source = token(key="bridge")
    source = replace(source, identity=replace(source.identity, provider=Provider.BRIDGE))
    rows = tuple(replace(event(source, n), kind=TimelineKind.SOURCE_OUTPUT) for n in range(130))
    assert [len(run) for run in output_runs(rows)] == [MAX_PAGE_EVENTS, MAX_PAGE_EVENTS, 2]
    model = TimelineWindow()
    model.bind(source)
    separated = tuple(
        replace(event(source, n, actor=f"actor-{n}"), kind=TimelineKind.SOURCE_OUTPUT)
        for n in range(MAX_EXPANDED_GROUPS + 1)
    )
    assert model.apply_snapshot(snapshot(source, separated)).applied
    layout = TimelineLayout()
    layout.rebuild(model, 80)
    for row in separated[:MAX_EXPANDED_GROUPS]:
        assert layout.toggle_output(row.ref).applied
        layout.rebuild(model, 80)
    result = layout.toggle_output(separated[-1].ref)
    assert result is not None and not result.applied and "budget exhausted" in result.reason
    layout.rebuild(model, 80)
    assert len(layout.blocks) == MAX_EXPANDED_GROUPS * 2 + 1
    assert len(layout._expanded_output) == MAX_EXPANDED_GROUPS
    assert model.expanded == frozenset()


def test_output_group_anchor_and_expansion_follow_mixed_source_split_and_merge() -> None:
    bridge = token(key="bridge")
    bridge = replace(bridge, identity=replace(bridge.identity, provider=Provider.BRIDGE))
    agenda = token(key="agenda")
    agenda = replace(agenda, identity=replace(agenda.identity, provider=Provider.AGENDA))
    model = TimelineWindow()
    rows = tuple(replace(event(bridge, n), kind=TimelineKind.SOURCE_OUTPUT) for n in range(4))
    assert model.bind(bridge).applied and model.apply_snapshot(snapshot(bridge, rows)).applied
    layout = TimelineLayout()
    layout.rebuild(model, 80)
    assert layout.toggle_output(rows[0].ref).applied
    layout.rebuild(model, 80)
    anchor = ViewportAnchor(rows[2].ref, OUTPUT_GROUP_LINE, 0)
    interleaved = replace(
        event(agenda, 1), observed_at=rows[1].observed_at + timedelta(microseconds=1),
        kind=TimelineKind.AGENDA_TRANSITION,
    )
    assert model.bind(agenda).applied and model.apply_snapshot(snapshot(agenda, (interleaved,))).applied
    layout.rebuild(model, 80)
    assert [len(block.members) for block in layout.blocks if block.members] == [2, 2]
    assert len(layout.blocks) == 7
    assert layout.anchor_row(anchor) == layout._groups[rows[2].ref].start
    assert layout.visible_ref(rows[2].ref, group=True) == rows[2].ref
    renewed = replace(agenda, identity=replace(agenda.identity, generation="next-agenda"))
    assert model.bind(renewed).applied
    layout.rebuild(model, 80)
    assert len(layout.blocks) == 5
    assert layout.anchor_row(anchor) == 0
    assert layout.visible_ref(rows[2].ref, group=True) == rows[0].ref
    assert tuple(model.events) == rows


def test_full_output_window_reflow_reuses_geometry_and_keeps_raw_integrity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = token(key="bridge")
    source = replace(source, identity=replace(source.identity, provider=Provider.BRIDGE))
    model = TimelineWindow()
    assert model.bind(source).applied
    latest = 5_000 - MAX_PAGE_EVENTS
    make = lambda n: replace(event(source, n), kind=TimelineKind.SOURCE_OUTPUT)
    assert model.apply_snapshot(snapshot(
        source, tuple(make(n) for n in range(latest, 5_000)), before=f"older-{latest}",
    )).applied
    while latest:
        before = model.sources[source.identity.source_key].before
        assert before is not None and model.request_history(source, before).applied
        start = max(0, latest - MAX_PAGE_EVENTS)
        assert model.prepend_page(HistoryPage(
            source, before, position(source, f"older-{start}") if start else None,
            tuple(make(n) for n in range(start, latest)), Completeness.PARTIAL,
        )).applied
        latest = start
    layout = TimelineLayout()
    layout.rebuild(model, 80)
    assert len(model.events) == 5_000 and len(layout.blocks) == 79
    assert sum(len(block.members) for block in layout.blocks) == 5_000
    counts = {"wraps": 0}
    original_count = TimelineLayout._count

    def count(value: str, width: int, limit: int) -> tuple[int, bool]:
        counts["wraps"] += 1
        return original_count(value, width, limit)

    monkeypatch.setattr(TimelineLayout, "_count", staticmethod(count))
    layout.rebuild(model, 80)
    assert counts["wraps"] == 0 and layout.materialized_rows == 0
    assert model.apply_delta(LiveDelta(
        source, position(source, "live-0"), position(source, "live-1"), (make(5_000),),
    )).applied
    layout.rebuild(model, 80)
    assert counts["wraps"] <= 3
    assert len(model.events) == 5_000 and len(model._proofs) == 5_001 and model.evicted == 1
    assert len(layout.blocks) == 79 and not model.reconciliation_required
    for n in range(12):
        assert layout.row(n, model) is not None
    assert layout.materialized_rows == 12

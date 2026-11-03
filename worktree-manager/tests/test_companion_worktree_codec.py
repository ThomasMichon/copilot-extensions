"""Actual owner wire construction through the independent Manager projection."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import asyncio
import json
import os

import pytest
from textual.widgets import Button, TabbedContent

from worktree_manager.companion_timeline_contract import (
    Availability, Completeness, EventRef, EvidenceKind, Provider, SourceToken, TimelineContractError,
    TimelineGap, TimelineKind, TimelineSnapshot,
)
from worktree_manager.companion_timeline_model import ResultCode, TimelineWindow
from worktree_manager.companion_worktree_codec import OwnerReadError, WorktreeCodec
from worktree_manager.companion_worktree_source import WorktreeTimelineSource
from worktree_manager.companion_timeline_widget import SubwayTimeline
from worktree_manager import mux_companion as companion
from worktree_manager import companion_timeline_attachment as attachment_module
from worktree_manager import agenda_transport
from worktree_manager.engine_process import EngineCleanupError
from worktree_manager.companion_timeline_controller import TimelineCleanupError
from worktree_manager.companion_timeline_attachment import TimelineAttachment
from test_companion_timeline_widget import TimelineApp
import _worktree_timeline_fixtures as fixtures
from test_companion_worktree_source import configured as configured, status
from test_companion_agenda_live import until
from _worktree_timeline_fixtures import (
    JOURNAL, RESIDENT, cursor, delta, gap, page, raw, snapshot,
)


def codec() -> WorktreeCodec:
    return WorktreeCodec("example", "selected", "head-a", RESIDENT)


def test_actual_owner_snapshot_projects_current_facts_without_created_event() -> None:
    reader = codec()
    value = reader.decode(raw(snapshot()))
    token = reader.token(value, 3)
    observed = reader.snapshot(value, token)
    assert token.identity.generation == JOURNAL and token.identity.owner_ref == RESIDENT
    assert observed.purpose == "Current accepted purpose."
    assert observed.effort_ref == "efforts/active/example/README.md"
    assert observed.creation_context is None and observed.events == ()
    assert observed.completeness is Completeness.PARTIAL
    assert observed.position is not None and observed.position.cursor == cursor(40)


def test_actual_owner_history_events_retain_source_evidence_and_literal_text() -> None:
    reader = codec()
    selected = reader.token(reader.decode(raw(snapshot())), 4)
    before = reader.position(selected, cursor(21))
    result = reader.page(reader.decode(raw(page(before=21))), selected, before)
    assert not isinstance(result, TimelineGap)
    assert result.before == before and result.next_before is None
    assert len(result.events) == 20
    assert all(item.kind is TimelineKind.OBSERVED_CHANGE for item in result.events)
    assert all(item.evidence_kind is EvidenceKind.OBSERVED_CHANGE for item in result.events)
    assert result.events[0].occurred_at is None and result.events[0].data_json is not None


@pytest.mark.parametrize("change", [
    {"contract_version": True}, {"extra": True}, {"worktree_id": "wrong"},
    {"project": "wrong"}, {"head_session_id": "head-b"}, {"source_generation": "opaque-invalid"},
    {"resident_generation": "c" * 32}, {"replay_barrier": "wrong"},
    {"history_scope": "lifetime"}, {"replay_complete": 1},
    {"facts": None}, {"entries": [None]}, {"availability": "Observed"},
])
def test_malformed_or_wrong_selection_is_not_an_empty_snapshot(change: dict) -> None:
    reader = codec()
    value = snapshot()
    value.update(change)
    with pytest.raises((TimelineContractError, OwnerReadError, ValueError)):
        decoded = reader.decode(raw(value))
        reader.snapshot(decoded, reader.token(decoded, 1))


@pytest.mark.parametrize("member", ["creation_nonce", "last_session_id", "id", "repo"])
def test_unknown_incarnation_and_wrong_current_fact_identity_are_rejected(member: str) -> None:
    value = snapshot()
    value["facts"]["row"][member] = None
    with pytest.raises(TimelineContractError):
        codec().decode(raw(value))


def test_source_replacement_and_page_boundary_fences_remain_exact() -> None:
    reader = codec()
    token = reader.token(reader.decode(raw(snapshot())), 1)
    value = page(before=21)
    value["source"] = "current-roster:replacement-record"
    with pytest.raises(OwnerReadError, match="source-replaced"):
        reader.page(reader.decode(raw(value)), token, reader.position(token, cursor(21)))
    with pytest.raises(TimelineContractError, match="boundary"):
        reader.page(reader.decode(raw(page(before=21))), token, reader.position(token, cursor(20)))


def test_record_incarnation_cannot_change_under_an_existing_source_header() -> None:
    reader = codec()
    reader.decode(raw(snapshot()))
    value = delta()
    value["facts"]["row"]["creation_nonce"] = "replacement-record"
    with pytest.raises(OwnerReadError, match="incarnation"):
        reader.decode(raw(value))


def test_history_gap_preserves_floor_reason_and_live_current_position() -> None:
    reader = codec()
    token = reader.token(reader.decode(raw(snapshot())), 7)
    model = TimelineWindow()
    model.bind(token)
    assert model.apply_snapshot(reader.snapshot(reader.decode(raw(snapshot(after=21))), token)).applied
    before = reader.position(token, cursor(22))
    model.request_history(token, before)
    value = gap(22)
    result = reader.page(reader.decode(raw(value)), token, before)
    assert isinstance(result, TimelineGap)
    state = model.sources[token.identity.source_key]
    rows = model.events
    assert not model.apply_gap(result).applied
    assert result.reason == "retention" and result.floor.cursor == cursor(22)
    assert result.reconcile == "snapshot-and-history" and result.before == before
    current = model.sources[token.identity.source_key]
    assert current.position == state.position and current.availability is Availability.OBSERVED
    assert current.requested_before is None and model.events == rows
    assert model.context()[0] == "Current accepted purpose."
    renewed = replace(token, request_epoch=8)
    assert model.bind(renewed).applied
    assert not model.apply_gap(result).applied


def test_same_barrier_replay_slices_do_not_conflict_but_changed_event_content_does() -> None:
    reader = codec()
    value = reader.decode(raw(snapshot(after=21)))
    token = reader.token(value, 1)
    model = TimelineWindow()
    model.bind(token)
    assert model.apply_snapshot(reader.snapshot(value, token)).applied
    assert model.apply_snapshot(reader.snapshot(reader.decode(raw(snapshot(after=40))), token)).applied
    changed = deepcopy(value)
    changed["entries"][0]["summary"] = "Conflicting immutable event"
    result = model.apply_snapshot(reader.snapshot(reader.decode(raw(changed)), token))
    assert not result.applied and result.code.value == "content-conflict"


def test_wire_limits_duplicate_members_boolean_types_and_sampled_event_qualification() -> None:
    reader = codec()
    for data in (b'{"type":"snapshot","type":"delta"}', b"\xff", b" " * (35 * 1024 + 1)):
        with pytest.raises(TimelineContractError):
            reader.decode(data)
    value = delta()
    value["entries"][0]["kind"] = "claim-added"
    parsed = reader.decode(raw(value))
    with pytest.raises(TimelineContractError, match="sampled"):
        reader.events(parsed, reader.token(parsed, 1))
    value = snapshot()
    value["facts"]["row"]["summary"] = "x" * 4097
    with pytest.raises(TimelineContractError):
        reader.decode(raw(value))
    for bad in (2**63, "text\x00"):
        value = snapshot()
        value["facts"]["row"]["untrusted"] = bad
        with pytest.raises(TimelineContractError):
            reader.decode(raw(value))


@pytest.mark.skipif(os.name == "nt", reason="owned POSIX fixture executable")
@pytest.mark.parametrize("mode", [
    "refresh", "reconcile-button", "short-baseline", "archived-conflict", "ordinary-renewal", "invalid", "unavailable",
    "unqualified", "rewound", "conflicting-event", "conflicting-facts",
    "over-budget", "retention-budget", "stale-epoch", "wrong-head", "superseded",
])
def test_manager_explicit_refresh_recovers_exhausted_owner_window(configured, monkeypatch, mode: str) -> None:
    root, _calls = configured

    def push(value: dict) -> None:
        pending = root / "push.next"
        pending.write_text(json.dumps(value), encoding="utf-8")
        pending.replace(root / "push.json")

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(root))
        app._timeline_panel.window = TimelineWindow(max_proofs=21)
        async with app.run_test(size=(80, 30)) as pilot:
            await until(pilot, lambda: not app._loading)
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            timeline = app.query_one(SubwayTimeline)
            await until(pilot, lambda: len(timeline.window.events) == 20)
            timeline.scroll_to(y=15, animate=False, immediate=True)
            await pilot.pause()
            push(delta())
            await until(pilot, lambda: len(timeline.window.events) == 21)
            push(fixtures.wire.frame("delta", fixtures.SOURCE, cursor(42), {
                **fixtures.qualified(), "base_cursor": cursor(41),
                "entries": [fixtures.entry(42)],
                "facts": fixtures.facts("Updated purpose from pushed owner facts."),
            }))
            await until(pilot, lambda: bool(timeline.window.reconciliation_required))
            old = timeline.window
            token = next(iter(old.sources.values())).token
            proofs, events = dict(old._proofs), old.events
            assert len(proofs) == old.max_proofs == 21
            assert old.bind(replace(token, request_epoch=token.request_epoch + 1)).code is ResultCode.LIMIT
            tip = 40 if mode == "rewound" else 41 if mode == "conflicting-facts" else 42
            first = 41 if mode == "short-baseline" else 21 if mode == "over-budget" else 28
            responses = {
                "point": fixtures.snapshot(tip, summary="Fresh owner baseline."),
                "replay": fixtures.snapshot(tip, after=first, summary="Fresh owner baseline."),
                "follow": fixtures.snapshot(tip, after=tip, summary="Fresh owner baseline."),
                "latest": fixtures.wire.frame("history-page", fixtures.SOURCE, cursor(tip), {
                    **fixtures.qualified(), "before_cursor": None, "next_before": cursor(first),
                    "entries": [fixtures.entry(number) for number in range(first, tip + 1)], "has_more": True,
                }),
                "older": fixtures.page(before=21),
                "heartbeat": fixtures.wire.frame("heartbeat", fixtures.SOURCE, cursor(tip), {}),
            }
            if mode == "invalid":
                responses["point"]["facts"]["row"]["creation_nonce"] = None
            elif mode == "unavailable":
                responses["point"] = {"type": "error", "contract_version": 1,
                                      "error": {"code": "unavailable", "message": "fixture-offline"}}
            elif mode == "unqualified":
                responses["point"].update(
                    availability="unavailable", facts=None, entries=[], history_floor=cursor(tip),
                )
            elif mode == "conflicting-event":
                responses["latest"]["entries"][0]["summary"] = "Conflicting immutable event"
            (root / "responses.json").write_text(json.dumps(responses), encoding="utf-8")
            if mode == "retention-budget":
                monkeypatch.setattr(attachment_module, "MAX_RETAINED_EVIDENCE_BYTES", 0)
            anchor = timeline.capture_anchor()
            assert anchor is not None
            anchor_offset = timeline._anchor_character_offset
            prepared, retired = asyncio.Event(), asyncio.Event()
            if mode in {"stale-epoch", "wrong-head", "superseded"}:
                prepare = WorktreeTimelineSource.prepare

                async def late(self: WorktreeTimelineSource, epoch: int, *, stop: asyncio.Event) -> TimelineSnapshot:
                    value = await prepare(self, epoch, stop=stop)
                    prepared.set()
                    if mode == "stale-epoch":
                        return replace(value, token=replace(value.token, request_epoch=epoch - 1))
                    if mode == "wrong-head":
                        return replace(value, events=(), token=replace(
                            value.token, identity=replace(value.token.identity, head_session_id="head-b"),
                        ))
                    await stop.wait()
                    retired.set()
                    return value

                monkeypatch.setattr(WorktreeTimelineSource, "prepare", late)
            if mode == "ordinary-renewal":
                await app._timeline_attachment.set_selection(("example", "selected", "head-a"))
            else:
                button = ".timeline-reconcile" if mode == "reconcile-button" else "#refresh-btn"
                control = app.query_one(button, Button)
                if mode == "reconcile-button":
                    await until(pilot, lambda: (
                        control.display and not control.disabled and control.region.width > 0
                        and control.region.height > 0 and control.region.x >= 0 and control.region.y >= 0
                        and control.region.right <= app.size.width and control.region.bottom <= app.size.height
                        and app.get_widget_at(control.region.x, control.region.y)[0] is control
                    ))
                anchor = timeline.capture_anchor()
                assert anchor is not None
                anchor_offset = timeline._anchor_character_offset
                assert await pilot.click(control), {
                    "button": button, "region": control.region, "display": control.display,
                    "disabled": control.disabled, "status": status(app),
                }
            if mode == "superseded":
                await until(pilot, prepared.is_set)
                app.query_one("#views", TabbedContent).active = "summary-tab"
                await until(pilot, retired.is_set)
                await until(pilot, lambda: app._timeline_attachment.controller is None)
            elif mode not in {"refresh", "reconcile-button", "short-baseline", "archived-conflict"}:
                diagnostic = {
                    "ordinary-renewal": "same-source epoch renewal", "invalid": "owner reference",
                    "unavailable": "fixture-offline", "unqualified": "fresh observed owner facts",
                    "rewound": "older known live position", "conflicting-event": "immutable event evidence",
                    "conflicting-facts": "same live position", "over-budget": "baseline was not admitted",
                    "stale-epoch": "request epoch", "wrong-head": "active selection",
                    "retention-budget": "Retained evidence budget",
                }[mode]
                await until(pilot, lambda: diagnostic in status(app))
            if mode not in {"refresh", "reconcile-button", "short-baseline", "archived-conflict"}:
                assert timeline.window is old and old._proofs == proofs and old.events == events
                assert old.reconciliation_required
                assert not app._timeline_attachment.retained_windows
                assert app._timeline_attachment._cleanup_error is None
                if mode != "superseded":
                    retained = timeline.capture_anchor()
                    assert retained == anchor and timeline._anchor_character_offset == anchor_offset
                app.query_one("#views", TabbedContent).active = "summary-tab"
                await pilot.pause()
                assert app._data.error is None
                await pilot.press("escape")
                return
            try:
                await until(pilot, lambda: (
                    timeline.window is not old and not timeline.window.reconciliation_required
                    and timeline.window.events[-1].event_id == "event-42"
                ))
            except AssertionError as exc:
                raise AssertionError({
                    "mode": mode, "status": status(app), "same_window": timeline.window is old,
                    "required": timeline.window.reconciliation_required,
                    "proof_count": len(timeline.window._proofs),
                }) from exc
            assert old._proofs == proofs and old.events == events
            assert old.reconciliation_required
            assert app._timeline_attachment.retained_windows == (old,)
            current = next(iter(timeline.window.sources.values()))
            assert current.token.identity == token.identity and current.token.request_epoch > token.request_epoch
            assert current.availability is Availability.OBSERVED and current.requested_before is None
            assert timeline.window.max_proofs == old.max_proofs
            row = timeline.layout_index.anchor_row(anchor, character_offset=anchor_offset)
            if mode == "short-baseline":
                assert row is None
            else:
                assert row is not None and row + timeline._padding - int(timeline.scroll_offset.y) == anchor.screen_row
            assert not timeline.window.follow_live and len(timeline.window.unread) == 2
            if mode == "archived-conflict":
                changed = fixtures.entry(21)
                changed["summary"] = "Conflict after the fresh baseline"
                responses["older"] = fixtures.wire.frame("history-page", fixtures.SOURCE, cursor(42), {
                    **fixtures.qualified(), "before_cursor": cursor(28), "next_before": None,
                    "entries": [changed], "has_more": False,
                })
                (root / "responses.json").write_text(json.dumps(responses), encoding="utf-8")
                timeline.focus()
                await pilot.press("home")
                await until(pilot, lambda: "retained exhausted-window evidence" in status(app))
                assert timeline.window.event(fixtures_to_ref(token, "event-21")) is None
                assert old._proofs == proofs
                assert app._timeline_attachment._cleanup_error is None
            app.query_one("#views", TabbedContent).active = "summary-tab"
            await pilot.pause()
            assert app._data.error is None
            await pilot.press("escape")

    asyncio.run(check())


def fixtures_to_ref(token: SourceToken, event_id: str) -> EventRef:
    return EventRef(token.identity.source_key, token.identity.generation, event_id)


@pytest.mark.skipif(os.name == "nt", reason="owned POSIX fixture executable")
@pytest.mark.parametrize("failure_kind", ["reader", "history"])
def test_actual_retirement_failure_is_latched_and_blocks_new_reads(configured, monkeypatch, failure_kind) -> None:
    root, calls = configured
    resource = root / "descriptor-lease"
    resource.write_bytes(b"private fixture resource")
    leases = []
    frames = agenda_transport.command_frames

    def failed_retirement(argv, *, cancelled, cwd):
        stream = frames(argv, cancelled=cancelled, cwd=cwd)
        lease = None
        try:
            if (
                failure_kind == "reader" and "--follow" in argv
                or failure_kind == "history" and "--before-cursor" in argv
            ):
                lease = resource.open("rb")
                leases.append(lease)
            yield from stream
        finally:
            stream.close()
            if lease is not None and not lease.closed:
                raise EngineCleanupError("fixture descriptor lease did not retire")

    monkeypatch.setattr(agenda_transport, "command_frames", failed_retirement)

    async def check() -> None:
        app = TimelineApp(TimelineWindow())
        async with app.run_test(size=(100, 36)) as pilot:
            panel = app.query_one(attachment_module.CompanionTimelinePanel)
            attachment = TimelineAttachment(panel, cwd=str(root))
            try:
                await attachment.set_selection(("example", "selected", "head-a"))
                if failure_kind == "history":
                    await until(pilot, lambda: len(panel.window.events) == 20)
                    selected = next(iter(panel.window.sources.values()))
                    assert selected.before is not None
                    assert attachment.controller is not None
                    assert attachment.controller.request_history(selected.token, selected.before)
                    page = attachment.controller._pages[Provider.WORKTREES]
                    await asyncio.gather(page, return_exceptions=True)
                    with pytest.raises(TimelineCleanupError):
                        attachment.controller.request_history(selected.token, selected.before)
                await until(pilot, lambda: bool(leases))
                await attachment.set_selection(None)
                assert attachment._cleanup_error is not None
                assert "descriptor lease" in attachment._cleanup_error
                assert not leases[0].closed
                before = calls.read_text()
                await attachment.set_selection(("example", "selected", "head-a"))
                assert calls.read_text() == before
                assert attachment._cleanup_error is not None
                with pytest.raises(TimelineCleanupError, match="descriptor lease"):
                    await attachment.close()
            finally:
                for lease in leases:
                    lease.close()

    asyncio.run(check())

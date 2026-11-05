"""Shared head fences and immutable Agenda proofs survive Worktree reconciliation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from threading import Event

import pytest
from textual.widgets import Static, TabbedContent

from worktree_manager import companion_timeline_attachment as worktree_attachment
from worktree_manager import mux_companion as companion
from worktree_manager.companion_agenda_attachment import AgendaReader
from worktree_manager.companion_timeline_contract import (
    Availability, Completeness, HistoryPage, LiveDelta, Provider, SourceIdentity,
    SourcePosition, SourceToken, TimelineSnapshot,
)
from worktree_manager.companion_timeline_model import ResultCode, TimelineWindow
from worktree_manager.companion_timeline_widget import SubwayTimeline
from worktree_manager.companion_worktree_codec import OwnerReadError
from _companion_timeline_fixtures import event
from test_companion_agenda_live import until
from test_companion_agenda_races import configure, publish, seed_worktree, managed as managed

pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.skipif(os.name == "nt", reason="POSIX attributed-interpreter fixture; native qualification remains open"),
]


def test_tokenless_head_invalidation_survives_hide_and_failed_worktree_reopen(managed, monkeypatch) -> None:
    real_selection = worktree_attachment.TimelineAttachment.set_selection
    configure(managed, monkeypatch)
    captured, release = Event(), Event()
    original = AgendaReader.latest
    calls = []

    def delayed(self, *args):
        calls.append(1)
        pages = original(self, *args)
        captured.set()
        assert release.wait(5)
        return pages

    async def unavailable(*args, **kwargs):
        raise OwnerReadError("unavailable", "fixture failed Worktree preparation")

    monkeypatch.setattr(AgendaReader, "latest", delayed)
    monkeypatch.setattr(worktree_attachment.WorktreeTimelineSource, "prepare", unavailable)

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            try:
                await until(pilot, lambda: not app._loading)
                app._agenda_cancel = Event()
                publish(app, managed["publication"]())
                owner = seed_worktree(app._timeline_panel, populated=True)
                app.query_one("#views", TabbedContent).active = "timeline-tab"
                attachment = app._rich_agenda_attachment
                await until(pilot, captured.is_set)
                assert attachment.token is None
                app._timeline_panel.apply_snapshot(replace(owner, availability=Availability.HEAD_CHANGED, events=()))
                app.query_one("#views", TabbedContent).active = "summary-tab"
                await until(pilot, lambda: attachment.selection is None)
                release.set()
                await until(pilot, lambda: attachment._task is None)
                assert attachment._invalidated_selection == ("example", "selected", "head-a")
                monkeypatch.setattr(worktree_attachment.TimelineAttachment, "set_selection", real_selection)
                app.query_one("#views", TabbedContent).active = "timeline-tab"
                await until(pilot, lambda: attachment._task is not None and attachment._task.done())
                assert calls == [1] and attachment.token is None
                assert not app._timeline_panel.window.events
                assert app._timeline_panel.window.sources[owner.token.identity.source_key].availability is Availability.HEAD_CHANGED
            finally:
                release.set()

    asyncio.run(check())


def owner_snapshot(epoch: int, head: str = "head-a") -> TimelineSnapshot:
    token = SourceToken(SourceIdentity(
        Provider.WORKTREES, "fixture-worktree", "example", "selected", head,
        "fixture-generation", owner_ref="fixture-owner",
    ), epoch)
    return TimelineSnapshot(
        token, SourcePosition(token.identity.source_key, token.identity.generation, f"fixture-position-{epoch}"),
        Availability.OBSERVED, Completeness.PARTIAL,
        events=(event(token, 0), event(token, 1)) if epoch else (event(token, 0),),
        as_of=datetime.now(timezone.utc), facts_json='{"coverage":"fixture"}',
    )


def test_agenda_only_head_transition_prepares_new_worktree_in_the_same_refresh(managed, monkeypatch) -> None:
    real_selection = worktree_attachment.TimelineAttachment.set_selection
    configure(managed, monkeypatch)
    from test_companion_agenda_rich import current_managed_data
    selected = {"head": "head-a"}

    def data(*args, **kwargs):
        value = current_managed_data(managed)
        value.sessions = [{"id": selected["head"], "is_head": True}]
        return value

    class Source:
        async def prepare(self, epoch, *, stop):
            self.initial = owner_snapshot(epoch, "head-b")
            return self.initial

        async def observe(self, token, *, stop):
            yield self.initial
            await stop.wait()

    monkeypatch.setattr(companion, "_load_current_worktree", data)
    monkeypatch.setattr(worktree_attachment, "WorktreeTimelineSource", lambda *args, **kwargs: Source())

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            await until(pilot, lambda: not app._loading)
            app._agenda_cancel = Event()
            publish(app, managed["publication"]())
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            await until(pilot, lambda: app._rich_agenda_attachment.token is not None)
            selected["head"] = "head-b"
            newer = managed["update"](session="head-b")
            monkeypatch.setattr(worktree_attachment.TimelineAttachment, "set_selection", real_selection)
            app.action_refresh()
            await until(pilot, lambda: not app._loading and app._data.sessions[0]["id"] == "head-b")
            await until(pilot, lambda: app._rich_agenda_attachment.selection == ("example", "selected", "head-b"))
            app._agenda_cancel = Event()
            publish(app, newer)
            await until(pilot, lambda: app._rich_agenda_attachment.token is not None)
            states = app._timeline_panel.window.sources.values()
            assert {state.token.identity.provider for state in states} == {Provider.WORKTREES, Provider.AGENDA}
            assert all(state.token.identity.head_session_id == "head-b" for state in states)

    asyncio.run(check())


def test_refresh_invalidates_a_blocked_show_before_retirement_awaits(managed, monkeypatch) -> None:
    real_selection = worktree_attachment.TimelineAttachment.set_selection
    configure(managed, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    loading, finish_load = Event(), Event()

    class Source:
        async def prepare(self, epoch, *, stop):
            entered.set()
            await release.wait()
            self.initial = owner_snapshot(epoch)
            return self.initial

        async def observe(self, token, *, stop):
            yield self.initial
            await stop.wait()

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            try:
                await until(pilot, lambda: not app._loading)
                app._agenda_cancel = Event()
                publish(app, managed["publication"]())
                original_load = companion._load_current_worktree

                def held_load(*args, **kwargs):
                    loading.set()
                    assert finish_load.wait(10)
                    return original_load(*args, **kwargs)

                monkeypatch.setattr(companion, "_load_current_worktree", held_load)
                monkeypatch.setattr(worktree_attachment, "WorktreeTimelineSource", lambda *args, **kwargs: Source())
                monkeypatch.setattr(worktree_attachment.TimelineAttachment, "set_selection", real_selection)
                app.query_one("#views", TabbedContent).active = "timeline-tab"
                await until(pilot, entered.is_set)
                app.action_refresh()
                await until(pilot, lambda: app._loading)
                assert app._rich_agenda_attachment._stop.is_set()
                release.set()
                await until(pilot, loading.is_set)
                assert app._rich_agenda_attachment.selection is None
                assert app._rich_agenda_attachment.token is None
            finally:
                release.set()
                finish_load.set()
                await until(pilot, lambda: not app._loading)
                await pilot.pause()

    asyncio.run(check())


@pytest.mark.parametrize("reopen", [False, True])
def test_agenda_invalidation_fences_a_successful_inflight_worktree_prepare(managed, monkeypatch, reopen) -> None:
    real_selection = worktree_attachment.TimelineAttachment.set_selection
    configure(managed, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    class Source:
        async def prepare(self, epoch, *, stop):
            self.initial = owner_snapshot(epoch)
            entered.set()
            await release.wait()
            return self.initial

        async def observe(self, token, *, stop):
            yield self.initial
            await stop.wait()

    monkeypatch.setattr(worktree_attachment, "WorktreeTimelineSource", lambda *args, **kwargs: Source())

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            await until(pilot, lambda: not app._loading)
            app._agenda_cancel = Event()
            publish(app, managed["publication"]())
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            await until(pilot, lambda: app._rich_agenda_attachment.token is not None)
            if reopen:
                await app._rich_agenda_attachment.set_selection(None)
                assert app._rich_agenda_attachment.selection is None
            pending = asyncio.create_task(real_selection(
                app._timeline_attachment, ("example", "selected", "head-a"),
            ))
            try:
                await entered.wait()
                publish(app, {**managed["publication"](), "availability": "head-changed", "observation": None})
                assert not app._timeline_panel.window.events
            finally:
                release.set()
                await pending
            assert not app._timeline_panel.window.events
            assert app._timeline_attachment.controller is None

    asyncio.run(check())


@pytest.mark.parametrize("overlap", [False, True])
def test_hidden_demand_stops_rich_before_blocked_worktree_retirement(managed, monkeypatch, overlap) -> None:
    real_selection = worktree_attachment.TimelineAttachment.set_selection
    configure(managed, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    reads = []
    original = AgendaReader.latest

    def latest(reader, *args):
        reads.append(1)
        return original(reader, *args)

    monkeypatch.setattr(AgendaReader, "latest", latest)

    async def blocked_retirement(self, selection, **kwargs):
        if self.provider is Provider.BRIDGE:
            return await real_selection(self, selection, **kwargs)
        if selection is None:
            entered.set()
            await release.wait()

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            try:
                await until(pilot, lambda: not app._loading)
                app._agenda_cancel = Event()
                publish(app, managed["publication"]())
                app.query_one("#views", TabbedContent).active = "timeline-tab"
                attachment = app._rich_agenda_attachment
                await until(pilot, lambda: attachment.token is not None)
                monkeypatch.setattr(worktree_attachment.TimelineAttachment, "set_selection", blocked_retirement)
                app.query_one("#views", TabbedContent).active = "summary-tab"
                await until(pilot, entered.is_set)
                assert attachment._stop.is_set()
                if overlap:
                    app.query_one("#views", TabbedContent).active = "timeline-tab"
                    await until(pilot, lambda: not attachment._stop.is_set())
                    app.query_one("#views", TabbedContent).active = "summary-tab"
                    await until(pilot, attachment._stop.is_set)
                count = len(reads)
                publish(app, managed["update"]())
                await pilot.pause()
                assert len(reads) == count
                release.set()
                await until(pilot, lambda: attachment.selection is None)
            finally:
                release.set()

    asyncio.run(check())


def test_newer_publication_does_not_starve_an_already_completed_rich_read(managed, monkeypatch) -> None:
    configure(managed, monkeypatch)
    original = AgendaReader.latest
    blocked, release = Event(), Event()
    calls = []

    async def check() -> None:
        loop = asyncio.get_running_loop()
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))

        def delayed(reader, *args):
            pages = original(reader, *args)
            calls.append(1)
            if len(calls) == 1:
                newer = managed["update"]()
                loop.call_soon_threadsafe(publish, app, newer)
            elif len(calls) == 2:
                blocked.set()
                assert release.wait(10)
            return pages

        monkeypatch.setattr(AgendaReader, "latest", delayed)
        async with app.run_test(size=(110, 40)) as pilot:
            try:
                await until(pilot, lambda: not app._loading)
                app._agenda_cancel = Event()
                publish(app, managed["publication"]())
                app.query_one("#views", TabbedContent).active = "timeline-tab"
                attachment = app._rich_agenda_attachment
                await until(pilot, blocked.is_set, attempts=400)
                assert attachment.token is not None
                assert len(app._timeline_panel.window.events) == 1
                release.set()
                await until(pilot, lambda: len(app._timeline_panel.window.events) == 2)
            finally:
                release.set()

    asyncio.run(check())


def test_reader_info_revision_fences_a_rewind_before_the_first_page(managed, monkeypatch) -> None:
    reader = AgendaReader(
        ("example", "selected", "head-a"), "b" * 32,
        cwd=str(managed["cwd"]), project_root=str(managed["cwd"]),
    )
    reader.commands.discover(Event())
    publication = managed["publication"]()["observation"]
    older = reader.commands.read(Event(), history=True)
    managed["update"]()
    original = reader.commands.read

    def rewind(cancelled, *, history=False, before=None):
        return older if history else original(cancelled)

    monkeypatch.setattr(reader.commands, "read", rewind)
    with pytest.raises(OwnerReadError, match="source-replaced"):
        reader.latest(publication, Event(), None)
    assert reader.revision == publication["revision"] + 1


def test_retired_viewer_history_message_cannot_invalidate_healthy_source(managed, monkeypatch) -> None:
    configure(managed, monkeypatch)
    for _ in range(35):
        managed["update"]()
    monkeypatch.setattr(SubwayTimeline, "action_request_history", lambda self: None)

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            await until(pilot, lambda: not app._loading)
            app._agenda_cancel = Event()
            publish(app, managed["publication"]())
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            attachment = app._rich_agenda_attachment
            await until(pilot, lambda: attachment.token is not None)
            old = attachment.token
            before = app._timeline_panel.window.sources[old.identity.source_key].before
            assert before is not None
            await attachment.set_selection(None)
            await attachment.set_selection(
                ("example", "selected", "head-a"), managed["publication"](),
                project_root=str(managed["cwd"]),
            )
            await until(pilot, lambda: attachment.token is not None)
            current = attachment.token
            assert current != old
            window = app._timeline_panel.window
            state = window.sources[current.identity.source_key]
            assert state.availability is Availability.OBSERVED
            assert window.request_history(current, state.before).applied
            requested = window.sources[current.identity.source_key].requested_before
            assert requested is not None
            attachment.history(SubwayTimeline.HistoryRequested(old, before))
            state = window.sources[current.identity.source_key]
            assert state.availability is Availability.OBSERVED
            assert state.requested_before == requested

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["latest", "history"])
def test_agenda_integrity_and_backward_history_survive_reconciled_window(managed, monkeypatch, kind) -> None:
    real_selection = worktree_attachment.TimelineAttachment.set_selection
    configure(managed, monkeypatch)
    monkeypatch.setattr(SubwayTimeline, "action_request_history", lambda self: None)
    if kind == "history":
        for _ in range(35):
            managed["update"]()

    class Source:
        async def prepare(self, epoch, *, stop):
            self.initial = owner_snapshot(epoch)
            return self.initial

        async def observe(self, token, *, stop):
            yield self.initial
            await stop.wait()

        async def history(self, token, before, *, stop):
            return HistoryPage(token, before, None, (), Completeness.PARTIAL)

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            await until(pilot, lambda: not app._loading)
            app._agenda_cancel = Event()
            app._timeline_panel.replace_window(TimelineWindow(max_proofs=64))
            owner = owner_snapshot(0)
            assert app._timeline_panel.bind_source(owner.token).applied
            assert app._timeline_panel.apply_snapshot(owner).applied
            publish(app, managed["publication"]())
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            attachment = app._rich_agenda_attachment
            await until(pilot, lambda: attachment.token is not None)
            old = app._timeline_panel.window
            retained = next(row for row in old.events if row.source.provider is Provider.AGENDA)
            result = app._timeline_panel.apply_delta(LiveDelta(
                owner.token, owner.position,
                SourcePosition(owner.token.identity.source_key, owner.token.identity.generation, "fixture-overflow"),
                tuple(event(owner.token, number) for number in range(1, 65)),
            ))
            assert result.code is ResultCode.LIMIT and owner.token.identity.source_key in old.reconciliation_required
            monkeypatch.setattr(worktree_attachment, "WorktreeTimelineSource", lambda *args, **kwargs: Source())
            await real_selection(
                app._timeline_attachment, ("example", "selected", "head-a"), reconcile=True,
            )
            window = app._timeline_panel.window
            assert window is not old and app._timeline_attachment.retained_windows == (old,)
            assert retained.ref in old._proofs and retained.ref in window._proofs
            assert {state.token.identity.provider for state in window.sources.values()} == {Provider.WORKTREES, Provider.AGENDA}
            reader, token = attachment.reader, attachment.token
            original = reader.commands.read
            changed = []

            def corrupt(cancelled, *, history=False, before=None):
                frame = original(cancelled, history=history, before=before)
                if history and kind == "latest" and before is None:
                    frame = deepcopy(frame)
                    for entry in frame["entries"]:
                        if entry["event_id"] == retained.event_id:
                            entry["result"]["records"][0]["body"]["fixture_note"] = "rewritten retained content"
                            changed.append(entry["event_id"])
                    reader.codec.events(reader.codec.decode(json.dumps(frame).encode()), token)
                return frame

            monkeypatch.setattr(reader.commands, "read", corrupt)
            if kind == "history":
                for _ in range(10):
                    managed["update"]()
            previous = window.sources[token.identity.source_key].position
            publish(app, managed["publication"]())
            if kind == "history":
                await until(pilot, lambda: window.sources[token.identity.source_key].position != previous)
                state = window.sources[token.identity.source_key]
                assert state.before is not None and retained.ref in window._proofs
                rows = len(window.events)
                assert window.request_history(state.token, state.before).applied
                attachment.history(SubwayTimeline.HistoryRequested(state.token, state.before))
                await until(pilot, lambda: window.sources[token.identity.source_key].requested_before is None)
                assert len(window.events) > rows
            else:
                await until(pilot, lambda: "immutable event conflicts" in str(
                    app.query_one("#timeline-agenda-status", Static).content,
                ))
                assert changed == [retained.event_id]
            assert retained.ref in old._proofs and retained.ref in window._proofs
            assert all("rewritten retained content" not in (row.data_json or "") for row in window.events)

    asyncio.run(check())


@pytest.mark.parametrize("delay_worktree", [False, True])
def test_refresh_reconciles_both_exhausted_owners_and_preserves_healthy_bridge(
    managed, monkeypatch, delay_worktree,
) -> None:
    real_selection = worktree_attachment.TimelineAttachment.set_selection
    configure(managed, monkeypatch)
    monkeypatch.setattr(SubwayTimeline, "action_request_history", lambda self: None)
    for _ in range(35):
        managed["update"]()
    entered, release = asyncio.Event(), asyncio.Event()

    class Source:
        async def prepare(self, epoch, *, stop):
            entered.set()
            if delay_worktree:
                await release.wait()
            self.initial = owner_snapshot(epoch)
            return self.initial

        async def observe(self, token, *, stop):
            yield self.initial
            await stop.wait()

    async def no_bridge(*args, **kwargs):
        return None

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            try:
                await until(pilot, lambda: not app._loading)
                app._agenda_cancel = Event()
                app._timeline_panel.replace_window(TimelineWindow(max_proofs=64))
                owner = owner_snapshot(0)
                app._timeline_panel.bind_source(owner.token)
                app._timeline_panel.apply_snapshot(owner)
                bridge = SourceToken(replace(
                    owner.token.identity, provider=Provider.BRIDGE,
                    source_key="healthy-bridge", head_session_id=None,
                ), 0)
                app._timeline_panel.bind_source(bridge)
                bridge_row = event(bridge, 0)
                app._timeline_panel.apply_snapshot(TimelineSnapshot(
                    bridge, bridge_row.position, Availability.OBSERVED, Completeness.PARTIAL,
                    events=(bridge_row,),
                ))
                publish(app, managed["publication"]())
                app.query_one("#views", TabbedContent).active = "timeline-tab"
                attachment = app._rich_agenda_attachment
                await until(pilot, lambda: attachment.token is not None)
                old = app._timeline_panel.window
                agenda_key = attachment.token.identity.source_key
                bridge_state = old.sources[bridge.identity.source_key]
                previous = old.sources[agenda_key].position
                for _ in range(40):
                    managed["update"]()
                publish(app, managed["publication"]())
                await until(pilot, lambda: old.sources[agenda_key].position != previous)
                for _ in range(30):
                    managed["update"]()
                publish(app, managed["publication"]())
                await until(pilot, lambda: agenda_key in old.reconciliation_required)
                assert not app._timeline_panel.apply_delta(LiveDelta(
                    owner.token, owner.position,
                    SourcePosition(owner.token.identity.source_key, owner.token.identity.generation, "overflow"),
                    tuple(event(owner.token, number) for number in range(1, 65)),
                )).applied
                assert old.reconciliation_required == frozenset({agenda_key, owner.token.identity.source_key})
                monkeypatch.setattr(worktree_attachment.TimelineAttachment, "set_selection", real_selection)
                monkeypatch.setattr(worktree_attachment, "WorktreeTimelineSource", lambda *args, **kwargs: Source())
                monkeypatch.setattr(app._bridge_attachment, "set_selection", no_bridge)
                app._sync_timeline(reconcile=True)
                await until(pilot, entered.is_set)

                async def recovered(predicate):
                    try:
                        await until(pilot, predicate)
                    except AssertionError as error:
                        raise AssertionError({
                            "agenda": str(app.query_one("#timeline-agenda-status", Static).content),
                            "worktree": str(app.query_one(".timeline-state", Static).content),
                            "fences": app._timeline_panel.window.reconciliation_required,
                            "states": app._timeline_panel.window.sources,
                        }) from error

                if delay_worktree:
                    await recovered(lambda: agenda_key not in app._timeline_panel.window.reconciliation_required)
                    assert owner.token.identity.source_key in app._timeline_panel.window.reconciliation_required
                    release.set()
                await recovered(lambda: not app._timeline_panel.window.reconciliation_required)
                window = app._timeline_panel.window
                assert window is not old and len(app._timeline_attachment.retained_windows) == 2
                assert app._timeline_attachment.retained_windows[0] is old
                assert window.sources[bridge.identity.source_key] == bridge_state
                assert window._proofs[bridge_row.ref] == old._proofs[bridge_row.ref]
                assert bridge_row in window.events
                assert bridge_state.availability is Availability.UNKNOWN
                assert all(state.availability is Availability.OBSERVED for state in window.sources.values()
                           if state.token.identity.provider is not Provider.BRIDGE)
                previous = window.sources[agenda_key].position
                publish(app, managed["update"]())
                await until(pilot, lambda: window.sources[agenda_key].position != previous)
                assert not window.reconciliation_required
            finally:
                release.set()

    asyncio.run(check())

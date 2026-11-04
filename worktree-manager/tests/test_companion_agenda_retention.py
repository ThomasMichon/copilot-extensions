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


def owner_snapshot(epoch: int) -> TimelineSnapshot:
    token = SourceToken(SourceIdentity(
        Provider.WORKTREES, "fixture-worktree", "example", "selected", "head-a",
        "fixture-generation", owner_ref="fixture-owner",
    ), epoch)
    return TimelineSnapshot(
        token, SourcePosition(token.identity.source_key, token.identity.generation, f"fixture-position-{epoch}"),
        Availability.OBSERVED, Completeness.PARTIAL,
        events=(event(token, 0), event(token, 1)) if epoch else (event(token, 0),),
        as_of=datetime.now(timezone.utc), facts_json='{"coverage":"fixture"}',
    )


def test_agenda_invalidation_fences_a_successful_inflight_worktree_prepare(managed, monkeypatch) -> None:
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


@pytest.mark.parametrize("kind", ["latest", "history"])
def test_agenda_conflict_is_rejected_against_actual_reconciled_window_archive(managed, monkeypatch, kind) -> None:
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
            assert retained.ref in old._proofs and retained.ref not in window._proofs
            assert all(state.token.identity.provider is Provider.WORKTREES for state in window.sources.values())
            reader, token = attachment.reader, attachment.token
            original = reader.commands.read
            changed = []

            def corrupt(cancelled, *, history=False, before=None):
                frame = original(cancelled, history=history, before=before)
                if history and ((kind == "latest" and before is None) or (kind == "history" and before is not None)):
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
            publish(app, managed["publication"]())
            if kind == "history":
                await until(pilot, lambda: token.identity.source_key in window.sources)
                state = window.sources[token.identity.source_key]
                assert state.before is not None and retained.ref not in window._proofs
                assert window.request_history(state.token, state.before).applied
                attachment.history(SubwayTimeline.HistoryRequested(state.token, state.before))
            await until(pilot, lambda: "immutable event conflicts" in str(
                app.query_one("#timeline-agenda-status", Static).content,
            ))
            assert changed == [retained.event_id]
            assert retained.ref in old._proofs and retained.ref not in window._proofs
            assert all("rewritten retained content" not in (row.data_json or "") for row in window.events)

    asyncio.run(check())

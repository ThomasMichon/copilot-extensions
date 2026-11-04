"""Concurrent owner invalidation, independent page reads and selected-root activation."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
from threading import Event

import pytest
from textual.widgets import Static, TabbedContent

from worktree_manager import mux_companion as companion
from worktree_manager.companion_agenda_attachment import AgendaReader
from worktree_manager.companion_timeline_contract import (
    Availability, Completeness, Provider, SourceIdentity, SourcePosition,
    SourceToken, TimelineSnapshot,
)
from worktree_manager.companion_timeline_widget import SubwayTimeline
from worktree_manager.companion_worktree_codec import OwnerReadError
from _companion_timeline_fixtures import event
from test_companion_agenda_live import until
from test_companion_agenda_rich import current_managed_data, managed as managed

pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.skipif(os.name == "nt", reason="POSIX attributed-interpreter fixture; native qualification remains open"),
]


def configure(managed, monkeypatch) -> None:
    async def no_worktree_source(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(companion, "_load_current_worktree", lambda *args, **kwargs: current_managed_data(managed))
    monkeypatch.setattr(companion.MuxCompanionApp, "_start_agenda", lambda self: None)
    monkeypatch.setattr(companion.MuxCompanionApp, "_poll_attention", lambda *args, **kwargs: None)
    monkeypatch.setattr(companion.TimelineAttachment, "set_selection", no_worktree_source)


def publish(app, frame, error=None) -> None:
    app._receive_agenda(app._data, app._agenda_generation, app._agenda_cancel, frame, error)


def seed_worktree(panel, *, populated: bool = False) -> TimelineSnapshot:
    token = SourceToken(SourceIdentity(
        Provider.WORKTREES, "fixture-worktree", "example", "selected", "head-a", "fixture-generation",
    ), 0)
    snapshot = TimelineSnapshot(
        token, SourcePosition(token.identity.source_key, token.identity.generation, "fixture-position"),
        Availability.OBSERVED, Completeness.PARTIAL,
        events=(event(token, 0),) if populated else (),
    )
    assert panel.bind_source(token).applied
    assert panel.apply_snapshot(snapshot).applied
    return snapshot


@pytest.mark.parametrize("phase", ["initial", "same-revision", "history"])
def test_worktree_head_change_fences_inflight_rich_results_without_agenda_callback(
    managed, monkeypatch, phase: str,
) -> None:
    configure(managed, monkeypatch)
    if phase == "history":
        for _ in range(35):
            managed["update"]()
    blocked, captured, release = Event(), Event(), Event()
    original = AgendaReader.history if phase == "history" else AgendaReader.latest

    def delayed(self, *args):
        value = original(self, *args)
        if blocked.is_set():
            captured.set()
            assert release.wait(5), "fixture did not release its captured owner result"
        return value

    monkeypatch.setattr(AgendaReader, "history" if phase == "history" else "latest", delayed)

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            try:
                await until(pilot, lambda: not app._loading)
                app._agenda_cancel = Event()
                publish(app, managed["publication"]())
                owner = seed_worktree(app._timeline_panel)
                if phase == "initial":
                    blocked.set()
                app.query_one("#views", TabbedContent).active = "timeline-tab"
                attachment = app._rich_agenda_attachment
                if phase != "initial":
                    await until(pilot, lambda: attachment.token is not None)
                    blocked.set()
                    if phase == "history":
                        state = app._timeline_panel.window.sources[attachment.token.identity.source_key]
                        assert state.before is not None
                        assert app._timeline_panel.window.request_history(state.token, state.before).applied
                        attachment.history(SubwayTimeline.HistoryRequested(state.token, state.before))
                    else:
                        publish(app, managed["publication"]())
                await until(pilot, captured.is_set)
                version, stop = attachment._version, attachment._stop
                assert not stop.is_set()
                assert app._timeline_panel.apply_snapshot(replace(
                    owner, availability=Availability.HEAD_CHANGED,
                )).applied
                assert not app._timeline_panel.window.events
                assert attachment._version == version and not stop.is_set()
                release.set()
                await until(pilot, stop.is_set)
                await pilot.pause()
                assert not app._timeline_panel.window.events
                assert all(
                    state.availability is Availability.HEAD_CHANGED
                    for state in app._timeline_panel.window.sources.values()
                )
                publish(app, managed["publication"]())
                await pilot.pause()
                assert not app._timeline_panel.window.events
            finally:
                release.set()

    asyncio.run(check())


def test_agenda_head_change_before_first_token_clears_displayed_worktree_rows(managed, monkeypatch) -> None:
    configure(managed, monkeypatch)
    captured, release = Event(), Event()
    original = AgendaReader.latest

    def delayed(self, *args):
        pages = original(self, *args)
        captured.set()
        assert release.wait(5)
        return pages

    monkeypatch.setattr(AgendaReader, "latest", delayed)

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            try:
                await until(pilot, lambda: not app._loading)
                app._agenda_cancel = Event()
                publish(app, managed["publication"]())
                seed_worktree(app._timeline_panel, populated=True)
                app.query_one("#views", TabbedContent).active = "timeline-tab"
                await until(pilot, captured.is_set)
                assert app._rich_agenda_attachment.token is None
                assert app._timeline_panel.window.events
                publish(app, {**managed["publication"](), "availability": "head-changed", "observation": None})
                assert not app._timeline_panel.window.events
                assert all(
                    state.availability is Availability.HEAD_CHANGED
                    for state in app._timeline_panel.window.sources.values()
                )
                release.set()
                await until(pilot, lambda: app._rich_agenda_attachment._task.done())
                assert not app._timeline_panel.window.events
            finally:
                release.set()

    asyncio.run(check())


@pytest.mark.parametrize("outcome", ["page", "error"])
def test_retired_backward_request_cannot_downgrade_a_recovered_live_source(managed, monkeypatch, outcome) -> None:
    configure(managed, monkeypatch)
    for _ in range(35):
        managed["update"]()
    captured, release = Event(), Event()

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
                reader, token = attachment.reader, attachment.token
                original = reader.commands.read
                history_revisions = []

                def delayed(cancelled, *, history=False, before=None):
                    frame = original(cancelled, history=history, before=before)
                    if before is not None:
                        history_revisions.append(reader.codec.revision(frame["cursor"]))
                        if len(history_revisions) == 1:
                            captured.set()
                            assert release.wait(5)
                            if outcome == "error":
                                raise OwnerReadError("source-unavailable", "retired history read")
                    return frame

                monkeypatch.setattr(reader.commands, "read", delayed)
                window = app._timeline_panel.window
                before = window.sources[token.identity.source_key].before
                assert before is not None and window.request_history(token, before).applied
                attachment.history(SubwayTimeline.HistoryRequested(token, before))
                await until(pilot, captured.is_set)
                publish(app, None, "fixture transient disconnect")
                state = window.sources[token.identity.source_key]
                assert state.availability is Availability.UNAVAILABLE and state.requested_before is None
                publish(app, managed["update"]())
                await until(pilot, lambda: window.sources[token.identity.source_key].availability is Availability.OBSERVED)
                state = window.sources[token.identity.source_key]
                live = state.position
                assert attachment.token == token
                state_before = state.requested_before
                release.set()
                await until(pilot, lambda: attachment._page.done() and window.sources[token.identity.source_key].requested_before is None)
                assert window.sources[token.identity.source_key].availability is Availability.OBSERVED
                assert window.sources[token.identity.source_key].position == live
                if state_before is not None:
                    assert len(history_revisions) == 2 and history_revisions[1] > history_revisions[0]
            finally:
                release.set()

    asyncio.run(check())


def test_explicit_new_head_refresh_supersedes_agenda_only_retired_evidence(managed, monkeypatch) -> None:
    configure(managed, monkeypatch)
    selected = {"head": "head-a"}

    def data(*args, **kwargs):
        current = current_managed_data(managed)
        current.sessions = [{"id": selected["head"], "is_head": True}]
        return current

    monkeypatch.setattr(companion, "_load_current_worktree", data)

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(managed["cwd"]))
        async with app.run_test(size=(110, 40)) as pilot:
            await until(pilot, lambda: not app._loading)
            app._agenda_cancel = Event()
            publish(app, managed["publication"]())
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            attachment = app._rich_agenda_attachment
            await until(pilot, lambda: attachment.token is not None)
            publish(app, {**managed["publication"](), "availability": "head-changed", "observation": None})
            assert not app._timeline_panel.window.events
            selected["head"] = "head-b"
            next_publication = managed["update"](session="head-b")
            app.action_refresh()
            await until(pilot, lambda: not app._loading and app._data.sessions[0]["id"] == "head-b")
            await until(pilot, lambda: attachment.selection == ("example", "selected", "head-b"))
            app._agenda_cancel = Event()
            publish(app, next_publication)
            await until(pilot, lambda: attachment.token is not None and attachment.token.identity.head_session_id == "head-b")
            assert all(
                row.source.head_session_id == "head-b" for row in app._timeline_panel.window.events
            )
            assert len(app._timeline_panel.window.events) == 2
            assert not any(
                state.token.identity.provider is Provider.WORKTREES
                for state in app._timeline_panel.window.sources.values()
            )

    asyncio.run(check())


def test_live_update_preserves_an_inflight_backward_page_and_its_start_revision(managed, monkeypatch) -> None:
    configure(managed, monkeypatch)
    for _ in range(35):
        managed["update"]()
    captured, release = Event(), Event()

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
                reader, token = attachment.reader, attachment.token
                original = reader.commands.read
                historical = []

                def delayed(cancelled, *, history=False, before=None):
                    frame = original(cancelled, history=history, before=before)
                    if before is not None:
                        historical.append(frame)
                        captured.set()
                        assert release.wait(5), "fixture did not release its captured history page"
                    return frame

                monkeypatch.setattr(reader.commands, "read", delayed)
                window = app._timeline_panel.window
                state = window.sources[token.identity.source_key]
                before, start_revision = state.before, reader.revision
                assert before is not None
                assert window.request_history(token, before).applied
                attachment.history(SubwayTimeline.HistoryRequested(token, before))
                await until(pilot, captured.is_set)
                publish(app, managed["update"]())
                await until(pilot, lambda: reader.revision == start_revision + 1)
                await until(pilot, lambda: reader.codec.revision(
                    window.sources[token.identity.source_key].position.cursor,
                ) == start_revision + 1)
                live = window.sources[token.identity.source_key].position
                assert window.sources[token.identity.source_key].requested_before == before
                release.set()
                await until(pilot, lambda: attachment._page.done())
                state = window.sources[token.identity.source_key]
                assert state.availability is Availability.OBSERVED and state.position == live
                assert state.requested_before is None
                assert reader.codec.revision(historical[0]["cursor"]) == start_revision
                assert all(
                    any(event.event_id == entry["event_id"] for event in window.events)
                    for entry in historical[0]["entries"]
                )
            finally:
                release.set()

    asyncio.run(check())


@pytest.mark.parametrize("advance", ["catch-up", "history"])
def test_every_owner_page_retains_its_revision_fence(managed, monkeypatch, advance) -> None:
    reader = AgendaReader(
        ("example", "selected", "head-a"), "b" * 32,
        cwd=str(managed["cwd"]), project_root=str(managed["cwd"]),
    )
    initial = reader.latest(managed["publication"]()["observation"], Event(), None)[0]
    for _ in range(35):
        managed["update"]()
    publication = managed["publication"]()["observation"]
    original = reader.commands.read
    older = original(Event(), history=True)
    assert older["next_before"] is not None
    advanced = False

    def read(cancelled, *, history=False, before=None):
        nonlocal advanced
        if advance == "catch-up" and before is not None and not advanced:
            managed["update"]()
            advanced = True
        return original(cancelled, history=history, before=before)

    monkeypatch.setattr(reader.commands, "read", read)
    reader.latest(publication, Event(), initial["cursor"] if advance == "catch-up" else None)
    if advance == "history":
        managed["update"]()
        reader.history(Event(), older["next_before"])
    assert reader.revision == publication["revision"] + 1

    def rewound(cancelled, *, history=False, before=None):
        return older if history and before is None else original(
            cancelled, history=history, before=before,
        )

    monkeypatch.setattr(reader.commands, "read", rewound)
    with pytest.raises(OwnerReadError, match="source-replaced"):
        reader.latest(publication, Event(), None)
    assert reader.revision == publication["revision"] + 1


def test_history_advance_preserves_an_already_inflight_latest_page(managed, monkeypatch) -> None:
    reader = AgendaReader(
        ("example", "selected", "head-a"), "b" * 32,
        cwd=str(managed["cwd"]), project_root=str(managed["cwd"]),
    )
    for _ in range(35):
        managed["update"]()
    publication = managed["publication"]()["observation"]
    initial = reader.latest(publication, Event(), None)[0]
    original = reader.commands.read
    captured, release = Event(), Event()

    def delayed(cancelled, *, history=False, before=None):
        frame = original(cancelled, history=history, before=before)
        if history and before is None:
            captured.set()
            assert release.wait(10)
        return frame

    monkeypatch.setattr(reader.commands, "read", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(reader.latest, publication, Event(), None)
        try:
            assert captured.wait(10)
            managed["update"]()
            reader.history(Event(), initial["next_before"])
            assert reader.revision == publication["revision"] + 1
        finally:
            release.set()
        pages = pending.result(timeout=10)
    assert reader.codec.revision(pages[-1]["cursor"]) == publication["revision"]
    assert reader.revision == publication["revision"] + 1


@pytest.mark.parametrize("disabled", [False, True])
def test_nested_launch_uses_selected_root_activation_and_respects_project_disable(
    managed, monkeypatch, disabled: bool,
) -> None:
    configure(managed, monkeypatch)
    nested = managed["cwd"] / "src"
    nested.mkdir()
    if disabled:
        global_settings = Path(os.environ["HOME"]) / ".copilot/settings.json"
        global_settings.parent.mkdir()
        global_settings.write_text(managed["settings"].read_text(), encoding="utf-8")
        managed["settings"].write_text(json.dumps({
            "enabledPlugins": {"efforts@source-fixture": False},
        }), encoding="utf-8")

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(nested))
        async with app.run_test(size=(110, 40)) as pilot:
            await until(pilot, lambda: not app._loading)
            app._agenda_cancel = Event()
            publish(app, managed["publication"]())
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            attachment = app._rich_agenda_attachment
            if disabled:
                await until(pilot, lambda: "unambiguous enabled efforts" in str(
                    app.query_one("#timeline-agenda-status", Static).content,
                ))
                assert not app._timeline_panel.window.events
                assert attachment.reader.commands.script is None
            else:
                # Discovery composes several independently bounded owner commands.
                await until(pilot, lambda: attachment.token is not None, attempts=1200)
                assert app._timeline_panel.window.events
            assert attachment.reader.commands.cwd == str(nested)
            assert attachment.reader.commands.project_root == str(managed["cwd"])
            assert app._data.agenda_error is None

    asyncio.run(check())

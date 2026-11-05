"""Actual parked callbacks reach a view independently of its retained event window."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import importlib
import json
import os
from types import SimpleNamespace
from threading import Thread

import pytest
from textual.widgets import Select, Static, TabbedContent

from worktree_manager import companion_bridge_source as bridge
from worktree_manager import mux_companion as companion
from worktree_manager.companion_bridge_current_requests import (
    CurrentRequestProjection, decode_current_requests,
)
from worktree_manager.companion_bridge_attachment import BridgeTimelineAttachment
from worktree_manager.companion_bridge_tool_statistics import decode_tool_statistics
from worktree_manager.companion_timeline_widget import CompanionTimelinePanel
from worktree_manager.companion_timeline_contract import TimelineContractError
from worktree_manager.companion_worktree_source import WorktreeTimelineSource
from test_companion_agenda_live import current_data, until
from test_companion_bridge_codec import OWNER, member
from test_companion_bridge_session_source import semantic_peer as semantic_peer, source

pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.skipif(os.name == "nt", reason="POSIX private CLI pipe; native Windows qualification is separate"),
]


@pytest.fixture
def actual_waiters(semantic_peer):
    schema = importlib.import_module("acp.schema")
    client_type = importlib.import_module("agent_bridge.acp_client").AcpClient
    callback_type = importlib.import_module("agent_bridge.acp_callbacks")._BridgeClientImpl
    tasks = []

    async def initialize():
        client = client_type()
        client._connection = object()
        client._process = SimpleNamespace(
            returncode=None, stdin=SimpleNamespace(is_closing=lambda: False),
            stdout=SimpleNamespace(at_eof=lambda: False),
        )
        client._acp_session_id = "synthetic-session"
        client.auto_approve = False
        client.current_requests.bind(callback_type(client), client._connection, client._process)
        client.current_requests.session_changed(client.acp_session_id)
        semantic_peer["session"].client = client
        semantic_peer["session"].acp_session_id = client.acp_session_id
        semantic_peer["session"].status = "running"
        return client

    loop = semantic_peer["owner_loop"]
    client = asyncio.run_coroutine_threadsafe(initialize(), loop).result(timeout=5)

    async def action(name: str):
        if name == "input":
            mode = schema.ElicitationFormSessionMode(
                session_id=client.acp_session_id, tool_call_id="private-tool",
                requested_schema=schema.ElicitationSchema.model_validate({"type": "object"}),
            )
            tasks.append(asyncio.create_task(
                client.current_requests._producer.create_elicitation("Private question", mode)
            ))
        elif name == "permission":
            tasks.append(asyncio.create_task(client.current_requests._producer.request_permission(
                [schema.PermissionOption(option_id="private-option", name="Private option", kind="allow_once")],
                session_id=client.acp_session_id,
                tool_call=schema.ToolCallUpdate(tool_call_id="private-permission", title="Private title"),
            )))
        elif name == "clear":
            client.resolve_elicitation("private-tool", {}, action="cancel")
        elif name == "reset":
            client._reset_buffers()
        elif name == "disconnect":
            client._connection = None
            client.current_requests.changed()
        elif name == "cleanup":
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
        return client.current_requests.snapshot(client).as_dict()

    async def invoke(name: str):
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(action(name), loop))

    try:
        yield invoke
    finally:
        asyncio.run_coroutine_threadsafe(action("cleanup"), loop).result(timeout=5)
        assert all(task.done() for task in tasks)


def test_current_revision_is_independent_of_clock_and_keeps_loss_qualification(actual_waiters) -> None:
    async def check() -> None:
        pending = decode_current_requests(await actual_waiters("input"))
        projection = CurrentRequestProjection()
        assert projection.accept(pending)
        assert not projection.accept(replace(pending, observed_at=pending.observed_at + timedelta(seconds=1)))
        with pytest.raises(TimelineContractError, match="regressed"):
            projection.accept(replace(pending, requests=()))
        with pytest.raises(TimelineContractError, match="regressed"):
            projection.accept(replace(pending, revision=pending.revision - 1))
        projection.accept(decode_current_requests({
            "version": 1, "client_incarnation": None, "revision": None, "availability": "unknown",
            "completeness": "unknown", "reason": "no-current-client",
            "observed_at": pending.observed_at.isoformat(), "requests": [],
        }))
        with pytest.raises(TimelineContractError, match="regressed"):
            projection.accept(replace(pending, revision=pending.revision - 1))
        projection.invalidate()
        assert projection.current is None and projection.accept(pending)
        assert projection.accept(replace(pending, client_incarnation="b" * 32, revision=1))
        unknown = decode_current_requests(await actual_waiters("disconnect"))
        assert projection.accept(unknown) and "unknown" in unknown.summary()

    asyncio.run(check())


def test_busy_ui_has_one_queued_current_update_and_retired_callbacks_cannot_revive(
    actual_waiters, tmp_path, monkeypatch,
) -> None:
    async def check() -> None:
        state = decode_current_requests(await actual_waiters("input"))
        delivered, queued, failures = [], [], []
        selection = ("example", "selected", "head-a")
        attachment = BridgeTimelineAttachment(
            CompanionTimelinePanel(), cwd=str(tmp_path), owner_ref=lambda: OWNER,
            project_root=lambda: str(tmp_path), current_status=delivered.append,
            head_current=lambda selected: selected == selection,
        )
        attachment.selection = selection
        attachment._chosen = member(), "fixture-generation"
        monkeypatch.setattr(attachment, "_renderable", lambda: True)
        reader = attachment._source(selection)
        statistics = decode_tool_statistics({
            "version": 1, "availability": "observed", "reason": "native-event-range",
            "basis": "native-tool-start-record-occurrences", "coverage": "partial",
            "durability": "unknown", "start_occurrences": 100, "observed_records": 100,
            "floor": 1, "head": 100, "contiguous": True,
        }, {"journal_incarnation": "fixture"}, {"floor": 1, "head": 100})
        loop = asyncio.get_running_loop()
        original = loop.call_soon_threadsafe

        def schedule(callback, *args, **kwargs):
            queued.append(callback)
            return original(callback, *args, **kwargs)

        monkeypatch.setattr(loop, "call_soon_threadsafe", schedule)

        def produce() -> None:
            try:
                for revision in range(1, 101):
                    reader._current(replace(state, revision=revision))
                    reader._statistics(replace(statistics, head=revision, observed_records=revision,
                                               start_occurrences=revision))
            except BaseException as error:
                failures.append(error)

        worker = Thread(target=produce)
        worker.start()
        # Deliberately hold the UI loop while its actual producer thread posts.
        worker.join(timeout=3)
        assert not worker.is_alive() and not failures
        assert len(queued) == 1 and not delivered
        await asyncio.sleep(0)
        assert delivered[-1].revision == 100
        assert attachment.tool_statistics == statistics
        reader._current(replace(state, revision=101))
        reader._statistics(replace(statistics, head=101, observed_records=101, start_occurrences=101))
        attachment.stop_now()
        await asyncio.sleep(0)
        assert delivered[-1] is None and attachment.current_requests is None
        assert attachment.tool_statistics is None

    asyncio.run(check())


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(version=True),
    lambda value: value.update(revision=True),
    lambda value: value.update(completeness="complete", reason="waiter-coverage"),
    lambda value: value.update(availability="unknown", completeness="unknown", reason="client-disconnected"),
    lambda value: value["requests"][0].update(answer="private-answer"),
    lambda value: value["requests"].append(deepcopy(value["requests"][0])),
    lambda value: value["requests"][0].update(occurrence_id="private-tool"),
    lambda value: value.update(observed_at="2026-11-04T00:00:00"),
])
def test_current_projection_rejects_malformed_or_answer_bearing_source(actual_waiters, mutate) -> None:
    async def check() -> None:
        value = await actual_waiters("input")
        mutate(value)
        with pytest.raises(TimelineContractError):
            decode_current_requests(value)

    asyncio.run(check())


@pytest.mark.parametrize("older", ["old_cli", "old_provider"])
def test_older_selected_cli_or_running_provider_retains_baseline(
    semantic_peer, actual_waiters, tmp_path, older: str,
) -> None:
    semantic_peer[older].touch()
    semantic_peer["log"].append("turn_complete", {})

    async def check() -> None:
        await actual_waiters("input")
        reader = source(tmp_path)
        snapshot = await reader.prepare(1, stop=asyncio.Event())
        assert snapshot.events and reader.current_requests.current is None
        assert reader.commands.current_requests is False
        assert all("current_requests" not in request["path"] for request in semantic_peer["requests"])

    asyncio.run(check())


@pytest.mark.parametrize("declaration", [
    {"schema": "agent-bridge.cli-contracts", "version": True, "contracts": {}},
    {"schema": "agent-bridge.cli-contracts", "version": 1, "contracts": {
        "bridge-session-current-requests-v1": {
            "version": True, "capabilities": ["observe-session-current-requests"],
        },
    }},
    {"schema": "agent-bridge.cli-contracts", "version": 1, "contracts": {
        "bridge-session-current-requests-v1": {"version": 1, "capabilities": "observe-session-current-requests"},
    }},
])
def test_malformed_selected_declaration_never_guesses_new_syntax(semantic_peer, tmp_path, declaration) -> None:
    semantic_peer["declaration_override"].write_text(json.dumps(declaration))
    semantic_peer["log"].append("turn_complete", {})

    async def check() -> None:
        reader = source(tmp_path)
        assert (await reader.prepare(1, stop=asyncio.Event())).events
        assert reader.commands.current_requests is False
        assert all("current_requests" not in request["path"] for request in semantic_peer["requests"])

    asyncio.run(check())


def test_unrecognized_optional_invocation_never_retries_baseline(semantic_peer, tmp_path) -> None:
    semantic_peer["reject_flag"].touch()
    semantic_peer["log"].append("turn_complete", {})

    async def check() -> None:
        with pytest.raises(bridge.OwnerReadError, match="unrecognized arguments"):
            await source(tmp_path).prepare(1, stop=asyncio.Event())
        assert not semantic_peer["requests"]

    asyncio.run(check())


@pytest.mark.parametrize("version", [True, 1.0])
def test_malformed_prequery_refusal_never_retries_baseline(semantic_peer, tmp_path, version) -> None:
    semantic_peer["refusal_version"].write_text(json.dumps(version))
    semantic_peer["log"].append("turn_complete", {})

    async def check() -> None:
        with pytest.raises(TimelineContractError, match="action or version"):
            await source(tmp_path).prepare(1, stop=asyncio.Event())
        assert not semantic_peer["requests"]

    asyncio.run(check())


def test_empty_history_delivers_live_state_without_fabricating_event_continuity(
    semantic_peer, actual_waiters, tmp_path,
) -> None:
    async def check() -> None:
        reader, stop, delivered = source(tmp_path), asyncio.Event(), []
        reader._current = delivered.append
        preparing = asyncio.create_task(reader.prepare(1, stop=stop))
        try:
            await actual_waiters("input")
            for _ in range(100):
                if any(value is not None and value.requests for value in delivered):
                    break
                await asyncio.sleep(0.05)
            assert any(value is not None and value.requests for value in delivered)
            assert not preparing.done() and reader.initial is None and reader._cursor is None
            assert reader.codec.source["journal_incarnation"] is None
            await actual_waiters("clear")
            for _ in range(100):
                if delivered[-1] is not None and not delivered[-1].requests:
                    break
                await asyncio.sleep(0.05)
            assert delivered[-1] is not None and not delivered[-1].requests
            assert semantic_peer["log"]._next_id == 1
        finally:
            stop.set()
            await asyncio.gather(preparing, return_exceptions=True)
        assert reader.current_requests.current is None and delivered[-1] is None

    asyncio.run(check())


def test_state_only_changes_and_backward_pages_do_not_share_an_event_cursor(
    semantic_peer, actual_waiters, tmp_path,
) -> None:
    for index in range(70):
        semantic_peer["log"].append("tool_call_start", {"tool_call_id": str(index)})

    async def wait_for(predicate) -> None:
        for _ in range(100):
            if predicate():
                return
            await asyncio.sleep(0.05)
        pytest.fail("current request observation did not arrive")

    async def check() -> None:
        await actual_waiters("input")
        reader, stop = source(tmp_path), asyncio.Event()
        initial = await reader.prepare(1, stop=stop)
        stream = reader.observe(initial.token, stop=stop)
        await anext(stream)
        waiting = asyncio.create_task(anext(stream))
        try:
            await wait_for(lambda: reader.current_requests.current is not None)
            before, current = reader._cursor, reader.current_requests.current
            page = await reader.history(initial.token, initial.before, stop=asyncio.Event())
            assert page.events and reader._cursor == before and reader.current_requests.current == current
            await actual_waiters("clear")
            await wait_for(lambda: reader.current_requests.current is not None
                           and not reader.current_requests.current.requests)
            assert reader._cursor == before and not waiting.done()
            semantic_peer["log"].append("turn_complete", {})
            delivered = await waiting
            assert delivered.base == initial.position and delivered.events[0].event_id == "event:71"
        finally:
            stop.set()
            await asyncio.gather(waiting, return_exceptions=True)
            await stream.aclose()
        assert reader.current_requests.current is None

    asyncio.run(check())


@pytest.mark.parametrize("screen", ["pending", "cleared", "partial", "unknown"])
def test_actual_empty_history_waiters_render_without_focus_or_history_coupling(
    semantic_peer, actual_waiters, tmp_path, monkeypatch, record_property, screen: str,
) -> None:
    from test_companion_visual import offline_svg

    def data():
        result = current_data()
        result.worktree["path"] = str(tmp_path)
        result.bundle = {"facts": {"claims": {"value": {"owner_ref": OWNER}, "confirmed": True}}}
        return result

    async def unavailable(*args, **kwargs):
        raise bridge.OwnerReadError("unavailable", "fixture Worktrees source unavailable")

    monkeypatch.setattr(companion, "_load_current_worktree", lambda *args, **kwargs: data())
    monkeypatch.setattr(companion.MuxCompanionApp, "_start_agenda", lambda self: None)
    monkeypatch.setattr(companion.MuxCompanionApp, "_poll_attention", lambda *args, **kwargs: None)
    monkeypatch.setattr(WorktreeTimelineSource, "prepare", unavailable)

    async def check() -> None:
        await actual_waiters("permission" if screen == "partial" else "input")
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test(size=(110, 40)) as pilot:
            await until(pilot, lambda: not app._loading)
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            await until(pilot, lambda: "owned:child" in app._bridge_attachment._members)
            control = app.query_one("#bridge-feed", Select)
            control.focus()
            control.value = "owned:child"
            label = app.query_one("#timeline-current-input", Static)
            await until(pilot, lambda: "Observed pending waiters" in str(label.content))
            if screen == "partial":
                assert "0 input, 1 permission" in str(label.content)
            focused = app.focused
            if screen != "pending":
                await actual_waiters({"cleared": "clear", "partial": "reset", "unknown": "disconnect"}[screen])
            expected = {
                "pending": "Observed pending waiters", "cleared": "No supported local waiters",
                "partial": "partial coverage, absence unknown", "unknown": "Current input unknown",
            }[screen]
            await until(pilot, lambda: expected in str(label.content))
            assert app.focused is focused
            assert not app._timeline_panel.window.events and semantic_peer["log"]._next_id == 1
            assert app._bridge_attachment.controller is None
            assert "Private" not in str(label.content) and "private-tool" not in str(label.content)
            record_property("sidecar-state", "current-" + screen)
            record_property("sidecar-svg", offline_svg(app.export_screenshot()))
            app._bridge_attachment.stop_now()
            await pilot.pause()
            assert "Current input unknown" in str(label.content)
            await actual_waiters("permission")
            await pilot.pause()
            assert app._bridge_attachment.current_requests is None

    asyncio.run(check())

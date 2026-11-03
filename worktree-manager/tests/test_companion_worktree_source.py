"""Owned synthetic CLI processes joined to the actual Companion and controller."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from threading import Event, enumerate as threads

import pytest
from textual.widgets import Static, TabbedContent

from worktree_manager import engine_client as ec
from worktree_manager import mux_companion as companion
from worktree_manager import agenda_transport
from worktree_manager.companion_client import cancellable_read_runner
from worktree_manager.companion_timeline_widget import SubwayTimeline
from worktree_manager.companion_timeline_contract import (
    Availability, Completeness, SourcePosition, SourceToken, TimelineGap,
)
from worktree_manager.companion_worktree_codec import OwnerReadError
from worktree_manager.companion_worktree_source import (
    OwnerCommands, WorktreeTimelineSource, _PROBE, owned,
)
import _worktree_timeline_fixtures as fixtures
from _worktree_timeline_fixtures import cursor, delta, gap, peer
from test_companion_agenda_live import current_data, until
from test_agenda_end_to_end import SOURCE_PATHS

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX synthetic executable; no Windows qualification")


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    executable, calls = peer(tmp_path)
    monkeypatch.setattr(ec, "engine_base_command", lambda: [str(executable), "-m", "agent_worktrees"])
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *args, **kwargs: current_data())
    monkeypatch.setattr(companion.MuxCompanionApp, "_start_agenda", lambda self: None)
    monkeypatch.setattr(companion.MuxCompanionApp, "_poll_attention", lambda *args, **kwargs: None)
    return tmp_path, calls


def status(app: companion.MuxCompanionApp) -> str:
    return str(app.query_one(".timeline-state", Static).content)


def not_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_real_probe_refuses_old_peer_before_any_new_verb_or_default_launch(tmp_path, monkeypatch) -> None:
    executable, calls = peer(tmp_path, supported=False)
    monkeypatch.setattr(ec, "engine_base_command", lambda: [str(executable), "-m", "agent_worktrees"])

    async def check() -> None:
        source = WorktreeTimelineSource("example", "selected", "head-a", cwd=str(tmp_path))
        with pytest.raises(OwnerReadError, match="unsupported"):
            await source.prepare(1, stop=asyncio.Event())
        assert not calls.exists(), "an old provider received a new verb or launch dispatch"

    asyncio.run(check())


def test_actual_root_engine_parser_and_passive_probe_admit_exact_owner_vectors(tmp_path) -> None:
    commands = OwnerCommands("example", "selected", "head-a", cwd=str(tmp_path))
    commands.base = [sys.executable, "-m", "agent_worktrees"]
    commands.capabilities = frozenset({
        "worktree-observations-feed-v1", "worktree-observations-history-v1",
    })
    vectors = [
        commands.argv(), commands.argv(follow=True, after=cursor(40)),
        commands.argv(history=True, source_generation=fixtures.JOURNAL, before=cursor(21)),
    ]
    health = {
        "published": True, "status": "ready", "capabilities": sorted(commands.capabilities),
        "worktrees": {"status": "ready", "generation": fixtures.RESIDENT},
        "worktree_observations": {"status": "ready", "generation": fixtures.RESIDENT},
    }
    script = (
        "import sys\nfrom pathlib import Path\n"
        f"sys.path[:0]={[str(path) for path in SOURCE_PATHS]!r}\n"
        "try:\n"
        "    from agent_worktrees.__main__ import build_parser, _extract_project_flag\n"
        "except ImportError as exc:\n"
        "    capture=Path(sys.path[0])/'agent_worktrees'/'tracking_save_capture.py'\n"
        "    raise ImportError(f'{exc}; snapshot capture module present={capture.is_file()}') from exc\n"
        f"vectors={vectors!r}\n"
        "parser=build_parser()\n"
        "for vector in vectors:\n"
        "    remaining,project=_extract_project_flag(vector[3:])\n"
        "    assert project=='example'\n"
        "    parsed=parser.parse_args(remaining)\n"
        "    assert parsed.worktree=='selected' and parsed.session=='head-a'\n"
        "    assert parsed.contract_version==1 and parsed.json and callable(parsed.func)\n"
        "from agent_worktrees import worktrees_client\n"
        f"worktrees_client.read_health=lambda:{health!r}\n"
        "sys.argv=['-c','example','selected','head-a']\n"
        f"exec({_PROBE!r},{{'__name__':'__main__'}})\n"
    )
    result = cancellable_read_runner(Event(), cwd=str(tmp_path))(
        [sys.executable, "-I", "-B", "-c", script], 5,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["ok"] is True, (value, result.stderr)


def test_optional_unsupported_peer_does_not_disable_summary_or_launch(configured) -> None:
    root, calls = configured
    (root / "modules" / "agent_worktrees" / "worktrees_client.py").write_text(
        "def read_health():\n    return {'capabilities':[]}\n", encoding="utf-8",
    )

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(root))
        async with app.run_test(size=(100, 36)) as pilot:
            await until(pilot, lambda: not app._loading)
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            await until(pilot, lambda: "unsupported" in status(app))
            assert not calls.exists()
            app.query_one("#views", TabbedContent).active = "summary-tab"
            await pilot.pause()
            assert app._data.error is None
            assert app.query_one("#views", TabbedContent).active == "summary-tab"
            await pilot.press("escape")

    asyncio.run(check())


def test_repeated_caller_cancellation_retains_thread_cleanup_custody() -> None:
    async def check() -> None:
        cancelled, started, cleaning, release, finished = (Event() for _ in range(5))

        def operation() -> None:
            started.set()
            assert cancelled.wait(3)
            cleaning.set()
            assert release.wait(3)
            finished.set()

        request = asyncio.create_task(owned(operation, asyncio.Event(), cancelled))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            request.cancel()
            assert await asyncio.to_thread(cleaning.wait, 2)
            request.cancel()
            await asyncio.sleep(0)
            assert not request.done() and not finished.is_set()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await request
        assert finished.is_set()

    asyncio.run(check())


@pytest.mark.parametrize("terminal_size", [(100, 36), (100, 60)])
def test_real_process_current_facts_live_push_and_page_prepend_keep_independent_cursors(
    configured, terminal_size: tuple[int, int],
) -> None:
    root, calls = configured
    (root / "block-history").write_text("bounded owned fixture", encoding="utf-8")

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(root))
        async with app.run_test(size=terminal_size) as pilot:
            await until(pilot, lambda: not app._loading)
            assert app.query_one("#views", TabbedContent).active == "summary-tab"
            assert not calls.exists()
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            timeline = app.query_one(SubwayTimeline)
            await until(pilot, lambda: len(timeline.window.events) == 20 and (root / "feed-pid").exists())
            assert "Current accepted purpose." in str(app.query_one(".timeline-context", Static).content)
            assert "efforts/active/example/README.md" in str(app.query_one(".timeline-context", Static).content)
            assert "partial" in status(app)
            assert {event.kind.value for event in timeline.window.events} == {"observed-change"}
            timeline.focus()
            await pilot.press("pageup")
            try:
                await until(pilot, lambda: (root / "page-pid").exists())
            except AssertionError as exc:
                controller = app._timeline_attachment.controller
                raise AssertionError({
                    "viewport": timeline.size, "virtual": timeline.virtual_size,
                    "scroll": timeline.scroll_offset, "layout_height": timeline.layout_index.height,
                    "padding": timeline._padding, "follow_live": timeline.window.follow_live,
                    "focus": timeline.has_focus, "demand_visible": timeline.demand_visible,
                    "sources": timeline.window.sources,
                    "reader_count": None if controller is None else controller.reader_count,
                    "page_count": None if controller is None else controller.page_count,
                    "status": status(app),
                    "commands": calls.read_text() if calls.exists() else None,
                }) from exc
            anchor = timeline.capture_anchor()
            assert anchor is not None and not timeline.window.follow_live
            (root / "push.json").write_text(json.dumps(delta()), encoding="utf-8")
            await until(pilot, lambda: timeline.window.events[-1].event_id == "event-41")
            assert "Updated purpose" in str(app.query_one(".timeline-context", Static).content)
            selected = next(iter(timeline.window.sources.values()))
            assert selected.position is not None and selected.position.cursor == cursor(41)
            assert len(timeline.window.unread) == 1
            row = timeline.layout_index.anchor_row(anchor)
            assert row is not None
            assert row + timeline._padding - int(timeline.scroll_offset.y) == anchor.screen_row
            (root / "release-history").write_text("release own page", encoding="utf-8")
            await until(pilot, lambda: len(timeline.window.events) == 41)
            retained = timeline.capture_anchor()
            row = timeline.layout_index.anchor_row(anchor)
            assert row is not None
            assert row + timeline._padding - int(timeline.scroll_offset.y) == anchor.screen_row
            if anchor.screen_row == 0:
                assert retained is not None and retained.event == anchor.event
            state = next(iter(timeline.window.sources.values()))
            assert state.position is not None and state.position.cursor == cursor(41)
            assert state.before is None and len(timeline.window.unread) == 1
            controller = app._timeline_attachment.controller
            assert controller is not None and controller.reader_count == 1
            feed_pid = int((root / "feed-pid").read_text())
            await pilot.press("escape")
        assert not_alive(feed_pid)
        assert not any(thread.name.startswith("agenda-feed-") for thread in threads())
        commands = [json.loads(line) for line in calls.read_text().splitlines()]
        assert sum("--follow" in command for command in commands) == 1
        assert all(command[:2] == ["--project", "example"] for command in commands)

    asyncio.run(check())


def test_actual_retention_gap_reaches_widget_without_consuming_live_boundary(configured) -> None:
    root, _calls = configured

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(root))
        async with app.run_test(size=(100, 36)) as pilot:
            await until(pilot, lambda: not app._loading)
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            timeline = app.query_one(SubwayTimeline)
            await until(pilot, lambda: len(timeline.window.events) == 20)
            responses = json.loads((root / "responses.json").read_text())
            responses["older"] = gap(21)
            (root / "responses.json").write_text(json.dumps(responses), encoding="utf-8")
            timeline.focus()
            await pilot.press("home")
            await until(pilot, lambda: bool(timeline.window.source_gaps))
            retained = next(iter(timeline.window.source_gaps.values()))
            assert retained.reason == "retention" and retained.floor.cursor == cursor(21)
            assert "snapshot-and-history" in status(app) and "retention" in status(app)
            state = next(iter(timeline.window.sources.values()))
            assert state.position is not None and state.position.cursor == cursor(40)
            assert state.requested_before is None and state.availability.value == "observed"
            assert "Current accepted purpose." in str(app.query_one(".timeline-context", Static).content)
            await pilot.press("escape")

    asyncio.run(check())


def test_refresh_replaces_journal_and_stops_old_feed_and_pending_page(configured, monkeypatch) -> None:
    root, _calls = configured
    (root / "block-history").write_text("bounded own fixture", encoding="utf-8")

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(root))
        async with app.run_test(size=(100, 36)) as pilot:
            await until(pilot, lambda: not app._loading)
            app.query_one("#views", TabbedContent).active = "timeline-tab"
            timeline = app.query_one(SubwayTimeline)
            await until(pilot, lambda: len(timeline.window.events) == 20 and (root / "feed-pid").exists())
            old_feed = int((root / "feed-pid").read_text())
            old_token = next(iter(timeline.window.sources.values())).token
            timeline.focus()
            await pilot.press("home")
            await until(pilot, lambda: (root / "page-pid").exists())
            old_page = int((root / "page-pid").read_text())
            monkeypatch.setattr(fixtures, "SOURCE", fixtures.wire.Source(
                "example", "selected", fixtures.SOURCE.source, "c" * 32, fixtures.RESIDENT, "head-a",
            ))
            values = {
                "point": fixtures.snapshot(), "replay": fixtures.snapshot(after=21),
                "follow": fixtures.snapshot(after=40), "latest": fixtures.page(),
                "older": fixtures.page(before=21),
                "heartbeat": fixtures.wire.frame("heartbeat", fixtures.SOURCE, fixtures.cursor(40), {}),
            }
            (root / "responses.json").write_text(json.dumps(values), encoding="utf-8")
            await app._reload().wait()
            await until(pilot, lambda: (
                bool(timeline.window.sources)
                and next(iter(timeline.window.sources.values())).token.identity.generation == "c" * 32
                and len(timeline.window.events) == 20
            ))
            assert not_alive(old_feed) and not_alive(old_page)
            current = next(iter(timeline.window.sources.values())).token
            assert current.request_epoch > old_token.request_epoch
            result = timeline.apply_gap(fixtures_to_late_gap(old_token))
            assert not result.applied and not timeline.window.source_gaps
            app.query_one("#views", TabbedContent).active = "summary-tab"
            await until(pilot, lambda: app._timeline_attachment.controller is None)
            assert app.query_one("#views", TabbedContent).active == "summary-tab"
            assert app._data.error is None
            await pilot.press("escape")
        assert not any(thread.name.startswith("agenda-feed-") for thread in threads())

    asyncio.run(check())


def fixtures_to_late_gap(token: SourceToken) -> TimelineGap:
    def pos(value: str) -> SourcePosition:
        return SourcePosition(token.identity.source_key, token.identity.generation, value)

    return TimelineGap(
        token, pos("old-current"), pos("old-floor"), "retention", "Late old page.",
        Availability.UNAVAILABLE, Completeness.PARTIAL,
    )


@pytest.mark.timeout(fixtures.JOIN_TEST_SECONDS)
def test_actual_record_projection_resident_cli_and_textual_private_join(tmp_path, monkeypatch) -> None:
    with fixtures.resident_join(tmp_path, monkeypatch) as joined:
        owner, server = joined["owner"], joined["server"]

        async def check() -> None:
            app = companion.MuxCompanionApp(cwd=str(joined["checkout"]))
            async with app.run_test(size=(110, 40)) as pilot:
                await until(pilot, lambda: not app._loading)
                assert app.query_one("#views", TabbedContent).active == "summary-tab"
                assert not joined["streams"], "initial Summary started a timeline observer"
                preparation = fixtures.JoinPhase("prepared-events", fixtures.JOIN_PREPARATION_SECONDS)
                joined["mark"]("test.prepare.begin")
                app.query_one("#views", TabbedContent).active = "timeline-tab"
                timeline = app.query_one(SubwayTimeline)

                def failure():
                    if joined["failures"]:
                        return joined["failures"][0]
                    attachment = app._timeline_attachment
                    if attachment._cleanup_failure is not None:
                        return attachment._cleanup_failure
                    if attachment.controller is not None:
                        for task in attachment.controller._readers.values():
                            if task.done():
                                return (RuntimeError("timeline reader cancelled") if task.cancelled()
                                        else task.exception() or RuntimeError("timeline reader ended"))
                        for task in attachment.controller._pages.values():
                            if task.done() and not task.cancelled() and task.exception() is not None:
                                return task.exception()
                    if timeline.window.source_gaps or any(
                        state.availability in {Availability.UNAVAILABLE, Availability.UNSUPPORTED, Availability.HEAD_CHANGED}
                        for state in timeline.window.sources.values()
                    ):
                        return RuntimeError("selected owner became unavailable or reported a gap")
                    return None

                def diagnostic():
                    controller = app._timeline_attachment.controller
                    return {
                        "status": status(app), "sources": timeline.window.sources,
                        "event_count": len(timeline.window.events),
                        "owner": owner.observations.counts(),
                        "actions": joined["actions"], "streams": joined["streams"],
                        "stages": joined["stages"],
                        "children": joined["trace"].read_text() if joined["trace"].exists() else "",
                        "tasks": [] if controller is None else [
                            {"name": task.get_name(), "done": task.done(), "cancelled": task.cancelled(),
                             "exception": repr(task.exception()) if task.done() and not task.cancelled() else None,
                             "stack": [(frame.f_code.co_name, frame.f_lineno) for frame in task.get_stack(limit=1)]}
                            for task in controller._readers.values()
                        ],
                    }

                await preparation.wait(
                    lambda: len(timeline.window.events) == 2, pilot.pause, failure, diagnostic,
                )
                joined["mark"]("test.prepare.visible")
                admission = fixtures.JoinPhase("held-stream-admission", fixtures.JOIN_ADMISSION_SECONDS)
                await admission.wait(
                    lambda: owner.observations.counts()["streams"] == 1, pilot.pause, failure, diagnostic,
                )
                joined["mark"]("test.feed.admitted")
                row = joined["row"]
                state = next(iter(timeline.window.sources.values()))
                observed = state.snapshot
                assert observed is not None
                assert observed.purpose == row["summary"] == "Effort private-join: Current slice"
                assert observed.effort_ref == row["active_effort"]["path"]
                assert row["active_effort"]["participant"] == "receiver"
                assert row["active_effort"]["state"] == "open"
                assert observed.creation_context is None
                assert state.token.identity.head_session_id == row["last_session_id"] == "head-a"
                assert state.token.identity.owner_ref == owner.store.generation
                actual = owner.observation_read(joined["selection"])
                assert state.token.identity.source_key == actual["source"]
                assert state.token.identity.generation == actual["source_generation"]
                facts = json.loads(observed.facts_json)
                assert facts["row"]["creation_nonce"] == row["creation_nonce"]
                assert facts["row"]["active_effort"] == row["active_effort"]
                assert observed.completeness is Completeness.PARTIAL
                assert {item.kind.value for item in timeline.window.events} == {"observed-change"}
                assert all(item.occurred_at is None for item in timeline.window.events)
                assert all("worktree-created" not in item.summary for item in timeline.window.events)
                assert row["summary"] in str(app.query_one(".timeline-context", Static).content)
                old_position = state.position
                updated = await asyncio.to_thread(joined["publish"], "Live accepted slice", 4)
                await fixtures.JoinPhase("live-update", agenda_transport.IDLE_TIMEOUT).wait(
                    lambda: len(timeline.window.events) == 3 and timeline.window.context()[0] == updated["summary"],
                    pilot.pause, failure, diagnostic,
                )
                joined["mark"]("test.live.visible")
                current = next(iter(timeline.window.sources.values()))
                assert current.position is not None and current.position != old_position
                live = timeline.window.events[-1]
                assert live.evidence_kind.value == "observed-change"
                assert live.kind.value == "observed-change"
                assert json.loads(live.data_json)["payload"]["facts_omitted"] is True
                assert updated["summary"] in str(app.query_one(".timeline-context", Static).content)
                live_position = current.position
                timeline.focus()
                await pilot.press("home")
                await fixtures.JoinPhase(
                    "backward-page", agenda_transport.STARTUP_TIMEOUT + fixtures.JOIN_COMMAND_CLEANUP,
                ).wait(
                    lambda: next(iter(timeline.window.sources.values())).before is None,
                    pilot.pause, failure, diagnostic,
                )
                joined["mark"]("test.history.visible")
                assert next(iter(timeline.window.sources.values())).position == live_position
                pages = [payload for action, payload in joined["actions"]
                         if action == "worktree-observations-history"]
                assert len(pages) >= 2
                assert all(payload["limit"] == 64 for payload in pages)
                assert any(payload["before_cursor"] is not None for payload in pages)
                assert len(joined["streams"]) == 1
                await pilot.press("escape")
            await asyncio.to_thread(owner.observation_wait_closed, 3)
            for _ in range(120):
                if server.active_handler_count() == 0 and joined["settled"]():
                    break
                await asyncio.sleep(0.025)
            assert server.active_handler_count() == 0 and joined["settled"]()
            assert not app.is_running
            counts = owner.observations.counts()
            assert counts["streams"] == counts["readers"] == counts["producer"] == counts["publication"] == 0
            assert not joined["forbidden"].exists()
            assert not any(thread.name.startswith("agenda-feed-") for thread in threads())

        asyncio.run(check())


@pytest.mark.parametrize("fault", ["disconnect", "timeout"])
@pytest.mark.parametrize("recovery", ["refresh", "reopen"])
def test_owned_transport_failure_can_recover_after_reader_retirement(configured, fault, recovery) -> None:
    root, _calls = configured

    async def check() -> None:
        app = companion.MuxCompanionApp(cwd=str(root))
        async with app.run_test(size=(100, 36)) as pilot:
            await until(pilot, lambda: not app._loading)
            views = app.query_one("#views", TabbedContent)
            views.active = "timeline-tab"
            timeline = app.query_one(SubwayTimeline)
            await until(pilot, lambda: len(timeline.window.events) == 20 and (root / "feed-pid").exists())
            controller = app._timeline_attachment.controller
            assert controller is not None
            reader = next(iter(controller._readers.values()))
            old_token = next(iter(timeline.window.sources.values())).token
            (root / "feed-behavior").write_text(fault, encoding="utf-8")
            await asyncio.wait_for(
                asyncio.gather(reader, return_exceptions=True), agenda_transport.IDLE_TIMEOUT + 2,
            )
            await pilot.pause()
            diagnostic = "disconnected" if fault == "disconnect" else "frame deadline"
            assert diagnostic in status(app)
            assert not any(thread.name.startswith("agenda-feed-") for thread in threads())
            assert not_alive(int((root / "feed-pid").read_text()))
            (root / "feed-behavior").write_text("normal", encoding="utf-8")
            if recovery == "refresh":
                assert await pilot.click("#refresh-btn")
            else:
                views.active = "summary-tab"
                await until(pilot, lambda: app._timeline_attachment.controller is None
                            or app._timeline_attachment._cleanup_error is not None)
                assert app._data.error is None
                views.active = "timeline-tab"
            try:
                await until(pilot, lambda: (
                    app._timeline_attachment.controller is not None
                    and app._timeline_attachment.controller.reader_count == 1
                    and next(iter(timeline.window.sources.values())).token.request_epoch > old_token.request_epoch
                    and next(iter(timeline.window.sources.values())).availability is Availability.OBSERVED
                ))
            except AssertionError as exc:
                raise AssertionError({
                    "fault": fault, "recovery": recovery, "status": status(app),
                    "cleanup_error": app._timeline_attachment._cleanup_error,
                }) from exc
            assert app._timeline_attachment._cleanup_error is None
            await pilot.press("escape")
        assert not app.is_running
        assert not any(thread.name.startswith("agenda-feed-") for thread in threads())

    asyncio.run(check())

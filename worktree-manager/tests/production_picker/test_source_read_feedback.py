"""List feedback preserves retry progress and last-good data without host claims."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import threading
from types import SimpleNamespace

import pytest
from textual.widgets import OptionList

from test_picker_tui import _fixture_source
from worktree_manager.production_picker.picker_tui import data_ssh
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.source_read_notices import worktree_read_feedback
from worktree_manager.worktree_feed_contract import CurrentFeed, FeedUnavailable


def screen_and_loader(*, provider=False):
    screen = PickerScreen(_fixture_source(), live=False)
    screen.setup_sync_for_tests()
    screen.machine_idx = screen.local_index()
    _, machine, environment, _ = screen.machines[screen.machine_idx]
    source = data_ssh.Source(
        machine, environment, None, local=True,
        **({"source_kind": "provider-exec", "source_id": "provider-exec:fixture:target"}
           if provider else {}),
    )
    screen.source_tabs[screen.machine_idx]["source_id"] = source.source_id
    screen.source_tabs[screen.machine_idx]["local"] = True
    screen.source_tabs[screen.machine_idx]["source_kind"] = source.source_kind
    screen.loader = data_ssh.LiveLoader([source])
    screen.live = True
    screen.data = []
    return screen, screen.loader, source


def body(screen):
    rows = []
    screen.worktrees_view.build_data(lambda text, **kwargs: rows.append(text), 100, ("M", 0))
    return "\n".join(row.plain for row in rows)


@pytest.mark.parametrize("connected", [False, True])
def test_initial_connection_and_content_failures_have_distinct_list_panes(connected):
    screen, loader, source = screen_and_loader()

    def fail(_source, _generation):
        if connected:
            CurrentFeed().receive({
                "type": "error",
                "error": {"code": "invalid-request", "message": "fixture read rejected"},
            })
        raise FeedUnavailable("fixture connection unavailable", retry=False)

    loader._feed_once = fail
    loader._load_current_feed(source, 0)
    text = body(screen)
    assert ("Worktrees could not load" if connected else "Worktrees connection unavailable") in text
    assert ("fixture read rejected" if connected else "fixture connection unavailable") in text
    assert "Press r to retry" in text
    assert "(none)" not in text and "── Active" not in text
    tab = screen._machine_group([screen.machine_idx], focused=True).plain
    assert "WT" not in tab
    assert ("✓" if connected else "✗") in tab


@pytest.mark.parametrize("rows", [[], [{"id": "fixture-win-a1b2", "title": "Kept worktree"}]])
def test_later_failure_preserves_rows_and_marks_even_empty_snapshots_quietly(rows):
    screen, loader, source = screen_and_loader()
    loader._feed_commit(source, 0, rows)
    screen.data = loader.records()
    assert worktree_read_feedback(screen) == []
    loader._feed_error(source, 0, "fixture disconnected")
    loader._feed_error(source, 0, "fixture still disconnected")
    screen.data = loader.records()
    text = body(screen)
    assert "Updates paused · connection unavailable" in text
    assert "showing last loaded worktrees" in text
    assert "Worktrees connection unavailable\n" not in text and "Press r to retry" not in text
    assert "~" in screen._machine_group([screen.machine_idx], True).plain
    assert "✗" not in screen._machine_group([screen.machine_idx], True).plain
    for row in loader.records():
        assert row["title"] == "Kept worktree"
        assert row["raw"]["_availability"]["status"] == "stale"
        assert "Kept worktree" in text and "[stale]" not in text
    screen._setup_epoch += 1
    assert screen.machine_state(screen.machine_idx) == "loading"
    assert "Refreshing" in body(screen) and "Worktrees could not load" not in body(screen)
    screen.machine_idx = 0
    assert len(screen._scope_data()) == len(rows)
    tab = screen.machines[1]
    screen.machines[1] = (*tab[:3], False)
    assert screen._scope_data() == []
    screen.machines[1] = tab
    screen._setup_applied_epoch = screen._setup_epoch
    loader._feed_commit(source, 0, rows)
    screen.data = loader.records()
    assert worktree_read_feedback(screen) == []


@pytest.mark.parametrize("provider", [False, True])
@pytest.mark.parametrize("kind", ["connection", "content"])
@pytest.mark.parametrize("rows", [[], [{"id": "one", "title": "Kept worktree"}]])
def test_failed_replacement_loader_keeps_cached_presentation(provider, kind, rows):
    screen, loader, source = screen_and_loader(provider=provider)
    loader._feed_commit(source, 0, rows)
    screen.data = loader.records()
    assert worktree_read_feedback(screen) == []
    screen.loader = data_ssh.LiveLoader([source])
    screen.loader._feed_error(source, 0, "replacement failed", kind=kind)
    assert screen.loader.state_for_source(source.source_id) == "failed"
    assert worktree_read_feedback(screen)[0].cached
    assert "Updates paused" in body(screen) and "Press r to retry" not in body(screen)
    tab = screen._machine_group([screen.machine_idx], True).plain
    assert "~" in tab and "✗" not in tab and "✓" not in tab


def test_off_scope_empty_completion_is_remembered_before_loader_replacement():
    screen, _loader, source = screen_and_loader()
    other = data_ssh.Source("other-host", "Win", None)
    screen.machines.append(("Other source", other.machine, other.env, True))
    screen.source_tabs.append({
        "label": "Other source", "machine": other.machine, "env": other.env,
        "source_kind": other.source_kind, "source_id": other.source_id,
    })
    screen.loader = data_ssh.LiveLoader([source, other])
    screen.loader._feed_commit(other, 0, [])
    worktree_read_feedback(screen)
    screen.loader = data_ssh.LiveLoader([source, other])
    screen.loader._feed_error(other, 0, "replacement failed")
    screen.machine_idx = len(screen.machines) - 1
    assert worktree_read_feedback(screen)[0].cached
    assert "Updates paused" in body(screen) and "Worktrees connection unavailable" not in body(screen)


@pytest.mark.parametrize("all_sources", [False, True])
def test_reload_preserves_selected_source_across_roster_reordering(all_sources):
    source = _fixture_source()
    local = source.machines()[0]
    remote = ("Other machine", "other-host", "Win", True)
    source.machines = lambda: [local, remote]
    screen = PickerScreen(source, live=False)
    screen.setup_sync_for_tests()
    screen.machine_idx = 0 if all_sources else 2
    key = screen._current_tab_key()
    payload = screen._collect_setup_payload()
    payload = replace(payload, source_tabs=[
        payload.source_tabs[0], payload.source_tabs[2], payload.source_tabs[1],
    ])
    screen._apply_setup_payload(payload)
    assert screen._current_tab_key() == key
    assert screen.machine_idx == (0 if all_sources else 1)


def test_legacy_refresh_failure_is_visible_without_losing_last_good_rows(monkeypatch):
    screen, loader, source = screen_and_loader()
    loader._feed_commit(source, 0, [{"id": "one", "title": "Kept worktree"}])
    monkeypatch.setattr(data_ssh, "_stream_enabled", lambda: False)

    def fail(*args, **kwargs):
        raise RuntimeError("fixture refresh unavailable")

    monkeypatch.setattr(data_ssh, "_fetch", fail)
    loader._refresh_one_serial(source, 0)
    screen.data = loader.records()
    assert "Updates paused · refresh failed" in body(screen)
    assert loader.records()[0]["title"] == "Kept worktree"
    monkeypatch.setattr(data_ssh, "_fetch", lambda *args, **kwargs: [])
    loader._refresh_one_serial(source, 0)
    assert loader.records() == [] and worktree_read_feedback(screen) == []


def test_initial_loader_completion_cannot_replace_a_newer_retry(monkeypatch):
    source = _fixture_source()
    cancelled = threading.Event()
    loader = SimpleNamespace(
        start=lambda: None, records=lambda: [], cancel=cancelled.set,
    )
    source.make_loader = lambda *args: loader
    screen = PickerScreen(source, live=True)
    callbacks = []
    monkeypatch.setattr(screen, "_setup_live_pivots", lambda: None)
    monkeypatch.setattr(screen, "_apply_from_worker", callbacks.append)
    screen._setup_live_async()
    assert len(callbacks) == 1
    screen._setup_epoch += 1
    screen._busy_label = "Refreshing worktrees"
    callbacks[0]()
    assert cancelled.is_set()
    assert screen.loader is not loader
    assert screen._busy_label == "Refreshing worktrees"


def test_initial_worker_captures_epoch_before_it_can_be_delayed(monkeypatch):
    from worktree_manager.production_picker.picker_tui import engine_loading

    screen, loader, _source = screen_and_loader()
    scheduled = []

    class DelayedThread:
        def __init__(self, *, target, args=(), **kwargs):
            self.target, self.args = target, args

        def start(self):
            scheduled.append(self)

    monkeypatch.delenv("AGENT_WORKTREES_PICKER_FRAME_HEALTH", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_LAUNCH_TRACE", raising=False)
    monkeypatch.setattr(engine_loading.threading, "Thread", DelayedThread)
    screen._after_first_refresh()
    initial, = scheduled
    screen._setup_epoch += 1
    screen.src.make_loader = lambda *a: pytest.fail("superseded initial setup constructed a loader")
    initial.target(*initial.args)
    assert screen.loader is loader and scheduled == [initial]


@pytest.mark.parametrize("rows", [[], [{"id": "one", "title": "Kept worktree"}]])
def test_setup_collection_failure_retains_muted_snapshot_and_stops_spinner(rows):
    screen, loader, source = screen_and_loader()
    loader._feed_commit(source, 0, rows)
    screen.data = loader.records()
    worktree_read_feedback(screen)
    screen._setup_epoch += 1
    screen._apply_setup_failure(screen._setup_epoch, RuntimeError("configuration unavailable"))
    assert screen.machine_state(screen.machine_idx) == "failed"
    assert screen._source_failure_marker(screen.machine_idx)[0] == "~"
    assert "Updates paused" in body(screen)
    screen.loader = None
    screen._loaded_worktree_sources.clear()
    screen.data = []
    assert screen.machine_state(screen.machine_idx) == "failed"
    assert "Worktrees could not load" in body(screen)
    assert "Loading worktrees" not in body(screen)


@pytest.mark.parametrize("busy", ["Loading…", "Existing action"])
def test_successful_retry_during_startup_clears_only_the_startup_label(busy):
    screen = PickerScreen(_fixture_source(), live=False)
    screen.setup_sync_for_tests()
    payload = screen._collect_setup_payload()
    screen._busy_label = busy
    screen.debug = "worktree reload requested"
    screen._apply_setup_payload(payload)
    assert screen._busy_label == (None if busy == "Loading…" else busy)


@pytest.mark.parametrize("rows", [[], [{"id": "one", "title": "Kept worktree"}]])
def test_stale_availability_frames_keep_quiet_source_feedback_even_when_empty(rows):
    from test_current_worktrees_feed import head, receive, snapshot

    screen, loader, source = screen_and_loader()
    receiver = CurrentFeed()
    admitted = receive(receiver, snapshot(rows))
    loader._feed_commit(source, 0, admitted, availability=receiver.availability)
    stale = {
        "type": "availability", "projection": head(count=len(rows)),
        "availability": {"status": "stale", "reason": "refresh failed"},
    }
    admitted = receiver.receive(stale)
    loader._feed_commit(source, 0, admitted, availability=receiver.availability)
    screen.data = loader.records()
    assert "Updates paused · refresh failed" in body(screen)
    assert "~" in screen._machine_group([screen.machine_idx], True).plain
    for row in loader.records():
        assert row["title"] == "Kept worktree" and row["raw"]["_availability"]["status"] == "stale"
    admitted = receiver.receive({**stale, "availability": None})
    loader._feed_commit(source, 0, admitted, availability=receiver.availability)
    assert worktree_read_feedback(screen) == []


def test_provider_replacement_cannot_inherit_an_old_instances_cached_rows():
    screen, loader, source = screen_and_loader(provider=True)
    source.instance_id = screen.source_tabs[screen.machine_idx]["instance_id"] = "old"
    loader._feed_commit(source, 0, [{"id": "one", "title": "Old instance"}])
    screen.data = loader.records()
    worktree_read_feedback(screen)
    replacement = data_ssh.Source(
        source.machine, source.env, None, local=True, source_kind=source.source_kind,
        source_id=source.source_id, instance_id="new",
    )
    screen.source_tabs[screen.machine_idx]["instance_id"] = "new"
    screen.loader = data_ssh.LiveLoader([replacement])
    screen.loader._feed_error(replacement, 0, "new instance unavailable")
    assert not worktree_read_feedback(screen)[0].cached
    assert screen._scope_data() == [] and "Worktrees connection unavailable" in body(screen)
    screen.machine_idx = 0
    assert screen._scope_data() == []


@pytest.mark.parametrize("rows", [[], [{"id": "one", "title": "Replacement worktree"}]])
def test_provider_refresh_uses_the_validated_replacement_without_reloading_tabs(monkeypatch, rows):
    from worktree_manager.production_picker.picker_tui import derive

    screen, loader, source = screen_and_loader(provider=True)
    source.instance_id = screen.source_tabs[screen.machine_idx]["instance_id"] = "old"
    loader._feed_commit(source, 0, [{"id": "old-row", "title": "Old instance"}])
    screen.data = loader.records()
    worktree_read_feedback(screen)

    def resolve(current, _runner):
        assert current is source
        current.instance_id = "new"
        assert not loader.read_status_for_source(source.source_id)["has_snapshot"]
        return True

    def fetch(current, **kwargs):
        return [
            derive.norm(row, current.machine, current.env, source_kind=current.source_kind,
                        source_id=current.source_id, source_metadata={"instance_id": "new"})
            for row in rows
        ]

    monkeypatch.setattr(data_ssh, "_resolve_provider_source", resolve)
    monkeypatch.setattr(data_ssh, "_fetch", fetch)
    loader._refresh_one_serial(source, 0)
    screen.data = loader.records()
    assert screen.source_tabs[screen.machine_idx]["instance_id"] == "old"
    assert loader.read_status_for_source(source.source_id)["has_snapshot"]
    assert worktree_read_feedback(screen) == []
    assert [row["id"] for row in screen._scope_data()] == [row["id"] for row in rows]
    screen.machine_idx = 0
    assert [row["id"] for row in screen._scope_data()] == [row["id"] for row in rows]
    loader._feed_error(source, 0, "new instance disconnected")
    screen.machine_idx = 1
    assert worktree_read_feedback(screen)[0].cached
    assert screen._source_failure_marker(screen.machine_idx)[0] == "~"


@pytest.mark.parametrize("other_action", [None, "Existing action"])
def test_retry_key_repaints_loading_before_the_background_reload_finishes(monkeypatch, other_action):
    monkeypatch.setattr(PickerScreen, "_tick", lambda self: None)
    release = threading.Event()
    entered = threading.Event()

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 30)) as pilot:
            screen = app.query_one(PickerScreen)
            assert await asyncio.to_thread(screen._ensure_setup_completion_event().wait, 3)
            await pilot.pause()
            screen.live = True
            screen.machine_idx = screen.local_index()
            _, machine, environment, _ = screen.machines[screen.machine_idx]
            source = data_ssh.Source(machine, environment, None, local=True)
            screen.source_tabs[screen.machine_idx]["source_id"] = source.source_id
            screen.loader = data_ssh.LiveLoader([source])
            screen.loader._feed_error(source, 0, "fixture unavailable")
            screen.data = []
            screen._busy_label = other_action

            def collect():
                entered.set()
                assert release.wait(5)
                raise RuntimeError("fixture reload failed")

            monkeypatch.setattr(screen, "_collect_setup_payload", collect)
            monkeypatch.setattr(screen, "_prime_setup_reload", lambda: None)
            monkeypatch.setattr(screen, "_invalidate_setup_reload_caches", lambda: None)
            monkeypatch.setattr(screen, "_poll_orphan_state", lambda **kwargs: None)
            screen.refresh()
            await pilot.pause()

            def rendered():
                native = screen.query_one("#nf-body-data", OptionList)
                return "\n".join(
                    native.get_option_at_index(index).prompt.plain
                    for index in range(native.option_count)
                )

            assert "Worktrees connection unavailable" in rendered()
            await pilot.press("r")
            assert entered.is_set() and not release.is_set()
            assert screen.machine_state(screen.machine_idx) == "loading"
            assert screen._busy_label == other_action
            assert "Loading worktrees" in rendered()
            assert "fixture unavailable" not in rendered()
            assert "✗" not in screen._machine_group([screen.machine_idx], True).plain
            first_body = rendered()
            first_tab = screen._machine_group([screen.machine_idx], True).plain
            screen.frame += 1
            screen.refresh()
            await pilot.pause()
            assert rendered() != first_body
            assert screen._machine_group([screen.machine_idx], True).plain != first_tab
            release.set()
            assert await asyncio.to_thread(screen._ensure_setup_completion_event().wait, 3)
            await pilot.pause()
            assert "Worktrees could not load" in rendered()
            assert "fixture reload failed" in rendered()
            assert screen._busy_label == other_action

    try:
        asyncio.run(run())
    finally:
        release.set()

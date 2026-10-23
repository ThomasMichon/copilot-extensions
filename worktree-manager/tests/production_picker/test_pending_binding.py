"""Consumer startup admission precedes engine authority, never substitutes for it."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from worktree_manager.production_picker.picker_tui import engine
from worktree_manager.production_picker.picker_tui.engine_loading import PickerBinding


def test_pending_binding_paints_and_escapes_before_slow_bootstrap():
    async def exercise():
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        activated = []

        def prepare():
            entered.set()
            try:
                assert release.wait(3)
                return PickerBinding(object(), True, lambda: activated.append(True))
            finally:
                finished.set()

        app = engine.PickerApp(None, prepare=prepare, project_label="example")
        try:
            async with app.run_test() as pilot:
                assert await asyncio.to_thread(entered.wait, 1)
                assert not app.screen.query(engine.PickerScreen)
                assert "example" in str(app.screen.query_one("#binding-status").render())
                await pilot.press("escape")
                assert app.result is None
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 1)
        assert activated == []

    asyncio.run(exercise())


def test_binding_failure_is_visible_and_preserved():
    async def exercise():
        def prepare():
            raise RuntimeError("unknown project: example")

        app = engine.PickerApp(None, prepare=prepare, project_label="example")
        async with app.run_test() as pilot:
            for _ in range(30):
                if app.binding_error is not None:
                    break
                await pilot.pause(0.02)
            assert "unknown project" in str(app.screen.query_one("#binding-status").render())
            assert not app.screen.query(engine.PickerScreen)
            await pilot.press("escape")
        assert str(app.binding_error) == "unknown project: example"

    asyncio.run(exercise())


def test_successful_binding_mounts_operational_widget_in_original_app(monkeypatch):
    from worktree_manager.production_picker.picker_tui import data_local

    class Source:
        LOCAL = ("host", "Win")
        bucket = staticmethod(data_local.bucket)
        for_machine = staticmethod(data_local.for_machine)

    monkeypatch.setattr(engine.PickerScreen, "_finish_mount", lambda self: None)
    monkeypatch.setattr(engine.PickerScreen, "_after_first_refresh", lambda self: None)

    async def exercise():
        activated = []
        app = engine.PickerApp(
            None,
            prepare=lambda: PickerBinding(Source(), True, lambda: activated.append(True)),
        )
        async with app.run_test() as pilot:
            for _ in range(30):
                if app.query(engine.PickerScreen) and not app.query("#binding-status"):
                    break
                await pilot.pause(0.02)
            assert app.binding_error is None
            screen = app.query_one(engine.PickerScreen)
            assert screen.live
            assert activated == [True]
            assert not app.query("#binding-status")
            app.exit()

    asyncio.run(exercise())


def test_authoritative_loader_does_not_wait_for_advisory(monkeypatch):
    class Source:
        LOCAL = ("host", "Win")

        def bootstrap_rows(self):
            raise AssertionError("advisory read must not run before current feed")

        def source_snapshot(self):
            return ()

        def make_loader(self, snapshot):
            assert snapshot == ()
            return loader

    class Loader:
        def start(self, *, focus_keys=None):
            calls.append({"focus_keys": focus_keys})

    calls = []
    loader = Loader()
    screen = engine.PickerScreen(Source(), live=True)
    monkeypatch.setattr(screen, "_setup_live_pivots", lambda: None)
    monkeypatch.setattr(screen, "_prepare_live_source", lambda _: {"local": Source.LOCAL})
    monkeypatch.setattr(screen, "_apply_from_worker", lambda fn: fn())
    monkeypatch.setattr(screen, "_apply_live_source", lambda prepared, value: calls.append(value))
    screen._setup_live_async()
    assert calls == [{"focus_keys": {Source.LOCAL}}, loader]


@pytest.mark.parametrize("mode", ["batched", "failed", "empty"])
def test_nonlive_setup_reload_retains_scalars_for_locality_input(monkeypatch, tmp_path, mode):
    from worktree_manager.production_picker import context, project_config as cfg
    from worktree_manager.production_picker.picker_tui import data_local, tasks

    class Source:
        LOCAL = data_local.LOCAL
        REPO, BRANCH = "example", "dev"
        bucket = staticmethod(data_local.bucket)
        for_machine = staticmethod(data_local.for_machine)
        is_local = staticmethod(data_local.is_local)
        host_cols = staticmethod(data_local.host_cols)
        target_envs = staticmethod(data_local.target_envs)

        @staticmethod
        def machines():
            return [("Local", *Source.LOCAL, True)]

        @staticmethod
        def load():
            cfg.load_config()
            return []

    calls = []
    alias = "fixture-local"

    def legacy_read(*args):
        calls.append(args)
        if mode == "failed":
            raise cfg.engine_client.EngineError("temporary timeout")
        if mode == "empty":
            return ""
        raise AssertionError("bound scalars must survive setup/reload")

    monkeypatch.setattr(cfg.engine_client, "get_value", legacy_read)
    monkeypatch.setattr(cfg.harness_state, "home", lambda: tmp_path)
    monkeypatch.setattr(cfg.harness_state, "project_config", lambda name: {"machine": alias})
    monkeypatch.setattr(
        cfg.harness_state, "build_projects",
        lambda: [SimpleNamespace(name="example", anchor=str(tmp_path))],
    )
    monkeypatch.setattr(cfg.harness_state, "repos_registry", lambda: {})
    monkeypatch.setattr(tasks, "prewarm_optional_modules", lambda: None)
    monkeypatch.setattr(engine.PickerScreen, "_scan_pivot_payload", lambda self: None)
    monkeypatch.setattr(engine.PickerScreen, "_prewarm_machine_key_map", lambda self: None)
    for name in ("_poll_update_state", "_poll_manager_update_state", "_poll_orphan_state"):
        monkeypatch.setattr(engine.PickerScreen, name, lambda self: None)
    context.set_project("example")
    cfg.bind_bootstrap("example", {
        "config_dir": str(tmp_path), "repo_dir": str(tmp_path), "machine": alias,
    } if mode == "batched" else None)

    async def exercise():
        app = engine.PickerApp(Source(), live=False)
        async with app.run_test() as pilot:
            screen = app.query_one(engine.PickerScreen)
            for phase in ("initial", "reload"):
                if phase == "reload":
                    screen._start_setup_reload_worker()
                for _ in range(50):
                    if screen._setup_epoch and screen._setup_applied_epoch == screen._setup_epoch:
                        break
                    await pilot.pause(0.02)
                assert screen._setup_epoch and screen._setup_applied_epoch == screen._setup_epoch
                prepared_calls = list(calls)
                generation = cfg._file_generation
                assert generation is not None and generation.current() and not generation.expired(), (
                    phase, generation and (generation.ttl, generation.current(), generation.expired()),
                    screen._config_cache_session.ttl,
                )
                assert data_local.is_local(alias, Source.LOCAL[1])
                decision = screen._resume_decision({
                    "raw": {"id": "fixture"}, "machine": alias, "env": Source.LOCAL[1],
                })
                assert decision["action"] == "resume" and decision["is_local"] is True
                assert calls == prepared_calls
                if mode == "batched":
                    assert calls == []
                else:
                    assert len(calls) == (3 if phase == "initial" else 6)
            app.exit()

    try:
        asyncio.run(exercise())
    finally:
        cfg.bind_bootstrap("example", None)
        context.reset()

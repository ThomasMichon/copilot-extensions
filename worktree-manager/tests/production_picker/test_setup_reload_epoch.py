from __future__ import annotations

import asyncio
import threading
import time

import pytest

pytest.importorskip("textual", reason="textual not installed (optional TUI dep)")

from worktree_manager.production_picker.picker_tui import derive
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.engine_helpers import (
    _DEFAULT_HOST_COLS,
    _DEFAULT_TARGET_ENVS,
)
from worktree_manager.production_picker.picker_tui.engine_runtime import (
    PickerScreenRuntimeMixin,
    _SetupPayload,
)


def _fixture_source():
    class Src:
        LOCAL = ("host", "Win")
        LOCAL_LABEL = "host · win"

        @staticmethod
        def machines():
            return [("host Win", "host", "Win", True)]

        @staticmethod
        def load():
            return []

        bucket = staticmethod(derive.bucket)
        for_machine = staticmethod(derive.for_machine)

    return Src()


def _payload(tag: str) -> _SetupPayload:
    source_tabs = [
        {
            "label": "All",
            "machine": None,
            "env": None,
            "ready": True,
            "source_kind": "all",
            "source_id": None,
            "capabilities": {},
        },
        {
            "label": "host Win",
            "machine": "host",
            "env": "Win",
            "ready": True,
            "source_kind": "machine-ssh",
            "source_id": "machine-ssh:host:win",
            "capabilities": {},
            "local": True,
        },
    ]
    pivot_payload = (
        [],
        [{"label": f"{tag} Tasks", "kind": "tasks", "pivot": None}],
        [],
        [],
    )
    data = [
        derive.norm(
            {
                "id": f"{tag}-id",
                "title": tag,
                "status": "active",
                "state": "wip",
                "session_count": 1,
            },
            "host",
            "Win",
        )
    ]
    return _SetupPayload(
        pivot_payload=pivot_payload,
        source_tabs=source_tabs,
        source_local=("host", "Win"),
        source_repo_branch=(f"{tag}-repo", f"{tag}-branch"),
        loader=None,
        data=data,
        load_delay={0: 0.0, 1: 0.0},
        host_cols=list(_DEFAULT_HOST_COLS),
        target_env_list=list(_DEFAULT_TARGET_ENVS),
    )


async def _settle_threads(*events: threading.Event) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if all(event.is_set() for event in events):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for setup workers to finish")


def test_setup_reload_disposes_payload_when_marshal_back_to_ui_fails():
    disposed = threading.Event()

    class _Loader:
        def cancel(self):
            disposed.set()

    class _App:
        def call_from_thread(self, fn):
            raise RuntimeError("app already exited")

    class _Screen(PickerScreenRuntimeMixin):
        def __init__(self):
            self.app = _App()
            self._bg_cancel = threading.Event()
            self._setup_epoch = 0
            self._setup_applied_epoch = 0
            self._setup_failed_epoch = 0
            self.applied = []

        def _prime_setup_reload(self):
            return None

        def _collect_setup_payload(self):
            return _payload("live").__class__(
                **{
                    **_payload("live").__dict__,
                    "loader": _Loader(),
                }
            )

        def _invalidate_setup_reload_caches(self):
            return None

        def _apply_setup_payload(self, payload):
            self.applied.append(payload)

        def _apply_setup_failure(self, epoch, err):
            raise AssertionError(f"unexpected failure path: {epoch} {err}")

        def refresh(self):
            return None

    screen = _Screen()
    screen._start_setup_reload_worker()
    assert disposed.wait(timeout=5)
    assert screen.applied == []


def test_setup_reload_supersession_discards_stale_success(
    wait_for_current_setup_epoch_applied,
):
    calls = []
    first_release = threading.Event()
    first_started = threading.Event()
    second_release = threading.Event()
    first_done = threading.Event()
    second_done = threading.Event()

    def _collect():
        call = len(calls)
        calls.append(call)
        if call == 0:
            first_started.set()
            first_release.wait(timeout=5)
            first_done.set()
            return _payload("stale")
        second_release.wait(timeout=5)
        second_done.set()
        return _payload("fresh")

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.query_one(PickerScreen)
            base_epoch = screen._setup_epoch
            screen._prime_setup_reload = lambda: None
            screen._collect_setup_payload = _collect
            screen._reconcile_wt_sel = lambda: None
            applied = []
            original_apply = screen._apply_setup_payload

            def _record_apply(payload):
                applied.append(payload.data[0]["title"])
                original_apply(payload)

            screen._apply_setup_payload = _record_apply

            screen._start_setup_reload_worker()
            assert first_started.wait(timeout=5)
            screen._start_setup_reload_worker()
            second_release.set()
            await wait_for_current_setup_epoch_applied(pilot, screen)
            assert applied == ["fresh"]

            first_release.set()
            await _settle_threads(first_done, second_done)
            for _ in range(20):
                await pilot.pause()
                await asyncio.sleep(0.01)

            assert screen._setup_epoch == base_epoch + 2
            assert screen._setup_applied_epoch == base_epoch + 2
            assert applied == ["fresh"]
            assert screen.data[0]["title"] == "fresh"

    asyncio.run(run())


def test_setup_reload_stale_failure_does_not_clobber_newer_success(
    wait_for_current_setup_epoch_applied,
):
    first_release = threading.Event()
    first_started = threading.Event()
    second_release = threading.Event()
    first_done = threading.Event()
    second_done = threading.Event()
    call_index = 0

    def _collect():
        nonlocal call_index
        call = call_index
        call_index += 1
        if call == 0:
            first_started.set()
            first_release.wait(timeout=5)
            first_done.set()
            raise RuntimeError("stale boom")
        second_release.wait(timeout=5)
        second_done.set()
        return _payload("fresh")

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.query_one(PickerScreen)
            base_epoch = screen._setup_epoch
            screen._prime_setup_reload = lambda: None
            screen._collect_setup_payload = _collect
            screen._reconcile_wt_sel = lambda: None
            failures = []

            def _record_failure(epoch, err):
                failures.append((epoch, str(err)))

            screen._apply_setup_failure = _record_failure

            screen._start_setup_reload_worker()
            assert first_started.wait(timeout=5)
            screen._start_setup_reload_worker()
            second_release.set()
            await wait_for_current_setup_epoch_applied(pilot, screen)

            first_release.set()
            await _settle_threads(first_done, second_done)
            for _ in range(20):
                await pilot.pause()
                await asyncio.sleep(0.01)

            assert failures == []
            assert screen._setup_applied_epoch == screen._setup_epoch == base_epoch + 2
            assert screen.data[0]["title"] == "fresh"

    asyncio.run(run())


def test_setup_reload_drops_results_after_unmount():
    release = threading.Event()
    worker_done = threading.Event()

    def _collect():
        release.wait(timeout=5)
        worker_done.set()
        return _payload("late")

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.query_one(PickerScreen)
            base_applied_epoch = screen._setup_applied_epoch
            screen._prime_setup_reload = lambda: None
            screen._collect_setup_payload = _collect
            screen._reconcile_wt_sel = lambda: None
            applied = []
            screen._apply_setup_payload = lambda payload: applied.append(payload)

            screen._start_setup_reload_worker()
            screen.on_unmount()
            release.set()
            await _settle_threads(worker_done)
            for _ in range(20):
                await pilot.pause()
                await asyncio.sleep(0.01)

            assert applied == []
            assert screen._setup_applied_epoch == base_applied_epoch

    asyncio.run(run())


def test_setup_reload_applies_pivots_and_rows_from_one_epoch(
    wait_for_current_setup_epoch_applied,
):
    first_release = threading.Event()
    first_started = threading.Event()
    second_release = threading.Event()
    first_done = threading.Event()
    second_done = threading.Event()
    seen = []
    call_index = 0

    def _collect():
        nonlocal call_index
        call = call_index
        call_index += 1
        if call == 0:
            first_started.set()
            first_release.wait(timeout=5)
            first_done.set()
            return _payload("alpha")
        second_release.wait(timeout=5)
        second_done.set()
        return _payload("beta")

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.query_one(PickerScreen)
            screen._prime_setup_reload = lambda: None
            screen._collect_setup_payload = _collect
            original_apply = screen._apply_setup_payload
            screen._reconcile_wt_sel = lambda: None

            def _record_apply(payload):
                original_apply(payload)
                seen.append((tuple(screen.htabs), screen.data[0]["title"]))

            screen._apply_setup_payload = _record_apply

            screen._start_setup_reload_worker()
            assert first_started.wait(timeout=5)
            screen._start_setup_reload_worker()
            second_release.set()
            await wait_for_current_setup_epoch_applied(pilot, screen)

            first_release.set()
            await _settle_threads(first_done, second_done)
            for _ in range(20):
                await pilot.pause()
                await asyncio.sleep(0.01)

            assert seen == [(("beta Tasks",), "beta")]
            assert tuple(screen.htabs) == ("beta Tasks",)
            assert screen.data[0]["title"] == "beta"

    asyncio.run(run())

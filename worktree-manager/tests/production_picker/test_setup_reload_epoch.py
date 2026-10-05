"""Phase 3c setup/reload UI-thread boundary tests for the production Picker.

These guards pair with the action/menu offload tests in ``test_picker_tui.py``:
both files define the standing "no blocking I/O on the render thread" contract
for Phase 3c's setup/reload and modal/action surfaces.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("textual", reason="textual not installed (optional TUI dep)")

from worktree_manager.production_picker.picker_tui import derive
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.engine_helpers import (
    _DEFAULT_HOST_COLS,
    _DEFAULT_TARGET_ENVS,
)
from worktree_manager.production_picker.picker_tui.engine_input import (
    PickerScreenInputMixin,
)
from worktree_manager.production_picker.picker_tui.engine_loading import (
    PickerScreenLoadingMixin,
)
from worktree_manager.production_picker.picker_tui.engine_pivot_actions import (
    PickerScreenPivotActionsMixin,
)
from worktree_manager.production_picker.picker_tui.engine_runtime import (
    PickerScreenRuntimeMixin,
    _SetupPayload,
)
from worktree_manager.production_picker.picker_tui.engine_worktree_actions import (
    PickerScreenWorktreeActionsMixin,
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


def _setup_state(screen: PickerScreen) -> dict[str, object]:
    return {
        "htabs": tuple(screen.htabs),
        "source_tabs": [dict(tab) for tab in screen.source_tabs],
        "machines": list(screen.machines),
        "source_local": screen._source_local,
        "source_repo_branch": screen._source_repo_branch,
        "machine_idx": screen.machine_idx,
        "data": list(screen.data),
        "load_delay": dict(screen.load_delay),
        "host_cols": list(screen.host_cols),
        "targets": list(screen.targets),
        "grid": dict(screen.grid),
        "applied": dict(screen.applied),
        "prof_unavailable": set(screen._prof_unavailable),
        "loader": screen.loader,
    }


async def _settle_threads(*events: threading.Event) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if all(event.is_set() for event in events):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for setup workers to finish")


def _wait_for_current_setup_epoch_applied_sync(
    screen,
    *,
    timeout: float = 5.0,
) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = getattr(screen, "_setup_epoch", 0)
        if current != 0 and getattr(screen, "_setup_applied_epoch", 0) == current:
            return current
        time.sleep(0.01)
    current = getattr(screen, "_setup_epoch", 0)
    applied = getattr(screen, "_setup_applied_epoch", 0)
    raise AssertionError(
        f"timed out waiting for setup epoch {current} to apply (applied={applied})"
    )


class _ImmediateApp:
    def call_from_thread(self, fn):
        fn()


class _SetupRaceScreen(
    PickerScreenInputMixin,
    PickerScreenPivotActionsMixin,
    PickerScreenWorktreeActionsMixin,
    PickerScreenRuntimeMixin,
):
    def __init__(self):
        self.app = _ImmediateApp()
        self.src = SimpleNamespace(REPO="repo")
        self._bg_cancel = threading.Event()
        self._bg_threads = set()
        self._setup_epoch = 0
        self._setup_applied_epoch = 0
        self._setup_failed_epoch = 0
        self._pending_setup_payloads = {}
        self._setup_payloads_lock = threading.Lock()
        self._busy_label = None
        self.debug = ""
        self.sel = ("L", 0)
        self.last_l = 0
        self.last_pr = 0
        self.cmd_mode = False
        self.data = []
        self.htabs = []
        self.applied_titles = []

    def default_sel(self):
        return ("L", 0)

    def stops(self):
        return [("L", 0)]

    def _prime_setup_reload(self):
        return None

    def _invalidate_setup_reload_caches(self):
        return None

    def _apply_setup_payload(self, payload):
        self.htabs = [item["label"] for item in payload.pivot_payload[1]]
        self.data = list(payload.data)
        self.applied_titles.append(payload.data[0]["title"])

    def refresh(self):
        return None

    def _run_bg(self, label, work, done=None, *, quiet=False):
        if done is not None:
            done((True, "done"))

    def _pivot_machine_id(self):
        return "host"

    def post_message(self, message):
        """Mirrors ``_ImmediateApp.call_from_thread``'s old intent: apply
        deterministically/immediately regardless of which thread posted,
        so this race-condition fixture stays synchronous and assertion-
        friendly without a real Textual App/event loop running."""
        from worktree_manager.production_picker.picker_tui.inbox import ensure_inbox

        ensure_inbox(self).drain_apply()
        return True


def _install_setup_race(screen, *, first_tag: str, second_tag: str) -> dict[str, object]:
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
            return _payload(first_tag)
        second_release.wait(timeout=5)
        second_done.set()
        return _payload(second_tag)

    screen._collect_setup_payload = _collect
    return {
        "first_release": first_release,
        "first_started": first_started,
        "second_release": second_release,
        "first_done": first_done,
        "second_done": second_done,
    }


def test_setup_reload_records_a_diagnosed_failure_when_waking_the_render_flow_fails():
    """#5220: when waking the render flow to apply the collected payload
    fails (most commonly the App's event loop not running, or already
    stopped/shutting down -- here simulated by a screen with no working
    ``post_message`` at all), the collected payload must not be silently
    dropped -- a diagnosed failure must land in ``_setup_failed_epoch`` so a
    poller (capture.py's ``_wait_for_initial_setup``) sees the actual cause
    instead of spinning until its own unrelated timeout.
    """
    disposed = threading.Event()
    failed = threading.Event()

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
            self._pending_setup_payloads = {}
            self._setup_payloads_lock = threading.Lock()
            self.applied = []
            self.failures: list[tuple[int, Exception]] = []

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
            self.failures.append((epoch, err))
            failed.set()

        def refresh(self):
            return None

    screen = _Screen()
    epoch = screen._start_setup_reload_worker()
    assert disposed.wait(timeout=5)
    assert failed.wait(timeout=5)
    assert screen.applied == []
    assert len(screen.failures) == 1
    failed_epoch, err = screen.failures[0]
    assert failed_epoch == epoch
    assert "could not wake the render flow" in str(err)


def test_setup_reload_wake_failure_leaves_no_stale_closure_for_a_later_drain():
    """A regression for the wake-failure fallback above: the posted
    ``_apply`` closure must be invalidated/removed from the inbox when the
    wake itself fails, not merely left to be (re-)drained later. Otherwise
    a subsequent, unrelated drain (a render tick) would still invoke the
    stale closure, which re-reads the by-then-already-disposed payload via
    ``_release_setup_payload(epoch) or payload`` and re-applies it --
    exactly the double-apply-after-dispose bug this guards against.
    """
    disposed = threading.Event()
    failed = threading.Event()

    class _Loader:
        def cancel(self):
            disposed.set()

    class _Screen(PickerScreenRuntimeMixin):
        def __init__(self):
            self.app = object()  # resolvable but has no post_message at all
            self._bg_cancel = threading.Event()
            self._setup_epoch = 0
            self._setup_applied_epoch = 0
            self._setup_failed_epoch = 0
            self._pending_setup_payloads = {}
            self._setup_payloads_lock = threading.Lock()
            self.applied = []
            self.failures: list[tuple[int, Exception]] = []

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
            self.failures.append((epoch, err))
            failed.set()

        def refresh(self):
            return None

    screen = _Screen()
    screen._start_setup_reload_worker()
    assert disposed.wait(timeout=5)
    assert failed.wait(timeout=5)
    # The fallback already recorded a diagnosed failure -- confirm the
    # actual regression: nothing is left pending for a later, unrelated
    # drain to wrongly pick up and re-apply.
    assert screen.inbox.pending_slots() == frozenset()
    assert screen.inbox.drain_apply() == 0
    assert screen.applied == []


def test_setup_reload_records_a_diagnosed_failure_when_app_is_unresolvable():
    """#5220's other traced failure mode: ``self.app`` raising/being ``None``
    when the worker was scheduled (e.g. the screen wasn't yet mounted into
    a running App). Must record a diagnosed failure, not drop silently.
    """
    disposed = threading.Event()
    failed = threading.Event()

    class _Loader:
        def cancel(self):
            disposed.set()

    class _Screen(PickerScreenRuntimeMixin):
        def __init__(self):
            self._bg_cancel = threading.Event()
            self._setup_epoch = 0
            self._setup_applied_epoch = 0
            self._setup_failed_epoch = 0
            self._pending_setup_payloads = {}
            self._setup_payloads_lock = threading.Lock()
            self.applied = []
            self.failures: list[tuple[int, Exception]] = []

        @property
        def app(self):
            raise RuntimeError("no active app for this screen")

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
            self.failures.append((epoch, err))
            failed.set()

        def refresh(self):
            return None

    screen = _Screen()
    epoch = screen._start_setup_reload_worker()
    assert disposed.wait(timeout=5)
    assert failed.wait(timeout=5)
    assert screen.applied == []
    assert len(screen.failures) == 1
    failed_epoch, err = screen.failures[0]
    assert failed_epoch == epoch
    assert "no active app" in str(err)


def test_setup_reload_unmount_disposes_payload_if_marshalled_callback_never_runs():
    disposed = threading.Event()
    marshalled = threading.Event()

    class _Loader:
        def cancel(self):
            disposed.set()

    class _App:
        def call_from_thread(self, fn):
            marshalled.set()

    class _Screen(PickerScreenLoadingMixin, PickerScreenRuntimeMixin):
        def __init__(self):
            self.app = _App()
            self._bg_cancel = threading.Event()
            self._setup_epoch = 0
            self._setup_applied_epoch = 0
            self._setup_failed_epoch = 0
            self._pending_setup_payloads = {}
            self._setup_payloads_lock = threading.Lock()
            self.loader = None
            self._provider_loader_lock = threading.Lock()
            self._provider_loader = None
            self._provider_cancelled = False
            self._frame_health = None
            self._pivot_runtimes = {}
            self.applied = []

        def post_message(self, message):
            """Mirrors the old fake ``_App.call_from_thread``'s intent: the
            outcome is scheduled (signalling ``marshalled``) but -- unlike
            ``_SetupRaceScreen``'s fixture -- deliberately never actually
            drained/applied, so ``on_unmount``'s own payload disposal (not
            the ``_apply`` closure itself) is what this test exercises."""
            marshalled.set()
            return True

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
    assert marshalled.wait(timeout=5)
    screen.on_unmount()
    assert disposed.wait(timeout=5)
    assert screen.applied == []


def test_setup_reload_disposes_payload_when_apply_raises():
    disposed = threading.Event()
    raised = threading.Event()

    class _Loader:
        def cancel(self):
            disposed.set()

    class _App:
        def call_from_thread(self, fn):
            try:
                fn()
            except RuntimeError:
                raised.set()

    class _Screen(PickerScreenRuntimeMixin):
        def __init__(self):
            self.app = _App()
            self._bg_cancel = threading.Event()
            self._setup_epoch = 0
            self._setup_applied_epoch = 0
            self._setup_failed_epoch = 0
            self._pending_setup_payloads = {}
            self._setup_payloads_lock = threading.Lock()

        def post_message(self, message):
            """Mirrors the old fake ``_App.call_from_thread``: drains and
            applies immediately, swallowing the ``RuntimeError`` ``_apply``
            itself re-raises after disposing the payload -- this is NOT a
            wake failure (so it must never trip the new "could not wake"
            fallback in ``_start_setup_reload_worker``), it's the
            already-handled "apply itself raised" path."""
            from worktree_manager.production_picker.picker_tui.inbox import (
                ensure_inbox,
            )

            try:
                ensure_inbox(self).drain_apply()
            except RuntimeError:
                raised.set()
            return True

        def _prime_setup_reload(self):
            return None

        def _collect_setup_payload(self):
            return replace(_payload("live"), loader=_Loader())

        def _invalidate_setup_reload_caches(self):
            return None

        def _apply_setup_payload(self, payload):
            raise RuntimeError("apply blew up")

        def _apply_setup_failure(self, epoch, err):
            raise AssertionError(f"unexpected failure path: {epoch} {err}")

        def refresh(self):
            return None

    screen = _Screen()
    screen._start_setup_reload_worker()
    assert raised.wait(timeout=5)
    assert disposed.wait(timeout=5)


def test_sync_setup_disposes_payload_when_apply_raises():
    disposed = threading.Event()

    class _Loader:
        def cancel(self):
            disposed.set()

    class _Screen(PickerScreenRuntimeMixin):
        def __init__(self):
            self._bg_cancel = threading.Event()
            self._setup_epoch = 0
            self._setup_applied_epoch = 0
            self._setup_failed_epoch = 0

        def _prime_setup_reload(self):
            return None

        def _collect_setup_payload(self):
            return replace(_payload("live"), loader=_Loader())

        def _invalidate_setup_reload_caches(self):
            return None

        def _apply_setup_payload(self, payload):
            raise RuntimeError("apply blew up")

    screen = _Screen()
    with pytest.raises(RuntimeError, match="apply blew up"):
        screen.setup_sync_for_tests()

    assert disposed.is_set()


def test_setup_sync_runs_one_local_reconcile_batch_per_epoch():
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng
    from worktree_manager.production_picker.picker_tui import data_local

    calls = {"batch": 0}

    screen = eng.PickerScreen(data_local, live=False)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(data_local.context, "project", lambda: "example")
    monkeypatch.setattr(
        data_local.engine_client,
        "list_worktree_rows",
        lambda *_args, **_kwargs: [{"id": "wt-a", "state": "wip"}],
    )
    monkeypatch.setattr(
        data_local.engine_group_c,
        "picker_reconcile_local",
        lambda *_args, **_kwargs: calls.__setitem__("batch", calls["batch"] + 1)
        or type("Batch", (), {"rows": [{"id": "wt-a"}], "summary": {"mux_scan_ok": True}})(),
    )

    try:
        screen.setup_sync_for_tests()
        screen.setup_sync_for_tests()
    finally:
        monkeypatch.undo()

    assert calls == {"batch": 2}


def test_apply_setup_payload_cancels_replaced_loader():
    disposed = threading.Event()

    class _Loader:
        def __init__(self, on_cancel=None):
            self._on_cancel = on_cancel

        def cancel(self):
            if self._on_cancel is not None:
                self._on_cancel.set()

    screen = PickerScreen(_fixture_source(), live=False)
    screen._reconcile_wt_sel = lambda: None
    screen.loader = _Loader(disposed)
    payload = replace(_payload("replacement"), loader=_Loader())

    screen._apply_setup_payload(payload)

    assert disposed.is_set()
    assert screen.loader is payload.loader


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
            await wait_for_current_setup_epoch_applied(pilot, screen)
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
            await wait_for_current_setup_epoch_applied(pilot, screen)
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


def test_setup_reload_drops_results_after_unmount(
    wait_for_current_setup_epoch_applied,
):
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
            await wait_for_current_setup_epoch_applied(pilot, screen)
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
            await wait_for_current_setup_epoch_applied(pilot, screen)
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


def test_non_live_mount_paints_skeleton_before_blocked_load_returns(
    monkeypatch,
    wait_for_current_setup_epoch_applied,
):
    load_started = threading.Event()
    release_load = threading.Event()

    class Src:
        LOCAL = ("host", "Win")
        REPO = "repo"
        BRANCH = "branch"

        @staticmethod
        def machines():
            return [("host Win", "host", "Win", True)]

        @staticmethod
        def load():
            load_started.set()
            release_load.wait(timeout=5)
            return [
                derive.norm(
                    {
                        "id": "loaded-id",
                        "title": "loaded",
                        "status": "active",
                        "state": "wip",
                        "session_count": 1,
                    },
                    "host",
                    "Win",
                )
            ]

        bucket = staticmethod(derive.bucket)
        for_machine = staticmethod(derive.for_machine)

    pivot_payload = (
        [],
        [{"label": "Loaded Tasks", "kind": "tasks", "pivot": None}],
        [],
        [],
    )
    monkeypatch.setattr(PickerScreen, "_scan_pivot_payload", lambda self: pivot_payload)

    async def run():
        app = PickerApp(Src(), live=False)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.query_one(PickerScreen)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if load_started.is_set():
                    break
                await pilot.pause()
                await asyncio.sleep(0.01)
            assert load_started.is_set()

            assert screen._setup_epoch == 1
            assert screen._setup_applied_epoch == 0
            assert screen._busy_label == "Loading…"
            assert screen.loader is None
            assert screen.data == []
            assert tuple(screen.htabs)
            assert "Loaded Tasks" not in tuple(screen.htabs)
            assert len(screen.source_tabs) == 2
            assert screen.source_tabs[0]["label"] == "All"
            assert screen.machine_idx == 1

            release_load.set()
            await wait_for_current_setup_epoch_applied(pilot, screen)

            assert screen._busy_label is None
            assert tuple(screen.htabs) == ("Loaded Tasks",)
            assert screen.data[0]["title"] == "loaded"

    asyncio.run(run())


def test_non_live_mount_eventually_matches_sync_test_setup_result(
    monkeypatch,
    wait_for_current_setup_epoch_applied,
):
    release_load = threading.Event()

    def _rows():
        return [
            derive.norm(
                {
                    "id": "loaded-id",
                    "title": "loaded",
                    "status": "active",
                    "state": "wip",
                    "session_count": 1,
                },
                "host",
                "Win",
            )
        ]

    class SyncSrc:
        LOCAL = ("host", "Win")
        REPO = "repo"
        BRANCH = "branch"

        @staticmethod
        def machines():
            return [("host Win", "host", "Win", True)]

        @staticmethod
        def load():
            return _rows()

        bucket = staticmethod(derive.bucket)
        for_machine = staticmethod(derive.for_machine)

    class AsyncSrc(SyncSrc):
        @staticmethod
        def load():
            release_load.wait(timeout=5)
            return _rows()

    pivot_payload = (
        [],
        [{"label": "Loaded Tasks", "kind": "tasks", "pivot": None}],
        [],
        [],
    )
    monkeypatch.setattr(PickerScreen, "_scan_pivot_payload", lambda self: pivot_payload)

    baseline = PickerScreen(SyncSrc(), live=False)
    baseline.setup_sync_for_tests()
    expected = _setup_state(baseline)

    async def run():
        app = PickerApp(AsyncSrc(), live=False)
        async with app.run_test(size=(100, 30)) as pilot:
            screen = app.query_one(PickerScreen)
            release_load.set()
            await wait_for_current_setup_epoch_applied(pilot, screen)

            assert _setup_state(screen) == expected
            assert screen._busy_label is None

    asyncio.run(run())


@pytest.mark.parametrize(
    ("label", "trigger", "expected_debug"),
    [
        (
            "manual-reload-key",
            lambda screen: screen._dispatch_key("r"),
            "refreshed · reloaded worktrees",
        ),
        (
            "config-section-rescan",
            lambda screen: screen._run_config_section(
                SimpleNamespace(label="Config", source="plugin")
            ),
            "Config (plugin): done",
        ),
        (
            "worktree-action-rescan",
            lambda screen: screen._run_wt_action(
                SimpleNamespace(label="Action", source="plugin"),
                {
                    "id4": "wt-id",
                    "machine": "host",
                    "env": "Win",
                    "title": "Example worktree",
                    "raw": {"id": "wt-id", "machine": "host"},
                },
            ),
            "Action (plugin): done",
        ),
    ],
)
def test_setup_reload_ui_entrypoints_do_not_block_while_collecting_payload(
    label,
    trigger,
    expected_debug,
):
    """Phase 3c boundary: UI-thread setup/reload entrypoints must never wait on
    ``_collect_setup_payload()`` inline.

    The worker's collect phase is gated behind an Event. If any entrypoint is
    changed back to synchronous inline setup (for example
    ``setup_sync_for_tests()``) or an equivalent direct-I/O path,
    the trigger call itself blocks here and this test fails with a targeted
    message naming the offending UI callback.
    """
    screen = _SetupRaceScreen()
    race = _install_setup_race(
        screen,
        first_tag=f"{label}-result",
        second_tag="unused",
    )

    started = time.monotonic()
    trigger(screen)
    elapsed = time.monotonic() - started

    assert race["first_started"].wait(timeout=5), (
        f"{label} never scheduled a setup worker; expected "
        "_start_setup_reload_worker()"
    )
    assert elapsed < 1.0, (
        f"{label} blocked on _collect_setup_payload(); keep setup/reload I/O "
        "off the UI thread via _start_setup_reload_worker()"
    )
    assert screen._setup_epoch == 1
    assert screen._setup_applied_epoch == 0
    assert screen.applied_titles == []
    assert screen.debug == expected_debug

    race["first_release"].set()
    _wait_for_current_setup_epoch_applied_sync(screen)

    assert screen._setup_applied_epoch == 1
    assert screen.applied_titles == [f"{label}-result"]
    assert screen.data[0]["title"] == f"{label}-result"
    assert tuple(screen.htabs) == (f"{label}-result Tasks",)


def test_manual_reload_key_prefers_newer_epoch():
    screen = _SetupRaceScreen()
    race = _install_setup_race(screen, first_tag="stale", second_tag="fresh")

    started = time.monotonic()
    screen._dispatch_key("r")
    elapsed_first = time.monotonic() - started
    assert race["first_started"].wait(timeout=5)

    started = time.monotonic()
    screen._dispatch_key("r")
    elapsed_second = time.monotonic() - started

    assert elapsed_first < 1.0
    assert elapsed_second < 1.0
    assert screen._setup_epoch == 2
    assert screen._setup_applied_epoch == 0
    assert screen.sel == ("L", 0)
    assert screen.debug == "refreshed · reloaded worktrees"

    race["second_release"].set()
    _wait_for_current_setup_epoch_applied_sync(screen)
    assert screen.applied_titles == ["fresh"]

    race["first_release"].set()
    assert race["first_done"].wait(timeout=5)
    assert race["second_done"].wait(timeout=5)
    time.sleep(0.05)

    assert screen._setup_epoch == 2
    assert screen._setup_applied_epoch == 2
    assert screen.applied_titles == ["fresh"]
    assert tuple(screen.htabs) == ("fresh Tasks",)
    assert screen.data[0]["title"] == "fresh"


@pytest.mark.parametrize(
    ("order", "expected_title"),
    [
        ("rescan-then-reload", "manual-reload"),
        ("reload-then-rescan", "config-rescan"),
    ],
)
def test_config_section_rescan_race_prefers_newer_epoch(order, expected_title):
    screen = _SetupRaceScreen()
    race = _install_setup_race(
        screen,
        first_tag="config-rescan" if order == "rescan-then-reload" else "manual-reload",
        second_tag=expected_title,
    )
    section = SimpleNamespace(label="Config", source="plugin")

    if order == "rescan-then-reload":
        started = time.monotonic()
        screen._run_config_section(section)
        elapsed_first = time.monotonic() - started
        assert race["first_started"].wait(timeout=5)
        started = time.monotonic()
        screen._dispatch_key("r")
        elapsed_second = time.monotonic() - started
        assert screen.debug == "refreshed · reloaded worktrees"
    else:
        started = time.monotonic()
        screen._dispatch_key("r")
        elapsed_first = time.monotonic() - started
        assert race["first_started"].wait(timeout=5)
        started = time.monotonic()
        screen._run_config_section(section)
        elapsed_second = time.monotonic() - started
        assert screen.debug == "Config (plugin): done"

    assert elapsed_first < 1.0
    assert elapsed_second < 1.0
    assert screen._setup_epoch == 2
    assert screen._setup_applied_epoch == 0
    assert screen.sel == ("L", 0)

    race["second_release"].set()
    _wait_for_current_setup_epoch_applied_sync(screen)
    assert screen.applied_titles == [expected_title]

    race["first_release"].set()
    assert race["first_done"].wait(timeout=5)
    assert race["second_done"].wait(timeout=5)
    time.sleep(0.05)

    assert screen._setup_epoch == 2
    assert screen._setup_applied_epoch == 2
    assert screen.applied_titles == [expected_title]
    assert tuple(screen.htabs) == (f"{expected_title} Tasks",)
    assert screen.data[0]["title"] == expected_title


@pytest.mark.parametrize(
    ("order", "expected_title"),
    [
        ("rescan-then-reload", "manual-reload"),
        ("reload-then-rescan", "wt-rescan"),
    ],
)
def test_worktree_action_rescan_race_prefers_newer_epoch(order, expected_title):
    screen = _SetupRaceScreen()
    race = _install_setup_race(
        screen,
        first_tag="wt-rescan" if order == "rescan-then-reload" else "manual-reload",
        second_tag=expected_title,
    )
    action = SimpleNamespace(label="Action", source="plugin")
    rec = {
        "id4": "wt-id",
        "machine": "host",
        "env": "Win",
        "title": "Example worktree",
        "raw": {"id": "wt-id", "machine": "host"},
    }

    if order == "rescan-then-reload":
        started = time.monotonic()
        screen._run_wt_action(action, rec)
        elapsed_first = time.monotonic() - started
        assert race["first_started"].wait(timeout=5)
        started = time.monotonic()
        screen._dispatch_key("r")
        elapsed_second = time.monotonic() - started
        assert screen.debug == "refreshed · reloaded worktrees"
    else:
        started = time.monotonic()
        screen._dispatch_key("r")
        elapsed_first = time.monotonic() - started
        assert race["first_started"].wait(timeout=5)
        started = time.monotonic()
        screen._run_wt_action(action, rec)
        elapsed_second = time.monotonic() - started
        assert screen.debug == "Action (plugin): done"

    assert elapsed_first < 1.0
    assert elapsed_second < 1.0
    assert screen._setup_epoch == 2
    assert screen._setup_applied_epoch == 0
    assert screen.sel == ("L", 0)

    race["second_release"].set()
    _wait_for_current_setup_epoch_applied_sync(screen)
    assert screen.applied_titles == [expected_title]

    race["first_release"].set()
    assert race["first_done"].wait(timeout=5)
    assert race["second_done"].wait(timeout=5)
    time.sleep(0.05)

    assert screen._setup_epoch == 2
    assert screen._setup_applied_epoch == 2
    assert screen.applied_titles == [expected_title]
    assert tuple(screen.htabs) == (f"{expected_title} Tasks",)
    assert screen.data[0]["title"] == expected_title

"""First-paint regressions carried forward from the bundled picker."""

from __future__ import annotations

import importlib
import json
import sys
import threading
import time

import pytest


def test_data_local_import_does_not_load_config(monkeypatch):
    from worktree_manager.production_picker import config as cfg

    def boom(*_a, **_k):
        raise AssertionError("load_config must not run at import")

    monkeypatch.setattr(cfg, "load_config", boom)
    sys.modules.pop("worktree_manager.production_picker.picker_tui.data_local", None)
    mod = importlib.import_module(
        "worktree_manager.production_picker.picker_tui.data_local"
    )
    assert mod.LOCAL
    assert mod.LOCAL_LABEL


def test_data_ssh_import_does_not_load_config(monkeypatch):
    from worktree_manager.production_picker import config as cfg

    def boom(*_a, **_k):
        raise AssertionError("load_config must not run at import")

    monkeypatch.setattr(cfg, "load_config", boom)
    sys.modules.pop("worktree_manager.production_picker.picker_tui.data_ssh", None)
    mod = importlib.import_module(
        "worktree_manager.production_picker.picker_tui.data_ssh"
    )
    assert mod.LOCAL_LABEL


def test_picker_init_skips_pivot_scan(monkeypatch):
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    def boom(*_a, **_k):
        raise AssertionError("scan_pivot_registry must not run in __init__")

    monkeypatch.setattr(
        "worktree_manager.production_picker.picker_tui.pivots.scan_pivot_registry",
        boom,
    )

    class Src:
        LOCAL = ("host", "Win")

    screen = eng.PickerScreen(Src(), live=True)
    kinds = [d["kind"] for d in screen.pivots]
    assert "worktrees" in kinds


def test_setup_live_pivots_prewarms_optional_modules(monkeypatch):
    """``_setup_live_pivots`` (already a background thread, alongside the
    pivot filesystem scan) must warm ``tasks.prewarm_optional_modules`` --
    see that function's own docstring for why: a registered pivot's first
    switch/render previously paid a real, synchronous multi-module import
    hitch on the render/key-handling thread, profiled at roughly 40% of the
    total switch latency -- exactly the momentary freeze reported against
    the Tasks pivot."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng
    from worktree_manager.production_picker.picker_tui import tasks as tasks_mod

    calls = []
    monkeypatch.setattr(tasks_mod, "prewarm_optional_modules", lambda: calls.append(1))

    class Src:
        LOCAL = ("host", "Win")

    screen = eng.PickerScreen(Src(), live=True)
    monkeypatch.setattr(screen, "_scan_pivot_payload", lambda: None)
    screen._setup_live_pivots()

    assert calls == [1]


def test_setup_live_pivots_prewarm_starts_before_the_pivot_scan(monkeypatch):
    """Same ordering requirement as ``setup()`` (see
    ``test_setup_prewarm_starts_before_the_pivot_scan``): even though
    ``_setup_live_pivots`` already runs off the render thread, starting the
    prewarm import before the scan (rather than after) maximizes its head
    start over the operator's first pivot-switch keypress, wall-clock-wise
    from mount."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng
    from worktree_manager.production_picker.picker_tui import tasks as tasks_mod

    order = []
    monkeypatch.setattr(
        tasks_mod, "prewarm_optional_modules", lambda: order.append("prewarm"))

    class Src:
        LOCAL = ("host", "Win")

    screen = eng.PickerScreen(Src(), live=True)

    def recording_scan():
        order.append("scan")
        return None

    monkeypatch.setattr(screen, "_scan_pivot_payload", recording_scan)
    screen._setup_live_pivots()

    assert order == ["prewarm", "scan"], (
        "prewarm_optional_modules must start before _scan_pivot_payload, not after")


def test_setup_live_pivots_prewarms_machine_key_map(monkeypatch):
    """``_setup_live_pivots`` must also warm the machine-key-map RESULT (not
    just the ``data_ssh`` import): ``_machine_key_map()``'s underlying
    ``agent_worktrees.config.load_config()`` call is uncached and was
    profiled at several seconds on a machine with many registered repos --
    far more than the import cost alone. ``_prewarm_machine_key_map`` runs
    the actual compute on its own background thread (inlined here via a
    monkeypatched ``threading.Thread`` for a deterministic assertion) and
    applies the result via ``_apply_from_worker``."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    class InlineThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(eng.threading, "Thread", InlineThread)

    class Src:
        LOCAL = ("host", "Win")

    screen = eng.PickerScreen(Src(), live=True)
    monkeypatch.setattr(screen, "_scan_pivot_payload", lambda: None)

    from worktree_manager.production_picker.picker_tui import data_ssh
    monkeypatch.setattr(data_ssh, "machine_key_map", lambda: {"Host": "host"})

    assert screen._mkey_map is None
    screen._setup_live_pivots()

    assert screen._mkey_map == {"Host": "host"}


def test_prewarm_machine_key_map_does_not_block_the_calling_thread(monkeypatch):
    """``setup()`` must run ``_prewarm_machine_key_map``'s compute on a
    background thread, not inline -- a slow/cold ``load_config()`` call
    there must never reintroduce the freeze this exists to remove."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng
    from worktree_manager.production_picker.picker_tui import tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "prewarm_optional_modules", lambda: None)

    release = threading.Event()

    class Src:
        LOCAL = ("host", "Win")

        @staticmethod
        def machines():
            return [("host Win", "host", "Win", True)]

        @staticmethod
        def load():
            return []

    screen = eng.PickerScreen(Src(), live=False)

    from worktree_manager.production_picker.picker_tui import data_ssh

    def slow_machine_key_map():
        release.wait(timeout=5)
        return {"Host": "host"}

    monkeypatch.setattr(data_ssh, "machine_key_map", slow_machine_key_map)
    try:
        t0 = time.perf_counter()
        screen.setup()
        elapsed = time.perf_counter() - t0
    finally:
        release.set()  # let the worker thread's slow call unblock and finish

    assert elapsed < 1.0


def test_prewarm_machine_key_map_is_a_noop_once_already_resolved(monkeypatch):
    """A second call (e.g. a later 'r' reload) must not recompute the map
    once it's already resolved -- avoids paying the expensive
    ``load_config()`` call again for no reason."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    class Src:
        LOCAL = ("host", "Win")

    screen = eng.PickerScreen(Src(), live=True)
    screen._mkey_map = {"already": "resolved"}

    calls = []
    from worktree_manager.production_picker.picker_tui import data_ssh
    monkeypatch.setattr(data_ssh, "machine_key_map", lambda: calls.append(1))

    screen._prewarm_machine_key_map()

    assert calls == []
    assert screen._mkey_map == {"already": "resolved"}


def test_machine_key_map_never_blocks_even_on_a_stuck_compute(monkeypatch):
    """``_machine_key_map()`` itself must NEVER call ``data_ssh.machine_key_map``
    (and its uncached, multi-second ``load_config()``) directly, even when
    nothing has prewarmed the result yet -- it degrades to ``{}`` immediately
    and only kicks the background prewarm as a safety net. This is the core
    of the fix: previously, an operator's pivot-switch keypress landing before
    the prewarm finished would still block on a fresh, uncached compute --
    reproduced here with a ``data_ssh.machine_key_map`` that never returns
    within the test's lifetime."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    class Src:
        LOCAL = ("host", "Win")

    screen = eng.PickerScreen(Src(), live=True)

    stuck = threading.Event()
    from worktree_manager.production_picker.picker_tui import data_ssh

    def never_returns():
        stuck.wait()  # blocks until the test itself releases it (or times out)
        return {"Host": "host"}

    monkeypatch.setattr(data_ssh, "machine_key_map", never_returns)
    try:
        t0 = time.perf_counter()
        result = screen._machine_key_map()
        elapsed = time.perf_counter() - t0
    finally:
        stuck.set()  # let the background thread unblock and finish

    assert elapsed < 1.0
    assert result == {}


def test_prewarm_machine_key_map_does_not_spawn_a_second_thread_while_inflight(
        monkeypatch):
    """Calling ``_prewarm_machine_key_map`` again while a previous call's
    background compute is still running must not spawn a second thread
    (and therefore not run the expensive compute twice concurrently)."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    class Src:
        LOCAL = ("host", "Win")

    screen = eng.PickerScreen(Src(), live=True)

    thread_starts = []
    real_thread = eng.threading.Thread

    class TrackedThread:
        def __init__(self, target, **kwargs):
            thread_starts.append(1)
            self._real = real_thread(target=target, **kwargs)

        def start(self):
            self._real.start()

        def join(self, *a, **k):
            self._real.join(*a, **k)

    monkeypatch.setattr(eng.threading, "Thread", TrackedThread)

    release = threading.Event()
    from worktree_manager.production_picker.picker_tui import data_ssh

    def slow_machine_key_map():
        release.wait(timeout=5)
        return {"Host": "host"}

    monkeypatch.setattr(data_ssh, "machine_key_map", slow_machine_key_map)
    try:
        screen._prewarm_machine_key_map()
        screen._prewarm_machine_key_map()  # must be a no-op: already in flight
        assert thread_starts == [1]
    finally:
        release.set()



def test_setup_prewarms_optional_modules_too(monkeypatch):
    """``setup()`` -- the shared non-live-mount / manual-reload ('r') path --
    must warm the same modules as ``_setup_live_pivots``: a registered pivot
    can be *first discovered* here too (e.g. a plugin installed after the
    picker started, picked up on the next 'r' reload), and unlike
    ``_setup_live_pivots`` this path already runs synchronously either way
    (pre-existing pivot filesystem scan), so it must not be the one place
    left paying the import hitch on the UI thread."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng
    from worktree_manager.production_picker.picker_tui import tasks as tasks_mod

    calls = []
    monkeypatch.setattr(tasks_mod, "prewarm_optional_modules", lambda: calls.append(1))

    class Src:
        LOCAL = ("host", "Win")

        @staticmethod
        def machines():
            return [("host Win", "host", "Win", True)]

        @staticmethod
        def load():
            return []

    screen = eng.PickerScreen(Src(), live=False)
    calls.clear()  # __init__/on_mount may already have called setup() once
    screen.setup()

    assert calls == [1]


def test_setup_prewarm_starts_before_the_pivot_scan(monkeypatch):
    """``setup()`` must kick off the ``prewarm_optional_modules`` thread
    BEFORE running the (potentially slow, synchronous) pivot-registry scan,
    not after it.

    The prewarm thread exists purely to give ``data_ssh``'s import a head
    start over the operator's first pivot-switch keypress (see
    ``prewarm_optional_modules``'s own docstring). If ``setup()`` runs the
    scan first and only starts the prewarm thread once the scan returns, the
    render thread is blocked for the scan's own duration AND the prewarm
    thread barely has a head start once input resumes -- the operator's very
    next keypress (often landing the instant the app looks responsive again)
    can still race the same import lock the prewarm was meant to avoid. This
    was reported as a live pivot-switch freeze even with the prewarm fix
    already in place; asserting the ordering here keeps a future edit from
    silently re-introducing it."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng
    from worktree_manager.production_picker.picker_tui import tasks as tasks_mod

    order = []
    monkeypatch.setattr(
        tasks_mod, "prewarm_optional_modules", lambda: order.append("prewarm"))

    class Src:
        LOCAL = ("host", "Win")

        @staticmethod
        def machines():
            return [("host Win", "host", "Win", True)]

        @staticmethod
        def load():
            return []

    screen = eng.PickerScreen(Src(), live=False)

    def recording_load_pivots(*a, **k):
        order.append("scan")
        return original_load_pivots(*a, **k)

    original_load_pivots = screen._load_pivots
    monkeypatch.setattr(screen, "_load_pivots", recording_load_pivots)
    order.clear()  # __init__/on_mount may already have called setup() once
    screen.setup()

    assert order == ["prewarm", "scan"], (
        "prewarm_optional_modules must start before _load_pivots, not after")


def test_prewarm_optional_modules_imports_data_ssh(monkeypatch):
    """Unlike the call-count test above (which spies on the seam so
    ``_setup_live_pivots`` stays independently testable), this exercises the
    real function to confirm it actually imports ``data_ssh`` -- not merely
    *some* import. Spies on ``builtins.__import__`` (the function uses
    ``from . import data_ssh``) rather than popping the module from
    ``sys.modules``: popping doesn't clear the parent package's own cached
    attribute, so a subsequent ``from . import x`` can silently rebind the
    stale attribute without ever re-registering the module in
    ``sys.modules`` -- an import-system quirk that made an earlier version
    of this test spuriously fail."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import tasks as tasks_mod

    class InlineThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(tasks_mod.threading, "Thread", InlineThread)

    imported = []
    import builtins

    real_import = builtins.__import__

    def import_spy(name, globals=None, locals=None, fromlist=(), level=0):
        mod = real_import(name, globals, locals, fromlist, level)
        if level and fromlist:
            for item in fromlist:
                if item == "data_ssh":
                    imported.append(item)
        return mod

    monkeypatch.setattr(builtins, "__import__", import_spy)

    tasks_mod.prewarm_optional_modules()

    assert "data_ssh" in imported


def test_prewarm_optional_modules_survives_import_error(monkeypatch):
    """Best-effort: a broken/uninstallable optional module must not crash
    the background pivot-scan thread it shares with (#B pivot filesystem
    scan)."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import tasks as tasks_mod

    class InlineThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(tasks_mod.threading, "Thread", InlineThread)

    import builtins

    real_import = builtins.__import__

    def boom(name, globals=None, locals=None, fromlist=(), level=0):
        # ``from . import data_ssh`` calls ``__import__('', ..., ('data_ssh',),
        # 1)`` -- the relative-import ``name`` is empty and the submodule
        # shows up in ``fromlist``, not appended to ``name`` (an earlier
        # version of this test checked ``name.endswith(".data_ssh")``, which
        # never matched, so the simulated failure was never exercised).
        if level and "data_ssh" in fromlist:
            raise ImportError("simulated broken optional module")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", boom)

    tasks_mod.prewarm_optional_modules()  # must not raise


def test_prewarm_optional_modules_spawns_no_thread_of_its_own(monkeypatch):
    """``prewarm_optional_modules()`` itself must import inline (no nested
    thread of its own): ``_setup_live_pivots`` (already a background thread)
    relies on this call completing *before* it schedules the UI-thread
    ``apply()`` that installs/activates registered pivots -- spawning a
    second, independent thread here would race a keypress that lands on a
    registered pivot while that inner thread is still mid-import (CPython's
    per-module import lock would then block the render thread on the same
    import anyway, only shrinking the freeze window instead of closing it).
    A caller reachable from the UI thread (``setup()``) is responsible for
    wrapping this call in its own worker thread instead -- see the sibling
    test below."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import tasks as tasks_mod

    def boom(*_a, **_k):
        raise AssertionError("prewarm_optional_modules must not spawn a thread")

    monkeypatch.setattr(tasks_mod.threading, "Thread", boom)

    tasks_mod.prewarm_optional_modules()  # must not raise


def test_setup_prewarm_call_does_not_block_the_calling_thread(monkeypatch):
    """``setup()`` -- the shared non-live-mount / manual-reload ('r') path,
    which runs synchronously on the render/key-handling thread either way --
    must wrap ``tasks.prewarm_optional_modules()`` in its own worker thread,
    so a slow/cold import there cannot reintroduce the exact freeze the fix
    exists to remove."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng
    from worktree_manager.production_picker.picker_tui import tasks as tasks_mod

    release = threading.Event()

    def slow_prewarm():
        release.wait(timeout=5)

    monkeypatch.setattr(tasks_mod, "prewarm_optional_modules", slow_prewarm)

    class Src:
        LOCAL = ("host", "Win")

        @staticmethod
        def machines():
            return [("host Win", "host", "Win", True)]

        @staticmethod
        def load():
            return []

    screen = eng.PickerScreen(Src(), live=False)
    try:
        t0 = time.perf_counter()
        screen.setup()
        elapsed = time.perf_counter() - t0
    finally:
        release.set()  # let the worker thread's slow_prewarm unblock and finish

    assert elapsed < 1.0


def test_skeleton_does_not_touch_src_local():
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    class Src:
        @property
        def LOCAL(self):
            raise AssertionError("src.LOCAL must not run during skeleton paint")

    screen = eng.PickerScreen(Src(), live=True)
    screen._setup_skeleton()
    assert screen.data == []
    assert screen.loader is None
    assert screen.machines
    screen.local_index()
    assert screen.machine_state(0) == "ready"
    assert screen.machine_state(1) == "loading"


def test_skeleton_paint_renders_with_uncached_local_identity():
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    class Src:
        LOCAL = ("host", "Win")

    screen = eng.PickerScreen(Src(), live=True)
    assert screen._source_local is None
    assert screen.is_all()

    row = screen.new_worktree_row(80, True, 0)
    assert "…" in row.plain

    l1, _l2 = screen.topbar(80)
    assert "…" in l1.plain


@pytest.mark.parametrize("live", [False, True])
def test_first_refresh_callback_is_scheduled_in_every_mode(monkeypatch, live):
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    class Src:
        LOCAL = ("host", "Win")

    screen = eng.PickerScreen(Src(), live=live)
    deferred = []
    monkeypatch.setattr(screen, "_setup_skeleton", lambda: None)
    monkeypatch.setattr(screen, "setup", lambda: None)
    monkeypatch.setattr(screen, "_finish_mount", lambda: None)
    monkeypatch.setattr(screen, "call_after_refresh", deferred.append)

    screen.on_mount()

    assert deferred == [screen._after_first_refresh]


def test_post_refresh_callback_starts_on_worker(monkeypatch):
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    monkeypatch.delenv("AGENT_WORKTREES_PICKER_FRAME_HEALTH", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_LAUNCH_TRACE", raising=False)
    calls = []

    class InlineThread:
        def __init__(self, target, **kwargs):
            calls.append(("thread", kwargs))
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(eng.threading, "Thread", InlineThread)
    screen = eng.PickerScreen(
        object(),
        live=False,
        after_first_refresh=lambda: calls.append(("callback", {})),
    )

    screen._after_first_refresh()

    assert calls[0][0] == "thread"
    assert calls[0][1]["name"] == "picker-after-first-refresh"
    assert calls[1][0] == "callback"


def test_data_ssh_bootstrap_rows_skip_full_config(monkeypatch):
    from worktree_manager.production_picker.picker_tui import data_ssh

    monkeypatch.setattr(
        data_ssh.cfg,
        "load_config",
        lambda: (_ for _ in ()).throw(
            AssertionError("bootstrap rows must not load full config")
        ),
    )
    monkeypatch.setattr(
        data_ssh.data_local,
        "load",
        lambda **kwargs: [kwargs],
    )

    assert data_ssh.bootstrap_rows() == [{"classify": False}]


def test_load_config_cache_scope_reuses_one_session_per_picker():
    """``_load_config_cache_scope()`` must create the ConfigCacheSession ONCE
    per Picker instance and hand out the SAME session on every subsequent
    call -- that's what lets the roster thread, the pivot-prewarm thread,
    and a later manual 'r' reload all share one warm cache."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    class Src:
        LOCAL = ("host", "Win")

    screen = eng.PickerScreen(Src(), live=True)
    with screen._load_config_cache_scope() as first:
        pass
    with screen._load_config_cache_scope() as second:
        pass
    assert first is second


def test_load_config_cache_scope_shares_across_threads():
    """A session handed out by ``_load_config_cache_scope()`` is a plain
    object reference -- entering its ``.scope()`` from a second, real OS
    thread must reuse the same cached entries as the first thread, unlike a
    bare ``cached_load_config_scope()`` (thread-local by design)."""
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    class Src:
        LOCAL = ("host", "Win")

    screen = eng.PickerScreen(Src(), live=True)
    calls = []

    def fake_uncached(*_a, **_k):
        calls.append(1)
        return "config-value"

    from agent_worktrees import config as real_cfg
    from worktree_manager.production_picker import config as pm_cfg
    orig = real_cfg._load_config_uncached
    real_cfg._load_config_uncached = fake_uncached
    try:
        results = []
        with screen._load_config_cache_scope():
            results.append(pm_cfg.load_config())

        def worker():
            with screen._load_config_cache_scope():
                results.append(pm_cfg.load_config())

        t = threading.Thread(target=worker)
        t.start()
        t.join()
    finally:
        real_cfg._load_config_uncached = orig

    assert len(calls) == 1  # the second (real) thread's call hit the cache
    assert results == ["config-value", "config-value"]


def test_setup_live_async_records_failure():
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    class Src:
        LOCAL = ("host", "Win")

        @staticmethod
        def source_tabs(_snapshot=None):
            return []

        @staticmethod
        def setup_metadata(_snapshot):
            return {}

        @staticmethod
        def make_loader(*_a, **_k):
            raise RuntimeError("roster exploded")

    screen = eng.PickerScreen(Src(), live=True)
    screen._setup_skeleton()
    screen._setup_live_async()
    assert "roster exploded" in screen.debug
    assert screen._busy_label == "Load failed"


def test_setup_live_async_keeps_bootstrap_rows_on_roster_failure(monkeypatch):
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    class InlineThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    class Src:
        LOCAL = ("host", "Win")

        @staticmethod
        def source_tabs(_snapshot=None):
            return []

        @staticmethod
        def setup_metadata(_snapshot):
            return {}

        @staticmethod
        def bootstrap_rows():
            return [{"id": "cached"}]

        @staticmethod
        def make_loader(*_a, **_k):
            raise RuntimeError("roster exploded")

    monkeypatch.setattr(eng.threading, "Thread", InlineThread)
    screen = eng.PickerScreen(Src(), live=True)
    monkeypatch.setattr(screen, "_scan_pivot_payload", lambda: None)
    screen._setup_skeleton()
    screen._setup_live_async()

    assert screen.data == [{"id": "cached"}]
    assert "roster exploded" in screen.debug
    assert screen._busy_label == "Load failed"


def test_failed_worker_handoff_never_mutates_ui_off_thread(monkeypatch):
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    class App:
        @staticmethod
        def call_from_thread(_callback):
            raise RuntimeError("app stopped")

    class Screen(eng.PickerScreen):
        @property
        def app(self):
            return App()

    class Src:
        LOCAL = ("host", "Win")

    screen = Screen(Src(), live=True)
    called = []

    worker = eng.threading.Thread(
        target=lambda: screen._apply_from_worker(lambda: called.append(True))
    )
    worker.start()
    worker.join()

    assert called == []


def test_partial_stream_merges_over_bootstrap_until_roster_is_authoritative():
    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import engine as eng

    class Loader:
        records_value = [{
            "id4": "cached-a",
            "selection_id": "machine-ssh:host:win\x1fcached-a",
            "source_id": "machine-ssh:host:win",
            "title": "live-a",
        }]
        authoritative = set()

        @classmethod
        def records(cls):
            return cls.records_value

        @classmethod
        def authoritative_source_ids(cls):
            return cls.authoritative

        @staticmethod
        def counts():
            return (1, 0, 0)

    class Src:
        LOCAL = ("host", "Win")

        @staticmethod
        def setup_metadata(_snapshot):
            raise AssertionError("source metadata must not run on the UI thread")

    prepared = {
        "tabs": [{
            "label": "host Win",
            "machine": "host",
            "env": "Win",
            "ready": True,
            "local": True,
            "source_kind": "machine-ssh",
            "source_id": "machine-ssh:host:win",
            "capabilities": {},
        }],
        "host_cols": [("host·Win", "host", "Win")],
        "target_envs": [("host", "Win")],
    }

    screen = eng.PickerScreen(Src(), live=True)
    screen._setup_skeleton()
    screen.data = [
        {
            "id4": "cached-a",
            "selection_id": "machine-ssh:host:win\x1fcached-a",
            "source_id": "machine-ssh:host:win",
            "title": "cached-a",
            "machine": "host",
            "env": "Win",
        },
        {
            "id4": "cached-b",
            "selection_id": "machine-ssh:host:win\x1fcached-b",
            "source_id": "machine-ssh:host:win",
            "title": "cached-b",
            "machine": "host",
            "env": "Win",
        },
    ]
    screen.update_state = "idle"
    screen._maybe_repoll = lambda: None
    screen._maybe_repoll_pivot = lambda: None
    screen.refresh = lambda: None

    screen._apply_live_source(prepared, Loader())
    screen._tick()

    assert [row["title"] for row in screen.data] == ["live-a", "cached-b"]

    Loader.authoritative = {"machine-ssh:host:win"}
    screen._tick()

    assert [row["title"] for row in screen.data] == ["live-a"]
    assert screen.machine_idx == 1
    assert screen.host_cols == [("host·Win", "host", "Win")]


def test_stream_source_is_not_authoritative_until_done(monkeypatch):
    from worktree_manager.production_picker.picker_tui import data_ssh

    first_row = threading.Event()
    finish = threading.Event()

    class Stdout:
        def __iter__(self):
            yield json.dumps({"type": "worktree", "wt": {"id": "wt-a"}})
            first_row.set()
            assert finish.wait(2)
            yield json.dumps({"type": "done"})

    class Proc:
        stdout = Stdout()
        returncode = 0

        @staticmethod
        def communicate(timeout=None):
            return "", ""

    source = data_ssh.Source(
        "host", "Win", ["agent-worktrees", "list"], local=True
    )
    loader = data_ssh.LiveLoader([source])
    monkeypatch.setattr(loader, "_spawn_stream", lambda _argv: Proc())
    monkeypatch.setattr(
        data_ssh.derive,
        "norm",
        lambda wt, *_a, **_k: {
            "id4": wt["id"],
            "selection_id": f"{source.source_id}\x1f{wt['id']}",
            "source_id": source.source_id,
        },
    )

    worker = threading.Thread(
        target=loader._load_remote_stream, args=(source, 0)
    )
    worker.start()
    assert first_row.wait(2)

    assert loader.records_for_source(source.source_id)
    assert loader.authoritative_source_ids() == set()

    finish.set()
    worker.join(2)

    assert not worker.is_alive()
    assert loader.authoritative_source_ids() == {source.source_id}


# ── start_loader: focus_keys only when the loader's own signature accepts it ─

def test_start_loader_passes_focus_keys_when_supported():
    from worktree_manager.production_picker.picker_tui import engine_helpers

    calls = []

    class NewLoader:
        def start(self, focus_keys=None):
            calls.append(("new", focus_keys))

    engine_helpers.start_loader(NewLoader(), focus_keys={("book2", "Win")})

    assert calls == [("new", {("book2", "Win")})]


def test_start_loader_falls_back_for_a_signature_that_lacks_it():
    """An older engine's LiveLoader.start() takes no argument at all --
    detected from the signature, not by calling and catching TypeError (which
    would also swallow a real bug inside the current start())."""
    from worktree_manager.production_picker.picker_tui import engine_helpers

    calls = []

    class OldLoader:
        def start(self):
            calls.append("old")

    engine_helpers.start_loader(OldLoader(), focus_keys={("book2", "Win")})

    assert calls == ["old"]


def test_start_loader_does_not_mask_a_real_error_from_the_new_signature():
    """A genuine bug inside a focus_keys-supporting start() must propagate,
    never be silently reinterpreted as an old-engine signature mismatch and
    retried without focus_keys."""
    from worktree_manager.production_picker.picker_tui import engine_helpers

    class BrokenLoader:
        def start(self, focus_keys=None):
            raise TypeError("boom -- an unrelated bug, not a signature issue")

    with pytest.raises(TypeError, match="boom"):
        engine_helpers.start_loader(BrokenLoader(), focus_keys=None)


# ── PickerScreen integration: nav-driven activation/cancellation (#3447 #3) ──

def test_rotate_machine_promotes_destination_and_cancels_previous_tab(monkeypatch):
    """The end-to-end wiring _rotate_machine relies on: navigating onto a
    ping-only tab promotes it (ensure_loaded), and navigating further away
    cancels the tab just left (cancel_source) -- without disturbing the
    local tab or the "All" tab's own semantics."""
    import types as _types

    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import data_ssh, engine as eng

    local = data_ssh.Source("book2", "Win", None, local=True, ready=True)
    dev6 = data_ssh.Source(
        "dev6", "Win",
        data_ssh._argv_for("pwsh", "host-dev6", "dotfiles", classify=True),
        ready=True, alias="host-dev6",
    )
    cloud1 = data_ssh.Source(
        "cloud1", "Win",
        data_ssh._argv_for("pwsh", "host-cloud1", "dotfiles", classify=True),
        ready=True, alias="host-cloud1",
    )
    loader = data_ssh.LiveLoader([local, dev6, cloud1])
    monkeypatch.setattr(loader, "_load_one", lambda s, gen=None: None)
    monkeypatch.setattr(
        data_ssh.threading, "Thread",
        lambda target, args=(), name=None, daemon=None: _types.SimpleNamespace(
            start=lambda: None),  # promoted but never actually resolves
    )
    # Local already resolved; dev6/cloud1 are ping-only (start()'s deferral).
    loader._state[local.cache_key] = "ready"
    loader._records[local.cache_key] = [{"id4": "aaaa"}]
    loader._pinged_only.update({dev6.cache_key, cloud1.cache_key})
    loader._state[dev6.cache_key] = "ready"
    loader._state[cloud1.cache_key] = "ready"

    class Src:
        LOCAL = ("book2", "Win")
        from worktree_manager.production_picker.picker_tui import derive as _derive
        bucket = staticmethod(_derive.bucket)
        for_machine = staticmethod(_derive.for_machine)

    scr = eng.PickerScreen(Src(), live=True)
    scr.loader = loader
    scr.data = []
    scr.source_tabs = [
        {"label": "All", "machine": None, "env": None, "ready": True,
         "source_kind": "all", "source_id": None, "local": False},
        {"label": "book2 Win", "machine": "book2", "env": "Win", "ready": True,
         "source_kind": "machine-ssh", "source_id": None, "local": True},
        {"label": "dev6 Win", "machine": "dev6", "env": "Win", "ready": True,
         "source_kind": "machine-ssh", "source_id": None, "local": False},
        {"label": "cloud1 Win", "machine": "cloud1", "env": "Win", "ready": True,
         "source_kind": "machine-ssh", "source_id": None, "local": False},
    ]
    scr.machines = [
        (t["label"], t["machine"], t["env"], t["ready"]) for t in scr.source_tabs
    ]
    scr.machine_idx = 1   # start on the local tab, like a real launch

    # Navigate onto dev6: promotes it (no longer ping-only, "loading").
    scr._rotate_machine(1)
    assert scr.machine_idx == 2
    assert dev6.cache_key not in loader._pinged_only
    assert loader.state("dev6", "Win") == "loading"
    # Local, just left, is untouched (already resolved with real records).
    assert loader.records_for_source(local.cache_key) == [{"id4": "aaaa"}]

    # Navigate onto cloud1: promotes it, AND cancels dev6 (just left, still
    # unresolved) back to a ping-only placeholder.
    scr._rotate_machine(1)
    assert scr.machine_idx == 3
    assert cloud1.cache_key not in loader._pinged_only
    assert loader.state("cloud1", "Win") == "loading"
    assert dev6.cache_key in loader._pinged_only
    assert loader.state("dev6", "Win") == "ready"          # re-armed, not stuck

    # Navigate to "All": ensure_all_loaded promotes every remaining ping-only
    # source (dev6, re-armed above) without touching already-loading cloud1.
    scr.machine_idx = 0
    scr._activate_current_machine_tab(scr._current_tab_key(), False)
    assert dev6.cache_key not in loader._pinged_only
    assert loader.state("dev6", "Win") == "loading"


def test_programmatic_all_view_selection_promotes_ping_only_sources(monkeypatch):
    """A headless capture helper (``capture.py``'s ``capture_async`` /
    ``capture_frames_async`` / ``capture_modal_async``) selects the "All"
    view by assigning ``scr.machine_idx = 0`` directly, then calls
    ``scr._activate_current_machine_tab()`` (picker-lazy-per-machine-loading
    #round-6 finding) -- exactly like this test does. Without that call, a
    live aggregate capture would leave every deferred remote ping-only and
    silently omit their rows from the documented All view."""
    import types as _types

    pytest.importorskip("textual")
    from worktree_manager.production_picker.picker_tui import data_ssh, engine as eng

    local = data_ssh.Source("book2", "Win", None, local=True, ready=True)
    dev6 = data_ssh.Source(
        "dev6", "Win",
        data_ssh._argv_for("pwsh", "host-dev6", "dotfiles", classify=True),
        ready=True, alias="host-dev6",
    )
    loader = data_ssh.LiveLoader([local, dev6])
    monkeypatch.setattr(loader, "_load_one", lambda s, gen=None: None)
    monkeypatch.setattr(
        data_ssh.threading, "Thread",
        lambda target, args=(), name=None, daemon=None: _types.SimpleNamespace(
            start=lambda: None),  # promoted but never actually resolves
    )
    loader._state[local.cache_key] = "ready"
    loader._records[local.cache_key] = [{"id4": "aaaa"}]
    loader._pinged_only.add(dev6.cache_key)
    loader._state[dev6.cache_key] = "ready"

    class Src:
        LOCAL = ("book2", "Win")
        from worktree_manager.production_picker.picker_tui import derive as _derive
        bucket = staticmethod(_derive.bucket)
        for_machine = staticmethod(_derive.for_machine)

    scr = eng.PickerScreen(Src(), live=True)
    scr.loader = loader
    scr.data = []
    scr.source_tabs = [
        {"label": "All", "machine": None, "env": None, "ready": True,
         "source_kind": "all", "source_id": None, "local": False},
        {"label": "book2 Win", "machine": "book2", "env": "Win", "ready": True,
         "source_kind": "machine-ssh", "source_id": None, "local": True},
        {"label": "dev6 Win", "machine": "dev6", "env": "Win", "ready": True,
         "source_kind": "machine-ssh", "source_id": None, "local": False},
    ]
    scr.machines = [
        (t["label"], t["machine"], t["env"], t["ready"]) for t in scr.source_tabs
    ]
    scr.machine_idx = 1   # a real launch starts on the local tab

    # Mirror capture.py's fix: set machine_idx=0 directly (view="all"), then
    # route it through the activation hook.
    scr.machine_idx = 0
    scr._activate_current_machine_tab()

    assert dev6.cache_key not in loader._pinged_only
    assert loader.state("dev6", "Win") == "loading"

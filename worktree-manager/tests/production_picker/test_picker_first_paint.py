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

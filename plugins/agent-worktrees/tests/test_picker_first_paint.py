"""First-paint must not run config/roster/pivot I/O (#1504)."""

from __future__ import annotations

import importlib
import sys

import pytest


def test_data_local_import_does_not_load_config(monkeypatch):
    import agent_worktrees.config as cfg

    def boom(*_a, **_k):
        raise AssertionError("load_config must not run at import")

    monkeypatch.setattr(cfg, "load_config", boom)
    sys.modules.pop("agent_worktrees.picker_tui.data_local", None)
    mod = importlib.import_module("agent_worktrees.picker_tui.data_local")
    assert mod.LOCAL
    assert mod.LOCAL_LABEL


def test_data_ssh_import_does_not_load_config(monkeypatch):
    import agent_worktrees.config as cfg

    def boom(*_a, **_k):
        raise AssertionError("load_config must not run at import")

    monkeypatch.setattr(cfg, "load_config", boom)
    sys.modules.pop("agent_worktrees.picker_tui.data_ssh", None)
    mod = importlib.import_module("agent_worktrees.picker_tui.data_ssh")
    assert mod.LOCAL_LABEL


def test_picker_init_skips_pivot_scan(monkeypatch):
    pytest.importorskip("textual")
    from agent_worktrees.picker_tui import engine as eng

    def boom(*_a, **_k):
        raise AssertionError("scan_pivot_registry must not run in __init__")

    monkeypatch.setattr(
        "agent_worktrees.picker_tui.pivots.scan_pivot_registry", boom
    )

    class Src:
        LOCAL = ("host", "Win")

    screen = eng.PickerScreen(Src(), live=True)
    kinds = [d["kind"] for d in screen.pivots]
    assert "worktrees" in kinds


def test_skeleton_does_not_touch_src_local():
    pytest.importorskip("textual")
    from agent_worktrees.picker_tui import engine as eng

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


def test_live_setup_starts_after_first_refresh(monkeypatch):
    pytest.importorskip("textual")
    from agent_worktrees.picker_tui import engine as eng

    class Src:
        LOCAL = ("host", "Win")

    screen = eng.PickerScreen(Src(), live=True)
    deferred = []
    monkeypatch.setattr(screen, "_setup_skeleton", lambda: None)
    monkeypatch.setattr(screen, "_finish_mount", lambda: None)
    monkeypatch.setattr(screen, "call_after_refresh", deferred.append)

    screen.on_mount()

    assert deferred == [screen._start_live_setup]


def test_data_ssh_bootstrap_rows_skip_full_config(monkeypatch):
    from agent_worktrees.picker_tui import data_ssh

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
    from agent_worktrees.picker_tui import engine as eng

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
    from agent_worktrees.picker_tui import engine as eng

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
    from agent_worktrees.picker_tui import engine as eng

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


def test_full_loader_does_not_blank_bootstrap_rows_while_loading():
    pytest.importorskip("textual")
    from agent_worktrees.picker_tui import engine as eng

    class Loader:
        @staticmethod
        def records():
            return []

        @staticmethod
        def counts():
            return (0, 1, 0)

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
        "host_cols": [("host\u00b7Win", "host", "Win")],
        "target_envs": [("host", "Win")],
    }

    screen = eng.PickerScreen(Src(), live=True)
    screen._setup_skeleton()
    screen.data = [{"id": "cached"}]
    screen.update_state = "idle"
    screen._maybe_repoll = lambda: None
    screen._maybe_repoll_pivot = lambda: None
    screen.refresh = lambda: None

    screen._apply_live_source(prepared, Loader())
    screen._tick()

    assert screen.data == [{"id": "cached"}]
    assert screen.machine_idx == 1
    assert screen.host_cols == [("host\u00b7Win", "host", "Win")]


def test_local_live_source_uses_streaming_subprocess(monkeypatch):
    from agent_worktrees.picker_tui import data_ssh

    argv = data_ssh._local_argv("example-project")
    source = data_ssh.Source("host", "Win", argv, local=True)
    loader = data_ssh.LiveLoader([source])
    calls = []
    monkeypatch.setattr(
        loader,
        "_load_remote_stream",
        lambda actual, generation: calls.append((actual, generation)) or True,
    )
    monkeypatch.setattr(
        loader,
        "_load_local_two_phase",
        lambda _source: (_ for _ in ()).throw(
            AssertionError("local authoritative load must not run in-process")
        ),
    )

    loader._load_one_serial(source, 0)

    assert calls == [(source, 0)]
    assert data_ssh._stream_argv(source) == [*argv, "--stream"]

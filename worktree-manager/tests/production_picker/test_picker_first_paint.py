"""First-paint regressions carried forward from the bundled picker."""

from __future__ import annotations

import importlib
import json
import sys
import threading

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
            "selection_id": "local\x1fcached-a",
            "source_id": "local",
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
            "selection_id": "local\x1fcached-a",
            "source_id": "local",
            "title": "cached-a",
        },
        {
            "id4": "cached-b",
            "selection_id": "local\x1fcached-b",
            "source_id": "local",
            "title": "cached-b",
        },
    ]
    screen.update_state = "idle"
    screen._maybe_repoll = lambda: None
    screen._maybe_repoll_pivot = lambda: None
    screen.refresh = lambda: None

    screen._apply_live_source(prepared, Loader())
    screen._tick()

    assert [row["title"] for row in screen.data] == ["live-a", "cached-b"]

    Loader.authoritative = {"local"}
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

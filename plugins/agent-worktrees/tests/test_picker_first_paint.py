"""First-paint must not run config/roster/pivot I/O (#1504)."""

from __future__ import annotations

import importlib
import json
import sys
import threading
from argparse import Namespace
from types import SimpleNamespace

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


def test_interactive_resolve_skips_full_config_for_non_base_repo(
    monkeypatch, tmp_path
):
    from agent_worktrees import __main__ as main
    from agent_worktrees import config as cfg
    from agent_worktrees import picker_tui

    monkeypatch.setattr(cfg, "peek_base_repo", lambda: False)
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: (_ for _ in ()).throw(
            AssertionError("ordinary Picker must not load full config before paint")
        ),
    )
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(picker_tui, "new_picker_enabled", lambda: True)
    monkeypatch.setattr(main, "_new_picker_blocked_by_ssh", lambda: False)
    monkeypatch.setattr(main, "_start_picker_monitor_root", lambda: None)
    monkeypatch.setattr(main, "_run_new_picker", lambda _config, _args: 0)
    monkeypatch.setattr(
        main.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: True),
    )
    monkeypatch.setattr(
        main.threading,
        "Thread",
        lambda **_kwargs: type("Thread", (), {"start": lambda self: None})(),
    )
    args = Namespace(
        json=False,
        base=False,
        new_worktree=False,
        auto=False,
        machine=None,
        worktree_id=None,
        no_mux=False,
        no_resume=False,
    )

    assert main.cmd_resolve(args) == 0


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


@pytest.mark.parametrize("live", [False, True])
def test_first_refresh_callback_is_scheduled_in_every_mode(monkeypatch, live):
    pytest.importorskip("textual")
    from agent_worktrees.picker_tui import engine as eng

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
    from agent_worktrees.picker_tui import engine as eng

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


def test_picker_housekeeping_continues_after_step_failure(
    monkeypatch, tmp_path
):
    from agent_worktrees import __main__ as main

    calls = []
    trace = tmp_path / "picker-launches.jsonl"
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_TRACE", str(trace))
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_ID", "housekeeping-123")

    def fail():
        calls.append("fail")
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "reap_orphan_mux_sessions", fail)
    monkeypatch.setattr(
        main, "_sweep_managed_on_exit", lambda: calls.append("managed")
    )
    monkeypatch.setattr(
        main, "_sweep_launcher_shells_on_exit", lambda: calls.append("shells")
    )
    monkeypatch.setattr(
        main,
        "_sweep_finished_sessions_on_cadence",
        lambda: calls.append("finished"),
    )

    main._run_picker_housekeeping()

    assert calls == ["fail", "managed", "shells", "finished"]
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    error = next(event for event in events if event["event"] == "housekeeping_error")
    assert error["step"] == "fail"
    assert error["error_type"] == "RuntimeError"


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


def test_partial_stream_merges_over_bootstrap_until_roster_is_authoritative():
    pytest.importorskip("textual")
    from agent_worktrees.picker_tui import engine as eng

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
        "host_cols": [("host\u00b7Win", "host", "Win")],
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
    assert screen.host_cols == [("host\u00b7Win", "host", "Win")]


def test_stream_source_is_not_authoritative_until_done(monkeypatch):
    from agent_worktrees.picker_tui import data_ssh

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

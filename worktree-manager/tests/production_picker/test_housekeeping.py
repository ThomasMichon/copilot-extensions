from __future__ import annotations

from pathlib import Path

from agent_worktrees import __main__ as cli
from agent_worktrees import tracking as engine_tracking
from agent_worktrees import resolve_picker_cli as resolve_picker_cli
from worktree_manager.production_picker import housekeeping


def _record(wt_id: str, *, status: str = "active", path: str, kind: str = "session"):
    return engine_tracking.WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=path,
        repo="owner/repo",
        machine="m",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status=status,
        completed_at=None,
        sessions=[],
        prs=[],
        kind=kind,
    )


class _SessionsStub:
    def __init__(self, sessions_map: dict[str, int] | None, activity: dict[str, float]):
        self.sessions_map = sessions_map
        self.activity = activity
        self.killed: list[str] = []

    def _list_mux_sessions(self):
        return self.sessions_map

    def _mux_session_activity(self):
        return self.activity

    def mux_session_index(self, by_id):
        return {self.mux_session_name(wt_id): wt_id for wt_id in by_id}

    def worktree_id_from_mux_session(self, name, *, index):
        return index.get(name, name[3:])

    def kill_tmux_session(self, wt_id):
        self.killed.append(wt_id)
        return True

    @staticmethod
    def mux_session_name(wt_id):
        return f"wt-{wt_id.replace('.', '_')}"


class _TrackingStub:
    MANAGED_KINDS = engine_tracking.MANAGED_KINDS

    def __init__(self, records):
        self.records = records
        self.stamped: list[tuple[str, bool, bool]] = []

    def list_records(self, _path):
        return list(self.records)

    def stamp_mux_live(self, wt_id, live, *, sync=False):
        self.stamped.append((wt_id, live, sync))

    @staticmethod
    def derive_execution_leg(record):
        return engine_tracking.derive_execution_leg(record)


class _ActivityStub:
    def __init__(self):
        self.events: list[tuple[str, str, str]] = []

    def log_event(self, name, *, worktree_id, reason):
        self.events.append((name, worktree_id, reason))


def _patch_engine_reaper(monkeypatch, sessions_map, records, *, activity=None):
    killed: list[str] = []
    stamped: list[tuple[str, bool, bool]] = []
    events: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cli.sessions, "_list_mux_sessions", lambda: sessions_map)
    monkeypatch.setattr(cli.sessions, "_mux_session_activity", lambda: activity or {})
    monkeypatch.setattr(cli.sessions, "kill_tmux_session", lambda wt_id: killed.append(wt_id) or True)
    monkeypatch.setattr(cli.tracking, "list_records", lambda _path: list(records))
    monkeypatch.setattr(cli.tracking, "stamp_mux_live", lambda wt_id, live, sync=False: stamped.append((wt_id, live, sync)))
    monkeypatch.setattr(cli.activity, "log_event", lambda name, **kw: events.append((name, kw["worktree_id"], kw["reason"])))
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda: Path("C:\tracking"))
    return killed, stamped, events


def _patch_manager_reaper(monkeypatch, sessions_map, records, *, activity=None):
    sessions_stub = _SessionsStub(sessions_map, activity or {})
    tracking_stub = _TrackingStub(records)
    activity_stub = _ActivityStub()
    monkeypatch.setattr(housekeeping, "_sessions", lambda: sessions_stub)
    monkeypatch.setattr(housekeeping, "_tracking", lambda: tracking_stub)
    monkeypatch.setattr(housekeeping, "_activity", lambda: activity_stub)
    monkeypatch.setattr(housekeeping, "_tracking_path", lambda: Path("C:\tracking"))
    return sessions_stub, tracking_stub, activity_stub


def test_reap_orphan_mux_sessions_matches_engine_behavior(tmp_path, monkeypatch):
    live = _record("live", status="active", path=str(tmp_path))
    finalized = _record("fin", status="finalized", path=str(tmp_path))
    system = _record("svc", status="finalized", path=str(tmp_path), kind="system")
    records = [live, finalized, system]
    sessions_map = {
        "wt-live": 0,
        "wt-fin": 0,
        "wt-svc": 0,
        "wt-held": 1,
        "wt-ghost": 0,
        "misc": 0,
    }
    activity = {"wt-fin": 0, "wt-held": 0, "wt-ghost": 0, "wt-svc": 0}

    engine_killed, engine_stamped, engine_events = _patch_engine_reaper(
        monkeypatch, sessions_map, records, activity=activity
    )
    manager_sessions, manager_tracking, manager_activity = _patch_manager_reaper(
        monkeypatch, sessions_map, records, activity=activity
    )

    engine_result = cli.reap_orphan_mux_sessions(now=1_000_000)
    manager_result = housekeeping.reap_orphan_mux_sessions(now=1_000_000)

    assert manager_result == engine_result
    assert manager_sessions.killed == engine_killed
    assert manager_tracking.stamped == engine_stamped
    assert manager_activity.events == engine_events


def test_reap_orphan_mux_sessions_matches_busy_and_unknown_guards(tmp_path, monkeypatch):
    busy = _record("busy", status="finalized", path=str(tmp_path))
    idle = _record("idle", status="finalized", path=str(tmp_path))
    records = [busy, idle]
    now = 1_000_000.0
    sessions_map = {"wt-busy": 0, "wt-idle": 0, "wt-unknown": 0}
    activity = {"wt-busy": now - 30, "wt-idle": now - 8 * 3600}

    engine_killed, _, _ = _patch_engine_reaper(
        monkeypatch, sessions_map, records, activity=activity
    )
    manager_sessions, _, _ = _patch_manager_reaper(
        monkeypatch, sessions_map, records, activity=activity
    )

    engine_result = cli.reap_orphan_mux_sessions(now=now)
    manager_result = housekeeping.reap_orphan_mux_sessions(now=now)

    assert manager_result == engine_result
    assert manager_sessions.killed == engine_killed == ["idle"]
    assert {"id": "busy", "reason": "busy"} in manager_result["skipped"]
    assert {"id": "unknown", "reason": "activity-unknown"} in manager_result["skipped"]


def test_manager_owned_helpers_cover_mux_registry_and_ahp_records(tmp_path, monkeypatch):
    mux_mapping_registry = housekeeping.mux_mapping_registry
    mux_mapping_registry.register_mapping(
        {
            "project": "demo",
            "worktree_id": "mapped",
            "worktree_path": "C:/wt/mapped",
            "mux_session": "wt-mapped",
            "mux_bin": "tmux",
            "mapping_revision": 1,
            "live": True,
        },
        root=tmp_path,
    )
    mux_mapping_registry.register_mapping(
        {
            "project": "other",
            "worktree_id": "other",
            "worktree_path": "C:/wt/other",
            "mux_session": "wt-other",
            "mux_bin": "tmux",
            "mapping_revision": 1,
            "live": True,
        },
        root=tmp_path,
    )
    ahp_record = _record("ahp-only", status="active", path="C:/wt/ahp")
    ahp_record.execution_leg = engine_tracking.ExecutionLegBinding(
        provider="ahp",
        state="active",
        binding_revision=1,
        blob={},
    )
    local_record = _record("local", status="active", path="C:/wt/local")

    monkeypatch.setattr(housekeeping, "_tracking", lambda: _TrackingStub([ahp_record, local_record]))

    assert housekeeping.manager_owned_mux_session_names(project="demo", root=tmp_path) == {"wt-mapped"}
    assert housekeeping.manager_owned_worktree_ids([ahp_record, local_record], project="demo", root=tmp_path) == {"mapped", "ahp-only"}
    assert housekeeping.is_manager_owned_launcher_shell(
        r"pwsh -File C:\Users\me\.worktree-manager\bin\launch-session.ps1"
    )
    assert not housekeeping.is_manager_owned_launcher_shell("python -m agent_worktrees")


def test_wrapper_sweeps_match_engine_messages(monkeypatch):
    engine_messages: list[str] = []
    manager_messages: list[str] = []
    monkeypatch.setattr(cli.output, "ok", lambda message: engine_messages.append(message))
    monkeypatch.setattr(housekeeping, "_output_ok", lambda message: manager_messages.append(message))

    monkeypatch.setattr(cli, "sweep_managed_worktrees", lambda: {"removed": [{"id": "managed-a"}], "skipped": []})
    monkeypatch.setattr(housekeeping, "sweep_managed_worktrees", lambda: {"removed": [{"id": "managed-a"}], "skipped": []})
    cli._sweep_managed_on_exit()
    housekeeping.sweep_managed_on_exit()

    monkeypatch.setattr(cli, "reap_orphan_launcher_shells", lambda **_kwargs: {"reaped": [101, 202], "available": True, "candidates": [], "skipped": [], "errors": []})
    monkeypatch.setattr(housekeeping, "reap_orphan_launcher_shells", lambda **_kwargs: {"reaped": [101, 202], "available": True, "candidates": [], "skipped": [], "errors": []})
    cli._sweep_launcher_shells_on_exit()
    housekeeping.sweep_launcher_shells_on_exit()

    monkeypatch.setattr(cli, "sweep_finished_session_worktrees", lambda: {"removed": [{"id": "done-a"}], "skipped": []})
    monkeypatch.setattr(housekeeping, "sweep_finished_session_worktrees", lambda: {"removed": [{"id": "done-a"}], "skipped": []})
    monkeypatch.delenv(housekeeping._NO_AUTO_CLEAN_ENV, raising=False)
    monkeypatch.delenv(cli._NO_AUTO_CLEAN_ENV, raising=False)
    cli._sweep_finished_sessions_on_cadence()
    housekeeping.sweep_finished_sessions_on_cadence()

    assert manager_messages == engine_messages


def test_wrapper_sweeps_swallow_failures(monkeypatch):
    monkeypatch.setattr(housekeeping, "sweep_managed_worktrees", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(housekeeping, "reap_orphan_launcher_shells", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(housekeeping, "sweep_finished_session_worktrees", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert housekeeping.sweep_managed_on_exit() is None
    assert housekeeping.sweep_launcher_shells_on_exit() is None
    assert housekeeping.sweep_finished_sessions_on_cadence() is None


def test_sweep_finished_respects_kill_switch(monkeypatch):
    called = []
    monkeypatch.setenv(housekeeping._NO_AUTO_CLEAN_ENV, "1")
    monkeypatch.setattr(housekeeping, "sweep_finished_session_worktrees", lambda: called.append(True) or {"removed": [], "skipped": []})
    housekeeping.sweep_finished_sessions_on_cadence()
    assert called == []


class _StubHeartbeat:
    def __init__(self, project, *, ensure_monitor=None, interval=10.0):
        self.project = project
        self.ensure_monitor = ensure_monitor
        self.started = True
        self.closed = False

    def start(self):
        return self.started

    def close(self):
        self.closed = True


def test_start_picker_monitor_root_matches_engine_glue(monkeypatch):
    registrations = []
    engine_heartbeat = _StubHeartbeat("demo")
    manager_heartbeat = _StubHeartbeat("demo")

    monkeypatch.setattr(resolve_picker_cli, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(resolve_picker_cli, "_ensure_status_monitor", lambda: True)
    monkeypatch.setattr(housekeeping, "_status_monitor_runtime", lambda: type("SMR", (), {
        "_status_monitor_enabled": staticmethod(lambda: True),
        "_ensure_status_monitor": staticmethod(lambda: True),
    }))
    monkeypatch.setattr(resolve_picker_cli.atexit, "register", lambda fn: registrations.append(("engine", fn)))
    monkeypatch.setattr(housekeeping.atexit, "register", lambda fn: registrations.append(("manager", fn)))
    import agent_worktrees.monitor_roots as engine_monitor_roots

    monkeypatch.setattr(resolve_picker_cli.cfg, "project_name", lambda: "demo")
    monkeypatch.setattr(engine_monitor_roots, "PickerHeartbeat", lambda project, ensure_monitor=None: engine_heartbeat)
    monkeypatch.setattr(housekeeping.monitor_roots, "PickerHeartbeat", lambda project, ensure_monitor=None: manager_heartbeat)
    monkeypatch.setattr(housekeeping.context, "project", lambda: "demo")

    assert resolve_picker_cli._start_picker_monitor_root() is engine_heartbeat
    assert housekeeping.start_picker_monitor_root() is manager_heartbeat
    assert len(registrations) == 2


def test_start_picker_monitor_root_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(housekeeping, "_status_monitor_runtime", lambda: type("SMR", (), {
        "_status_monitor_enabled": staticmethod(lambda: False),
        "_ensure_status_monitor": staticmethod(lambda: True),
    }))
    assert housekeeping.start_picker_monitor_root(project="demo") is None

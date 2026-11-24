from __future__ import annotations

from worktree_manager.production_picker import housekeeping


def test_manager_housekeeping_route_binds_project_without_anchor_heal(monkeypatch):
    from worktree_manager import __main__ as cli
    from worktree_manager.production_picker import runner

    calls = []
    monkeypatch.setattr(runner, "_prepare", lambda project, **kwargs: calls.append((project, kwargs)))
    monkeypatch.setattr(housekeeping, "run_sweeps", lambda **kwargs: calls.append(kwargs) or 1)

    assert cli._cmd_picker(["--housekeeping", "demo"]) == 1
    assert calls == [("demo", {"heal": False}), {"report": True}]


def test_tracking_rows_reads_execution_leg_provider(tmp_path, monkeypatch):
    tracking_dir = tmp_path / "worktrees"
    tracking_dir.mkdir()
    (tracking_dir / "wt-a.yaml").write_text(
        "worktree_id: wt-a\nexecution_leg:\n  provider: ahp\n",
        encoding="utf-8",
    )
    (tracking_dir / "wt-b.yaml").write_text(
        "worktree_id: wt-b\nstatus: active\n",
        encoding="utf-8",
    )
    (tracking_dir / "wt-c.yaml").write_text(
        "worktree_id: wt-c\nsession_backend:\n  kind: ahp\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(housekeeping, "_tracking_path", lambda: tracking_dir)

    assert housekeeping._tracking_rows() == [
        {"worktree_id": "wt-a", "execution_leg_provider": "ahp"},
        {"worktree_id": "wt-b", "execution_leg_provider": ""},
        {"worktree_id": "wt-c", "execution_leg_provider": "ahp"},
    ]


def test_manager_owned_helpers_cover_mux_registry_and_ahp_records(tmp_path):
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
    records = [
        {"worktree_id": "ahp-only", "execution_leg_provider": "ahp"},
        {"worktree_id": "local", "execution_leg_provider": ""},
    ]

    assert housekeeping.manager_owned_mux_session_names(project="demo", root=tmp_path) == {
        "wt-mapped"
    }
    assert housekeeping.manager_owned_worktree_ids(
        records, project="demo", root=tmp_path
    ) == {"mapped", "ahp-only"}
    assert housekeeping.is_manager_owned_launcher_shell(
        r"pwsh -File C:\Users\me\.worktree-manager\bin\launch-session.ps1"
    )
    assert not housekeeping.is_manager_owned_launcher_shell("python -m worktree_manager")
    assert not housekeeping.is_manager_owned_launcher_shell("python -m agent_worktrees")


def test_reap_orphan_mux_sessions_uses_owned_worktree_filters(monkeypatch):
    calls = []
    payload = {"available": True, "reaped": ["mapped"], "skipped": [], "errors": []}
    monkeypatch.setattr(housekeeping.context, "project", lambda: "demo")
    monkeypatch.setattr(
        housekeeping,
        "manager_owned_mux_session_names",
        lambda **_kwargs: {"wt-mapped"},
    )
    monkeypatch.setattr(
        housekeeping,
        "manager_owned_worktree_ids",
        lambda **_kwargs: {"mapped", "ahp-only"},
    )
    monkeypatch.setattr(
        housekeeping.engine_group_d,
        "reap_orphan_mux_sessions",
        lambda project, *, worktree_ids=None, dry_run=False, idle_grace_secs=None, timeout=120: calls.append(
            (project, list(worktree_ids or []), dry_run, idle_grace_secs, timeout)
        )
        or payload,
    )

    assert housekeeping.reap_orphan_mux_sessions(
        only_owned=True,
        dry_run=True,
        idle_grace_secs=900,
    ) == payload
    assert calls == [("demo", ["ahp-only", "mapped"], True, 900, 120)]


def test_reap_orphan_mux_sessions_short_circuits_without_owned_targets(monkeypatch):
    called = []
    monkeypatch.setattr(housekeeping.context, "project", lambda: "demo")
    monkeypatch.setattr(
        housekeeping,
        "manager_owned_mux_session_names",
        lambda **_kwargs: set(),
    )
    monkeypatch.setattr(
        housekeeping,
        "manager_owned_worktree_ids",
        lambda **_kwargs: set(),
    )
    monkeypatch.setattr(
        housekeeping.engine_group_d,
        "reap_orphan_mux_sessions",
        lambda *args, **kwargs: called.append(True),
    )

    assert housekeeping.reap_orphan_mux_sessions(only_owned=True) == {
        "available": True,
        "reaped": [],
        "skipped": [],
        "errors": [],
    }
    assert called == []


def test_headless_sweeps_report_each_step_and_keep_mux_scope(monkeypatch, capsys):
    monkeypatch.setattr(housekeeping.context, "project", lambda: "demo")
    mux_calls = []
    monkeypatch.setattr(
        housekeeping, "reap_orphan_mux_sessions",
        lambda **kwargs: mux_calls.append(kwargs) or {"reaped": []},
    )
    monkeypatch.setattr(
        housekeeping.engine_group_d,
        "sweep_managed_worktrees",
        lambda project: {"removed": [{"id": "managed-a"}], "skipped": []},
    )
    monkeypatch.setattr(
        housekeeping.engine_group_d,
        "reap_orphan_launcher_shells",
        lambda project: {
            "reaped": [101, 202],
            "available": True,
            "candidates": [],
            "skipped": [],
            "errors": [],
        },
    )
    monkeypatch.setattr(
        housekeeping.engine_group_d,
        "sweep_finished_session_worktrees",
        lambda project: {"removed": [{"id": "done-a"}], "skipped": []},
    )
    monkeypatch.delenv(housekeeping._NO_AUTO_CLEAN_ENV, raising=False)

    assert housekeeping.run_sweeps(report=True) == 0
    assert mux_calls == [{"only_owned": True}]

    captured = capsys.readouterr()
    assert "Orphaned mux sessions: 0 reaped" in captured.out
    assert "Leaked managed worktrees: 1 removed" in captured.out
    assert "Orphaned launcher shells: 2 reaped" in captured.out
    assert "Finished session worktrees: 1 removed" in captured.out
    assert not captured.err


def test_headless_sweeps_continue_after_partial_failure(monkeypatch, capsys):
    monkeypatch.setattr(housekeeping.context, "project", lambda: "demo")
    monkeypatch.setattr(housekeeping, "reap_orphan_mux_sessions", lambda **kwargs: {"reaped": []})
    monkeypatch.delenv(housekeeping._NO_AUTO_CLEAN_ENV, raising=False)
    monkeypatch.setattr(
        housekeeping.engine_group_d,
        "sweep_managed_worktrees",
        lambda project: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        housekeeping.engine_group_d,
        "reap_orphan_launcher_shells",
        lambda project: {"available": False, "reaped": []},
    )
    monkeypatch.setattr(
        housekeeping.engine_group_d,
        "sweep_finished_session_worktrees",
        lambda project: {"removed": [], "skipped": []},
    )

    assert housekeeping.run_sweeps(report=True) == 1
    captured = capsys.readouterr()
    assert "Leaked managed worktrees: boom" in captured.err
    assert "Orphaned launcher shells: inspection unavailable" in captured.err
    assert "Finished session worktrees: 0 removed" in captured.out


def test_sweep_finished_respects_kill_switch(monkeypatch):
    called = []
    monkeypatch.setenv(housekeeping._NO_AUTO_CLEAN_ENV, "1")
    monkeypatch.setattr(housekeeping, "reap_orphan_mux_sessions", lambda **kwargs: {"reaped": []})
    monkeypatch.setattr(housekeeping, "sweep_managed_worktrees", lambda: {"removed": []})
    monkeypatch.setattr(housekeeping, "reap_orphan_launcher_shells", lambda: {"reaped": []})
    monkeypatch.setattr(
        housekeeping.engine_group_d,
        "sweep_finished_session_worktrees",
        lambda project: called.append(project) or {"removed": [], "skipped": []},
    )
    housekeeping.run_sweeps()
    assert called == []


class _StubHeartbeat:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_start_picker_monitor_root_registers_close(monkeypatch):
    registrations = []
    heartbeat = _StubHeartbeat()
    monkeypatch.setattr(
        housekeeping.monitor_roots,
        "start_picker_monitor_root",
        lambda project=None: heartbeat,
    )
    monkeypatch.setattr(
        housekeeping.atexit,
        "register",
        lambda fn: registrations.append(fn),
    )

    assert housekeeping.start_picker_monitor_root(project="demo") is heartbeat
    assert registrations == [heartbeat.close]


def test_start_picker_monitor_root_handles_missing_root(monkeypatch):
    monkeypatch.setattr(
        housekeeping.monitor_roots,
        "start_picker_monitor_root",
        lambda project=None: None,
    )
    assert housekeeping.start_picker_monitor_root(project="demo") is None

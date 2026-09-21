"""Regression guard for the #2338 follow-up: a resume-by-id lookup that runs
BEFORE any plan exists (the interactive Picker's local "Open" decision, and
the non-interactive/JSON ``--worktree-id`` resume paths in ``cmd_resolve``)
must not report a false "Worktree not found" merely because the worktree
belongs to a different project than the ambient one (CWD/``--project``
resolved once by ``main()``).

The daemon/Picker are deliberately project-agnostic (they sweep and display
worktrees across every registered project), so a resume-by-id call may
legitimately target a worktree owned by a project the launcher never started
in. ``_relocate_active_project_for_worktree`` looks the id up by exact match
across every registered project's tracking dir and switches the process-wide
active project to match when it is found under exactly one other project --
mirroring the id-is-globally-unique lookup ``_find_tracking_file_exact`` /
``_project_for_tracking_file`` already use for ``conclude-disposable``.
"""

from __future__ import annotations


def _write_record(tracking_dir, wt_id):
    tracking_dir.mkdir(parents=True, exist_ok=True)
    (tracking_dir / f"{wt_id}.yaml").write_text("worktree_id: " + wt_id, encoding="utf-8")


def test_relocate_switches_active_project_when_found_elsewhere(tmp_path, monkeypatch):
    from agent_worktrees import __main__ as m
    from agent_worktrees import config as cfg
    from agent_worktrees import session_tracking_cli

    ambient_dir = tmp_path / "ambient" / "worktrees"
    other_dir = tmp_path / "other" / "worktrees"
    _write_record(other_dir, "wt-1")
    record_path = other_dir / "wt-1.yaml"

    monkeypatch.setattr(cfg, "tracking_dir", lambda: ambient_dir)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: (tmp_path / name))
    monkeypatch.setattr(cfg, "active_project", lambda: "ambient-project")

    switched = []
    monkeypatch.setattr(
        cfg, "set_active_project", lambda name: switched.append(name)
    )
    monkeypatch.setattr(
        session_tracking_cli, "_find_tracking_file_exact", lambda wt_id: record_path
    )
    monkeypatch.setattr(
        session_tracking_cli, "_project_for_tracking_file", lambda path: "other-project"
    )

    assert m._relocate_active_project_for_worktree("wt-1") is True
    assert switched == ["other-project"]


def test_relocate_is_noop_when_found_in_ambient_project(tmp_path, monkeypatch):
    from agent_worktrees import __main__ as m
    from agent_worktrees import config as cfg

    ambient_dir = tmp_path / "ambient" / "worktrees"
    _write_record(ambient_dir, "wt-1")
    monkeypatch.setattr(cfg, "tracking_dir", lambda: ambient_dir)

    switched = []
    monkeypatch.setattr(cfg, "set_active_project", lambda name: switched.append(name))

    assert m._relocate_active_project_for_worktree("wt-1") is False
    assert switched == []


def test_relocate_is_noop_when_not_found_anywhere(tmp_path, monkeypatch):
    from agent_worktrees import __main__ as m
    from agent_worktrees import config as cfg

    ambient_dir = tmp_path / "ambient" / "worktrees"
    monkeypatch.setattr(cfg, "tracking_dir", lambda: ambient_dir)
    monkeypatch.setattr(m, "_all_tracking_dirs", lambda: [ambient_dir])

    switched = []
    monkeypatch.setattr(cfg, "set_active_project", lambda name: switched.append(name))

    assert m._relocate_active_project_for_worktree("no-such-id") is False
    assert switched == []


def test_relocate_is_noop_when_ambiguous_across_projects(tmp_path, monkeypatch):
    """An id genuinely ambiguous across two *other* projects must not switch
    (and must not raise) -- the caller's existing not-found handling stands."""
    from agent_worktrees import __main__ as m
    from agent_worktrees import config as cfg

    ambient_dir = tmp_path / "ambient" / "worktrees"
    a_dir = tmp_path / "a" / "worktrees"
    b_dir = tmp_path / "b" / "worktrees"
    _write_record(a_dir, "wt-1")
    _write_record(b_dir, "wt-1")

    monkeypatch.setattr(cfg, "tracking_dir", lambda: ambient_dir)
    monkeypatch.setattr(m, "_all_tracking_dirs", lambda: [ambient_dir, a_dir, b_dir])

    switched = []
    monkeypatch.setattr(cfg, "set_active_project", lambda name: switched.append(name))

    assert m._relocate_active_project_for_worktree("wt-1") is False
    assert switched == []


def test_noninteractive_resume_reloads_config_after_relocation(tmp_path, monkeypatch):
    """Regression guard for the PR #3178 review follow-up:
    ``_resolve_noninteractive_worktree`` must not keep using a config cached
    from an earlier (ambient-project) probe once relocation switches the
    active project -- every downstream call (profile validation, preflight,
    profile resolution, resume) must see the *new* project's config."""
    from agent_worktrees import resolve_cli

    other_dir = tmp_path / "other" / "worktrees"
    _write_record(other_dir, "wt-1")

    ambient_config = object()
    other_config = object()

    state = resolve_cli.ResolveCommandState(
        args=object(),
        use_json=False,
        use_base=False,
        use_new=False,
        requested_machine=None,
        worktree_id="wt-1",
        config=ambient_config,
    )

    load_calls = {"n": 0}

    def fake_load_config():
        load_calls["n"] += 1
        return other_config

    seen_configs = []

    monkeypatch.setattr(resolve_cli, "_resolve_worktree_id", lambda wt_id: wt_id)
    monkeypatch.setattr(
        resolve_cli, "_relocate_active_project_for_worktree", lambda wt_id: True
    )
    monkeypatch.setattr(resolve_cli.cfg, "load_config", fake_load_config)
    monkeypatch.setattr(resolve_cli.cfg, "tracking_dir", lambda: other_dir)
    monkeypatch.setattr(
        resolve_cli.tracking, "load_record", lambda path: type("Record", (), {"worktree_path": "/tmp/wt-1"})()
    )
    monkeypatch.setattr(
        resolve_cli,
        "_validate_profile_assignment_config",
        lambda config: seen_configs.append(("validate", config)),
    )

    def fake_preflight(config, args, work_dir):
        seen_configs.append(("preflight", config))
        return type("Preflight", (), {"error": None})()

    monkeypatch.setattr(resolve_cli, "_preflight_launch", fake_preflight)

    def fake_resolve_profile(config, args):
        seen_configs.append(("profile", config))
        return object()

    monkeypatch.setattr(resolve_cli, "_resolve_profile", fake_resolve_profile)

    def fake_resolve_resume(record, config, args, *, profile, launch_preflight):
        seen_configs.append(("resume", config))
        return 0

    monkeypatch.setattr(resolve_cli, "_resolve_resume", fake_resolve_resume)

    result = resolve_cli._resolve_noninteractive_worktree(state)

    assert result == 0
    # Cache was cleared and reloaded exactly once, and every downstream call
    # saw the fresh (relocated-project) config -- never the stale ambient one.
    assert load_calls["n"] == 1
    assert state.config is other_config
    assert seen_configs == [
        ("validate", other_config),
        ("preflight", other_config),
        ("profile", other_config),
        ("resume", other_config),
    ]


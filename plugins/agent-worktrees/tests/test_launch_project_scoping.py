"""Regression guard for #2338: a launched worktree's plan must carry the
project that actually owns it (``config.repo_name``, resolved from the
worktree that was just created/resumed), never the mutable process-global
``cfg.active_project()``.

``launch-session.ps1``/``.sh`` treat the resolved plan's ``project`` field as
authoritative for every downstream out-of-process call (notably
``session-backend status``). Before this fix, ``_create_worktree_core`` filled
that field from ``cfg.active_project()`` -- a single, process-wide value that
can legitimately differ from the project actually being acted on (e.g. an
automated flow whose launcher started against one project but resolves a
worktree in another) -- and ``_resolve_resume`` never attached a ``project``
field at all. Either gap silently drops project scoping downstream, causing a
launcher to query the wrong project's tracking directory and report
"Worktree not found" for a worktree that was just created/resumed correctly.
"""

from __future__ import annotations

from pathlib import Path

_MAIN = (
    Path(__file__).resolve().parents[1] / "src" / "agent_worktrees" / "__main__.py"
)


def _source() -> str:
    return _MAIN.read_text(encoding="utf-8")


def test_create_worktree_core_scopes_plan_project_to_repo_name():
    """The create-path plan (used by both `_resolve_new` and `cmd_create`)
    must key its `project` off `config.repo_name` -- the project that was
    actually resolved for this specific creation -- not the ambient,
    process-wide `cfg.active_project()`."""
    src = _source()
    assert '"project": config.repo_name,' in src
    # The old, ambient-global-sourced assignment must be gone from the
    # launch-plan construction.
    stale = (
        'project = cfg.active_project()\n'
        '    if project:\n'
        '        result["launch"]["project"] = project'
    )
    assert stale not in src


def test_resolve_resume_attaches_project_to_plan():
    """The resume-path plan must carry `project` too, so a resumed worktree's
    launcher can scope its downstream calls just like a newly-created one."""
    src = _source()
    resume_start = src.index("def _resolve_resume(")
    resume_end = src.index("\ndef _resolve_new(")
    resume_body = src[resume_start:resume_end]
    assert '"project": config.repo_name,' in resume_body


def _config(tmp_path):
    from agent_worktrees import config as cfg

    anchor = tmp_path / "anchor"
    anchor.mkdir()
    return cfg.Config(
        srcroot=str(tmp_path),
        machine="test",
        platform="windows",
        repo_name="demo-repo",
        repos={
            "demo-repo": cfg.RepoConfig(
                anchor=str(anchor),
                worktree_root=str(tmp_path / "worktrees"),
                setup_hook={"windows": "setup.ps1", "linux": "setup.sh"},
            )
        },
    )


def _stub_create_worktree_core_internals(monkeypatch, m, tmp_path):
    """Neutralize every side-effecting internal `_create_worktree_core` calls,
    while running the real function body -- so its assembled `launch` dict is
    genuinely exercised, not hand-constructed by the test."""
    from types import SimpleNamespace

    monkeypatch.setattr(m.git_ops, "resolve_start_point", lambda *_a, **_k: "HEAD")
    monkeypatch.setattr(
        m,
        "_prepare_worktree_source",
        lambda *_a, **_k: SimpleNamespace(start_point="HEAD"),
    )
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a, **_k: None)
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path / "tracking")
    monkeypatch.setattr(
        m.tracking,
        "create_new_record",
        lambda **kwargs: SimpleNamespace(
            worktree_id=kwargs["worktree_id"],
            worktree_path=kwargs["worktree_path"],
            branch=kwargs["branch"],
            kind=kwargs["kind"],
        ),
    )
    monkeypatch.setattr(m.permissions, "clone_permissions", lambda *_a: False)
    monkeypatch.setattr(m.permissions, "add_trusted_folder", lambda *_a: False)
    monkeypatch.setattr(m.activity, "log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(m, "_worktree_to_dict", lambda record: {"id": record.worktree_id})
    monkeypatch.setattr(m, "_reconcile_marketplaces_for_checkout", lambda *_a, **_k: None)
    monkeypatch.setattr(
        m.state_root_mod,
        "resolve_state_root",
        lambda config, cwd=None: m.state_root.StateRoot(
            path=None, source="knowledge_repo", repo="", stateless=False,
            requires_external=False, bound=False, error=None,
        ),
    )
    monkeypatch.setattr(
        m,
        "_launch_profile_selection",
        lambda *_a, **_k: SimpleNamespace(profile=None, assignment=None),
    )
    monkeypatch.setattr(m, "_reflect_assignment", lambda *_a, **_k: None)
    monkeypatch.setattr(m, "_build_launch_cmd", lambda *_a, **_k: ["copilot"])
    monkeypatch.setattr(m, "_repo_session_env", lambda *_a, **_k: {})
    monkeypatch.setattr(m, "_build_env", lambda *_a, **_k: {})
    monkeypatch.setattr(m, "_apply_assignment_env", lambda env, _selection: env)


def test_create_worktree_core_plan_project_matches_repo_name_not_ambient_global(
    tmp_path, monkeypatch
):
    """Behavior-level guard: even when the ambient `cfg.active_project()`
    global points at a DIFFERENT project than the one actually being created
    (exactly the #2338 failure mode), the emitted plan's `project` must match
    the real target, `config.repo_name`."""
    from agent_worktrees import __main__ as m

    config = _config(tmp_path)
    _stub_create_worktree_core_internals(monkeypatch, m, tmp_path)
    # Simulate the daemon/ambient global sitting on a stale, unrelated project
    # while this specific call is scoped (via `config`) to "demo-repo".
    monkeypatch.setattr(m.cfg, "active_project", lambda: "some-other-stale-project")

    result = m._create_worktree_core(config)

    assert result["launch"]["project"] == "demo-repo"
    assert result["launch"]["project"] == config.repo_name


def test_resolve_resume_plan_project_matches_repo_name_not_ambient_global(
    tmp_path, monkeypatch
):
    """Same guard for the resume path: `_resolve_resume`'s plan must carry
    `config.repo_name`, independent of the ambient `cfg.active_project()`."""
    import argparse
    from types import SimpleNamespace

    from agent_worktrees import __main__ as m

    config = _config(tmp_path)
    import dataclasses
    config = dataclasses.replace(config, auto_fast_forward=False)
    monkeypatch.setattr(m.cfg, "active_project", lambda: "some-other-stale-project")

    worktree_path = tmp_path / "worktrees" / "wt-1"
    worktree_path.mkdir(parents=True)
    record = SimpleNamespace(
        worktree_id="wt-1",
        worktree_path=str(worktree_path),
        branch="worktree/wt-1",
        sessions=[],
        yaml_path=tmp_path / "tracking" / "wt-1.yaml",
        resume_count=0,
        last_resumed_at=None,
    )

    class _NullLock:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(m.tracking, "_RecordLock", lambda *_a, **_k: _NullLock())
    monkeypatch.setattr(m.tracking, "load_record", lambda *_a, **_k: record)
    monkeypatch.setattr(m.tracking, "mark_resumed", lambda *_a, **_k: None)
    monkeypatch.setattr(m.tracking, "save_record", lambda *_a, **_k: None)
    monkeypatch.setattr(m.activity, "log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(
        m,
        "_preflight_launch",
        lambda *_a, **_k: SimpleNamespace(error=None),
    )
    monkeypatch.setattr(
        m,
        "_launch_profile_selection",
        lambda *_a, **_k: SimpleNamespace(profile=None, assignment=None),
    )
    monkeypatch.setattr(m, "_reflect_assignment", lambda *_a, **_k: None)
    monkeypatch.setattr(m, "_build_launch_cmd", lambda *_a, **_k: ["copilot"])
    monkeypatch.setattr(m, "_repo_session_env", lambda *_a, **_k: {})
    monkeypatch.setattr(m, "_build_env", lambda *_a, **_k: {})
    monkeypatch.setattr(m, "_apply_assignment_env", lambda env, _selection: env)
    monkeypatch.setattr(m, "_emit_parent_context_hint", lambda *_a, **_k: None)
    monkeypatch.setattr(m, "_emit_plan", lambda plan: setattr(m, "_LAST_PLAN", plan))

    args = argparse.Namespace(
        worktree_id="wt-1", dry_run=False, no_mux=False, no_resume=True,
        restore=False, json=True, bare_resume=False, no_fast_forward=True,
    )

    m._resolve_resume(record, config, args)

    assert m._LAST_PLAN["project"] == "demo-repo"
    assert m._LAST_PLAN["project"] == config.repo_name


def test_launchers_prefer_resolved_plan_project_over_ambient(monkeypatch):
    """Once the plan carries `project`, the launcher scripts must let it win
    over whatever ambient/starting project the launcher itself had -- never
    only filling in an empty value (the #2338 bug)."""
    ps1 = (
        Path(__file__).resolve().parents[1] / "bin" / "launch-session.ps1"
    ).read_text(encoding="utf-8")
    sh = (
        Path(__file__).resolve().parents[1] / "bin" / "launch-session.sh"
    ).read_text(encoding="utf-8")

    # The stale guard ("only if empty") must be gone from both launchers.
    assert (
        "if (-not $script:LaunchProject -and $plan.PSObject.Properties.Name "
        "-contains 'project') {" not in ps1
    )
    assert 'if [[ -z "$LAUNCH_PROJECT" ]]; then\n    LAUNCH_PROJECT=$(printf' not in sh

    # The plan's project must now be preferred unconditionally when present.
    assert (
        "if ($plan.PSObject.Properties.Name -contains 'project' "
        "-and $plan.project) {" in ps1
    )
    assert "$script:LaunchProject = [string]$plan.project" in ps1
    assert '_PLAN_PROJECT=$(printf' in sh
    assert 'if [[ -n "$_PLAN_PROJECT" ]]; then\n    LAUNCH_PROJECT="$_PLAN_PROJECT"' in sh

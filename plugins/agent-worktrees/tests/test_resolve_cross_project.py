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

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg, tracking
from agent_worktrees.launch_preflight import LaunchPreflight


@pytest.mark.guard
@pytest.mark.parametrize(("json_mode", "worktree_id"), [(True, "wt-a"), (True, None), (False, "wt-a")])
def test_launch_config_preserves_policy_and_loads_once(
    monkeypatch: pytest.MonkeyPatch, json_mode: bool, worktree_id: str | None,
) -> None:
    from agent_worktrees import resolve_cli

    calls: list[dict[str, object]] = []
    config = cfg.Config(srcroot="", machine="fixture", platform="linux")

    def load_config(**kwargs: object) -> cfg.Config:
        calls.append(kwargs)
        return config

    monkeypatch.setattr(cfg, "load_config", load_config)
    state = resolve_cli.ResolveCommandState(
        args=argparse.Namespace(), use_json=json_mode, use_base=False,
        use_new=worktree_id is None, requested_machine=None, worktree_id=worktree_id,
    )
    assert state.load_config() is config
    assert state.load_config() is config
    assert calls == [{}]


def _write_record(tracking_dir: Path, wt_id: str) -> None:
    tracking_dir.mkdir(parents=True, exist_ok=True)
    (tracking_dir / f"{wt_id}.yaml").write_text("worktree_id: " + wt_id, encoding="utf-8")


def test_relocate_switches_active_project_when_found_elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    switched: list[str] = []
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


def test_relocate_is_noop_when_found_in_ambient_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import __main__ as m
    from agent_worktrees import config as cfg

    ambient_dir = tmp_path / "ambient" / "worktrees"
    _write_record(ambient_dir, "wt-1")
    monkeypatch.setattr(cfg, "tracking_dir", lambda: ambient_dir)

    switched: list[str] = []
    monkeypatch.setattr(cfg, "set_active_project", lambda name: switched.append(name))

    assert m._relocate_active_project_for_worktree("wt-1") is False
    assert switched == []


def test_relocate_is_noop_when_not_found_anywhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import __main__ as m
    from agent_worktrees import config as cfg

    ambient_dir = tmp_path / "ambient" / "worktrees"
    monkeypatch.setattr(cfg, "tracking_dir", lambda: ambient_dir)
    monkeypatch.setattr(m, "_all_tracking_dirs", lambda: [ambient_dir])

    switched: list[str] = []
    monkeypatch.setattr(cfg, "set_active_project", lambda name: switched.append(name))

    assert m._relocate_active_project_for_worktree("no-such-id") is False
    assert switched == []


def test_relocate_is_noop_when_ambiguous_across_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    switched: list[str] = []
    monkeypatch.setattr(cfg, "set_active_project", lambda name: switched.append(name))

    assert m._relocate_active_project_for_worktree("wt-1") is False
    assert switched == []


def test_noninteractive_resume_reloads_config_after_relocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard for the PR #3178 review follow-up:
    ``_resolve_noninteractive_worktree`` must not keep using a config cached
    from an earlier (ambient-project) probe once relocation switches the
    active project -- every downstream call (profile validation, preflight,
    profile resolution, resume) must see the *new* project's config."""
    from agent_worktrees import resolve_cli, worktree_identity

    other_dir = tmp_path / "other" / "worktrees"
    _write_record(other_dir, "wt-1")

    ambient_config = cfg.Config(srcroot=str(tmp_path), machine="fixture", platform="linux", repo_name="ambient")
    other_config = cfg.Config(srcroot=str(tmp_path), machine="fixture", platform="linux", repo_name="other")

    state = resolve_cli.ResolveCommandState(
        args=argparse.Namespace(),
        use_json=False,
        use_base=False,
        use_new=False,
        requested_machine=None,
        worktree_id="wt-1",
        config=ambient_config,
    )

    load_calls = {"n": 0}

    def fake_load_config() -> cfg.Config:
        load_calls["n"] += 1
        return other_config

    seen_configs: list[tuple[str, cfg.Config]] = []

    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda wt_id: wt_id)
    monkeypatch.setattr(
        resolve_cli, "_relocate_active_project_for_worktree", lambda wt_id: True
    )
    monkeypatch.setattr(cfg, "load_config", fake_load_config)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: other_dir)
    worktree = tmp_path / "wt-1"
    worktree.mkdir()
    monkeypatch.setattr(
        tracking, "load_record", lambda path: SimpleNamespace(worktree_path=str(worktree)),
    )
    monkeypatch.setattr(
        resolve_cli,
        "_validate_profile_assignment_config",
        lambda config: seen_configs.append(("validate", config)),
    )

    def fake_preflight(config: cfg.Config, args: argparse.Namespace, work_dir: str) -> LaunchPreflight:
        seen_configs.append(("preflight", config))
        return LaunchPreflight()

    monkeypatch.setattr(resolve_cli, "_preflight_launch", fake_preflight)

    def fake_resolve_profile(config: cfg.Config, args: argparse.Namespace) -> cfg.CopilotProfile:
        seen_configs.append(("profile", config))
        return cfg.CopilotProfile(name="example", label="Example")

    monkeypatch.setattr(resolve_cli, "_resolve_profile", fake_resolve_profile)

    def fake_resolve_resume(
        record: tracking.WorktreeRecord, config: cfg.Config, args: argparse.Namespace, *,
        profile: cfg.CopilotProfile | None, launch_preflight: LaunchPreflight,
    ) -> int:
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

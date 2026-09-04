"""Balanced profile-assignment policy, allocator, and launch integration."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import profile_assignment as assignment
from agent_worktrees import tracking


def _profiles(count: int = 6) -> list[cfg.CopilotProfile]:
    return [
        cfg.CopilotProfile(
            name=f"profile-{index}",
            label=f"Profile {index}",
            env={"PROFILE_ENV": str(index)},
            copilot_args=["--model", f"model-{index}"],
        )
        for index in range(count)
    ]


def _policy(
    profiles: list[cfg.CopilotProfile],
    *,
    armed: bool = True,
    lanes: tuple[str, ...] = ("new", "handoff-cutover"),
    error: str = "",
) -> cfg.ProfileAssignmentPolicy:
    return cfg.ProfileAssignmentPolicy(
        name="balanced-default",
        mode="balanced-random",
        armed=armed,
        profiles=tuple(profile.name for profile in profiles),
        assignment_label="cohort-a",
        eligible_lanes=lanes,
        error=error,
    )


def _record(
    tmp_path: Path,
    *,
    worktree_id: str = "wt-a",
    kind: tracking.WorktreeKind = "session",
    interface: tracking.WorktreeInterface | None = None,
    origin: tracking.WorktreeOrigin | None = None,
) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id=worktree_id,
        branch=f"worktree/{worktree_id}",
        worktree_path=str(tmp_path / worktree_id),
        repo="test-project",
        machine="test-machine",
        platform="linux",
        started_at="2026-09-01T10:00:00+00:00",
        last_resumed_at="2026-09-01T10:00:00+00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
        kind=kind,
        interface=interface,
        origin=origin,
    )


def _assert_profile_launch_effects(
    tmp_path: Path,
    profile: cfg.CopilotProfile | None,
) -> None:
    assert profile is not None
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test-machine",
        platform="linux",
        repo_name="test-project",
        repos={
            "test-project": cfg.RepoConfig(
                anchor=str(tmp_path),
                worktree_root=str(tmp_path / "worktrees"),
                launch={"linux": ["copilot"]},
            )
        },
    )
    command = m._build_launch_cmd(
        config,
        argparse.Namespace(copilot_args=[], recovery=False),
        str(tmp_path / "wt"),
        profile=profile,
    )
    model_index = command.index("--model")
    assert command[model_index:model_index + 2] == [
        "--model",
        profile.copilot_args[1],
    ]
    assert m._build_env(profile)["PROFILE_ENV"] == profile.env["PROFILE_ENV"]


@pytest.fixture
def assignment_home(tmp_path: Path, monkeypatch):
    assignment._DIAGNOSTIC_KEYS.clear()
    cfg.set_active_project("test-project")
    project = tmp_path / ".test-project"
    tracking_dir = project / "worktrees"
    tracking_dir.mkdir(parents=True)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tracking_dir)
    return project, tracking_dir


def _allocate_bag(
    profiles: list[cfg.CopilotProfile],
    policy: cfg.ProfileAssignmentPolicy,
    count: int,
) -> list[tracking.ProfileAssignment]:
    values = []
    for index in range(count):
        selected = assignment.allocate(
            policy,
            profiles,
            worktree_id=f"wt-{index}",
            lane="new",
            generation_key=f"new:wt-{index}",
            seed="fixed-seed",
            token=f"token-{index}",
        )
        assert selected.assignment is not None
        values.append(selected.assignment)
    return values


def test_six_profile_bag_is_balanced_and_seed_deterministic(
    assignment_home, monkeypatch, tmp_path: Path
):
    profiles = _profiles()
    policy = _policy(profiles)

    first = _allocate_bag(profiles, policy, 12)
    assert {item.selected_profile for item in first[:6]} == {
        profile.name for profile in profiles
    }
    assert {item.selected_profile for item in first[6:]} == {
        profile.name for profile in profiles
    }
    assert [item.bag_position for item in first] == list(range(6)) * 2
    assert [item.bag_generation for item in first] == [0] * 6 + [1] * 6

    second_project = tmp_path / ".second-project"
    second_project.mkdir()
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: second_project)
    second = _allocate_bag(profiles, policy, 12)
    assert [item.selected_profile for item in second] == [
        item.selected_profile for item in first
    ]


def _concurrent_allocate_worker(
    home: str,
    index: int,
    queue,
) -> None:
    os.environ["AGENT_HOME"] = home
    cfg.set_active_project("test-project")
    profiles = _profiles()
    selected = assignment.allocate(
        _policy(profiles),
        profiles,
        worktree_id=f"wt-{index}",
        lane="new",
        generation_key=f"new:wt-{index}",
        seed="concurrent-seed",
        token=f"token-{index}",
    )
    assert selected.assignment is not None
    queue.put((
        selected.assignment.bag_generation,
        selected.assignment.bag_position,
        selected.assignment.selected_profile,
    ))


def test_concurrent_allocation_serializes_bag_positions(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    workers = [
        context.Process(
            target=_concurrent_allocate_worker,
            args=(str(home), index, queue),
        )
        for index in range(12)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
        assert worker.exitcode == 0
    results = [queue.get(timeout=5) for _ in workers]
    slots = sorted((generation, position) for generation, position, _ in results)
    assert slots == [(0, index) for index in range(6)] + [
        (1, index) for index in range(6)
    ]
    for generation in (0, 1):
        assert len({
            profile
            for gen, _, profile in results
            if gen == generation
        }) == 6


def test_launch_retry_reuses_pending_assignment(assignment_home):
    profiles = _profiles()
    policy = _policy(profiles)
    first = assignment.allocate(
        policy,
        profiles,
        worktree_id="wt-retry",
        lane="new",
        generation_key="new:wt-retry",
        seed="retry-seed",
        token="first-token",
    )
    retry = assignment.allocate(
        policy,
        profiles,
        worktree_id="wt-retry",
        lane="new",
        generation_key="new:wt-retry",
        token="ignored-token",
    )
    assert retry.assignment == first.assignment
    state = json.loads(assignment.state_path().read_text(encoding="utf-8"))
    assert len(state["assignments"]) == 1
    assert state["position"] == 1


def test_binding_and_ordinary_resume_replay_the_same_profile(
    assignment_home,
    tmp_path: Path,
):
    _, tracking_dir = assignment_home
    profiles = _profiles()
    policy = _policy(profiles)
    record = _record(tmp_path, worktree_id="wt-resume")
    tracking.save_record(record, tracking_dir / "wt-resume.yaml")
    selected = assignment.allocate(
        policy,
        profiles,
        worktree_id="wt-resume",
        lane="new",
        generation_key="new:wt-resume",
        seed="resume-seed",
        token="resume-token",
    )
    bound = assignment.bind("resume-token", "session-1", "wt-resume")
    assert bound is not None
    state = json.loads(assignment.state_path().read_text(encoding="utf-8"))
    assert "launch_token" not in state["assignments"][0]
    assert state["assignments"][0]["retired_token_digest"]
    loaded = tracking.load_record(tracking_dir / "wt-resume.yaml")
    replayed = assignment.replay(
        assignment.assignment_for_session(loaded, "session-1"),
        profiles,
    )
    assert replayed.profile == selected.profile
    assert replayed.assignment is not None
    assert replayed.assignment.disposition == "bound"


def test_stale_pending_record_sync_cannot_overwrite_bound_assignment(
    assignment_home,
    tmp_path: Path,
    monkeypatch,
):
    _, tracking_dir = assignment_home
    profiles = _profiles(2)
    record = _record(tmp_path, worktree_id="wt-stale-sync")
    tracking.save_record(record, tracking_dir / "wt-stale-sync.yaml")
    pending_syncs: list[tuple[str, tracking.ProfileAssignment]] = []
    original_sync = assignment._sync_record_best_effort

    monkeypatch.setattr(
        assignment,
        "_sync_record_best_effort",
        lambda worktree_id, value: pending_syncs.append(
            (worktree_id, value)
        ),
    )
    selected = assignment.allocate(
        _policy(profiles),
        profiles,
        worktree_id="wt-stale-sync",
        lane="new",
        generation_key="new:wt-stale-sync",
        token="stale-sync-token",
    )
    assert selected.assignment is not None
    assert selected.assignment.disposition == "pending"
    assert pending_syncs == [("wt-stale-sync", selected.assignment)]

    monkeypatch.setattr(
        assignment,
        "_sync_record_best_effort",
        original_sync,
    )
    bound = assignment.bind(
        "stale-sync-token",
        "session-bound",
        "wt-stale-sync",
    )
    assert bound is not None
    original_sync(*pending_syncs[0])

    loaded = tracking.load_record(tracking_dir / "wt-stale-sync.yaml")
    mirrored = assignment.assignment_for_session(loaded, "session-bound")
    assert mirrored is not None
    assert mirrored.disposition == "bound"
    assert mirrored.session_id == "session-bound"
    assert mirrored.bound_at
    assert len(loaded.profile_assignments) == 1
    assert loaded.profile_assignment_revision == 1


def test_stale_pending_record_sync_cannot_overwrite_abandoned_assignment(
    assignment_home,
    tmp_path: Path,
    monkeypatch,
):
    _, tracking_dir = assignment_home
    profiles = _profiles(2)
    record = _record(tmp_path, worktree_id="wt-stale-abandoned")
    tracking.save_record(record, tracking_dir / "wt-stale-abandoned.yaml")
    pending_syncs: list[tuple[str, tracking.ProfileAssignment]] = []
    original_sync = assignment._sync_record_best_effort
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(
        assignment,
        "_sync_record_best_effort",
        lambda worktree_id, value: pending_syncs.append(
            (worktree_id, value)
        ),
    )
    selected = assignment.allocate(
        _policy(profiles),
        profiles,
        worktree_id="wt-stale-abandoned",
        lane="new",
        generation_key="new:wt-stale-abandoned",
        token="stale-abandoned-token",
        now=start,
    )
    assert selected.assignment is not None

    monkeypatch.setattr(
        assignment,
        "_sync_record_best_effort",
        original_sync,
    )
    assert assignment.expire_pending(
        now=start + timedelta(minutes=16),
    ) == 1
    original_sync(*pending_syncs[0])

    loaded = tracking.load_record(
        tracking_dir / "wt-stale-abandoned.yaml"
    )
    assert len(loaded.profile_assignments) == 1
    assert loaded.profile_assignments[0].disposition == "abandoned"
    assert loaded.profile_assignments[0].abandoned_at
    assert loaded.profile_assignment_revision == 1


def test_register_session_binds_environment_token_to_actual_session(
    assignment_home,
    tmp_path: Path,
    monkeypatch,
):
    _, tracking_dir = assignment_home
    profiles = _profiles(2)
    record = _record(tmp_path, worktree_id="wt-register")
    tracking.save_record(record, tracking_dir / "wt-register.yaml")
    assignment.allocate(
        _policy(profiles),
        profiles,
        worktree_id="wt-register",
        lane="new",
        generation_key="new:wt-register",
        seed="register-seed",
        token="register-token",
    )
    monkeypatch.setenv(
        assignment.ASSIGNMENT_TOKEN_ENV,
        "register-token",
    )
    args = argparse.Namespace(
        worktree_id="wt-register",
        session_id="actual-session",
        cwd=None,
        stdin=False,
        pid=None,
        pane=None,
        launch_id=None,
        assignment_token=None,
        emit_context=False,
        handoff_token=None,
    )

    assert m.cmd_register_session(args) == 0

    loaded = tracking.load_record(tracking_dir / "wt-register.yaml")
    bound = assignment.assignment_for_session(loaded, "actual-session")
    assert bound is not None
    assert bound.disposition == "bound"
    assert bound.bound_at
    assert "launch_token" not in assignment.metadata(bound)
    assert "launch_token" not in (
        tracking_dir / "wt-register.yaml"
    ).read_text(encoding="utf-8")


def test_double_bind_and_new_session_id_do_not_break_registration(
    assignment_home,
    tmp_path: Path,
    monkeypatch,
    capfd,
):
    _, tracking_dir = assignment_home
    profiles = _profiles(2)
    record = _record(tmp_path, worktree_id="wt-double-bind")
    tracking.save_record(record, tracking_dir / "wt-double-bind.yaml")
    selected = assignment.allocate(
        _policy(profiles),
        profiles,
        worktree_id="wt-double-bind",
        lane="new",
        generation_key="new:wt-double-bind",
        token="one-shot-token",
    )
    monkeypatch.setenv(
        assignment.ASSIGNMENT_TOKEN_ENV,
        selected.launch_token or "",
    )

    args = argparse.Namespace(
        worktree_id="wt-double-bind",
        session_id="session-1",
        cwd=None,
        stdin=False,
        pid=None,
        pane=None,
        launch_id=None,
        assignment_token=None,
        emit_context=False,
        handoff_token=None,
    )
    assert m.cmd_register_session(args) == 0
    args.session_id = "session-2"
    assert m.cmd_register_session(args) == 0

    loaded = tracking.load_record(tracking_dir / "wt-double-bind.yaml")
    assert [entry.session_id for entry in loaded.sessions or []] == [
        "session-1",
        "session-2",
    ]
    assert assignment.assignment_for_session(loaded, "session-1") is not None
    assert assignment.assignment_for_session(loaded, "session-2") is None
    assert "registration continues" in capfd.readouterr().err


@pytest.mark.parametrize("mismatch", ["expired", "foreign"])
def test_lifecycle_mismatch_bind_does_not_break_session_start(
    assignment_home,
    tmp_path: Path,
    monkeypatch,
    mismatch: str,
):
    _, tracking_dir = assignment_home
    profiles = _profiles(2)
    record = _record(tmp_path, worktree_id="wt-slow")
    tracking.save_record(record, tracking_dir / "wt-slow.yaml")
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    selected = assignment.allocate(
        _policy(profiles),
        profiles,
        worktree_id="wt-slow" if mismatch == "expired" else "wt-other",
        lane="new",
        generation_key=f"new:{mismatch}",
        now=start,
        token=f"{mismatch}-token",
    )
    if mismatch == "expired":
        assignment.expire_pending(now=start + timedelta(minutes=16))
    monkeypatch.setenv(
        assignment.ASSIGNMENT_TOKEN_ENV,
        selected.launch_token or "",
    )
    activity_events: list[tuple[tuple, dict]] = []
    updater_calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        m.activity,
        "log_event",
        lambda *args, **kwargs: activity_events.append((args, kwargs)),
    )
    monkeypatch.setattr(
        m,
        "_spawn_status_updater",
        lambda worktree_id, path: updater_calls.append((worktree_id, path)),
    )
    monkeypatch.setattr(
        m.session_context_mod,
        "render_registry_context",
        lambda *_args, **_kwargs: "registry context",
    )
    config_calls = []
    monkeypatch.setattr(
        m.cfg,
        "load_config",
        lambda **kwargs: config_calls.append(kwargs) or argparse.Namespace(),
    )
    args = argparse.Namespace(
        worktree_id="wt-slow",
        session_id=f"session-{mismatch}",
        cwd=None,
        stdin=False,
        pid=None,
        pane=None,
        launch_id=None,
        assignment_token=None,
        emit_context=True,
        handoff_token=None,
    )

    assert m.cmd_register_session(args) == 0
    loaded = tracking.load_record(tracking_dir / "wt-slow.yaml")
    assert any(
        entry.session_id == f"session-{mismatch}"
        for entry in loaded.sessions or []
    )
    assert any(event[0] == ("session_started",) for event in activity_events)
    assert updater_calls == [("wt-slow", str(tmp_path / "wt-slow"))]
    assert config_calls == [{"include_control_plane_related_pr": False}]


def test_handoff_redraws_after_binding_only_when_armed(assignment_home):
    profiles = _profiles()
    armed = _policy(profiles)
    first = assignment.allocate(
        armed,
        profiles,
        worktree_id="wt-handoff",
        lane="handoff-cutover",
        generation_key="handoff:session-1",
        seed="handoff-seed",
        token="handoff-1",
    )
    retry = assignment.allocate(
        armed,
        profiles,
        worktree_id="wt-handoff",
        lane="handoff-cutover",
        generation_key="handoff:session-1",
        token="handoff-retry",
    )
    assert retry.assignment == first.assignment
    assignment.bind("handoff-1", "session-2", "wt-handoff")
    successor = assignment.allocate(
        armed,
        profiles,
        worktree_id="wt-handoff",
        lane="handoff-cutover",
        generation_key="handoff:session-1",
        token="handoff-2",
        predecessor_session_id="session-1",
    )
    assert successor.assignment is not None
    assert successor.launch_token == "handoff-2"
    assert successor.assignment.bag_position == 1
    assert successor.assignment.predecessor_session_id == "session-1"

    unarmed = _policy(profiles, armed=False)
    assert assignment.allocate(
        unarmed,
        profiles,
        worktree_id="wt-off",
        lane="handoff-cutover",
        generation_key="handoff:off",
    ).assignment is None


def test_handoff_cutover_wires_assignment_profile_and_token(
    assignment_home,
    tmp_path: Path,
    monkeypatch,
    capfd,
):
    _, tracking_dir = assignment_home
    profiles = _profiles(2)
    record = _record(tmp_path, worktree_id="wt-cutover")
    tracking.save_record(record, tracking_dir / "wt-cutover.yaml")
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test-machine",
        platform="linux",
        repo_name="test-project",
        repos={
            "test-project": cfg.RepoConfig(
                anchor=str(tmp_path),
                worktree_root=str(tmp_path / "worktrees"),
            )
        },
        copilot_profiles=profiles,
        profile_assignment=_policy(profiles),
    )
    monkeypatch.setattr(m, "_infer_worktree_id_from_cwd", lambda: "wt-cutover")
    monkeypatch.setattr(m.cfg, "load_config", lambda **_kwargs: config)
    monkeypatch.setattr(m.sessions, "has_mux_session", lambda _wt: True)
    monkeypatch.setattr(m.sessions, "mux_active_pane", lambda _wt: "%1")
    monkeypatch.setattr(
        m,
        "_preflight_launch",
        lambda *_args, **_kwargs: m.LaunchPreflight(),
    )
    captured_profiles: list[str | None] = []

    def _build(*_args, **kwargs):
        profile = kwargs.get("profile")
        captured_profiles.append(profile.name if profile else None)
        return ["copilot"]

    captured_envs: list[dict[str, str]] = []

    def _window(_wt, _wd, _cmd, env, **_kwargs):
        captured_envs.append(dict(env))
        return {"ok": True, "new_pane": "%2", "prompt_received": True}

    monkeypatch.setattr(m, "_build_launch_cmd", _build)
    monkeypatch.setattr(m, "_build_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(m, "_repo_session_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(m.sessions, "mux_new_window", _window)
    monkeypatch.setattr(m.activity, "log_event", lambda *_args, **_kwargs: None)
    args = argparse.Namespace(
        seed="continue",
        worktree_id=None,
        session_id="predecessor",
        old_pane="%1",
        retire_pane=None,
        mux_session=None,
        require_mux_identity=False,
        dry_run=False,
        copilot_args=[],
        recovery=False,
    )

    assert m.cmd_handoff_cutover(args) == 0
    first = json.loads(capfd.readouterr().out)["profile_assignment"]
    assert captured_profiles[-1] in {profile.name for profile in profiles}
    first_token = captured_envs[-1][assignment.ASSIGNMENT_TOKEN_ENV]
    assert "launch_token" not in first
    assert first["predecessor_session_id"] == "predecessor"

    assert m.cmd_handoff_cutover(args) == 0
    retry = json.loads(capfd.readouterr().out)["profile_assignment"]
    assert captured_envs[-1][assignment.ASSIGNMENT_TOKEN_ENV] == first_token
    assert retry["bag_position"] == first["bag_position"]

    assignment.bind(
        first_token,
        "successor",
        "wt-cutover",
    )
    assert m.cmd_handoff_cutover(args) == 0
    successor = json.loads(capfd.readouterr().out)["profile_assignment"]
    assert captured_envs[-1][assignment.ASSIGNMENT_TOKEN_ENV] != first_token
    assert successor["bag_position"] == 1
    assert successor["predecessor_session_id"] == "predecessor"


def test_exclusions_and_explicit_profile_never_allocate(
    assignment_home,
    tmp_path: Path,
):
    profiles = _profiles()
    policy = _policy(profiles)
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test-machine",
        platform="linux",
        repo_name="test-project",
        repos={
            "test-project": cfg.RepoConfig(
                anchor=str(tmp_path),
                worktree_root=str(tmp_path / "worktrees"),
            )
        },
        copilot_profiles=profiles,
        profile_assignment=policy,
    )
    base_args = dict(
        profile=None,
        recovery=False,
        copilot_args=[],
    )
    explicit = m._launch_profile_selection(
        config,
        argparse.Namespace(**{**base_args, "profile": profiles[0].name}),
        _record(tmp_path),
        lane="new",
        generation_key="new:explicit",
        ordinary_profile=profiles[1],
        explicit_profile=profiles[0],
    )
    assert explicit.profile == profiles[0]
    assert explicit.assignment is None

    for args, record in (
        (argparse.Namespace(**{**base_args, "recovery": True}), _record(tmp_path)),
        (
            argparse.Namespace(**{**base_args, "emergency": True}),
            _record(tmp_path),
        ),
        (
            argparse.Namespace(**{**base_args, "copilot_args": ["--acp", "--stdio"]}),
            _record(tmp_path, interface="acp"),
        ),
        (
            argparse.Namespace(**base_args),
            _record(tmp_path, kind="system", origin="system"),
        ),
        (
            argparse.Namespace(**base_args),
            _record(tmp_path, origin="delegate"),
        ),
    ):
        selected = m._launch_profile_selection(
            config,
            args,
            record,
            lane="new",
            generation_key=f"new:{record.worktree_id}",
            ordinary_profile=profiles[0],
        )
        assert selected.profile == profiles[0]
        assert selected.assignment is None
        if getattr(args, "recovery", False):
            _assert_profile_launch_effects(tmp_path, selected.profile)
    assert not assignment.state_path().exists()


def test_armed_picker_base_repo_keeps_default_profile_args_and_env(
    assignment_home,
    tmp_path: Path,
    monkeypatch,
):
    profiles = _profiles(2)
    selected, explicit = m._picker_profile_choice(
        profiles,
        assignment_armed=True,
        profile_idx=0,
    )
    assert selected == profiles[0]
    assert explicit is None

    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test-machine",
        platform="linux",
        repo_name="test-project",
        repos={
            "test-project": cfg.RepoConfig(
                anchor=str(tmp_path),
                worktree_root=str(tmp_path / "worktrees"),
                launch={"linux": ["copilot"]},
            )
        },
        copilot_profiles=profiles,
        profile_assignment=_policy(profiles),
    )
    plans: list[dict] = []
    monkeypatch.setattr(m, "_emit_plan", plans.append)
    args = argparse.Namespace(
        copilot_args=[],
        recovery=False,
        dry_run=False,
        no_mux=True,
    )

    assert m._resolve_base_repo(config, args, profile=selected) == 0
    assert plans[-1]["cmd"][:3] == ["copilot", "--model", "model-0"]
    assert plans[-1]["env"]["PROFILE_ENV"] == "0"


def test_unassigned_resume_keeps_ordinary_profile_args_and_env(
    assignment_home,
    tmp_path: Path,
):
    profiles = _profiles(2)
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test-machine",
        platform="linux",
        repo_name="test-project",
        repos={
            "test-project": cfg.RepoConfig(
                anchor=str(tmp_path),
                worktree_root=str(tmp_path / "worktrees"),
                launch={"linux": ["copilot"]},
            )
        },
        copilot_profiles=profiles,
        profile_assignment=_policy(profiles),
    )
    selected = m._launch_profile_selection(
        config,
        argparse.Namespace(profile=None, recovery=False, copilot_args=[]),
        _record(tmp_path),
        lane="new",
        generation_key="new:pre-feature",
        ordinary_profile=profiles[0],
        resume_session="pre-feature-session",
    )

    assert selected.profile == profiles[0]
    assert selected.assignment is None
    _assert_profile_launch_effects(tmp_path, selected.profile)


@pytest.mark.parametrize(
    ("args", "record", "explicit_profile"),
    [
        pytest.param(
            argparse.Namespace(
                profile="profile-0",
                recovery=False,
                copilot_args=[],
            ),
            None,
            "explicit",
            id="explicit-profile",
        ),
        pytest.param(
            argparse.Namespace(
                profile=None,
                recovery=True,
                copilot_args=[],
            ),
            None,
            None,
            id="recovery",
        ),
        pytest.param(
            argparse.Namespace(
                profile=None,
                recovery=False,
                emergency=True,
                copilot_args=[],
            ),
            None,
            None,
            id="emergency",
        ),
        pytest.param(
            argparse.Namespace(
                profile=None,
                recovery=False,
                copilot_args=["--acp", "--stdio"],
            ),
            "acp",
            None,
            id="acp",
        ),
        pytest.param(
            argparse.Namespace(
                profile=None,
                recovery=False,
                copilot_args=[],
            ),
            "system",
            None,
            id="system",
        ),
        pytest.param(
            argparse.Namespace(
                profile=None,
                recovery=False,
                copilot_args=[],
            ),
            "delegate",
            None,
            id="delegated",
        ),
    ],
)
def test_invalid_user_policy_precedes_launch_class_exclusions(
    assignment_home,
    tmp_path: Path,
    args: argparse.Namespace,
    record: str | None,
    explicit_profile: str | None,
):
    profiles = _profiles(2)
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test-machine",
        platform="linux",
        repo_name="test-project",
        repos={
            "test-project": cfg.RepoConfig(
                anchor=str(tmp_path),
                worktree_root=str(tmp_path / "worktrees"),
            )
        },
        copilot_profiles=profiles,
        profile_assignment=_policy(
            profiles,
            error="invalid assignment configuration",
        ),
    )
    launch_record = _record(
        tmp_path,
        interface="acp" if record == "acp" else None,
        kind="system" if record == "system" else "session",
        origin="delegate" if record == "delegate" else None,
    )

    with pytest.raises(
        assignment.ProfileAssignmentError,
        match="invalid assignment configuration",
    ):
        m._launch_profile_selection(
            config,
            args,
            launch_record,
            lane="new",
            generation_key="new:excluded",
            explicit_profile=(
                profiles[0] if explicit_profile == "explicit" else None
            ),
        )
    assert not assignment.state_path().exists()


def test_missing_and_malformed_armed_policies_fail_closed(assignment_home):
    profiles = _profiles(2)
    missing = cfg.ProfileAssignmentPolicy(
        name="missing",
        mode="balanced-random",
        armed=True,
        profiles=("profile-0", "not-defined"),
    )
    with pytest.raises(assignment.ProfileAssignmentError, match="unavailable"):
        assignment.allocate(
            missing,
            profiles,
            worktree_id="wt-missing",
            lane="new",
            generation_key="new:wt-missing",
        )

    malformed_disarmed = _policy(
        profiles,
        armed=False,
        error="profile_assignment.armed must be a boolean",
    )
    with pytest.raises(
        assignment.ProfileAssignmentError,
        match="armed must be a boolean",
    ):
        assignment.validate_policy(malformed_disarmed, profiles)

    malformed = _policy(profiles, error="invalid assignment configuration")
    with pytest.raises(
        assignment.ProfileAssignmentError,
        match="invalid assignment configuration",
    ):
        assignment.allocate(
            malformed,
            profiles,
            worktree_id="wt-malformed",
            lane="new",
            generation_key="new:wt-malformed",
        )


def test_repository_template_cannot_arm_or_expand_user_pool():
    profiles = _profiles(4)
    repo_only = cfg._parse_profile_assignment(
        {},
        {
            "name": "balanced-default",
            "mode": "balanced-random",
            "armed": True,
            "profiles": ["profile-0", "profile-1"],
        },
        profiles,
    )
    assert repo_only is not None
    assert repo_only.armed is False
    assert repo_only.profiles == ()

    narrowed = cfg._parse_profile_assignment(
        {
            "name": "balanced-default",
            "mode": "balanced-random",
            "armed": True,
            "profiles": ["profile-0", "profile-1", "profile-2"],
            "eligible_lanes": ["new", "handoff-cutover"],
        },
        {
            "name": "balanced-default",
            "profiles": ["profile-1", "repo-only-profile"],
            "eligible_lanes": ["new"],
        },
        profiles,
    )
    assert narrowed is not None
    assert narrowed.armed is True
    assert narrowed.profiles == ("profile-1",)
    assert narrowed.eligible_lanes == ("new",)


@pytest.mark.parametrize(
    "repository_template",
    [
        pytest.param(
            {
                "name": "balanced-default",
                "profiles": ["profile-0"],
            },
            id="missing-mode",
        ),
        pytest.param(
            {
                "name": "balanced-default",
                "mode": "balanced-rnadom",
                "profiles": ["profile-0"],
            },
            id="typo-mode",
        ),
        pytest.param(
            {
                "name": "balanced-default",
                "mode": "balanced-random",
                "profiles": "profile-0",
            },
            id="profiles-scalar",
        ),
        pytest.param(
            {
                "name": "balanced-default",
                "mode": "balanced-random",
                "profiles": ["profile-0"],
                "assignment_label": 7,
            },
            id="bad-assignment-label",
        ),
    ],
)
def test_repository_only_malformed_templates_are_non_load_bearing(
    repository_template,
):
    profiles = _profiles(2)
    policy = cfg._parse_profile_assignment({}, repository_template, profiles)

    assert policy is not None
    assert policy.armed is False
    assert policy.error == ""
    assert policy.repository_error
    assignment.validate_policy(policy, profiles)


@pytest.mark.parametrize(
    "repository_template",
    [
        pytest.param(
            {"name": "balanced-default", "profiles": ["profile-0"]},
            id="missing-mode",
        ),
        pytest.param(
            {
                "name": "balanced-default",
                "mode": "balanced-rnadom",
                "profiles": ["profile-0"],
            },
            id="typo-mode",
        ),
        pytest.param(
            {
                "name": "balanced-default",
                "mode": "balanced-random",
                "profiles": "profile-0",
            },
            id="profiles-scalar",
        ),
        pytest.param(
            {
                "name": "balanced-default",
                "mode": "balanced-random",
                "profiles": ["profile-0"],
                "assignment_label": 7,
            },
            id="bad-assignment-label",
        ),
    ],
)
@pytest.mark.parametrize(
    ("launch_case", "lane", "resume_session", "origin"),
    [
        ("create", "new", None, None),
        ("resume", "new", "session-1", None),
        ("embody", "new", None, "system"),
        ("handoff", "handoff-cutover", None, None),
    ],
)
def test_repository_only_malformed_templates_do_not_block_launch_paths(
    assignment_home,
    tmp_path: Path,
    repository_template,
    launch_case: str,
    lane: str,
    resume_session: str | None,
    origin: tracking.WorktreeOrigin | None,
):
    profiles = _profiles(2)
    policy = cfg._parse_profile_assignment({}, repository_template, profiles)
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test-machine",
        platform="linux",
        repo_name="test-project",
        repos={
            "test-project": cfg.RepoConfig(
                anchor=str(tmp_path),
                worktree_root=str(tmp_path / "worktrees"),
            )
        },
        copilot_profiles=profiles,
        profile_assignment=policy,
    )

    selected = m._launch_profile_selection(
        config,
        argparse.Namespace(profile=None, recovery=False, copilot_args=[]),
        _record(tmp_path, origin=origin),
        lane=lane,
        generation_key=f"{launch_case}:wt-a",
        resume_session=resume_session,
    )

    assert selected.profile is None
    assert selected.assignment is None
    assert not assignment.state_path().exists()


def test_global_arming_machine_override_and_repo_narrowing(
    tmp_path: Path,
    monkeypatch,
):
    cfg.set_active_project("project")
    anchor = tmp_path / "repo"
    inrepo = anchor / cfg.INREPO_CONFIG_DIRNAME
    inrepo.mkdir(parents=True)
    (inrepo / "config.yaml").write_text(
        "profile_assignment:\n"
        "  name: balanced-default\n"
        "  profiles: [profile-1, repository-only]\n"
        "  eligible_lanes: [new]\n",
        encoding="utf-8",
    )
    global_path = tmp_path / "global.yaml"
    global_path.write_text(
        "copilot_profiles:\n"
        "  - name: profile-0\n"
        "  - name: profile-1\n"
        "profile_assignment:\n"
        "  name: balanced-default\n"
        "  mode: balanced-random\n"
        "  armed: true\n"
        "  profiles: [profile-0, profile-1]\n"
        "  assignment_label: global-label\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "global_config_path", lambda: global_path)
    machine_path = tmp_path / "machine.yaml"
    machine_path.write_text(
        "repo_name: project\n"
        "profile_assignment:\n"
        "  assignment_label: machine-label\n"
        "repos:\n"
        "  project:\n"
        f"    anchor: {anchor}\n",
        encoding="utf-8",
    )

    loaded = cfg.load_config(machine_path)

    assert loaded.profile_assignment is not None
    assert loaded.profile_assignment.armed is True
    assert loaded.profile_assignment.profiles == ("profile-1",)
    assert loaded.profile_assignment.eligible_lanes == ("new",)
    assert loaded.profile_assignment.assignment_label == "machine-label"


def test_malformed_arming_value_is_a_visible_config_error():
    profiles = _profiles(2)
    policy = cfg._parse_profile_assignment(
        {
            "name": "balanced-default",
            "mode": "balanced-random",
            "armed": "true",
            "profiles": ["profile-0", "profile-1"],
        },
        {},
        profiles,
    )
    assert policy is not None
    assert policy.armed is False
    with pytest.raises(
        assignment.ProfileAssignmentError,
        match="armed must be a boolean",
    ):
        assignment.validate_policy(policy, profiles)


@pytest.mark.parametrize(
    "state",
    [
        "{not-json",
        json.dumps({"schema_version": 999, "assignments": []}),
        json.dumps({
            "schema_version": 1,
            "assignments": [],
            "generation": {"bad": 1},
        }),
    ],
)
def test_corrupt_or_future_state_falls_back_to_ordinary_launch(
    assignment_home,
    tmp_path: Path,
    state: str,
):
    profiles = _profiles(2)
    assignment.state_path().write_text(state, encoding="utf-8")
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test-machine",
        platform="linux",
        repo_name="test-project",
        repos={
            "test-project": cfg.RepoConfig(
                anchor=str(tmp_path),
                worktree_root=str(tmp_path / "worktrees"),
            )
        },
        copilot_profiles=profiles,
        profile_assignment=_policy(profiles),
    )

    selected = m._launch_profile_selection(
        config,
        argparse.Namespace(profile=None, recovery=False, copilot_args=[]),
        _record(tmp_path),
        lane="new",
        generation_key="new:state-fallback",
        ordinary_profile=profiles[0],
    )

    assert selected.profile == profiles[0]
    assert selected.assignment is None
    assert "state is unavailable" in selected.warning
    assert assignment.state_path().read_text(encoding="utf-8") == state
    _assert_profile_launch_effects(tmp_path, selected.profile)


@pytest.mark.parametrize(
    ("target", "value"),
    [
        pytest.param("generation", float("inf"), id="infinite-generation"),
        pytest.param("position", float("nan"), id="nan-position"),
        pytest.param(
            "generation",
            tracking.MAX_PERSISTED_COUNTER + 1,
            id="oversized-generation",
        ),
        pytest.param("position", -1, id="negative-position"),
        pytest.param("position", 1.5, id="fractional-position"),
        pytest.param("position", "1", id="string-position"),
        pytest.param("bag_generation", True, id="boolean-assignment-generation"),
        pytest.param("bag_position", "bad", id="malformed-assignment-position"),
    ],
)
def test_invalid_numeric_state_falls_back_during_allocation(
    assignment_home,
    tmp_path: Path,
    target: str,
    value,
):
    profiles = _profiles(2)
    policy = _policy(profiles)
    assignment.allocate(
        policy,
        profiles,
        worktree_id="wt-existing",
        lane="new",
        generation_key="new:wt-existing",
        token="existing-token",
    )
    state = json.loads(assignment.state_path().read_text(encoding="utf-8"))
    if target.startswith("bag_"):
        state["assignments"][0][target] = value
    else:
        state[target] = value
    serialized = json.dumps(state)
    assignment.state_path().write_text(serialized, encoding="utf-8")

    selected = assignment.allocate_best_effort(
        policy,
        profiles,
        fallback_profile=profiles[0],
        worktree_id="wt-new",
        lane="new",
        generation_key="new:wt-new",
    )

    assert selected.profile == profiles[0]
    assert selected.assignment is None
    assert "state is unavailable" in selected.warning
    _assert_profile_launch_effects(tmp_path, selected.profile)
    assert assignment.state_path().read_text(encoding="utf-8") == serialized


@pytest.mark.parametrize(
    ("target", "value"),
    [
        pytest.param("generation", float("inf"), id="infinite-generation"),
        pytest.param("bag_position", "bad", id="malformed-assignment-position"),
    ],
)
def test_invalid_numeric_state_does_not_break_session_registration(
    assignment_home,
    tmp_path: Path,
    monkeypatch,
    capfd,
    target: str,
    value,
):
    _, tracking_dir = assignment_home
    profiles = _profiles(2)
    record = _record(tmp_path, worktree_id="wt-corrupt-bind")
    tracking.save_record(record, tracking_dir / "wt-corrupt-bind.yaml")
    assignment.allocate(
        _policy(profiles),
        profiles,
        worktree_id="wt-corrupt-bind",
        lane="new",
        generation_key="new:wt-corrupt-bind",
        token="corrupt-bind-token",
    )
    state = json.loads(assignment.state_path().read_text(encoding="utf-8"))
    if target.startswith("bag_"):
        state["assignments"][0][target] = value
    else:
        state[target] = value
    assignment.state_path().write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setenv(
        assignment.ASSIGNMENT_TOKEN_ENV,
        "corrupt-bind-token",
    )

    args = argparse.Namespace(
        worktree_id="wt-corrupt-bind",
        session_id="actual-session",
        cwd=None,
        stdin=False,
        pid=None,
        pane=None,
        launch_id=None,
        assignment_token=None,
        emit_context=False,
        handoff_token=None,
    )
    assert m.cmd_register_session(args) == 0

    loaded = tracking.load_record(tracking_dir / "wt-corrupt-bind.yaml")
    assert any(
        entry.session_id == "actual-session"
        for entry in loaded.sessions or []
    )
    assert assignment.assignment_for_session(loaded, "actual-session") is None
    assert "assignment binding was skipped" in capfd.readouterr().err


def test_lock_timeout_falls_back_without_assignment(
    assignment_home,
    tmp_path: Path,
    monkeypatch,
):
    profiles = _profiles(2)

    class _TimedOutLock:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            raise TimeoutError("contended")

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(tracking, "_RecordLock", _TimedOutLock)
    selected = assignment.allocate_best_effort(
        _policy(profiles),
        profiles,
        fallback_profile=profiles[0],
        worktree_id="wt-contended",
        lane="new",
        generation_key="new:wt-contended",
    )
    assert selected.profile == profiles[0]
    assert selected.assignment is None
    assert "state is unavailable" in selected.warning


def test_new_worktree_survives_optional_assignment_state_failure(
    assignment_home,
    tmp_path: Path,
    monkeypatch,
):
    profiles = _profiles(2)
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    worktree_root = tmp_path / "worktrees"
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test-machine",
        platform="linux",
        repo_name="test-project",
        repos={
            "test-project": cfg.RepoConfig(
                anchor=str(anchor),
                worktree_root=str(worktree_root),
                launch={"linux": ["copilot"]},
            )
        },
        copilot_profiles=profiles,
        profile_assignment=_policy(profiles),
    )
    assignment.state_path().write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(
        m,
        "_prepare_worktree_source",
        lambda *_args, **_kwargs: argparse.Namespace(start_point="HEAD"),
    )
    monkeypatch.setattr(
        m.git_ops,
        "create_worktree",
        lambda _anchor, path, _branch, _start: Path(path).mkdir(
            parents=True, exist_ok=True
        ),
    )
    monkeypatch.setattr(
        m.permissions,
        "clone_permissions",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        m.permissions,
        "add_trusted_folder",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        m,
        "_reconcile_marketplaces_for_checkout",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(m.activity, "log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(m, "_repo_session_env", lambda *_args, **_kwargs: {})

    result = m._create_worktree_core(
        config,
        profile=profiles[0],
        profile_is_explicit=False,
        no_mux=True,
        launch_preflight=m.LaunchPreflight(),
    )

    assert Path(result["worktree"]["path"]).exists()
    assert result["launch"]["cmd"][:3] == ["copilot", "--model", "model-0"]
    assert result["launch"]["env"]["PROFILE_ENV"] == "0"
    assert "profile_assignment" not in result["launch"]


@pytest.mark.parametrize(
    "launch_mode",
    ["assigned", "explicit-profile", "recovery", "system"],
)
def test_invalid_armed_policy_fails_before_worktree_side_effects(
    assignment_home,
    tmp_path: Path,
    monkeypatch,
    launch_mode: str,
):
    profiles = _profiles(2)
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test-machine",
        platform="linux",
        repo_name="test-project",
        repos={
            "test-project": cfg.RepoConfig(
                anchor=str(tmp_path / "anchor"),
                worktree_root=str(tmp_path / "worktrees"),
            )
        },
        copilot_profiles=profiles,
        profile_assignment=_policy(
            profiles,
            error="invalid assignment configuration",
        ),
    )
    prepared = False

    def _prepare(*_args, **_kwargs):
        nonlocal prepared
        prepared = True
        raise AssertionError("source preparation must not run")

    monkeypatch.setattr(m, "_prepare_worktree_source", _prepare)
    with pytest.raises(
        assignment.ProfileAssignmentError,
        match="invalid assignment configuration",
    ):
        m._create_worktree_core(
            config,
            no_mux=True,
            profile=profiles[0] if launch_mode == "explicit-profile" else None,
            recovery=launch_mode == "recovery",
            kind="system" if launch_mode == "system" else "session",
        )
    assert prepared is False


def test_missing_profile_replay_degrades_when_policy_is_disarmed(
    assignment_home,
    tmp_path: Path,
):
    persisted = tracking.ProfileAssignment(
        policy="balanced-default",
        assignment_label="cohort-a",
        selected_profile="renamed-profile",
        bag_generation=0,
        bag_position=0,
        assigned_at="2026-09-01T10:00:00+00:00",
        disposition="bound",
        session_id="session-1",
        lane="new",
        bound_at="2026-09-01T10:00:01+00:00",
    )
    record = _record(tmp_path, worktree_id="wt-cross-machine")
    record.sessions = [
        tracking.SessionEntry(
            session_id="session-1",
            started_at="2026-09-01T10:00:00+00:00",
        )
    ]
    record.profile_assignments = [persisted]
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="other-machine",
        platform="linux",
        repo_name="test-project",
        repos={
            "test-project": cfg.RepoConfig(
                anchor=str(tmp_path),
                worktree_root=str(tmp_path / "worktrees"),
            )
        },
        copilot_profiles=_profiles(2),
        profile_assignment=_policy(_profiles(2), armed=False),
    )

    selected = m._launch_profile_selection(
        config,
        argparse.Namespace(profile=None, recovery=False, copilot_args=[]),
        record,
        lane="new",
        generation_key="new:wt-cross-machine",
        ordinary_profile=config.copilot_profiles[0],
        resume_session="session-1",
    )
    assert selected.profile == config.copilot_profiles[0]
    assert selected.assignment is None
    assert "unavailable on this machine" in selected.warning
    _assert_profile_launch_effects(tmp_path, selected.profile)


def test_abandoned_expiry_consumes_position_and_history_is_bounded(
    assignment_home,
    tmp_path: Path,
):
    _, tracking_dir = assignment_home
    profiles = _profiles(3)
    policy = _policy(profiles)
    record = _record(tmp_path, worktree_id="wt-expire")
    tracking.save_record(record, tracking_dir / "wt-expire.yaml")
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    first = assignment.allocate(
        policy,
        profiles,
        worktree_id="wt-expire",
        lane="new",
        generation_key="new:wt-expire",
        now=start,
        seed="expiry-seed",
        token="expired-token",
    )
    assert assignment.expire_pending(
        now=start + timedelta(minutes=16),
        ttl_seconds=900,
        history_limit=2,
    ) == 1
    loaded = tracking.load_record(tracking_dir / "wt-expire.yaml")
    assert loaded.profile_assignments[-1].disposition == "abandoned"

    second = assignment.allocate(
        policy,
        profiles,
        worktree_id="wt-next",
        lane="new",
        generation_key="new:wt-next",
        now=start + timedelta(minutes=16),
        token="next-token",
        history_limit=2,
    )
    assert first.assignment is not None and second.assignment is not None
    assert first.assignment.bag_position == 0
    assert second.assignment.bag_position == 1
    assignment.bind(
        "next-token",
        "session-next",
        "wt-next",
        now=start + timedelta(minutes=16),
    )
    assignment.allocate(
        policy,
        profiles,
        worktree_id="wt-third",
        lane="new",
        generation_key="new:wt-third",
        now=start + timedelta(minutes=17),
        token="third-token",
        history_limit=2,
    )
    state = json.loads(assignment.state_path().read_text(encoding="utf-8"))
    assert len(state["assignments"]) <= 2


def test_compaction_preserves_pending_assignments_until_terminal(
    assignment_home,
    monkeypatch,
):
    profiles = _profiles(4)
    policy = _policy(profiles)
    monkeypatch.setattr(assignment, "DEFAULT_HISTORY_LIMIT", 2)
    selections = [
        assignment.allocate(
            policy,
            profiles,
            worktree_id=f"wt-pending-{index}",
            lane="new",
            generation_key=f"new:wt-pending-{index}",
            token=f"pending-token-{index}",
            history_limit=2,
        )
        for index in range(3)
    ]

    state = json.loads(assignment.state_path().read_text(encoding="utf-8"))
    assert len(state["assignments"]) == 3
    assert all(
        item["disposition"] == "pending"
        for item in state["assignments"]
    )

    retry = assignment.allocate(
        policy,
        profiles,
        worktree_id="wt-pending-0",
        lane="new",
        generation_key="new:wt-pending-0",
        token="ignored-retry-token",
        history_limit=2,
    )
    assert retry.assignment == selections[0].assignment
    assert retry.launch_token == "pending-token-0"

    assignment.bind(
        "pending-token-0",
        "session-pending-0",
        "wt-pending-0",
    )
    compacted = json.loads(
        assignment.state_path().read_text(encoding="utf-8")
    )
    assert len(compacted["assignments"]) == 2
    assert {
        item["worktree_id"] for item in compacted["assignments"]
    } == {"wt-pending-1", "wt-pending-2"}


def test_pending_retry_persists_terminal_history_compaction(
    assignment_home,
):
    profiles = _profiles(4)
    policy = _policy(profiles)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for index in range(3):
        token = f"terminal-token-{index}"
        assignment.allocate(
            policy,
            profiles,
            worktree_id=f"wt-terminal-{index}",
            lane="new",
            generation_key=f"new:wt-terminal-{index}",
            now=start + timedelta(seconds=index),
            token=token,
            history_limit=10,
        )
        assignment.bind(
            token,
            f"session-terminal-{index}",
            f"wt-terminal-{index}",
            now=start + timedelta(seconds=index, milliseconds=500),
        )
    pending = assignment.allocate(
        policy,
        profiles,
        worktree_id="wt-pending-retry",
        lane="new",
        generation_key="new:wt-pending-retry",
        now=start + timedelta(seconds=4),
        token="pending-retry-token",
        history_limit=10,
    )
    assert pending.assignment is not None

    retry = assignment.allocate(
        policy,
        profiles,
        worktree_id="wt-pending-retry",
        lane="new",
        generation_key="new:wt-pending-retry",
        now=start + timedelta(seconds=5),
        token="ignored-retry-token",
        history_limit=2,
    )

    assert retry.assignment == pending.assignment
    assert retry.launch_token == "pending-retry-token"
    state = json.loads(assignment.state_path().read_text(encoding="utf-8"))
    assert len(state["assignments"]) == 2
    assert [
        item["worktree_id"] for item in state["assignments"]
    ] == ["wt-terminal-2", "wt-pending-retry"]
    assert state["assignments"][-1]["disposition"] == "pending"


def test_maintenance_without_state_takes_no_lock_or_write(
    assignment_home,
    monkeypatch,
):
    monkeypatch.setattr(
        tracking,
        "_RecordLock",
        lambda *_args, **_kwargs: pytest.fail(
            "maintenance acquired an assignment lock without a state file"
        ),
    )
    monkeypatch.setattr(
        assignment,
        "_write_state",
        lambda *_args, **_kwargs: pytest.fail(
            "maintenance wrote assignment state without a state file"
        ),
    )

    assert assignment.maintain() == 0


def test_unchanged_maintenance_does_not_rewrite_state(
    assignment_home,
    monkeypatch,
):
    profiles = _profiles(2)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    assignment.allocate(
        _policy(profiles),
        profiles,
        worktree_id="wt-live",
        lane="new",
        generation_key="new:wt-live",
        now=start,
        token="live-token",
    )
    writes = 0
    original_write = assignment._write_state

    def _count_write(path, state):
        nonlocal writes
        writes += 1
        original_write(path, state)

    monkeypatch.setattr(assignment, "_write_state", _count_write)

    assert assignment.maintain(now=start + timedelta(minutes=1)) == 0
    assert writes == 0


def test_cache_only_and_cache_hit_lists_skip_assignment_maintenance(
    assignment_home,
    monkeypatch,
):
    assignment.state_path().write_text(
        json.dumps(assignment._empty_state()),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "_list_records_for_args", lambda _args: [])
    monkeypatch.setattr(
        assignment,
        "maintain",
        lambda: pytest.fail("fast list path ran assignment maintenance"),
    )
    monkeypatch.setattr(m, "_json_output", lambda _payload: None)

    base = dict(
        json=True,
        stream=False,
        mux_details=False,
        classify=False,
        all=True,
        tracking_status="all",
        include_other_platforms=False,
        fresh=False,
        worktree_id=None,
        refresh=False,
        glance=False,
        profile_assignment_history=False,
    )
    assert m.cmd_list(argparse.Namespace(**{**base, "cache_only": True})) == 0

    monkeypatch.setattr(m.list_cache, "cache_key", lambda *_args, **_kwargs: "k")
    monkeypatch.setattr(m.list_cache, "note_demand", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        m.list_cache,
        "read_fresh",
        lambda _key: {"worktrees": []},
    )
    monkeypatch.setattr(m, "_status_monitor_enabled", lambda: False)
    monkeypatch.setattr(cfg, "project_name", lambda: "test-project")
    assert m.cmd_list(argparse.Namespace(**{**base, "cache_only": False})) == 0


def test_pending_expiry_runs_while_policy_is_disarmed(
    assignment_home,
    tmp_path: Path,
):
    _, tracking_dir = assignment_home
    profiles = _profiles(2)
    tracking.save_record(
        _record(tmp_path, worktree_id="wt-disarmed-expiry"),
        tracking_dir / "wt-disarmed-expiry.yaml",
    )
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    assignment.allocate(
        _policy(profiles),
        profiles,
        worktree_id="wt-disarmed-expiry",
        lane="new",
        generation_key="new:wt-disarmed-expiry",
        now=start,
        token="expires-after-disarm",
    )

    assert assignment.maintain(
        now=start + timedelta(minutes=16),
    ) == 1
    state = json.loads(assignment.state_path().read_text(encoding="utf-8"))
    assert state["assignments"][0]["disposition"] == "abandoned"
    assert "launch_token" not in state["assignments"][0]


def test_record_metadata_and_legacy_loading_are_backward_compatible(
    assignment_home,
    tmp_path: Path,
):
    _, tracking_dir = assignment_home
    legacy = _record(tmp_path, worktree_id="wt-legacy")
    legacy_path = tracking_dir / "wt-legacy.yaml"
    tracking.save_record(legacy, legacy_path)
    original = legacy_path.read_text(encoding="utf-8")
    assert "profile_assignment" not in original
    loaded = tracking.load_record(legacy_path)
    assert loaded.profile_assignments == []
    tracking.save_record(loaded, legacy_path)
    assert legacy_path.read_text(encoding="utf-8") == original

    assigned = tracking.ProfileAssignment(
        policy="balanced-default",
        assignment_label="cohort-a",
        selected_profile="profile-0",
        bag_generation=2,
        bag_position=4,
        assigned_at="2026-09-01T10:00:00+00:00",
        disposition="bound",
        session_id="session-1",
        lane="new",
        bound_at="2026-09-01T10:00:01+00:00",
    )
    pending = tracking.ProfileAssignment(
        policy="balanced-default",
        assignment_label="cohort-a",
        selected_profile="profile-1",
        bag_generation=2,
        bag_position=5,
        assigned_at="2026-09-01T10:01:00+00:00",
        disposition="pending",
        lane="new",
    )
    loaded.profile_assignment_revision = 1
    loaded.profile_assignments = [assigned, pending]
    loaded.head_session = "session-1"
    loaded.sessions = [
        tracking.SessionEntry(
            session_id="session-1",
            started_at="2026-09-01T10:00:00+00:00",
        )
    ]
    data = m._worktree_to_dict(loaded)
    assert "profile_assignment" not in data
    assert data["current_profile_assignment"]["selected_profile"] == "profile-0"
    assert data["current_profile_assignment"]["session_id"] == "session-1"
    assert "profile_assignments" not in data
    assert "latest_profile_assignment" not in data
    detailed = m._worktree_to_dict(
        loaded,
        include_profile_assignment_history=True,
    )
    assert detailed["latest_profile_assignment"]["disposition"] == "pending"
    assert len(detailed["profile_assignments"]) == 2
    assert "launch_token" not in json.dumps(data)
    tracking.save_record(loaded, legacy_path)
    assert "launch_token" not in legacy_path.read_text(encoding="utf-8")


def test_ordinary_worktree_row_payload_does_not_scale_with_assignment_history(
    assignment_home,
    tmp_path: Path,
):
    record = _record(tmp_path, worktree_id="wt-bounded-row")
    record.head_session = "session-127"
    record.sessions = [
        tracking.SessionEntry(
            session_id="session-127",
            started_at="2026-09-01T10:00:00+00:00",
        )
    ]
    record.profile_assignments = [
        tracking.ProfileAssignment(
            policy="balanced-default",
            assignment_label="cohort-a",
            selected_profile=f"profile-{index % 6}",
            bag_generation=index // 6,
            bag_position=index % 6,
            assigned_at=f"2026-09-01T10:{index // 60:02d}:{index % 60:02d}+00:00",
            disposition="bound",
            session_id=f"session-{index}",
            lane="new",
            bound_at=f"2026-09-01T10:{index // 60:02d}:{index % 60:02d}+00:00",
        )
        for index in range(128)
    ]

    ordinary = m._worktree_to_dict(record)
    detailed = m._worktree_to_dict(
        record,
        include_profile_assignment_history=True,
    )

    assert ordinary["current_profile_assignment"]["session_id"] == "session-127"
    assert "profile_assignments" not in ordinary
    assert "latest_profile_assignment" not in ordinary
    assert len(json.dumps(ordinary)) < 2_000
    assert len(detailed["profile_assignments"]) == 128


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("bag_generation", float("nan"), id="nan-generation"),
        pytest.param("bag_position", "bad", id="malformed-position"),
        pytest.param(
            "profile_assignment_revision",
            tracking.MAX_PERSISTED_COUNTER + 1,
            id="oversized-revision",
        ),
    ],
)
def test_corrupt_numeric_record_assignment_metadata_is_ignored(
    assignment_home,
    tmp_path: Path,
    field: str,
    value,
):
    _, tracking_dir = assignment_home
    path = tracking_dir / "wt-corrupt-record.yaml"
    record = _record(tmp_path, worktree_id="wt-corrupt-record")
    record.profile_assignment_revision = 1
    record.profile_assignments = [
        tracking.ProfileAssignment(
            policy="balanced-default",
            assignment_label="cohort-a",
            selected_profile="profile-0",
            bag_generation=0,
            bag_position=0,
            assigned_at="2026-09-01T10:00:00+00:00",
        )
    ]
    tracking.save_record(record, path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if field == "profile_assignment_revision":
        data[field] = value
    else:
        data["profile_assignments"][0][field] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    loaded = tracking.load_record(path)

    assert loaded.worktree_id == "wt-corrupt-record"
    assert loaded.profile_assignments == []
    if field == "profile_assignment_revision":
        assert loaded.profile_assignment_revision == 0


def test_selected_profile_remains_an_ordinary_launch_profile():
    config = cfg.Config(
        srcroot="/src",
        machine="machine",
        platform="linux",
        repo_name="repo",
        repos={
            "repo": cfg.RepoConfig(
                anchor="/repo",
                worktree_root="/worktrees",
                launch={"linux": ["copilot"]},
            )
        },
    )
    profile = cfg.CopilotProfile(
        name="synthetic",
        label="Synthetic",
        env={"PROFILE_ENV": "1"},
        copilot_args=[
            "--model",
            "example-model",
            "--reasoning-effort",
            "medium",
            "--context-tier",
            "default",
        ],
    )
    command = m._build_launch_cmd(
        config,
        argparse.Namespace(copilot_args=[], recovery=False),
        "/worktrees/wt",
        profile=profile,
    )
    assert command[:1] == ["copilot"]
    assert profile.copilot_args == command[1:-1]

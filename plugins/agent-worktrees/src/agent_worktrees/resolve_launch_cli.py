"""Launch-planning helpers for the ``resolve`` command family."""

from __future__ import annotations

import argparse
import dataclasses
import os
import secrets
from datetime import datetime
from pathlib import Path

from . import activity, codename_tracking, git_ops, output, profile_assignment, sessions, tracking
from . import config as cfg


def _core():
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = getattr(_core(), name, None)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _build_env(*args, **kwargs):
    return _core()._build_env(*args, **kwargs)


def _build_launch_cmd(*args, **kwargs):
    return _core()._build_launch_cmd(*args, **kwargs)


def _coordination_failure_type():
    return getattr(_core(), "CoordinationReadinessFailure")


def _create_worktree_core(*args, **kwargs):
    return _core()._create_worktree_core(*args, **kwargs)


def _emit_coordination_rejection(*args, **kwargs):
    return _core()._emit_coordination_rejection(*args, **kwargs)


def _emit_parent_context_hint(*args, **kwargs):
    return _core()._emit_parent_context_hint(*args, **kwargs)


def _emit_plan(*args, **kwargs):
    return _core()._emit_plan(*args, **kwargs)


def _launch_preflight_error(*args, **kwargs):
    return _core()._launch_preflight_error(*args, **kwargs)


def _perform_remux(*args, **kwargs):
    return _core()._perform_remux(*args, **kwargs)


def _preflight_launch(*args, **kwargs):
    return _core()._preflight_launch(*args, **kwargs)


def _repo_session_env(*args, **kwargs):
    return _core()._repo_session_env(*args, **kwargs)


def _worktree_to_dict(*args, **kwargs):
    return _core()._worktree_to_dict(*args, **kwargs)


def _session_bind_project_key() -> str:
    return getattr(_core(), "_SESSION_BIND_PROJECT")


def _session_bind_session_key() -> str:
    return getattr(_core(), "_SESSION_BIND_SESSION")


def _session_bind_worktree_key() -> str:
    return getattr(_core(), "_SESSION_BIND_WORKTREE")


@dataclasses.dataclass(slots=True)
class ResolveLaunchContext:
    """Explicit shared state for ``resolve`` launch sub-flows."""

    config: cfg.Config
    args: argparse.Namespace
    profile: cfg.CopilotProfile | None = None
    profile_is_explicit: bool = True
    record: tracking.WorktreeRecord | None = None
    launch_preflight: object | None = None


def _resolve_profile(
    config: cfg.Config,
    args: argparse.Namespace,
) -> cfg.CopilotProfile | None:
    """Resolve --profile flag to a CopilotProfile object."""
    requested = getattr(args, "profile", None)
    if not requested:
        return None
    profiles = config.copilot_profiles or [cfg.DEFAULT_PROFILE]
    for profile in profiles:
        if profile.name == requested:
            return profile
    return None


def _picker_profile_choice(
    profiles: list[cfg.CopilotProfile],
    *,
    assignment_armed: bool,
    profile_idx: int,
) -> tuple[cfg.CopilotProfile, cfg.CopilotProfile | None]:
    """Return the ordinary picker profile and any manual authority override."""
    if assignment_armed and profile_idx == 0:
        return profiles[0], None
    selected = profiles[profile_idx - 1 if assignment_armed else profile_idx]
    return selected, selected


def _validate_profile_assignment_config(config: cfg.Config) -> None:
    """Validate authoritative assignment config before launch-class filtering."""
    profile_assignment.validate_policy(
        getattr(config, "profile_assignment", None),
        getattr(config, "copilot_profiles", []),
    )


def _launch_profile_selection(
    config: cfg.Config,
    args: argparse.Namespace,
    record: tracking.WorktreeRecord | None,
    *,
    lane: str,
    generation_key: str,
    ordinary_profile: cfg.CopilotProfile | None = None,
    explicit_profile: cfg.CopilotProfile | None = None,
    resume_session: str | None = None,
    predecessor_session: str | None = None,
    allocate_new: bool = True,
) -> profile_assignment.LaunchProfileSelection:
    """Resolve manual, replayed, or newly allocated launch profile identity."""
    policy = getattr(config, "profile_assignment", None)
    _validate_profile_assignment_config(config)
    if explicit_profile is not None or getattr(args, "profile", None):
        return profile_assignment.LaunchProfileSelection(profile=explicit_profile)
    if getattr(args, "recovery", False) or getattr(args, "emergency", False):
        return profile_assignment.LaunchProfileSelection(profile=ordinary_profile)
    if record is None:
        return profile_assignment.LaunchProfileSelection(profile=ordinary_profile)
    copilot_args = getattr(args, "copilot_args", []) or []
    if "--acp" in copilot_args or getattr(record, "resolved_interface", "cli") != "cli":
        return profile_assignment.LaunchProfileSelection(profile=ordinary_profile)
    if (
        getattr(record, "resolved_origin", "user") != "user"
        or getattr(record, "kind", "session") != "session"
    ):
        return profile_assignment.LaunchProfileSelection(profile=ordinary_profile)
    if resume_session:
        return profile_assignment.replay(
            profile_assignment.assignment_for_session(record, resume_session),
            getattr(config, "copilot_profiles", []),
            fallback_profile=ordinary_profile,
        )
    if not allocate_new:
        return profile_assignment.LaunchProfileSelection(profile=ordinary_profile)
    if policy is None:
        return profile_assignment.LaunchProfileSelection(profile=ordinary_profile)
    if not policy.armed:
        return profile_assignment.LaunchProfileSelection(profile=ordinary_profile)
    return profile_assignment.allocate_best_effort(
        policy,
        getattr(config, "copilot_profiles", []),
        fallback_profile=ordinary_profile,
        worktree_id=getattr(record, "worktree_id", generation_key),
        lane=lane,
        generation_key=generation_key,
        predecessor_session_id=predecessor_session,
    )


def _apply_assignment_env(
    env: dict[str, str],
    selection: profile_assignment.LaunchProfileSelection,
) -> dict[str, str]:
    """Add the durable assignment token to the launched session environment."""
    if not selection.launch_token:
        return env
    merged = dict(env)
    merged[profile_assignment.ASSIGNMENT_TOKEN_ENV] = selection.launch_token
    return merged


def _reflect_assignment(
    record: tracking.WorktreeRecord,
    selection: profile_assignment.LaunchProfileSelection,
) -> None:
    """Reflect a just-persisted assignment in an already-loaded record object."""
    assignment = selection.assignment
    if assignment is None:
        return
    identity = (
        assignment.policy,
        assignment.bag_generation,
        assignment.bag_position,
        assignment.assigned_at,
    )
    record.profile_assignments = [
        item
        for item in record.profile_assignments
        if (
            item.policy,
            item.bag_generation,
            item.bag_position,
            item.assigned_at,
        )
        != identity
    ]
    record.profile_assignments.append(assignment)


def _dispatch_validate_profile_assignment_config(config: cfg.Config) -> None:
    return _core_helper(
        "_validate_profile_assignment_config", _validate_profile_assignment_config
    )(config)


def _dispatch_launch_profile_selection(*args, **kwargs):
    return _core_helper("_launch_profile_selection", _launch_profile_selection)(*args, **kwargs)


def _dispatch_reflect_assignment(
    record: tracking.WorktreeRecord,
    selection: profile_assignment.LaunchProfileSelection,
) -> None:
    return _core_helper("_reflect_assignment", _reflect_assignment)(record, selection)


def _dispatch_apply_assignment_env(
    env: dict[str, str],
    selection: profile_assignment.LaunchProfileSelection,
) -> dict[str, str]:
    return _core_helper("_apply_assignment_env", _apply_assignment_env)(env, selection)


def _resolve_base_repo(
    config: cfg.Config,
    args: argparse.Namespace,
    profile: cfg.CopilotProfile | None = None,
) -> int:
    """Resolve launch plan for base repo mode."""
    try:
        _dispatch_validate_profile_assignment_config(config)
    except profile_assignment.ProfileAssignmentError as exc:
        output.err(str(exc))
        _emit_plan({"action": "error", "error": str(exc), "exit_code": 3})
        return 3
    repo = config.default_repo
    launch_preflight = _preflight_launch(config, args, repo.anchor)
    if launch_preflight.error:
        return _launch_preflight_error(launch_preflight)
    print()
    print("📂 Base Repo Mode -- No Worktree")
    print(f"   Path: {repo.anchor}")
    print()
    output.warn("Commits will go directly to the current branch.")
    print()

    dirty = git_ops.get_dirty_files(repo.anchor) if os.isatty(0) else []
    if dirty:
        output.warn(f"Anchor repo has {len(dirty)} uncommitted change(s):")
        for path in dirty[:5]:
            print(f"     {path}")
        if len(dirty) > 5:
            print(f"     ... and {len(dirty) - 5} more")
        print()

    launch_cmd = _build_launch_cmd(
        config,
        args,
        repo.anchor,
        profile=profile,
        preflight=launch_preflight,
    )
    merged_env = _build_env(profile, _repo_session_env(config, repo.anchor), work_dir=repo.anchor)
    if args.dry_run:
        output.dry_run(f"Would launch: {' '.join(launch_cmd)}")
        if merged_env:
            env_str = ", ".join(f"{key}={value}" for key, value in merged_env.items())
            output.dry_run(f"Would set env: {env_str}")
        _emit_plan({"action": "none", "exit_code": 0})
        return 0

    _emit_plan(
        {
            "action": "exec",
            "work_dir": repo.anchor,
            "cmd": launch_cmd,
            "env": merged_env,
            "worktree_id": None,
            "post_exit": False,
            "no_mux": getattr(args, "no_mux", False),
        }
    )
    return 0


def _resolve_resume(
    record: tracking.WorktreeRecord,
    config: cfg.Config,
    args: argparse.Namespace,
    profile: cfg.CopilotProfile | None = None,
    profile_is_explicit: bool = True,
    launch_preflight=None,
) -> int:
    """Resolve launch plan for resuming an existing worktree."""
    return _resolve_resume_context(
        ResolveLaunchContext(
            config=config,
            args=args,
            profile=profile,
            profile_is_explicit=profile_is_explicit,
            record=record,
            launch_preflight=launch_preflight,
        )
    )


def _resolve_resume_context(context: ResolveLaunchContext) -> int:
    config = context.config
    args = context.args
    record = context.record
    assert record is not None

    try:
        _dispatch_validate_profile_assignment_config(config)
    except profile_assignment.ProfileAssignmentError as exc:
        output.err(str(exc))
        _emit_plan({"action": "error", "error": str(exc), "exit_code": 3})
        return 3
    print()
    print(f"🌳 Resuming worktree: {record.worktree_id}")
    print(f"   Path: {record.worktree_path}")

    interactive = not getattr(args, "json", False) and not getattr(args, "base", False)
    verdict = None
    if interactive and not args.dry_run:
        try:
            verdict = sessions.verify_worktree_active(record)
        except Exception:
            verdict = None
        if verdict is not None and verdict.mux_live:
            if getattr(args, "no_mux", False) or getattr(args, "bare_resume", False):
                print(
                    "   ↻ Live mux session found -- reattaching it "
                    "(overriding the requested launch mode)."
                )
            args.no_mux = False
            args.bare_resume = False

    bare_resume = getattr(args, "bare_resume", False)
    plan_work_dir = os.path.expanduser("~") if bare_resume else record.worktree_path
    launch_preflight = context.launch_preflight or _preflight_launch(config, args, plan_work_dir)
    if launch_preflight.error:
        return _launch_preflight_error(launch_preflight)

    with tracking._RecordLock(record.yaml_path):
        fresh = tracking.load_record(record.yaml_path)
        tracking.mark_resumed(fresh, save=False)
        tracking.save_record(fresh)
    record.resume_count = fresh.resume_count
    record.last_resumed_at = fresh.last_resumed_at
    if hasattr(fresh, "codename") and not fresh.codename:
        try:
            fresh = codename_tracking.ensure_codename(
                fresh,
                cfg.tracking_dir(),
                codename_tracking.wordlist_for_repo(config),
                **codename_tracking.allocation_policy_kwargs_for_repo(config),
            )
        except codename_tracking.CodenameAttributionPolicyError as exc:
            output.err(str(exc))
            _emit_plan({"action": "error", "error": str(exc), "exit_code": 3})
            return 3
        record.codename = fresh.codename
        record.codename_source = fresh.codename_source

    activity.log_event(
        "worktree_resumed",
        worktree_id=record.worktree_id,
        branch=record.branch,
        resume_count=record.resume_count,
    )

    if verdict is not None:
        try:
            tracking.stamp_mux_live(record.worktree_id, verdict.mux_live, refresh=True)
            tracking.stamp_bound_live(
                record.worktree_id, bool(verdict.live_session_ids), refresh=True
            )
        except Exception:
            pass

    if (
        not args.dry_run
        and getattr(config, "auto_fast_forward", True)
        and not getattr(args, "no_fast_forward", False)
    ):
        repo = config.default_repo
        ff = git_ops.fast_forward_worktree(
            record.worktree_path,
            remote=repo.remote,
            default_branch=repo.default_branch,
            do_fetch=True,
        )
        if ff.updated:
            plural = "s" if ff.behind != 1 else ""
            print(
                f"   ⬆ Fast-forwarded {ff.behind} commit{plural} to "
                f"{repo.remote}/{repo.default_branch}"
            )
        elif ff.reason in ("ahead", "diverged"):
            print(f"   ⚠ Local commits present -- skipping auto-update ({ff.reason})")

    resume_target = None
    if not getattr(args, "no_resume", False):
        resume_target = sessions.resolve_resume_target(record)
    try:
        selection = _dispatch_launch_profile_selection(
            config,
            args,
            record,
            lane="new",
            generation_key=f"new:{record.worktree_id}",
            ordinary_profile=context.profile,
            explicit_profile=context.profile if context.profile_is_explicit else None,
            resume_session=resume_target,
            allocate_new=(not args.dry_run and not bool(verdict and verdict.mux_live)),
        )
    except profile_assignment.ProfileAssignmentError as exc:
        output.err(str(exc))
        _emit_plan({"action": "error", "error": str(exc), "exit_code": 3})
        return 3
    _dispatch_reflect_assignment(record, selection)

    launch_cmd = _build_launch_cmd(
        config,
        args,
        record.worktree_path,
        profile=selection.profile,
        preflight=launch_preflight,
    )
    merged_env = _dispatch_apply_assignment_env(
        _build_env(
            selection.profile,
            _repo_session_env(config, record.worktree_path),
            work_dir=record.worktree_path,
        ),
        selection,
    )

    if bare_resume:
        launch_cmd = _build_launch_cmd(
            config,
            args,
            plan_work_dir,
            profile=selection.profile,
            preflight=launch_preflight,
        )
        merged_env = _dispatch_apply_assignment_env(
            _build_env(
                selection.profile,
                _repo_session_env(config, plan_work_dir),
                work_dir=plan_work_dir,
            ),
            selection,
        )

    no_resume = getattr(args, "no_resume", False) or bare_resume
    if not no_resume:
        if resume_target:
            launch_cmd.append(f"--resume={resume_target}")
            print(f"   Resuming session: {resume_target[:12]}…")
        else:
            _emit_parent_context_hint(record)
    elif bare_resume:
        print(
            f"   Bare resume: launching Copilot in {plan_work_dir} "
            f"(no auto-resume, dodges the worktree-cwd start bug)."
        )
        if resume_target:
            print(f"   Inside Copilot, run:  /resume {resume_target}")

    if bare_resume and resume_target:
        merged_env = dict(merged_env)
        merged_env[_session_bind_project_key()] = config.repo_name
        merged_env[_session_bind_worktree_key()] = record.worktree_id
        merged_env[_session_bind_session_key()] = resume_target

    print()

    if args.dry_run:
        output.dry_run(f"Would launch: {' '.join(launch_cmd)}")
        if merged_env:
            env_str = ", ".join(f"{key}={value}" for key, value in merged_env.items())
            output.dry_run(f"Would set env: {env_str}")
        _emit_plan({"action": "none", "exit_code": 0})
        return 0

    plan = {
        "action": "exec",
        "work_dir": plan_work_dir,
        "status_path": record.worktree_path,
        "cmd": launch_cmd,
        "env": merged_env,
        "worktree_id": record.worktree_id,
        "post_exit": True,
        "no_mux": getattr(args, "no_mux", False),
        "project": config.repo_name,
    }
    if selection.assignment is not None:
        plan["profile_assignment"] = profile_assignment.metadata(selection.assignment)
    _emit_plan(plan)
    return 0


def _resolve_new(
    config: cfg.Config,
    args: argparse.Namespace,
    profile: cfg.CopilotProfile | None = None,
    profile_is_explicit: bool = True,
) -> int:
    """Resolve launch plan for creating a new worktree."""
    return _resolve_new_context(
        ResolveLaunchContext(
            config=config,
            args=args,
            profile=profile,
            profile_is_explicit=profile_is_explicit,
        )
    )


def _resolve_new_context(context: ResolveLaunchContext) -> int:
    config = context.config
    args = context.args

    try:
        _dispatch_validate_profile_assignment_config(config)
    except profile_assignment.ProfileAssignmentError as exc:
        output.err(str(exc))
        _emit_plan({"action": "error", "error": str(exc), "exit_code": 3})
        return 3
    repo = config.default_repo
    platform_name = cfg.detect_platform()
    plat_short = "win" if platform_name == "windows" else platform_name

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)
    worktree_id = f"{config.machine}-{plat_short}-{timestamp}-{suffix}"
    branch = f"worktree/{worktree_id}"
    worktree_path = str(Path(repo.worktree_root) / worktree_id)

    print()
    print(f"🌳 {config.repo_name.replace('-', ' ').title()} -- New Worktree")
    print(f"   Worktree: {worktree_id}")
    print(f"   Path:     {worktree_path}")
    print()

    launch_preflight = _preflight_launch(config, args, worktree_path)
    if launch_preflight.error:
        return _launch_preflight_error(launch_preflight)

    if args.dry_run:
        output.dry_run(f"Would fetch from {repo.remote}")
        output.dry_run(f"Would create worktree at {worktree_path} on branch {branch}")
        output.dry_run("Would write tracking YAML")
        output.dry_run("Would clone permissions")
        output.dry_run("Would add worktree path to trustedFolders")
        launch_cmd = _build_launch_cmd(
            config,
            args,
            worktree_path,
            profile=context.profile,
            preflight=launch_preflight,
        )
        merged_env = _build_env(
            context.profile,
            _repo_session_env(config, worktree_path),
            work_dir=worktree_path,
        )
        output.dry_run(f"Would launch: {' '.join(launch_cmd)}")
        if merged_env:
            env_str = ", ".join(f"{key}={value}" for key, value in merged_env.items())
            output.dry_run(f"Would set env: {env_str}")
        print()
        output.ok("Dry run complete -- no changes made")
        _emit_plan({"action": "none", "exit_code": 0})
        return 0

    try:
        result = _create_worktree_core(
            config,
            profile=context.profile,
            profile_is_explicit=context.profile_is_explicit,
            no_mux=getattr(args, "no_mux", False),
            parent_session=getattr(args, "parent_session", None),
            caller_worktree=getattr(args, "caller_worktree", None),
            owner_ref=getattr(args, "owner_ref", None),
            launch_preflight=launch_preflight,
            recovery=getattr(args, "recovery", False),
        )
    except _coordination_failure_type() as exc:
        _emit_coordination_rejection(exc.readiness, json_out=False)
        _emit_plan(
            {
                "action": "error",
                "error": exc.readiness.error,
                "code": exc.readiness.code,
                "exit_code": 3,
            }
        )
        return 3
    except profile_assignment.ProfileAssignmentError as exc:
        output.err(str(exc))
        _emit_plan({"action": "error", "error": str(exc), "exit_code": 3})
        return 3
    _emit_plan(
        {
            "action": "exec",
            **result["launch"],
        }
    )
    return 0

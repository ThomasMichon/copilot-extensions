"""Command-dispatch wrapper for the ``resolve`` launch planner."""

from __future__ import annotations

import argparse
import dataclasses
import platform
import sys
import threading

from . import activity, embody_resume, output, pending_seed as pending_seed_mod, profile_assignment, sessions, tracking, worktree_identity
from . import codename_tracking, config as cfg
from .launch_trace import append_launch_event
from .resolve_picker_cli import ResolvePickerContext, run_legacy_picker


def _core():
    from . import __main__ as core

    return core


def _apply_assignment_env(*args, **kwargs):
    return _core()._apply_assignment_env(*args, **kwargs)


def _build_env(*args, **kwargs):
    return _core()._build_env(*args, **kwargs)


def _build_launch_cmd(*args, **kwargs):
    return _core()._build_launch_cmd(*args, **kwargs)


def _create_worktree_core(*args, **kwargs):
    return _core()._create_worktree_core(*args, **kwargs)


def _emit_parent_context_hint(*args, **kwargs):
    return _core()._emit_parent_context_hint(*args, **kwargs)


def _emit_plan(*args, **kwargs):
    return _core()._emit_plan(*args, **kwargs)


def _emit_remote_plan_for_env(*args, **kwargs):
    return _core()._emit_remote_plan_for_env(*args, **kwargs)


def _heal_stale_anchor_if_self_missing(*args, **kwargs):
    return _core()._heal_stale_anchor_if_self_missing(*args, **kwargs)






def _launch_profile_selection(*args, **kwargs):
    return _core()._launch_profile_selection(*args, **kwargs)


def _new_picker_blocked_by_ssh(*args, **kwargs):
    return _core()._new_picker_blocked_by_ssh(*args, **kwargs)


def _perform_remux(*args, **kwargs):
    return _core()._perform_remux(*args, **kwargs)


def _preflight_launch(*args, **kwargs):
    return _core()._preflight_launch(*args, **kwargs)


def _reflect_assignment(*args, **kwargs):
    return _core()._reflect_assignment(*args, **kwargs)


def _relocate_active_project_for_worktree(*args, **kwargs):
    return _core()._relocate_active_project_for_worktree(*args, **kwargs)


def _repo_session_env(*args, **kwargs):
    return _core()._repo_session_env(*args, **kwargs)


def _resolve_base_repo(*args, **kwargs):
    return _core()._resolve_base_repo(*args, **kwargs)


def _resolve_codename_anywhere(*args, **kwargs):
    return _core()._resolve_codename_anywhere(*args, **kwargs)


def _resolve_new(*args, **kwargs):
    return _core()._resolve_new(*args, **kwargs)


def _run_new_picker(*args, **kwargs):
    return _core()._run_new_picker(*args, **kwargs)


def _resolve_profile(*args, **kwargs):
    return _core()._resolve_profile(*args, **kwargs)


def _resolve_resume(*args, **kwargs):
    return _core()._resolve_resume(*args, **kwargs)


def _restore_before_resume(*args, **kwargs):
    return _core()._restore_before_resume(*args, **kwargs)


def _try_machine_handoff(*args, **kwargs):
    return _core()._try_machine_handoff(*args, **kwargs)


def _validate_profile_assignment_config(*args, **kwargs):
    return _core()._validate_profile_assignment_config(*args, **kwargs)


def _start_picker_monitor_root(*args, **kwargs):
    return _core()._start_picker_monitor_root(*args, **kwargs)


def _worktree_to_dict(*args, **kwargs):
    return _core()._worktree_to_dict(*args, **kwargs)


@dataclasses.dataclass(slots=True)
class ResolveCommandState:
    """Explicit shared state for ``cmd_resolve``'s sub-flows."""

    args: argparse.Namespace
    use_json: bool
    use_base: bool
    use_new: bool
    requested_machine: str | None
    worktree_id: str | None
    config: cfg.Config | None = None

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "ResolveCommandState":
        state = cls(
            args=args,
            use_json=getattr(args, "json", False),
            use_base=getattr(args, "base", False),
            use_new=getattr(args, "new_worktree", False) or getattr(args, "auto", False),
            requested_machine=getattr(args, "machine", None),
            worktree_id=getattr(args, "worktree_id", None),
        )
        state._resolve_codename_selector()
        state._normalize_modes()
        return state

    @property
    def ordinary_picker(self) -> bool:
        return not any(
            (
                self.use_json,
                self.use_base,
                self.use_new,
                self.requested_machine,
                self.worktree_id,
            )
        )

    def load_config(self) -> cfg.Config:
        if self.config is None:
            self.config = cfg.load_config()
        return self.config

    def _resolve_codename_selector(self) -> None:
        codename_arg = getattr(self.args, "codename", None)
        if codename_arg and not self.worktree_id:
            resolved_id, error_message = _resolve_codename_anywhere(codename_arg)
            if resolved_id is None:
                if self.use_json:
                    raise _ResolveEarlyExit(output._json_error(error_message))
                output.err(error_message)
                raise _ResolveEarlyExit(1)
            self.args.worktree_id = resolved_id
            self.worktree_id = resolved_id

    def _normalize_modes(self) -> None:
        if self.use_json:
            self.args.no_mux = True
            selectors = sum(bool(value) for value in (self.worktree_id, self.use_new, self.use_base))
            if selectors != 1:
                raise _ResolveEarlyExit(
                    output._json_error("--json requires exactly one of --worktree-id, --new, or --base")
                )
            if (
                getattr(self.args, "restore", False)
                and not self.requested_machine
                and platform.system() != "Windows"
            ):
                raise _ResolveEarlyExit(
                    output._json_error(
                        "--restore with --json is unsupported on Linux/WSL because a "
                        "successful reptyr adoption must attach the existing mux; run "
                        "`remux` interactively instead"
                    )
                )

        if self.use_base:
            self.args.no_mux = True
            self.args.no_resume = True


class _ResolveEarlyExit(RuntimeError):
    def __init__(self, exit_code: int):
        super().__init__(str(exit_code))
        self.exit_code = exit_code


def add_parsers(sub) -> None:
    """Register the ``resolve`` subcommand."""
    parser = sub.add_parser("resolve", help="Resolve launch plan as JSON (for shell wrappers)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recovery", action="store_true")
    parser.add_argument(
        "--no-resume", action="store_true", help="Don't auto-resume the last Copilot session"
    )
    parser.add_argument(
        "--bare-resume",
        action="store_true",
        help="Two-step restore: create the worktree's mux, but launch "
        "Copilot in the HOME dir with no --resume (dodges a CLI "
        "bug that fails to start Copilot inside a repo/worktree "
        "cwd). Finish with a manual '/resume <id>' inside.",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Before resuming, restore a Stop-unreachable bound "
        "Copilot through the platform remux path",
    )
    parser.add_argument(
        "--no-mux", action="store_true", help="Bypass tmux/psmux multiplexer (launch directly)"
    )
    parser.add_argument(
        "--no-fast-forward",
        action="store_true",
        help="Don't auto-fast-forward a stale clean worktree on resume",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Non-interactive JSON mode (requires --worktree-id or --codename)",
    )
    parser.add_argument(
        "--worktree-id", default=None,
        help="Worktree ID to resolve (required with --json, unless --codename is given)",
    )
    parser.add_argument(
        "--codename", default=None,
        help="Codename to resolve (alternative to --worktree-id)",
    )
    parser.add_argument(
        "--base", action="store_true", help="Resolve for the anchor repo (no picker, no worktree)"
    )
    parser.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--new",
        action="store_true",
        dest="new_worktree",
        help="Create a worktree AND launch an interactive (muxed) "
        "session in it -- for humans and TTY handoffs (refused "
        "without a TTY). Agents/daemons should use "
        "'agent-worktrees create --json' instead (no launch, no mux).",
    )
    parser.add_argument(
        "--bridge",
        action="store_true",
        help="With --new: mark the worktree as agent-bridge-owned "
        "(kind=bridge: hidden from the Picker by default, exempt "
        "from routine cleanup)",
    )
    parser.add_argument("--profile", help="Copilot backend profile name (skips Tab toggle)")
    parser.add_argument("--machine", default=None, help="Target machine name (bypasses machine picker)")
    parser.add_argument(
        "--environment",
        default=None,
        help="With --machine: target environment label (Win/WSL/Linux)",
    )
    parser.add_argument(
        "--target-no-mux",
        action="store_true",
        help="With --json --machine: request a direct remote launch",
    )
    parser.add_argument(
        "--parent-session",
        default=None,
        dest="parent_session",
        help="With --new: session id that originated this worktree's "
        "work, recorded so a later resume restores context (#1029). "
        "Defaults to $COPILOT_AGENT_SESSION_ID.",
    )
    parser.add_argument(
        "--caller-worktree",
        default=None,
        dest="caller_worktree",
        help="With --new: the caller worktree id that requested this "
        "(bridge) worktree, recorded so the Picker can jump back "
        "to it (#2178).",
    )
    parser.add_argument(
        "--owner-ref",
        default=None,
        dest="owner_ref",
        help="With --new: qualified ref "
        "(machine/project/worktree_id[#session]) of the worktree "
        "that owns this one as an outbound resource -- stamps the "
        "new worktree's owner_ref so its finalize settles the "
        "owner's claim (resource-obligation-settlement). For a "
        "bridge spawn, the dispatching (caller) worktree's ref.",
    )
    parser.add_argument(
        "--seed",
        default=None,
        help="With --new or --worktree-id (not supported alongside "
        "--machine or --bare-resume): a prompt delivered as the session's "
        "first (--new) or next (--worktree-id resume) interactive turn "
        "once Copilot is actually ready. With --worktree-id, carried "
        "durably as a `--interactive` argument on the resume launch's own "
        "Copilot command line -- works identically whether the launch is "
        "muxed or --no-mux, since it never depends on a mux pane to type "
        "into. With --new, this command does not launch Copilot itself, "
        "so the seed is only ever persisted on the record (never embedded "
        "in a returned launch plan): delivered and cleared by "
        "`agent-worktrees embody`/`copilot` on a later first attach, or by "
        "a subsequent `resolve --worktree-id` resume re-resolve picking up "
        "the same persisted value.",
    )
    parser.add_argument("copilot_args", nargs="*", default=[])


def cmd_resolve(args: argparse.Namespace) -> int:
    """Resolve a launch plan and emit it as JSON."""
    append_launch_event("resolve_handler_start")
    try:
        state = ResolveCommandState.from_args(args)
    except _ResolveEarlyExit as exc:
        return exc.exit_code

    requested_seed = getattr(state.args, "seed", None)
    if requested_seed and not state.use_new and not state.worktree_id:
        # --seed is valid with --new (persisted onto a newly created
        # record -- resume-prompt-durable-seed-and-mux-fix) and with
        # --worktree-id (appended directly to the resume launch's own argv
        # -- see below); without either, --base and the interactive picker
        # path would otherwise silently succeed and discard the value.
        message = "--seed is only valid with --new or --worktree-id."
        if state.use_json:
            return output._json_error(message)
        output.err(message)
        return 2

    if state.requested_machine and requested_seed:
        # Validated here, before the JSON/non-JSON split: the non-JSON
        # dispatcher checks state.use_new before state.requested_machine
        # (below) and would otherwise silently create/resume a LOCAL
        # seeded worktree instead of honoring (or rejecting) --machine --
        # a confusing result regardless of --seed. The JSON path's own
        # reason still applies too: its remote dispatch relays a naively
        # space-joined command string with zero shell quoting, unsafe for
        # an arbitrary --seed value. Applies to both --new and
        # --worktree-id (resume) targets alike.
        message = (
            "--seed is not yet supported for a remote --machine target; "
            "use --seed on this machine only, or omit --machine."
        )
        if state.use_json:
            return output._json_error(message)
        output.err(message)
        return 2

    if requested_seed and getattr(state.args, "bare_resume", False):
        # --bare-resume deliberately skips seed injection in BOTH resume
        # code paths (it launches Copilot in HOME with no --resume at all,
        # to dodge a cwd-start bug -- there is no resumed conversation, and
        # arguably no well-defined "worktree session," for the seed to
        # join). Without this guard, a caller combining --bare-resume with
        # --seed got a silent, confusing partial success: the command
        # exits 0 but the prompt is quietly dropped. Reject the
        # combination explicitly instead.
        message = "--seed is not supported together with --bare-resume."
        if state.use_json:
            return output._json_error(message)
        output.err(message)
        return 2

    if (
        state.use_new
        and not state.use_json
        and not state.use_base
        and not getattr(args, "no_mux", False)
        and not sys.stdin.isatty()
    ):
        output.err(
            "Refusing '--new' without a TTY: it launches an interactive "
            "tmux/psmux session that a non-interactive caller cannot "
            "attach to (and would leak a terminal + mux session)."
        )
        output.err("To create a worktree programmatically (no launch, no mux):")
        output.err("    agent-worktrees create --json")
        output.err("Then start Copilot in the returned path, or resume later:")
        output.err("    agent-worktrees resolve --json --worktree-id <id>")
        return 2

    with output.stdout_to_stderr():
        base_hint = cfg.peek_base_repo() if state.ordinary_picker else None
        if base_hint is False:
            base_config, is_base_repo = None, False
        else:
            try:
                base_config = state.load_config()
                is_base_repo = base_config.default_repo.base_repo
            except Exception:
                base_config, is_base_repo = None, False
        if is_base_repo and base_config is not None and not state.requested_machine:
            base_profile = _resolve_profile(base_config, args)
            return _resolve_base_repo(base_config, args, profile=base_profile)

        if state.use_json:
            return _resolve_json_mode(state)

        if not state.use_new and not sys.stdin.isatty():
            output.err("No TTY detected and no worktree specified.")
            output.err("To create a worktree programmatically (no launch, no tmux/psmux session):")
            output.err("    agent-worktrees create --json")
            output.err("To resume an existing worktree non-interactively:")
            output.err("    agent-worktrees resolve --json --worktree-id <id>")
            output.err("Run 'agent-worktrees list' to see available worktrees.")
            return 1

        if state.worktree_id:
            return _resolve_noninteractive_worktree(state)

        if state.use_new:
            config = state.load_config()
            profile = _resolve_profile(config, args)
            try:
                return _resolve_new(config, args, profile=profile)
            except codename_tracking.CodenameAttributionPolicyError as exc:
                output.err(str(exc))
                return 1

        if state.requested_machine:
            config = state.load_config()
            if state.requested_machine != config.machine:
                rc = _try_machine_handoff(config, state.requested_machine)
                if rc is not None:
                    return rc

        tracking_path = cfg.tracking_dir()
        tracking_path.mkdir(parents=True, exist_ok=True)
        current_platform = cfg.detect_platform()

        if not _new_picker_blocked_by_ssh():
            def _heal_bg():
                try:
                    _heal_stale_anchor_if_self_missing(cfg.load_config())
                except Exception:
                    pass

            threading.Thread(target=_heal_bg, name="heal-anchor", daemon=True).start()
            picker_root = _start_picker_monitor_root()
            try:
                append_launch_event("picker_dispatch_start")
                return _run_new_picker(None, args)
            finally:
                if picker_root is not None:
                    picker_root.close()

        config = _heal_stale_anchor_if_self_missing(state.load_config())
        state.config = config
        return run_legacy_picker(
            ResolvePickerContext(
                config=config,
                args=args,
                tracking_path=tracking_path,
                current_platform=current_platform,
            )
        )


def _resolve_json_mode(state: ResolveCommandState) -> int:
    try:
        config = state.load_config()
    except Exception as exc:
        return output._json_error(str(exc))

    # Self-targeting (--machine <this machine's own name>) must behave like
    # --machine was never passed -- exactly as the interactive/non-JSON path
    # already does (its own `state.requested_machine != config.machine` guard
    # a few lines up, before calling `_try_machine_handoff`). Without this
    # check, `_emit_remote_plan_for_env` below looks the name up in
    # machines.yaml with no local-machine awareness at all (unlike
    # `_load_remote_machines`, which the interactive path's "Other Machines"
    # menu is built from) and happily emits a real "ssh <this box>" handoff
    # plan for a caller already running on it -- confirmed live: an
    # `agent-worktrees copilot`/Worktree Manager flow invoked with this
    # machine's own alias round-tripped through SSH back to itself instead of
    # resolving locally.
    #
    # BUT a same-machine target can still legitimately name a DIFFERENT
    # --environment (e.g. --machine some-host --environment WSL, invoked
    # from the Windows side of the same physical box) -- exactly the case
    # `_load_remote_machines` itself special-cases ("for the local machine,
    # only environments that differ from the current platform are
    # included"). That worktree lives in a separate OS/filesystem with its
    # own tracking directory, never visible to this process's local
    # `cfg.tracking_dir()`. Treating same-machine-name as proof of "no
    # dispatch needed" regardless of --environment made every same-machine
    # cross-platform resume (e.g. the Windows Worktree Picker launching a
    # WSL worktree) fail closed with a bogus "Worktree not found" instead of
    # SSHing into the requested environment -- confirmed live.
    same_machine = state.requested_machine == getattr(config, "machine", None)
    requested_environment = getattr(state.args, "environment", None) or ""
    same_environment = True
    if requested_environment:
        from . import resolve_machine_cli

        want_platform = resolve_machine_cli._ENV_LABEL_TO_NAME.get(requested_environment.lower())
        same_environment = want_platform is None or want_platform == cfg.detect_platform()
    if state.requested_machine and not (same_machine and same_environment):
        remote_args: list[str] = []
        if state.use_base:
            remote_args.append("--base")
        elif state.use_new:
            remote_args.append("--new")
        else:
            remote_args += ["--worktree-id", str(state.worktree_id)]
            if getattr(state.args, "bare_resume", False):
                remote_args.append("--bare-resume")
            if getattr(state.args, "restore", False):
                remote_args.append("--restore")
        if getattr(state.args, "target_no_mux", False):
            remote_args.append("--no-mux")
        rc = _emit_remote_plan_for_env(
            config,
            state.requested_machine,
            getattr(state.args, "environment", None) or "",
            remote_args,
        )
        if rc is not None:
            return rc
        return output._json_error(
            "unknown or unreachable remote machine: "
            f"{state.requested_machine} "
            f"{getattr(state.args, 'environment', None) or ''}".strip()
        )

    try:
        _validate_profile_assignment_config(config)
    except profile_assignment.ProfileAssignmentError as exc:
        return output._json_error(str(exc), exit_code=3)

    if state.use_base:
        repo = config.default_repo
        work_dir = repo.anchor
        launch_preflight = _preflight_launch(config, state.args, work_dir)
        if launch_preflight.error:
            return output._json_error(launch_preflight.error, exit_code=3)
        launch_cmd = _build_launch_cmd(
            config,
            state.args,
            work_dir,
            preflight=launch_preflight,
        )
        env = _build_env(None, _repo_session_env(config, work_dir), work_dir=work_dir)
        output._json_output(
            {
                "action": "exec",
                "work_dir": work_dir,
                "cmd": launch_cmd,
                "env": env,
                "post_exit": False,
                "no_mux": True,
            }
        )
        return 0

    if state.use_new:
        profile = _resolve_profile(config, state.args)
        try:
            result = _create_worktree_core(
                config,
                profile=profile,
                no_mux=True,
                kind="bridge" if getattr(state.args, "bridge", False) else "session",
                parent_session=getattr(state.args, "parent_session", None),
                caller_worktree=getattr(state.args, "caller_worktree", None),
                owner_ref=getattr(state.args, "owner_ref", None),
                recovery=getattr(state.args, "recovery", False),
                pending_seed=getattr(state.args, "seed", None),
            )
        except getattr(_core(), "CoordinationReadinessFailure") as exc:
            return _core()._emit_coordination_rejection(exc.readiness, json_out=True)
        except RuntimeError as exc:
            return output._json_error(str(exc))
        output._json_output(result)
        return 0

    assert state.worktree_id is not None
    worktree_id = worktree_identity._resolve_worktree_id(state.worktree_id)
    if _relocate_active_project_for_worktree(worktree_id):
        state.config = None
        try:
            config = state.load_config()
        except Exception as exc:
            return output._json_error(str(exc))
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return output._json_error(f"Worktree not found: {worktree_id}")
    record = tracking.load_record(yaml_path)
    launch_preflight = _preflight_launch(config, state.args, record.worktree_path)
    if launch_preflight.error:
        return output._json_error(launch_preflight.error, exit_code=3)
    if getattr(state.args, "restore", False):
        session_id = sessions.find_latest_session_id_fast(record.worktree_path, record.sessions)
        restored = _perform_remux(
            worktree_id=record.worktree_id,
            session_id=session_id,
            worktree_path=record.worktree_path,
            force_sudo=None,
            apply_windows=True,
        )
        if not restored.get("ok"):
            return output._json_error(restored.get("reason", "could not restore the session"))
    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        tracking.mark_resumed(record, save=False)
        tracking.save_record(record)

    activity.log_event(
        "worktree_resumed",
        worktree_id=record.worktree_id,
        branch=record.branch,
        resume_count=record.resume_count,
    )

    no_resume = getattr(state.args, "no_resume", False)
    last_session = None
    if not no_resume:
        last_session = sessions.find_latest_session_id_fast(record.worktree_path, record.sessions)
    explicit_profile = _resolve_profile(config, state.args)
    try:
        selection = _launch_profile_selection(
            config,
            state.args,
            record,
            lane="new",
            generation_key=f"new:{record.worktree_id}",
            explicit_profile=explicit_profile,
            resume_session=last_session,
        )
    except profile_assignment.ProfileAssignmentError as exc:
        return output._json_error(str(exc), exit_code=3)
    _reflect_assignment(record, selection)
    launch_cmd = _build_launch_cmd(
        config,
        state.args,
        record.worktree_path,
        profile=selection.profile,
        preflight=launch_preflight,
    )
    env = _apply_assignment_env(
        _build_env(
            selection.profile,
            _repo_session_env(config, record.worktree_path),
            work_dir=record.worktree_path,
        ),
        selection,
    )

    if last_session:
        launch_cmd.append(f"--resume={last_session}")
    elif not no_resume:
        _emit_parent_context_hint(record, to_stderr=True)

    # Durable seed delivery (resume-prompt-durable-seed-and-mux-fix): a
    # `--interactive` argument on this SAME returned command line, never a
    # mux pane send-keys side-channel -- works identically whether the real
    # launcher (launch-session.{ps1,sh}, which wraps or doesn't wrap this
    # exact `cmd` in a mux pane independently of anything decided here)
    # ends up muxed or `--no-mux`, since there is no pane to target either
    # way. `bare_resume` skips claiming/injecting entirely (mirrors
    # `resolve_launch_cli._resolve_resume_context`'s identical guard) --
    # there is no resumed conversation, and arguably no well-defined
    # "worktree session," for a seed to join, and a persisted
    # `pending_seed` must stay queued for a later real resume rather than
    # being silently consumed here. An explicit `--seed` on this call wins;
    # either way, any record-persisted `pending_seed` (queued at creation
    # time by `resolve --new --seed`, for the Picker's own two-hop
    # new-worktree flow, which re-resolves by --worktree-id here) is
    # claimed (cleared) under the existing race-safe write-guard so
    # `agent-worktrees embody`'s own fallback claim-and-send-keys delivery
    # never finds it again and double-delivers the same turn.
    #
    # Known, accepted scope boundary: claiming happens here, at
    # PLAN-BUILD time -- before the external launcher
    # (launch-session.{ps1,sh}) has actually exec'd this `cmd`. That script
    # still performs its own update/preflight work and (for a muxed launch)
    # mux-session creation AFTER this process already returned; a failure
    # there, before `cmd` ever starts, loses the claimed seed with no
    # restore. Deliberately not solved here: a true fix needs the launcher
    # itself to report "I failed before exec" back through
    # `pending_seed.restore_pending_seed` (the exact primitive `embody`'s
    # own mux-pane delivery already uses for its own post-attempt restore),
    # which means teaching the launcher scripts about this contract --
    # explicitly out of this phase's scope (tracked as a Phase 3 follow-up
    # in this effort's own README). Accepted because `resolve` already
    # performs several other irreversible side effects before returning
    # (`mark_resumed`/`save_record`, activity logging) with the same
    # "the external launcher might still fail after this" exposure, so this
    # is a known risk class for this function, not a new one introduced
    # here, and the alternative (never clearing a pending seed from this
    # function at all) reintroduces real double-delivery on every
    # subsequent successful resume instead of this narrow, infrequent loss
    # window.
    #
    # Live-mux parity with `resolve_launch_cli._resolve_resume_context`:
    # the JSON path is the Picker's own real code path, and both launcher
    # scripts (`launch-session.{sh,ps1}`) probe for an existing live mux
    # session themselves BEFORE this `cmd` would ever run -- if one exists,
    # they reattach it and never exec `cmd` at all, exactly the scenario
    # the non-JSON path's own `verdict.mux_live` branch already guards.
    # Without the identical check here, a seed claimed/embedded above would
    # be silently discarded on every JSON-mode live-mux reattach, which is
    # worse than the non-JSON path's own narrow, queued-but-delayed
    # limitation -- here the seed was never queued at all.
    #
    # A degraded probe is NOT the same as a confirmed "no live mux": it
    # means genuinely unknown, and claiming/embedding the seed on an
    # uncertain verdict risks the exact same silent loss a real live mux
    # would cause. Treat "uncertain" the same as "live" here -- the safer,
    # queue-not-embed branch -- rather than only gating on the narrower
    # `mux_live` flag. Specifically `mux_probe_ok` (the MUX probe's own
    # success), never the aggregate `probes_ok` (which also goes False on
    # an UNRELATED reclaim/lock-probe failure): a reclaim failure with a
    # conclusive "no mux" from the mux probe itself is not mux uncertainty,
    # and wrongly queuing in that case loses the prompt entirely on a
    # `--no-mux` launch (which execs the seedless command directly -- there
    # is no pane to later deliver a queued seed to).
    try:
        verdict = sessions.verify_worktree_active(record)
    except Exception:
        verdict = None
    live_or_uncertain_mux = verdict is None or not getattr(verdict, "mux_probe_ok", True) or getattr(verdict, "mux_live", False)
    seed_claimed = False
    seed_queue_failed = False
    if not getattr(state.args, "bare_resume", False) and not live_or_uncertain_mux:
        explicit_seed = getattr(state.args, "seed", None)
        claimed_seed = pending_seed_mod.claim_pending_seed(yaml_path)
        delivered_seed = explicit_seed or claimed_seed
        if delivered_seed:
            launch_cmd = embody_resume.with_seed(launch_cmd, delivered_seed)
            seed_claimed = True
    elif not getattr(state.args, "bare_resume", False) and live_or_uncertain_mux:
        explicit_seed = getattr(state.args, "seed", None)
        if explicit_seed:
            seed_queue_failed = not pending_seed_mod.set_pending_seed(yaml_path, explicit_seed)
        # A persisted `pending_seed` (no explicit one given) is deliberately
        # left untouched here too -- never claimed, so it stays queued for
        # whatever next attach actually delivers it.

    launch = {
        "action": "exec",
        "work_dir": record.worktree_path,
        "cmd": launch_cmd,
        "env": env,
        "worktree_id": record.worktree_id,
        "post_exit": True,
        "no_mux": True,
        # Explicit provenance: a delegated caller (the Worktree Manager's
        # relocated-launch re-invocation) must not GUESS whether `cmd`'s
        # trailing `--interactive <value>` pair is the seed
        # this call claimed, versus a configured launch/profile argument
        # that coincidentally ends the same way -- `_build_launch_cmd` can
        # legitimately produce either shape. True only when THIS call
        # itself appended the seed via `embody_resume.with_seed` above.
        "seed_claimed": seed_claimed,
    }
    if seed_queue_failed:
        # Honest, non-fatal degradation: `set_pending_seed` failed (lock
        # contention, an unreadable record, or a write failure) while
        # queuing an explicit seed for later delivery on a live-mux
        # reattach. The launch itself still proceeds (losing only the
        # seed, not the whole resume) -- surfaced here so a caller (the
        # Picker) can tell the operator their prompt did not make it in,
        # rather than silently discarding it with no signal at all.
        launch["seed_queue_failed"] = True
    if selection.assignment is not None:
        launch["profile_assignment"] = profile_assignment.metadata(selection.assignment)
    project = config.repo_name
    if project:
        launch["project"] = project
    output._json_output({"worktree": _worktree_to_dict(record), "launch": launch})
    return 0


def _resolve_noninteractive_worktree(state: ResolveCommandState) -> int:
    assert state.worktree_id is not None
    worktree_id = worktree_identity._resolve_worktree_id(state.worktree_id)
    if _relocate_active_project_for_worktree(worktree_id):
        state.config = None
    config = state.load_config()
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        output.err(f"Worktree not found: {worktree_id}")
        return 1
    record = tracking.load_record(yaml_path)
    try:
        _validate_profile_assignment_config(config)
    except profile_assignment.ProfileAssignmentError as exc:
        output.err(str(exc))
        _emit_plan({"action": "error", "error": str(exc), "exit_code": 3})
        return 3
    plan_work_dir = (
        str(__import__("os").path.expanduser("~"))
        if getattr(state.args, "bare_resume", False)
        else record.worktree_path
    )
    launch_preflight = _preflight_launch(config, state.args, plan_work_dir)
    if launch_preflight.error:
        return _core()._launch_preflight_error(launch_preflight)
    if getattr(state.args, "restore", False) and not _restore_before_resume(record):
        return 1
    profile = _resolve_profile(config, state.args)
    return _resolve_resume(
        record,
        config,
        state.args,
        profile=profile,
        launch_preflight=launch_preflight,
    )

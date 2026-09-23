"""Handoff/session embodiment CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from . import config as cfg, finalize as fin, output, profile_assignment, sessions, tracking


def _core():
    from . import __main__ as core
    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


LaunchPreflightError = _core().LaunchPreflightError

def _apply_assignment_env(*args, **kwargs): return _core()._apply_assignment_env(*args, **kwargs)
def _build_env(*args, **kwargs): return _core()._build_env(*args, **kwargs)
def _build_launch_cmd(*args, **kwargs): return _core()._build_launch_cmd(*args, **kwargs)
def _create_worktree_core(*args, **kwargs): return _core()._create_worktree_core(*args, **kwargs)
def _handoff_cutover_retire_result(*args, **kwargs): return _core()._handoff_cutover_retire_result(*args, **kwargs)
def _handoff_cutover_retry_result(*args, **kwargs): return _core()._handoff_cutover_retry_result(*args, **kwargs)
def _handoff_cutover_spawn_result(*args, **kwargs): return _core()._handoff_cutover_spawn_result(*args, **kwargs)
def _json_error(*args, **kwargs): return _core()._json_error(*args, **kwargs)
def _json_output(*args, **kwargs): return _core()._json_output(*args, **kwargs)
def _launch_profile_selection(*args, **kwargs): return _core()._launch_profile_selection(*args, **kwargs)
def _perform_remux(*args, **kwargs): return _core()._perform_remux(*args, **kwargs)
def _preflight_launch(*args, **kwargs): return _core()._preflight_launch(*args, **kwargs)
def _reflect_assignment(*args, **kwargs): return _core()._reflect_assignment(*args, **kwargs)
def _repo_for_record(*args, **kwargs): return _core()._repo_for_record(*args, **kwargs)
def _repo_session_env(*args, **kwargs): return _core()._repo_session_env(*args, **kwargs)
def _resolve_worktree_id(*args, **kwargs): return _core()._resolve_worktree_id(*args, **kwargs)
def _unsupported_hosted_launch(*args, **kwargs): return _core()._unsupported_hosted_launch(*args, **kwargs)
def _pending_handoff_retire_requests(*args, **kwargs): return _core()._pending_handoff_retire_requests(*args, **kwargs)
def _monitor_retire_handoff_predecessor(*args, **kwargs): return _core()._monitor_retire_handoff_predecessor(*args, **kwargs)
def resolve_worktree_id_by_codename(*args, **kwargs): return _core().resolve_worktree_id_by_codename(*args, **kwargs)


def add_parsers(sub) -> None:
    p = sub.add_parser("handoff-cutover", help="Live handoff: spawn a seeded successor Copilot in a new mux window (cut over to it), or retire an old pane")
    p.add_argument("--seed", default=None, help="Seed prompt for the successor's first interactive turn (copilot -i). Required in spawn mode.")
    p.add_argument("--worktree-id", dest="worktree_id", default=None, help="Target worktree (default: infer from cwd)")
    p.add_argument("--session-id", dest="session_id", default=None, help="Resumed session id -- authoritative worktree fallback when cwd is HOME (bare resume); resolves the worktree from the session registry")
    p.add_argument("--handoff-token", default=None, help="Pending handoff token to associate with the successor after its initial prompt creates a real session")
    p.add_argument("--mux-session", dest="mux_session", default=None, help="Retire mode: expected mux session containing the pane; a mismatch is treated as predecessor already gone")
    p.add_argument("--require-mux-identity", action="store_true", help="Retire mode: never signal a pane unless --mux-session was recorded and still matches; reap by session id only")
    p.add_argument("--old-pane", dest="old_pane", default=None, help="Explicit pane id to report as the old pane (default: the session's active pane)")
    p.add_argument("--retire-pane", dest="retire_pane", default=None, help="Retire mode: double-Ctrl-C this pane id (Copilot's clean quit) and report whether it exited")
    p.add_argument("--retry", action="store_true", help="Retry mode: refocus an already-live successor pane for this worktree's latest handoff, otherwise fall back to the ordinary spawn attempt")
    p.add_argument("--headless", action="store_true", help="Spawn mode: launch the successor as a fully detached background process with no mux at all (no pane, no attach target), instead of requiring an already-live mux session for this worktree. For hosts with no multiplexer present -- e.g. a coordinator-driven fallback launch with no human attaching to watch it.")
    p.add_argument("--successor-verified", action="store_true", help="Retire mode: record that the successor invoked the handoff consumer before retiring the old pane")
    p.add_argument("--retire-reason", default=None, help="Retire mode: high-level reason for activity logging")
    p.add_argument("--expected-copilot-pid", type=int, default=None, help="Retire mode: recorded predecessor Copilot pid")
    p.add_argument("--expected-copilot-start-time", default=None, help="Retire mode: recorded predecessor process creation identity")
    p.add_argument("--dry-run", action="store_true", help="Print the resolved plan without opening a window")
    p.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only; always on)")
    p = sub.add_parser("handoff-trace", help="Render the ordered 13-stage handoff trace for one worktree/session (defaults to the most recent attempt unless --token is given)")
    p.add_argument("trace_target", help="Worktree id or session id to inspect")
    p.add_argument("--project", default=None, help="Owning project when running from a neutral cwd or when the id is ambiguous")
    p.add_argument("--token", default=None, help="Specific handoff token to render (default: most recent attempt for the selector)")
    p.add_argument("--json", action="store_true", help="JSON output")
    p = sub.add_parser("handoffs-check", help="Diagnose (and with --execute, finish) a stalled handoff-cutover predecessor retirement -- the on-demand counterpart to the resident status-monitor's automatic sweep")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--worktree-id", dest="worktree_id", default=None, help="Check only this worktree")
    g.add_argument("--all", action="store_true", help="Check every tracked worktree in this project")
    p.add_argument("--execute", action="store_true", help="Retire each found stale predecessor now (default: read-only report)")
    p.add_argument("--json", action="store_true", help="JSON output")
    p = sub.add_parser("embody", help="Create or resume a DETACHED mux+Copilot CLI session in a worktree (the agent-facing embodiment verb; auto-registers with the bridge)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--worktree-id", dest="worktree_id", default=None, help="Embody in this existing worktree")
    g.add_argument("--new", action="store_true", help="Create a fresh worktree first, then embody in it")
    g.add_argument("--codename", default=None, help="Embody the worktree with this codename (pr-attribution-codenames Phase 2/3) -- resolved locally first, then via a cross-machine SSH scan. A codename found on a DIFFERENT machine fails closed with the machine name (remote launch is not supported); resolve/embody there directly instead.")
    g.add_argument("--anchor", action="store_true", help="Embody directly in the active project's ANCHOR checkout instead of any worktree -- no worktree is created or required. Matches the existing headless-dispatch behavior (a CodeSpace/container ACP session already runs in the anchor, never a worktree); this brings CLI-mode into line with that same contract. Session id is synthesized as `anchor-<repo_name>` (one live anchor session per repo, same one-live-session-per-target rule as an ordinary worktree). The anchor is not writable through this harness's own PR-gated flow (see AGENTS.md) -- use for reads/exploration or a repo whose anchor is otherwise safe to drive directly; an actual change still belongs in a worktree/PR.")
    p.add_argument("--seed", default=None, help="Seed prompt injected as the session's first interactive turn once Copilot is ready")
    p.add_argument("--seed-ready-timeout", dest="seed_ready_timeout", type=float, default=180.0, metavar="SECONDS", help="How long to wait for Copilot's input prompt before typing the --seed (default 180). A fresh MCP/skill-heavy autopilot can take much longer than the fast handoff default to become ready; if this is too short the seed is never delivered and the session idles at an empty prompt")
    p.add_argument("--driver", default=None, help="Label of the agent steering this session; stamps the 'driven by <agent>' banner (AGENT_BRIDGE_DRIVEN_BY) so a human taking over in Neuron Forge sees who's at the wheel")
    p.add_argument("--verify-timeout", dest="verify_timeout", type=float, default=0.0, metavar="SECONDS", help="Wait up to N seconds for the mux session to come up before returning (default 0: don't wait)")
    p.add_argument("--recovery", action="store_true", help="Use the repo's recovery launch command")
    p.add_argument("--ensure-mux", dest="ensure_mux", action="store_true", help="Best-effort self-heal a missing tmux/psmux before creating the session (apt-get/dnf/yum/apk, POSIX only). Explicit opt-in only: an operator running a BYO terminal/session manager instead of tmux must never have tmux installed underneath them by an ordinary embody call. Set this only when preparing a venue that has nothing else already managing sessions (e.g. a CLI-mode launch).")
    p.add_argument("--dry-run", action="store_true", help="Print the resolved plan without spawning anything")
    p.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only; always on)")

def cmd_handoff_cutover(args: argparse.Namespace) -> int:
    """Live-cutover handoff: spawn/refocus a successor Copilot or retire a pane.

    Two modes (JSON out on stdout either way):

    * **spawn** (default; needs ``--seed``): reconstruct this worktree's launch
      command (the same ``_build_launch_cmd`` the picker uses) for a **plain
      interactive** Copilot, open + select a NEW window in the worktree's
      ``wt-<id>`` mux session (cutting the operator over). The seed is encoded as
      a space-free pane-wrapper control argument; the wrapper decodes it after
      psmux's lossy argv reconstruction, appends native
      ``-i <seed>``, and writes a receipt before the parent reports
      success. Deliberately omits ``--resume``: a handoff wants a FRESH context
      window seeded by the prompt, not the old transcript replayed. Returns the
      OLD (pre-cutover) pane id so the successor-side handoff consumer can retire
      it after pickup.
    * **retire** (``--retire-pane <id>``): double-Ctrl-C that specific pane
      (Copilot's native clean quit), hard-killing it only if it will not exit.
      Then, when the predecessor ``--session-id`` is known, **verify its Copilot
      process is gone** -- reaping an orphan by its ``inuse.<pid>.lock`` pid (the
      successor is a different pid, untouched) -- so a hard-killed pane never
      leaves a lingering parallel session that the mux later restores as a
      reappearing pane. Success requires **both** the pane retired and the old
      Copilot process gone.
    * **retry** (``--retry``): inspect the most recent handoff on this worktree
      and positively confirm whether its recorded successor session already has
      a live pane in this mux. If so, do **not** spawn again -- just refocus
      that existing window. If not, fall back to the ordinary spawn path.

    The mux choreography lives here (agent-worktrees owns launch + mux); the
    context-handoff extension is a thin trigger that shells out to this command.
    """
    # ── Retire mode ──────────────────────────────────────────────────────
    retire_pane = getattr(args, "retire_pane", None)
    if retire_pane:
        rc, result = _handoff_cutover_retire_result(args)
        _json_output(result)
        return rc

    if getattr(args, "retry", False):
        rc, response = _handoff_cutover_retry_result(args)
        _json_output(response)
        return rc

    # ── Spawn / cutover mode ─────────────────────────────────────────────
    rc, response = _handoff_cutover_spawn_result(args)
    _json_output(response)
    return rc


def _resolve_codename_anywhere(codename_arg: str) -> tuple[str | None, str | None]:
    """Resolve *codename_arg* to a LOCAL worktree id, falling back to a
    cross-machine SSH scan (pr-attribution-codenames Phase 3) when no local
    match exists.

    Returns ``(worktree_id, None)`` on a local match, or ``(None, message)``
    where *message* is a ready-to-surface error string covering all three
    "not usable" outcomes: genuinely unmatched anywhere, matched on exactly
    one OTHER machine (fail-closed -- this never attempts a remote launch,
    only reports where to go), or an ambiguous cross-machine collision.
    """
    resolved_id = resolve_worktree_id_by_codename(codename_arg)
    if resolved_id is not None:
        return resolved_id, None
    from . import codename_reverse_lookup
    try:
        remote = codename_reverse_lookup.resolve_codename_cross_machine_unique(codename_arg)
    except codename_reverse_lookup.AmbiguousCodenameError as exc:
        return None, str(exc)
    if remote is not None:
        return None, (
            f"Codename '{codename_arg}' resolves to worktree "
            f"'{remote.worktree_id}' on machine '{remote.machine}', not this "
            f"machine. Resolve/embody it there directly (e.g. SSH to "
            f"'{remote.machine}') -- remote launch is not supported."
        )
    return None, f"No worktree found with codename '{codename_arg}'"


def cmd_embody(args: argparse.Namespace) -> int:
    """Create or resume a **detached** mux+Copilot CLI session in a worktree (D5).

    The agent-facing "embodiment" verb: spawn a durable, mux-wrapped interactive
    Copilot in a target worktree so it registers with the local bridge (Phase 1)
    and becomes viewable/messageable -- the CLI-first embodiment of
    ``visions/agent-fabric`` §lifetime-decides-embodiment, driven by an agent
    rather than a human at the picker. JSON out on stdout.

    Target selection:
      * ``--worktree-id <id>`` embodies in an existing worktree;
      * ``--new`` creates a fresh worktree first (the outlives-its-caller case).

    Resume semantics: **one live session per worktree**. If a ``wt-<id>`` mux
    session already exists, this does NOT spawn a duplicate -- it reports the
    existing session (``created=false``), honoring "to act in an occupied space,
    interrogate the occupant or embody a fresh space." Otherwise it creates the
    session detached (never attaching -- the operator or Neuron Forge attaches
    later). An optional ``--seed`` is injected as the first interactive turn via
    ``send-keys`` once Copilot is ready. Handoff cutover uses native
    ``-i`` startup instead because its attached successor does not
    need embody's post-launch bridge verification.

    Verification: the caller confirms the embodiment by polling
    ``agent-bridge live-sessions?worktree_id=<id>`` -- the bundled extension
    auto-registers the new session. ``--verify-timeout`` optionally waits here
    for the mux session to come up before returning.
    """
    make_new = getattr(args, "new", False)
    raw_id = getattr(args, "worktree_id", None)
    codename_arg = getattr(args, "codename", None)
    anchor_mode = getattr(args, "anchor", False)
    if codename_arg and not raw_id:
        # pr-attribution-codenames Phase 2/3: --codename is an alternate
        # selector for --worktree-id, resolved locally first then via a
        # cross-machine SSH scan; see _resolve_codename_anywhere.
        resolved_id, error_message = _core_helper(
            "_resolve_codename_anywhere", _resolve_codename_anywhere
        )(codename_arg)
        if resolved_id is None:
            return _json_error(error_message, exit_code=1)
        raw_id = resolved_id
    if make_new and raw_id:
        return _json_error(
            "--new is mutually exclusive with --worktree-id/--codename",
            exit_code=2,
        )
    if anchor_mode and (make_new or raw_id):
        return _json_error(
            "--anchor is mutually exclusive with --worktree-id/--codename/--new",
            exit_code=2,
        )
    if not anchor_mode and not make_new and not raw_id:
        return _json_error(
            "embody requires --worktree-id <id>, --codename <name>, --new, or --anchor",
            exit_code=2,
        )

    try:
        config = cfg.load_config()
    except Exception as e:
        return _json_error(str(e))

    if anchor_mode:
        return _cmd_embody_anchor(args, config)

    # Resolve target worktree id + path (creating a fresh worktree for --new).
    if make_new:
        try:
            with output.stdout_to_stderr():
                created = _create_worktree_core(
                    config,
                    no_mux=True,
                    kind="session",
                    recovery=getattr(args, "recovery", False),
                )
        except LaunchPreflightError as e:
            return _json_error(str(e), exit_code=3)
        except Exception as e:
            return _json_error(f"failed to create worktree: {e}")
        wt_id = created["worktree"]["id"]
        work_dir = created["worktree"]["path"]
    else:
        wt_id = _resolve_worktree_id(raw_id)
        yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
        if not yaml_path.exists():
            return _json_error(f"Worktree not found: {wt_id}")
        work_dir = tracking.load_record(yaml_path).worktree_path

    # Resume: a live mux session already embodies this worktree -- don't
    # duplicate (one live session per cwd). Report it and stop.
    already = sessions.has_mux_session(wt_id)
    seed = getattr(args, "seed", None)
    launch_preflight = None
    if getattr(args, "dry_run", False) or not already:
        launch_preflight = _preflight_launch(config, args, work_dir)
        if launch_preflight.error:
            return _json_error(launch_preflight.error, exit_code=3)

    record = None
    selection = profile_assignment.LaunchProfileSelection(profile=None)
    if not already:
        try:
            record = tracking.load_record(cfg.tracking_dir() / f"{wt_id}.yaml")
        except Exception:
            if not make_new:
                return _json_error(f"Worktree record not found: {wt_id}")
        if record is not None:
            backend_error = _unsupported_hosted_launch(
                record,
                "embody",
            )
            if backend_error:
                return _json_error(backend_error, exit_code=3)
            try:
                selection = _launch_profile_selection(
                    config,
                    args,
                    record,
                    lane="new",
                    generation_key=f"new:{wt_id}",
                    allocate_new=not getattr(args, "dry_run", False),
                )
                _reflect_assignment(record, selection)
            except profile_assignment.ProfileAssignmentError as exc:
                return _json_error(str(exc), exit_code=3)

    if getattr(args, "dry_run", False):
        launch_cmd = _build_launch_cmd(
            config,
            args,
            work_dir,
            profile=selection.profile,
            preflight=launch_preflight,
        )
        _json_output(
            {
                "ok": True,
                "dry_run": True,
                "worktree_id": wt_id,
                "session": sessions.mux_session_name(wt_id),
                "work_dir": work_dir,
                "would": "resume" if already else "create",
                "cmd": list(launch_cmd),
                "seed_len": len(seed) if seed else 0,
            }
        )
        return 0

    if already:
        _json_output(
            {
                "ok": True,
                "worktree_id": wt_id,
                "session": sessions.mux_session_name(wt_id),
                "work_dir": work_dir,
                "created": False,
                "resumed": True,
                "new_pane": (sessions.mux_copilot_pane(wt_id) or sessions.mux_active_pane(wt_id)),
                "note": "a live mux session already embodies this worktree",
            }
        )
        return 0

    launch_cmd = _build_launch_cmd(
        config,
        args,
        work_dir,
        profile=selection.profile,
        preflight=launch_preflight,
    )
    env = _apply_assignment_env(
        _build_env(
            selection.profile,
            _repo_session_env(config, work_dir),
            work_dir=work_dir,
        ),
        selection,
    )
    # D4: stamp the driver so the embodied session registers a "driven by
    # <agent>" banner (legible when a human takes it over in Neuron Forge).
    driver = getattr(args, "driver", None)
    if driver:
        env["AGENT_BRIDGE_DRIVEN_BY"] = driver
    if record is None:
        try:
            record = tracking.load_record(cfg.tracking_dir() / f"{wt_id}.yaml")
        except Exception:
            return _json_error(f"Worktree record not found: {wt_id}")
    repo = _repo_for_record(config, record)
    if repo is None:
        return _json_error(f"Repository not found for worktree: {record.repo}")
    lifecycle_lock = fin.FinalizeLock(
        Path(repo.worktree_root) / ".finalize.lock",
        timeout=300,
        stale_after=3600,
    )
    try:
        lifecycle_lock.acquire()
    except TimeoutError:
        return _json_error("worktree lifecycle remained busy for five minutes", exit_code=3,)
    try:
        # A managed-GC pass may have completed while launch preflight ran.
        # Re-check under the shared lifecycle fence before creating a process.
        try:
            launch_record = tracking.load_record(cfg.tracking_dir() / f"{wt_id}.yaml")
        except FileNotFoundError:
            return _json_error(f"Worktree record disappeared before launch: {wt_id}", exit_code=3,)
        if getattr(launch_record, "kind", None) in tracking.MANAGED_KINDS and getattr(
            launch_record, "status", None
        ) in {"complete", "completed", "finalized"}:
            return _json_error(
                f"Worktree {wt_id} is terminal and managed; refusing embodiment",
                exit_code=3,
            )
        if (
            getattr(launch_record, "worktree_id", wt_id) != wt_id
            or getattr(launch_record, "worktree_path", work_dir) != work_dir
            or (
                getattr(record, "branch", None) is not None
                and getattr(launch_record, "branch", None) != getattr(record, "branch", None)
            )
            or (
                getattr(record, "repo", None) is not None
                and getattr(launch_record, "repo", None) != getattr(record, "repo", None)
            )
        ):
            return _json_error(f"Worktree record changed before launch: {wt_id}", exit_code=3,)
        if sessions.has_mux_session(wt_id):
            _json_output(
                {
                    "ok": True,
                    "worktree_id": wt_id,
                    "session": sessions.mux_session_name(wt_id),
                    "work_dir": work_dir,
                    "created": False,
                    "resumed": True,
                    "new_pane": (
                        sessions.mux_copilot_pane(wt_id) or sessions.mux_active_pane(wt_id)
                    ),
                    "note": "a live mux session already embodies this worktree",
                }
            )
            return 0
        # A freshly-provisioned remote venue (CodeSpace/container) virtually
        # never has tmux preinstalled (confirmed against a real CodeSpace
        # devcontainer, agent-bridge-cli-mode-sessions Phase 4 prep) --
        # self-heal it here, once, right before the one call that actually
        # needs it, rather than failing with a raw "not found" or silently
        # downgrading to a non-reattachable headless launch. Explicit opt-in
        # only (--ensure-mux): an operator who deliberately runs a BYO
        # terminal/session manager (e.g. Herdr) instead of tmux on their OWN
        # machine must never have tmux silently installed underneath them by
        # an ordinary `embody` call -- opt-in-not-ambient-default applies
        # here just as it does to CLI mode itself. Only a caller that KNOWS
        # it is preparing a venue with nothing else already managing
        # sessions (agent-bridge's `cli-mode launch`) sets this.
        if getattr(args, "ensure_mux", False):
            sessions.ensure_mux_available()
        result = sessions.mux_new_session(wt_id, work_dir, launch_cmd, env)
    finally:
        lifecycle_lock.release()
    if not result.get("ok"):
        return _json_error(
            f"failed to create session wt-{wt_id}: {result.get('error')}",
            exit_code=4,
        )

    new_pane = result.get("new_pane")
    # A freshly-embodied session can be MCP/skill-heavy and take well over the
    # 20s handoff default to reach Copilot's input caret; seeding races that
    # load, and if it loses, the seed is never typed and the session sits idle
    # at an empty prompt. Give embody a generous, tunable seed-ready timeout so
    # a slow-loading autopilot is still driven autonomously.
    seed_ready_timeout = getattr(args, "seed_ready_timeout", None) or 180.0
    seed_result = (
        sessions.mux_seed_pane(new_pane, seed, ready_timeout=seed_ready_timeout)
        if (new_pane and seed)
        else {}
    )

    verified = None
    verify_timeout = getattr(args, "verify_timeout", 0.0) or 0.0
    if verify_timeout > 0:
        deadline = time.monotonic() + verify_timeout
        verified = False
        while time.monotonic() < deadline:
            if sessions.has_mux_session(wt_id):
                verified = True
                break
            time.sleep(0.3)

    response = {
        "ok": True,
        "worktree_id": wt_id,
        "session": sessions.mux_session_name(wt_id),
        "work_dir": work_dir,
        "created": True,
        "resumed": False,
        "new_pane": new_pane,
        "driven_by": driver,
        "seeded": bool(seed_result.get("sent")) if seed else False,
        "seed_ready": bool(seed_result.get("ready")) if seed else False,
        "seed_submitted": bool(seed_result.get("submitted")) if seed else False,
        "seed_reason": seed_result.get("reason") if seed else None,
        "mux_verified": verified,
        "verify_hint": (
            f"agent-bridge live-sessions | grep {wt_id}  "
            "(the embodied Copilot auto-registers with the local bridge)"
        ),
    }
    if selection.assignment is not None:
        response["profile_assignment"] = profile_assignment.metadata(selection.assignment)
    _json_output(response)
    return 0


def _cmd_embody_anchor(args: argparse.Namespace, config: cfg.Config) -> int:
    """The ``--anchor`` half of :func:`cmd_embody`: create-or-resume a
    detached mux+Copilot session directly in the active project's anchor
    checkout, with no worktree involved at all.

    Deliberately parallels headless dispatch's own existing behavior: a
    CodeSpace/container ACP session already runs `copilot --acp --stdio`
    straight in the venue's anchor checkout (its ``workspace_folder``),
    never in a worktree -- CLI mode had no equivalent, forcing every
    interactive venue session through worktree creation/tracking it didn't
    actually need. This reuses the exact same launch-building primitives
    (:func:`_build_launch_cmd`, :func:`_build_env`, :func:`_repo_session_env`,
    :func:`_preflight_launch`) the worktree path uses -- none of them
    require a worktree tracking record; they only need a ``work_dir`` string,
    confirmed by ``_build_env``'s own docstring ("A non-worktree work_dir (the
    anchor) ... simply omits the var"). Skips entirely the worktree-specific
    machinery that genuinely doesn't apply here: tracking-record
    load/re-verify, launch-profile fleet assignment (`_launch_profile_selection`,
    meant for routing across many parallel worktrees), and the managed-kind/
    terminal-status guard.

    One live anchor session per repo (mirrors "one live session per
    worktree"): the synthesized id ``anchor-<repo_name>`` is what
    ``sessions.has_mux_session``/``mux_new_session`` key off, so a second
    ``--anchor`` call resumes the same session rather than duplicating it,
    identically to the worktree case.
    """
    repo_name = config.repo_name
    if not repo_name:
        return _json_error(
            "--anchor requires an active project (cd into an adopted repo, "
            "or pass --project)",
            exit_code=2,
        )
    work_dir = config.default_repo.anchor
    if not work_dir:
        return _json_error(
            f"project {repo_name!r} has no resolved anchor path", exit_code=2,
        )
    wt_id = f"anchor-{repo_name}"
    seed = getattr(args, "seed", None)

    already = sessions.has_mux_session(wt_id)
    launch_preflight = None
    if getattr(args, "dry_run", False) or not already:
        launch_preflight = _preflight_launch(config, args, work_dir)
        if launch_preflight.error:
            return _json_error(launch_preflight.error, exit_code=3)

    if getattr(args, "dry_run", False):
        launch_cmd = _build_launch_cmd(
            config, args, work_dir, profile=None, preflight=launch_preflight,
        )
        _json_output({
            "ok": True,
            "dry_run": True,
            "worktree_id": wt_id,
            "anchor": True,
            "session": sessions.mux_session_name(wt_id),
            "work_dir": work_dir,
            "would": "resume" if already else "create",
            "cmd": list(launch_cmd),
            "seed_len": len(seed) if seed else 0,
        })
        return 0

    if already:
        _json_output({
            "ok": True,
            "worktree_id": wt_id,
            "anchor": True,
            "session": sessions.mux_session_name(wt_id),
            "work_dir": work_dir,
            "created": False,
            "resumed": True,
            "new_pane": (sessions.mux_copilot_pane(wt_id) or sessions.mux_active_pane(wt_id)),
            "note": "a live mux session already embodies this repo's anchor",
        })
        return 0

    launch_cmd = _build_launch_cmd(
        config, args, work_dir, profile=None, preflight=launch_preflight,
    )
    env = _build_env(None, _repo_session_env(config, work_dir), work_dir=work_dir)
    driver = getattr(args, "driver", None)
    if driver:
        env["AGENT_BRIDGE_DRIVEN_BY"] = driver

    # No worktree record, no repo.worktree_root-scoped lifecycle lock to
    # acquire -- there is no worktree lifecycle (create/finalize/GC) for the
    # anchor to race with here. The has_mux_session recheck above/below is
    # the only concurrency guard this path needs (mirrors the worktree path's
    # own re-check pattern, just without the record-staleness half of it).
    if sessions.has_mux_session(wt_id):
        _json_output({
            "ok": True,
            "worktree_id": wt_id,
            "anchor": True,
            "session": sessions.mux_session_name(wt_id),
            "work_dir": work_dir,
            "created": False,
            "resumed": True,
            "new_pane": (sessions.mux_copilot_pane(wt_id) or sessions.mux_active_pane(wt_id)),
            "note": "a live mux session already embodies this repo's anchor",
        })
        return 0
    if getattr(args, "ensure_mux", False):
        sessions.ensure_mux_available()
    result = sessions.mux_new_session(wt_id, work_dir, launch_cmd, env)
    if not result.get("ok"):
        return _json_error(
            f"failed to create session wt-{wt_id}: {result.get('error')}",
            exit_code=4,
        )

    new_pane = result.get("new_pane")
    seed_ready_timeout = getattr(args, "seed_ready_timeout", None) or 180.0
    seed_result = (
        sessions.mux_seed_pane(new_pane, seed, ready_timeout=seed_ready_timeout)
        if (new_pane and seed)
        else {}
    )

    verified = None
    verify_timeout = getattr(args, "verify_timeout", 0.0) or 0.0
    if verify_timeout > 0:
        deadline = time.monotonic() + verify_timeout
        verified = False
        while time.monotonic() < deadline:
            if sessions.has_mux_session(wt_id):
                verified = True
                break
            time.sleep(0.3)

    _json_output({
        "ok": True,
        "worktree_id": wt_id,
        "anchor": True,
        "session": sessions.mux_session_name(wt_id),
        "work_dir": work_dir,
        "created": True,
        "resumed": False,
        "new_pane": new_pane,
        "driven_by": driver,
        "seeded": bool(seed_result.get("sent")) if seed else False,
        "seed_ready": bool(seed_result.get("ready")) if seed else False,
        "seed_submitted": bool(seed_result.get("submitted")) if seed else False,
        "seed_reason": seed_result.get("reason") if seed else None,
        "mux_verified": verified,
        "verify_hint": (
            f"agent-bridge live-sessions | grep {wt_id}  "
            "(the embodied Copilot auto-registers with the local bridge)"
        ),
    })
    return 0


def _restore_before_resume(record: tracking.WorktreeRecord) -> bool:
    """Prepare one unreachable bound session for a normal muxed resume."""
    session_id = sessions.find_latest_session_id_fast(record.worktree_path, record.sessions)
    result = _perform_remux(
        worktree_id=record.worktree_id,
        session_id=session_id,
        worktree_path=record.worktree_path,
        force_sudo=None,
        apply_windows=True,
    )
    if result.get("ok") and not result.get("requires_resume"):
        if result.get("verified"):
            return True
        output.err(
            result.get("reason", "the live process was not confirmed inside the mux pane",)
        )
        return False
    if result.get("ok"):
        return True
    output.err(result.get("reason", "could not restore the session"))
    return False



def cmd_handoffs_check(args: argparse.Namespace) -> int:
    """Diagnose (and optionally finish) a stalled handoff-cutover retirement.

    A confirmed handoff cutover (the successor is recorded) can still leave
    its predecessor's mux pane alive and un-retired -- the failure mode
    behind a worktree accumulating stale panes across serial handoffs
    (efforts/active/handoff-cutover-lifecycle-journal). This is the
    on-demand counterpart to the resident status-monitor's own automatic
    sweep: any agent can invoke it directly instead of waiting for (or
    debugging) the background daemon.

    ``--worktree-id`` checks one worktree; ``--all`` checks every tracked
    worktree in the current project. Without ``--execute`` this only
    reports what it finds (read-only). With ``--execute`` it retires each
    found predecessor now, via the identical choreography
    (:func:`_monitor_retire_handoff_predecessor`) the daemon uses --
    including its process-identity guard (never retires a pid-reused
    impostor process), just run synchronously instead of on the daemon's
    own schedule.
    """
    worktree_id = getattr(args, "worktree_id", None)
    check_all = bool(getattr(args, "all", False))
    if not worktree_id and not check_all:
        return _json_error("handoffs-check requires --worktree-id or --all")
    if worktree_id and check_all:
        return _json_error("handoffs-check: pass --worktree-id or --all, not both")

    if check_all:
        records = tracking.list_records(cfg.tracking_dir())
    else:
        resolved = _resolve_worktree_id(worktree_id)
        record_path = cfg.tracking_dir() / f"{resolved}.yaml"
        if not record_path.exists():
            return _json_error(f"Worktree not found: {resolved}")
        records = [tracking.load_record(record_path)]

    execute = bool(getattr(args, "execute", False))
    findings: list[dict[str, object]] = []
    for record in records:
        # ALL stale predecessors on this worktree, not just the first -- a
        # worktree can accumulate several in a row (the exact failure mode
        # this tool exists to clean up), and a single check/--execute pass
        # must not require re-invoking once per predecessor.
        for request in _pending_handoff_retire_requests(record):
            finding: dict[str, object] = {
                "worktree_id": request.get("worktree_id"),
                "handoff_token": request.get("handoff_token"),
                "predecessor_session_id": request.get("predecessor_session_id"),
                "successor_session_id": request.get("successor_session_id"),
                "retire_pane": request.get("retire_pane"),
                "executed": False,
            }
            if execute:
                rc, response = _monitor_retire_handoff_predecessor(request)
                finding["executed"] = True
                # `_handoff_cutover_retire_result`'s returned dict has no
                # "outcome" key (that string is only computed for the
                # activity-log event, never merged back) -- its actual
                # success signal is `rc == 0` (== `response["ok"]`, set from
                # `gone and proc_ok`). Checking a nonexistent "outcome" key
                # made every real successful retire report retired=False.
                finding["retired"] = rc == 0 and bool(response.get("ok"))
                finding["result"] = response
            findings.append(finding)

    payload = {
        "checked": len(records),
        "found": len(findings),
        "executed": execute,
        "findings": findings,
    }
    if getattr(args, "json", False):
        _json_output(payload)
    else:
        if not findings:
            output.ok("handoffs-check: no stalled predecessor retirements found.")
        for finding in findings:
            if not execute:
                print(
                    f"  {finding['worktree_id']}: predecessor "
                    f"{finding['predecessor_session_id']} still alive "
                    f"(pane {finding['retire_pane']}) -- token "
                    f"{finding['handoff_token']}"
                )
            elif finding.get("retired"):
                output.ok(
                    f"{finding['worktree_id']}: retired predecessor "
                    f"{finding['predecessor_session_id']} (pane {finding['retire_pane']})"
                )
            else:
                output.err(
                    f"{finding['worktree_id']}: could not retire predecessor "
                    f"{finding['predecessor_session_id']}: "
                    f"{(finding.get('result') or {}).get('method', 'unknown')}"
                )
    if execute and any(not f.get("retired") for f in findings):
        return 1
    return 0

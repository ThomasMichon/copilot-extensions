"""Handoff-cutover choreography: spawn/retry/retire result builders.

Componentized out of ``__main__.py``'s launch core (module-size split,
``module-componentization-discipline`` effort). This owns the full
``handoff-cutover`` verb family's *result-building* logic (spawn a successor,
retry a stuck cutover, retire a confirmed-dead predecessor pane, and the
Stage 13 ``handoff_complete`` completion race) -- the parser/dispatch wiring
itself lives in ``handoff_cli.py`` (``cmd_handoff_cutover``), which already
calls back into these names via its own ``_core()`` proxy.

``__main__.py`` stays the compatibility/composition root: it imports and
re-exports every name defined here so existing callers (``handoff_cli.py``'s
proxies, the resident status monitor's ``_monitor_retire_handoff_predecessor``/
``_monitor_trigger_handoff_cutover``) and every test's
``monkeypatch.setattr(m, "<name>", ...)`` keep working unchanged. Every
cross-call between the functions below -- and every call out to a helper that
still lives in ``__main__.py`` proper (launch-plan building, worktree-id
resolution, project-scope checks) -- goes through ``_core()`` (the live
``__main__`` module object) rather than a bare local name, so a test's
monkeypatch on ``m.<name>`` is observed no matter which function in this band
makes the call, exactly as ``_self_override`` already guarantees for
``__main__``'s own sibling-module delegations.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import time
from pathlib import Path

from . import (
    activity,
    locks,
    obligations,
    pane_lifecycle,
    procs,
    profile_assignment,
    reclaim,
    sessions,
    sessions_pane_retire,
    tracking,
)
from . import config as cfg


def _core():
    from . import __main__ as core

    return core


# A losing Stage-13 claimant's brief retry window (see _maybe_emit_stage_13):
# short enough to add negligible latency to a hook/monitor call, long enough
# for the winner's own log_event() + failure-detection + rollback to settle.
_STAGE13_CLAIM_RETRIES = 3
_STAGE13_CLAIM_RETRY_DELAY_S = 0.05


def _wait_for_handoff_candidate(
    record_path: Path,
    token: str,
    pane_id: str | None,
    *,
    timeout: float = 30.0,
    mux_session: str | None = None,
    predecessor_session_id: str | None = None,
) -> tuple[str | None, str]:
    """Wait until a successor is confirmed for the exact handoff token.

    See ``sessions_pane_retire.wait_for_handoff_candidate`` (moved there to
    keep this grandfathered module's shrink-only size baseline).
    """
    return sessions_pane_retire.wait_for_handoff_candidate(
        record_path, token, pane_id, timeout=timeout, mux_session=mux_session,
        predecessor_session_id=predecessor_session_id,
    )


def _resolve_handoff_cutover_target(
    raw_id: str | None,
    session_id: str | None,
) -> tuple[int, dict[str, object]]:
    """Resolve the target worktree (or adopted anchor) for handoff cutover."""
    core = _core()
    # Stage D: session_binding_cli is cluster-free.
    from . import session_binding_cli as _session_binding_cli

    _activate_session_binding = core._self_override(
        "_activate_session_binding", _session_binding_cli._activate_session_binding)

    config = None
    if raw_id:
        wt_id = core._resolve_worktree_id(raw_id)
    else:
        wt_id = core._infer_worktree_id_from_cwd()
        # Bare-resume authoritative fallback (#4098): under a two-step "Bare
        # resume" the pane's cwd is HOME (to dodge the worktree-cwd start bug),
        # so cwd inference finds no worktree even though the session IS inside
        # its wt-<id> mux. Resolve the worktree from the session id instead --
        # the registry maps the resumed session to its worktree (the same
        # binding the sessionStart hook uses), so this is authoritative, not a
        # brittle guess. Activate the scoped bare-resume binding first (reads the
        # AGENT_WORKTREES_BIND_* env the launcher set), then match by session id.
        if not wt_id and session_id:
            wt_id = _activate_session_binding(session_id)
            if not wt_id:
                try:
                    wt_id = tracking.find_worktree_id_by_session(session_id)
                except Exception:
                    wt_id = None
        if not wt_id:
            try:
                config = cfg.load_config()
            except Exception as exc:
                return 2, {
                    "ok": False,
                    "error": (
                        "could not resolve a worktree or adopted anchor from "
                        "cwd; pass --worktree-id (or --session-id for a "
                        "bare-resumed worktree session). Project resolution "
                        f"failed: {exc}"
                    ),
                }
            if core._cwd_is_inside_project(Path(config.default_repo.anchor)):
                wt_id = tracking.ANCHOR_ID
            else:
                return 2, {
                    "ok": False,
                    "error": (
                        "could not resolve a worktree or adopted anchor from "
                        "cwd; run from the intended checkout, pass "
                        "--worktree-id, or pass --session-id for a "
                        "bare-resumed worktree session"
                    ),
                }
    return 0, {
        "ok": True,
        "worktree_id": wt_id,
        "anchor_mode": wt_id == tracking.ANCHOR_ID,
        "config": config,
    }


def _handoff_cutover_spawn_result(
    args: argparse.Namespace,
) -> tuple[int, dict[str, object]]:
    """Return the spawn-mode ``handoff-cutover`` result without printing JSON."""
    core = _core()
    # Stage D: resolve_launch_cli is cluster-free.
    from . import resolve_launch_cli as _resolve_launch_cli

    _apply_assignment_env = core._self_override("_apply_assignment_env", _resolve_launch_cli._apply_assignment_env)
    _reflect_assignment = core._self_override("_reflect_assignment", _resolve_launch_cli._reflect_assignment)
    _launch_profile_selection = core._self_override("_launch_profile_selection", _resolve_launch_cli._launch_profile_selection)

    seed = getattr(args, "seed", None)
    if not seed:
        return 1, {
            "ok": False, "error": "handoff-cutover requires --seed (or --retire-pane)",
        }
    raw_id = getattr(args, "worktree_id", None)
    session_id = getattr(args, "session_id", None)
    headless = bool(getattr(args, "headless", False))
    rc, resolved = core._resolve_handoff_cutover_target(raw_id, session_id)
    if rc != 0:
        return rc, resolved
    wt_id = str(resolved.get("worktree_id") or "")
    config = resolved.get("config")

    # A live cutover needs a mux session to cut into. Without one, the caller
    # (extension) must fall back to the store-task-and-reply flow -- unless
    # --headless was requested, which explicitly opts out of mux entirely
    # (context-handoff-overhaul Phase 3 §4.3's non-mux launch primitive).
    anchor_mode = bool(resolved.get("anchor_mode"))
    if headless and anchor_mode:
        return 2, {
            "ok": False,
            "error": "handoff-cutover --headless does not support anchor-mode cutover",
        }

    mux_session = None
    record = None
    record_path = None
    if anchor_mode:
        if config is None:
            try:
                config = cfg.load_config()
            except Exception as exc:
                return 1, {"ok": False, "error": str(exc)}
        mux_session = sessions.current_mux_session(getattr(args, "old_pane", None))
        if not mux_session or not sessions.has_mux_session_named(mux_session):
            return 3, {
                "ok": False,
                "error": (
                    "adopted anchor is not inside a live mux session; the "
                    "stored handoff remains available for paste/resume"
                ),
            }
        work_dir = config.default_repo.anchor
    else:
        if not headless and not sessions.has_mux_session(wt_id):
            return 3, {
                "ok": False,
                "error": f"no mux session wt-{wt_id}; not under mux",
            }
        if config is None:
            try:
                config = cfg.load_config()
            except Exception as exc:
                return 1, {"ok": False, "error": str(exc)}
        record_path = cfg.tracking_dir() / f"{wt_id}.yaml"
        if not record_path.exists():
            return 1, {"ok": False, "error": f"Worktree not found: {wt_id}"}
        record = tracking.load_record(record_path)
        work_dir = record.worktree_path

    backend_error = core._unsupported_hosted_launch(
        record,
        "handoff-cutover",
    )
    if backend_error:
        return 3, {"ok": False, "error": backend_error}

    launch_preflight = core._preflight_launch(config, args, work_dir)
    if launch_preflight.error:
        return 3, {"ok": False, "error": launch_preflight.error}
    try:
        selection = _launch_profile_selection(
            config,
            args,
            record,
            lane="handoff-cutover",
            generation_key=f"handoff:{session_id or wt_id}",
            predecessor_session=session_id,
            allocate_new=not getattr(args, "dry_run", False),
        )
    except profile_assignment.ProfileAssignmentError as exc:
        return 3, {"ok": False, "error": str(exc)}
    if record is not None:
        _reflect_assignment(record, selection)
    predecessor_binding = None
    if session_id:
        predecessor_binding = (
            sessions.mux_binding_for_session(session_id, expected_session_name=mux_session,)
            if anchor_mode and mux_session
            else sessions.mux_binding_for_session(session_id)
        )
    expected_mux_session = mux_session if anchor_mode else sessions.mux_session_name(wt_id)
    expected_copilot_pid = getattr(args, "expected_copilot_pid", None)
    expected_copilot_start = getattr(args, "expected_copilot_start_time", None)
    if (
        predecessor_binding
        and (
            predecessor_binding.get("session_name") != expected_mux_session
            or (
                expected_copilot_pid is not None
                and predecessor_binding.get("copilot_pid") != expected_copilot_pid
            )
            or (
                expected_copilot_start is not None
                and str(predecessor_binding.get("copilot_start_time"))
                != str(expected_copilot_start)
            )
        )
    ):
        predecessor_binding = None
    predecessor_copilot_path = None
    predecessor_pid = None
    predecessor_start = None
    if predecessor_binding:
        predecessor_pid = predecessor_binding.get("copilot_pid")
        predecessor_start = predecessor_binding.get("copilot_start_time")
        if predecessor_pid and predecessor_start:
            predecessor_copilot_path = procs.copilot_relaunch_path(
                procs.process_executable_path(predecessor_pid)
            )
            if not predecessor_copilot_path or locks.process_start_time(predecessor_pid) != str(
                predecessor_start
            ):
                predecessor_copilot_path = None
    if predecessor_pid is None:
        predecessor_pid = expected_copilot_pid
    if predecessor_start is None:
        predecessor_start = expected_copilot_start
    launch_cmd = core._build_launch_cmd(
        config,
        args,
        work_dir,
        profile=selection.profile,
        preflight=launch_preflight,
        fallback_copilot_path=predecessor_copilot_path,
    )
    env = _apply_assignment_env(
        core._build_env(
            selection.profile,
            core._repo_session_env(config, work_dir),
            work_dir=work_dir,
        ),
        selection,
    )
    handoff_token = getattr(args, "handoff_token", None)
    if handoff_token:
        env[core._SESSION_HANDOFF_TOKEN] = handoff_token

    if headless:
        # No pane, no mux session, no seed-typing choreography: the seed is
        # passed as a native ``-i <seed>`` argument directly by
        # headless_new_session itself (headless has no pane-wrapper argv
        # mangling to route around).
        if getattr(args, "dry_run", False):
            dry_result: dict[str, object] = {
                "ok": True,
                "dry_run": True,
                "headless": True,
                "work_dir": work_dir,
                "cmd": [*launch_cmd, "-i", "<seed>"],
                "seed_len": len(seed),
            }
            if selection.assignment is not None:
                dry_result["profile_assignment"] = profile_assignment.metadata(
                    selection.assignment)
            return 0, dry_result

        spawn_event_ctx = {
            "worktree_id": wt_id, "session_id": session_id, "source": "python",
            "handoff_token": handoff_token, "old_pane": None,
            "method": "headless_new_session",
        }
        activity.log_event("handoff_successor_spawn_started", **spawn_event_ctx)
        result = sessions.headless_new_session(wt_id, work_dir, launch_cmd, env, seed=seed)
        if not result.get("ok"):
            failure = dict(result, ok=False)
            failure["error"] = f"failed to spawn headless successor: {result.get('error')}"
            activity.log_event(
                "handoff_successor_spawn_failed", error=result.get("error"), **spawn_event_ctx)
            return 4, failure
        pid = result.get("pid")
        activity.log_event(
            "handoff_cutover_spawn", new_pane=None, pid=pid,
            seeded=True, seed_ready=True,
            candidate_session=None, candidate_status="awaiting-session-association",
            predecessor_copilot_pid=predecessor_pid,
            predecessor_copilot_start_time=predecessor_start,
            **spawn_event_ctx,
        )
        candidate_session = None
        if handoff_token and record_path is not None:
            candidate_session, candidate_status = core._wait_for_handoff_candidate(
                record_path, handoff_token, None,
            )
            if not candidate_session:
                return 4, {
                    "ok": False,
                    "headless": True,
                    "pid": pid,
                    "candidate_status": candidate_status,
                    "error": (
                        "successor did not create a token-associated Copilot "
                        f"session (status: {candidate_status})"
                    ),
                }
        response: dict[str, object] = {
            "ok": True,
            "headless": True,
            "pid": pid,
            "seed_len": len(seed),
            "seeded": True,
            "seed_ready": True,
            "seed_method": "interactive-argv",
        }
        if candidate_session:
            response["candidate_session"] = candidate_session
        if selection.assignment is not None:
            response["profile_assignment"] = profile_assignment.metadata(selection.assignment)
        return 0, response

    # Keep the multi-word seed OUT of the pane command argv: psmux space-joins
    # that argv before CreateProcess and irreversibly splits it. mux_new_window
    # sends base64 control tokens to the platform wrapper instead; the wrapper
    # decodes and appends native ``-i <seed>`` immediately before
    # launching the setup/Copilot command, after psmux has finished reconstructing
    # argv. A receipt handshake proves the wrapper performed that reconstruction
    # before this command reports success.

    # Capture the pane to retire (the operator's current Copilot) BEFORE opening
    # the new window, which becomes the active pane. ``--old-pane`` lets the
    # extension pin its own $TMUX_PANE explicitly.
    old_pane = (
        getattr(args, "old_pane", None)
        or (
            predecessor_binding.get("pane_id")
            if predecessor_binding
            else (
                sessions.mux_active_pane_named(mux_session)
                if anchor_mode and mux_session
                else sessions.mux_copilot_pane(wt_id, session_id)
            )
        )
        or (None if anchor_mode else sessions.mux_active_pane(wt_id))
    )

    if getattr(args, "dry_run", False):
        dry_result: dict[str, object] = {
            "ok": True,
            "dry_run": True,
            "session": mux_session or sessions.mux_session_name(wt_id),
            "old_pane": old_pane,
            "work_dir": work_dir,
            "cmd": list(launch_cmd),
            "seed_len": len(seed),
        }
        if selection.assignment is not None:
            dry_result["profile_assignment"] = profile_assignment.metadata(selection.assignment)
        return 0, dry_result

    spawn_event_ctx = {
        "worktree_id": wt_id, "session_id": session_id, "source": "python",
        "handoff_token": handoff_token, "old_pane": old_pane,
        "expected_mux_session": expected_mux_session,
        "method": "mux_new_window_interactive_argv",
    }
    def _spawn_failed(error: object) -> None:
        activity.log_event(
            "handoff_successor_spawn_failed", error=error, **spawn_event_ctx)
    activity.log_event("handoff_successor_spawn_started", **spawn_event_ctx)
    try:
        result = pane_lifecycle.pane_create(
            wt_id, work_dir, launch_cmd, env,
            initial_prompt=seed, session_name=mux_session)
    except Exception as exc:
        # mux_new_window guards only a known subset; an uncaught exception
        # must not leave the spawn stuck at "started".
        _spawn_failed(str(exc))
        raise
    if not result.get("ok"):
        failure = dict(result, ok=False)
        failure["error"] = f"failed to open successor window: {result.get('error')}"
        _spawn_failed(result.get("error"))
        return 4, failure
    new_pane = result.get("new_pane")
    activity.log_event(
        "handoff_cutover_spawn", new_pane=new_pane,
        seeded=bool(result.get("prompt_received")),
        seed_ready=bool(result.get("prompt_received")),
        candidate_session=None, candidate_status="awaiting-session-association",
        predecessor_copilot_pid=predecessor_pid,
        predecessor_copilot_start_time=predecessor_start,
        **spawn_event_ctx,
    )
    candidate_session = None
    if handoff_token and record_path is not None:
        candidate_session, candidate_status = core._wait_for_handoff_candidate(
            record_path,
            handoff_token,
            new_pane,
            mux_session=expected_mux_session,
            predecessor_session_id=session_id,
        )
        if not candidate_session:
            failure = dict(result)
            failure.update(
                {
                    "ok": False,
                    "candidate_status": candidate_status,
                    "error": (
                        "successor did not create a token-associated Copilot "
                        f"session (status: {candidate_status})"
                    ),
                }
            )
            return 4, failure

    response: dict[str, object] = dict(result)
    response.update(
        {
            "ok": True,
            "session": mux_session or sessions.mux_session_name(wt_id),
            "old_pane": old_pane,
            "new_pane": new_pane,
            "seed_len": len(seed),
            "seeded": bool(result.get("prompt_received")),
            "seed_ready": bool(result.get("prompt_received")),
            "seed_method": "interactive-argv",
        }
    )
    if candidate_session:
        response["candidate_session"] = candidate_session
    if selection.assignment is not None:
        response["profile_assignment"] = profile_assignment.metadata(selection.assignment)
    return 0, response


def _latest_handoff_cutover_retry_handoff(
    record: tracking.WorktreeRecord,
) -> tracking.SessionHandoff | None:
    """Return the most recent non-cancelled handoff on ``record``."""
    handoffs = [
        handoff
        for handoff in getattr(record, "handoffs", []) or []
        if getattr(handoff, "state", None) != "cancelled"
    ]
    if not handoffs:
        return None
    return max(handoffs, key=lambda handoff: getattr(handoff, "ordinal", 0))


def _handoff_cutover_retry_result(
    args: argparse.Namespace,
) -> tuple[int, dict[str, object]]:
    """Retry a stuck cutover without duplicating a live successor."""
    core = _core()
    # Stage D: status_monitor_runtime is cluster-free.
    from . import status_monitor_runtime as _smr

    _monitor_session_state_handoff_path = core._self_override("_monitor_session_state_handoff_path", _smr._monitor_session_state_handoff_path)
    _monitor_read_session_state_handoff = core._self_override("_monitor_read_session_state_handoff", _smr._monitor_read_session_state_handoff)

    session_id = getattr(args, "session_id", None)
    rc, resolved = core._resolve_handoff_cutover_target(
        getattr(args, "worktree_id", None),
        session_id,
    )
    if rc != 0:
        return rc, resolved
    wt_id = str(resolved.get("worktree_id") or "")
    if wt_id == tracking.ANCHOR_ID:
        return 3, {
            "ok": False,
            "error": "handoff-cutover --retry only supports tracked worktrees",
        }
    record_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not record_path.exists():
        return 1, {"ok": False, "error": f"Worktree not found: {wt_id}"}
    record = tracking.load_record(record_path)
    handoff = core._latest_handoff_cutover_retry_handoff(record)
    if handoff is None:
        return 1, {
            "ok": False,
            "error": f"worktree {wt_id} has no recorded handoff to retry",
        }
    predecessor_session = str(getattr(handoff, "predecessor", "") or "").strip() or None
    if session_id and predecessor_session and session_id != predecessor_session:
        return 2, {
            "ok": False,
            "error": (
                "handoff-cutover --retry must be invoked from the most recent "
                f"handoff predecessor ({predecessor_session}), not {session_id}"
            ),
        }
    handoff_token = str(getattr(handoff, "token", "") or "").strip() or None
    explicit_token = str(getattr(args, "handoff_token", "") or "").strip() or None
    if explicit_token and explicit_token != handoff_token:
        return 2, {
            "ok": False,
            "error": (
                f"handoff-cutover --retry targets the most recent handoff "
                f"token {handoff_token}, not {explicit_token}"
            ),
        }
    head_session = record.resolved_head_session
    recorded_successor = str(getattr(handoff, "successor", "") or "").strip() or None
    if recorded_successor and head_session not in {None, recorded_successor}:
        return 2, {
            "ok": False,
            "error": (
                "the most recent handoff's recorded successor disagrees with "
                f"the resolved worktree head ({head_session}); retry refused"
            ),
        }
    live_session = recorded_successor or (
        str(getattr(handoff, "candidate", "") or "").strip() or None
    )
    expected_mux_session = sessions.mux_session_name(wt_id)
    mux_bin = sessions._mux_bin()
    if live_session:
        try:
            binding = sessions.mux_binding_for_session(
                live_session,
                expected_session_name=expected_mux_session,
            )
        except Exception:
            binding = None
        pane_id = str((binding or {}).get("pane_id") or "").strip() or None
        if (
            binding
            and str(binding.get("session_name") or "").strip() == expected_mux_session
            and pane_id
            and sessions._mux_pane_alive(pane_id, mux_bin)
        ):
            response = {
                "ok": True,
                "outcome": "refocused",
                "worktree_id": wt_id,
                "handoff_token": handoff_token,
                "handoff_state": (
                    "linked" if recorded_successor else getattr(handoff, "state", None)
                ),
                "session": expected_mux_session,
                "successor_session": live_session,
                "successor_pane": pane_id,
            }
            if getattr(args, "dry_run", False):
                response["dry_run"] = True
                return 0, response
            if sessions.mux_focus_pane(expected_mux_session, pane_id):
                return 0, response
            response.update(
                {
                    "ok": False,
                    "error": (
                        "a live successor pane already exists, but its mux "
                        "window could not be focused"
                    ),
                }
            )
            return 5, response
    request = _monitor_read_session_state_handoff(
        _monitor_session_state_handoff_path(predecessor_session)
    )
    seed = str(getattr(args, "seed", "") or "").strip() or None
    request_token = str((request or {}).get("handoffId") or "").strip() or None
    if request_token in {None, handoff_token}:
        if recorded_successor:
            prompt_text = str((request or {}).get("promptText") or "").strip()
            if prompt_text:
                seed = prompt_text
        if not seed:
            request_seed = str((request or {}).get("seed") or "").strip()
            if request_seed:
                seed = request_seed
    spawn_args = argparse.Namespace(**vars(args))
    spawn_args.worktree_id = wt_id
    spawn_args.session_id = predecessor_session or session_id
    spawn_args.handoff_token = None if recorded_successor else handoff_token
    spawn_args.seed = seed
    rc, response = core._handoff_cutover_spawn_result(spawn_args)
    response.setdefault("worktree_id", wt_id)
    response.setdefault("handoff_token", handoff_token)
    if rc == 0 and response.get("ok"):
        response["outcome"] = "spawned"
    return rc, response


def _maybe_emit_stage_13(
    wt_id: str | None, handoff_token: str | None, *, launch_id: str | None = None,
) -> None:
    """Emit Stage 13 (handoff_complete) once the predecessor pane is gone AND
    the successor is head (linked, not just candidate) -- called from both
    the retire and claim sides since either can complete first. Dedup is an
    atomic exclusive-create claim file keyed by a collision-resistant digest
    of the exact (worktree, token) pair (NOT the lossy character-sanitized
    ``_monitor_handoff_claim_segment()``, which can map distinct tokens like
    "task:1" and "task_1" onto the same on-disk name): the successor's
    sessionStart process and the resident monitor's retire process run
    concurrently, so a plain read_events() check-then-act could let both
    sides pass before either logs, double-recording Stage 13. If the log
    write is detected to have failed (via
    ``activity.log_event_failure_count()``), the winner rolls its claim back
    so the failure stays retryable -- but that rollback can race a losing
    caller's own FileExistsError check, so a loser doesn't just give up: it
    waits briefly, and if the claim vanished (a rollback), retries the claim
    itself instead of silently losing Stage 13 forever. A hard process crash
    between the claim and the log write is a narrower residual gap left to
    Phase 3's durable trace store.
    """
    if not wt_id or not handoff_token:
        return
    retired = {
        str(e.get("handoff_token") or "").strip()
        for e in activity.read_events(worktree_id=wt_id, event="handoff_predecessor_retire")
        if e.get("outcome") == "gone"
    }
    if handoff_token not in retired:
        return
    try:
        record = tracking.load_record(cfg.tracking_dir() / f"{wt_id}.yaml")
    except Exception:
        return
    handoff = next((h for h in record.handoffs if h.token == handoff_token), None)
    if handoff is None or handoff.state != "linked":
        return
    digest = hashlib.sha256(f"{wt_id}\x00{handoff_token}".encode()).hexdigest()
    # Stage D: status_monitor_runtime is cluster-free.
    from . import status_monitor_runtime as _smr

    core = _core()
    _monitor_handoff_claim_root = core._self_override("_monitor_handoff_claim_root", _smr._monitor_handoff_claim_root)

    claim_path = _monitor_handoff_claim_root() / "stage13" / f"{digest}.json"
    try:
        claim_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return  # unwritable/malformed claim root -- fail open like the claim itself
    claimed = False
    for _attempt in range(core._STAGE13_CLAIM_RETRIES):
        try:
            with open(claim_path, "x", encoding="utf-8"):
                pass
        except FileExistsError:
            # A losing caller must not just give up: if the current holder's
            # log write fails and rolls back the claim (below), that rollback
            # can happen between our check and its own return, and no other
            # caller may ever retry -- silently losing Stage 13 forever. Give
            # the holder a brief window to either finish or roll back, then
            # retry the claim ourselves if it did.
            time.sleep(core._STAGE13_CLAIM_RETRY_DELAY_S)
            if claim_path.exists():
                return  # still held -- its owner is genuinely in flight
            continue
        except OSError:
            return  # unwritable claim dir -- no-op
        claimed = True
        break
    if not claimed:
        return
    failures_before = activity.log_event_failure_count()
    activity.log_event(
        "handoff_complete", worktree_id=wt_id, session_id=handoff.predecessor,
        successor_session_id=handoff.successor, handoff_token=handoff_token,
        launch_id=launch_id)
    if activity.log_event_failure_count() > failures_before:
        with contextlib.suppress(OSError):
            claim_path.unlink()


def _settle_predecessor_session_claim(wt_id: str | None, session_id: str) -> None:
    """Settle a confirmed-retired predecessor's ``session`` claim to at-rest.

    Runs for *every* confirmed retire (bare or token-bearing), unlike
    :func:`_conclude_retired_predecessor` which only concludes the
    ``SessionEntry`` on a bare retire -- a token-bearing retire's own
    ``SessionEntry`` transition is ``link_handoff``'s job, but nothing else
    settles the predecessor's Phase 8 ``session`` resource claim once its
    pane and Copilot process are positively confirmed gone. Settles (not
    releases) since ``settle_resource_claim`` never resurrects an
    already-``released`` claim (mirrors ``finalize.py``'s
    ``_settle_current_session_claim`` guard) -- a ``deregister_session`` that
    raced ahead and released the claim first is left alone. Best-effort:
    unknown worktree/session or a missing claim is a silent no-op, never a
    hard failure of the retire itself.
    """
    if not wt_id or not session_id:
        return
    with contextlib.suppress(Exception):
        yaml_path = tracking._owning_tracking_dir(wt_id) / f"{wt_id}.yaml"
        if not yaml_path.exists():
            return
        with tracking._RecordLock(yaml_path):
            record = tracking.load_record(yaml_path)
            predecessor_ref = tracking.format_claim_ref(
                record.machine, record.repo, record.worktree_id, session=session_id,
            )
            existing_claim = next(
                (c for c in record.resources if c.ref == predecessor_ref), None,
            )
            if existing_claim is None or existing_claim.state == "released":
                return
            tracking.settle_resource_claim(
                record, predecessor_ref, disposition=obligations.AT_REST, save=False,
            )
            tracking.save_record(record, yaml_path)


def _conclude_retired_predecessor(wt_id: str | None, session_id: str) -> None:
    """Mark a retire-confirmed predecessor's ``SessionEntry`` concluded.

    A confirmed ``--retire-pane`` (pane gone AND its Copilot process
    positively verified dead by pid) is a deliberate, verified act -- not
    liveness inference -- so it is safe to assert conclusion here, same as
    ``conclude-session``'s own contract. Without this, a session whose
    process died via a bare self-retire (no handoff-token successor link)
    leaves its ``SessionEntry.state`` stuck at ``"active"`` forever, and
    ``register_session``'s creation-guard then refuses to ever promote a
    later successor to head -- a zombie head pointer.

    Deliberately does NOT attempt to promote any other session found on the
    record: ``conclude_session`` (and this repair, which shares its
    contract) never infers a successor from list membership -- that's the
    same invariant a plain ``conclude-session`` upholds ("another active
    session is never promoted by list or timestamp order; a successor or
    adopter must assert the next transition explicitly"). A session that
    registered while this predecessor was still active is not necessarily
    its successor -- it could be an unrelated parallel session -- so
    guessing from "exactly one other active entry" would misattribute
    succession exactly as easily as it would complete a deferred one. A
    subsequent registration (any future hook/bind call) still correctly
    claims the now-cleared head via ``register_session``'s own ordinary
    path; closing the narrower race window where an already-registered
    session is never re-triggered needs an explicit adoption/relationship
    marker, which is a separate, larger change than this repair's scope.
    Best-effort: unknown worktree/session or an already-concluded entry is a
    silent no-op, never a hard failure of the retire itself.
    """
    if not wt_id or not session_id:
        return
    yaml_path = tracking._owning_tracking_dir(wt_id) / f"{wt_id}.yaml"
    if not yaml_path.exists():
        return
    with contextlib.suppress(Exception), tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        entry = record.session_entry(session_id)
        if entry is None or entry.state != "active":
            return
        tracking.conclude_session(record, session_id, state="concluded", save=False)
        tracking.save_record(record, yaml_path)


def _resolve_retire_pane_mux_session(
    retire_pane: str | None,
    expected_mux: str | None,
) -> str | None:
    """Return the mux session that actually owns ``retire_pane``, safely.

    ``sessions.current_mux_session()``/``mux_session_for_pane()`` runs a
    *bare* ``display-message -t <pane_id>`` -- unqualified by session. Live
    validation of a real ``handoff-cutover --retire-pane`` invocation from
    outside the target pane's own session (e.g. an orchestrator, not a
    predecessor self-retiring) showed psmux resolving that bare target to
    the *caller's own current/default session* rather than the session that
    genuinely contains ``retire_pane``, even with no real pane-id collision
    -- producing a false ``identity-mismatch-skip`` that silently no-ops a
    legitimate retire. Mirror the already-safe pattern used by
    ``pane_terminate``/``mux_focus_pane`` (#2890/#2892/#2896): check
    membership in the expected session first via a session-scoped
    ``list-panes -a`` filter, and only fall back to an unscoped lookup (also
    ``list-panes -a``-based, never a bare ``display-message``) when the pane
    isn't there.
    """
    if not expected_mux or not retire_pane:
        return None
    mux_bin = sessions_pane_retire._mux_bin()
    if sessions_pane_retire._list_matching_pane_targets(
        retire_pane, mux_bin, session_name=expected_mux,
    ):
        return expected_mux
    try:
        return sessions_pane_retire._resolve_unambiguous_pane_session(retire_pane, mux_bin)
    except sessions_pane_retire.MuxPaneTargetAmbiguityError:
        return None


def _handoff_cutover_retire_result(
    args: argparse.Namespace,
) -> tuple[int, dict[str, object]]:
    """Return the retire-mode ``handoff-cutover`` result without printing JSON."""
    core = _core()
    retire_pane = getattr(args, "retire_pane", None)
    raw_id = getattr(args, "worktree_id", None)
    wt_id = core._resolve_worktree_id(raw_id) if raw_id else None
    session_id = getattr(args, "session_id", None)
    expected_mux = getattr(args, "mux_session", None)
    require_mux_identity = bool(getattr(args, "require_mux_identity", False))
    expected_copilot_pid = getattr(args, "expected_copilot_pid", None)
    expected_copilot_start = getattr(args, "expected_copilot_start_time", None)
    strict_process_identity = (
        expected_copilot_pid is not None or expected_copilot_start is not None
    )
    current_mux = core._resolve_retire_pane_mux_session(retire_pane, expected_mux)
    pane_not_in_expected_mux = bool(expected_mux and current_mux != expected_mux)
    binding = (
        sessions.mux_binding_for_session(session_id)
        if (strict_process_identity and session_id and not pane_not_in_expected_mux)
        else None
    )
    process_identity_ok = True
    process_identity_reason = None
    if strict_process_identity and not pane_not_in_expected_mux:
        if expected_copilot_pid is None or not expected_copilot_start or binding is None:
            process_identity_ok = False
            process_identity_reason = "process-identity-unavailable"
        elif (
            binding.get("pane_id") != retire_pane
            or binding.get("copilot_pid") != expected_copilot_pid
            or str(binding.get("copilot_start_time")) != str(expected_copilot_start)
        ):
            process_identity_ok = False
            process_identity_reason = "process-identity-mismatch"
    if not process_identity_ok:
        result = {
            "ok": False,
            "pane": retire_pane,
            "gone": False,
            "method": process_identity_reason,
            "expected_copilot_pid": expected_copilot_pid,
            "expected_copilot_start_time": expected_copilot_start,
        }
    elif require_mux_identity and not expected_mux:
        result = {
            "ok": bool(session_id),
            "pane": retire_pane,
            "gone": True,
            "method": "identity-unavailable-skip",
            "expected_mux_session": None,
            "current_mux_session": None,
        }
    elif expected_mux and current_mux != expected_mux:
        result = {
            "ok": bool(session_id),
            "pane": retire_pane,
            "gone": True,
            "method": (
                "identity-mismatch-skip" if current_mux else "identity-unresolved-skip"
            ),
            "expected_mux_session": expected_mux,
            "current_mux_session": current_mux,
        }
    else:
        result = pane_lifecycle.pane_terminate(
            retire_pane,
            mux_session=expected_mux,
        )
    reap = {"checked": False}
    identity_skip = result.get("method") in {
        "process-identity-unavailable", "process-identity-mismatch",
    }
    if session_id and result.get("method") != "last-window-skip" and not identity_skip:
        if strict_process_identity:
            reap = reclaim.ensure_session_copilot_reaped(
                session_id,
                expected_pid=expected_copilot_pid,
                expected_start_time=expected_copilot_start,
            )
        else:
            reap = reclaim.ensure_session_copilot_reaped(session_id)
        result["copilot"] = reap
    proc_ok = ((not reap.get("checked")) or reap.get("survivors", 0) == 0) and reap.get(
        "identity_verified", True
    )
    skipped_identity = result.get("method") == "identity-mismatch-skip"
    overall_ok = bool(result.get("ok")) and proc_ok
    result["ok"] = overall_ok
    # A pane is only genuinely, positively confirmed retired when the real
    # mux retire ran and reported it gone -- never for a synthetic
    # "-skip" result (last-window guard, unresolved/mismatched mux identity,
    # unverifiable process identity), each of which reports success without
    # ever having signaled the pane. Concluding the predecessor on one of
    # those would mark a still-running session as "concluded".
    pane_confirmed_retired = bool(result.get("gone")) and result.get("method") not in {
        "process-identity-unavailable", "process-identity-mismatch",
        "identity-unavailable-skip", "identity-mismatch-skip",
        "identity-unresolved-skip", "last-window-skip",
    }
    # Only repair a BARE retire (no handoff token at all) here. A token-bearing
    # retire belongs to the ordinary handoff flow, where the successor's own
    # `register_session(..., handoff_token=...)` -> `link_handoff` concludes
    # the predecessor once it actually claims the token -- including the
    # legitimate case where a candidate has been associated
    # (`associate_handoff_candidate`) but not yet acknowledged: the
    # predecessor is still `active` and its resident monitor may still retire
    # it before that late acknowledgement lands. Concluding it here would
    # make that acknowledgement fail (`link_handoff` refuses an explicitly
    # concluded predecessor).
    bare_retire = not getattr(args, "handoff_token", None)
    if overall_ok and pane_confirmed_retired and session_id:
        core._settle_predecessor_session_claim(wt_id, session_id)
    if overall_ok and pane_confirmed_retired and bare_retire and session_id:
        core._conclude_retired_predecessor(wt_id, session_id)
    activity.log_event(
        "handoff_predecessor_retire",
        worktree_id=wt_id,
        session_id=session_id,
        source="python",
        handoff_token=getattr(args, "handoff_token", None),
        successor_session_id=getattr(args, "successor_session_id", None),
        old_pane=retire_pane,
        successor_verified=bool(getattr(args, "successor_verified", False)),
        reason=getattr(args, "retire_reason", None),
        method=result.get("method"),
        outcome=(
            "identity-mismatch"
            if skipped_identity
            else "gone"
            if (result.get("gone") and proc_ok)
            else "left-running"
        ),
        copilot_found=reap.get("found", 0),
        copilot_reaped=reap.get("reaped", 0),
        copilot_survivors=reap.get("survivors", 0),
    )
    if getattr(args, "handoff_token", None):
        from . import handoff_diagnostics

        handoff_diagnostics.stamp_session_state_handoff(
            session_id,
            handoff_token=getattr(args, "handoff_token", None),
            successor_session_id=getattr(args, "successor_session_id", None),
        )
    core._maybe_emit_stage_13(
        wt_id, getattr(args, "handoff_token", None),
        launch_id=getattr(args, "launch_id", None) or os.environ.get("WORKTREE_LAUNCH_ID"))
    return (0 if overall_ok else 1), result

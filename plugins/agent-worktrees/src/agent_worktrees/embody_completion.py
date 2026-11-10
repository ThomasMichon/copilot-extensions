"""Post-launch seed settlement, verification and embodiment response."""

from __future__ import annotations

import dataclasses
from pathlib import Path
import subprocess

from . import launch_seed_exec, launch_seed_state


def existing_launch(
    args, *, wt_id, work_dir, deferred, cfg, sessions, output, tracking_write,
    native_seed_id: str | None = None,
) -> int:
    from . import agent_policy

    pane = sessions.mux_copilot_pane(wt_id)
    display = pane or sessions.mux_active_pane(wt_id)
    seed_result, settled = {}, {}
    if pane and not deferred and getattr(args, "deliver_pending_seed", False):
        try:
            receipt = launch_seed_state.claim_creation(cfg.tracking_dir() / f"{wt_id}.yaml")
        except (ValueError, OSError, TimeoutError, tracking_write.AmbiguousWriteOutcome, tracking_write.ResidentRecoveryHeld) as exc:
            return output._json_error(f"Could not hand off staged New prompt: {exc}", exit_code=3)
        if receipt["seed"]:
            if refusal := agent_policy.json_refusal(work_dir):
                return refusal
            seed_result = sessions.mux_seed_pane(
                pane, receipt["seed"]["text"], session_name=sessions.mux_session_name(wt_id),
                ready_timeout=getattr(args, "seed_ready_timeout", None) or 180.0,
            )
            settled = launch_seed_state.settle_creation(
                cfg.tracking_dir() / f"{wt_id}.yaml", receipt, seed_result,
            )
    output._json_output({
        "ok": True, **deferred, "worktree_id": wt_id,
        "session": sessions.mux_session_name(wt_id), "work_dir": work_dir,
        "created": False, "resumed": True, "new_pane": display,
        "note": "a live mux session already embodies this worktree",
        "seeded": bool(seed_result.get("ok")), **settled,
        **({
            "seed_id": native_seed_id, "seed_unconfirmed": True,
            "seed_reason": "joined-after-preparation",
        } if native_seed_id else {}),
    })
    return 0


def prepare_startup_seed(
    command: list[str], record_path: Path, seed: str | None, *, explicit: bool,
    environment: dict[str, str] | None = None,
) -> tuple[list[str], str | None]:
    """Use the existing final-invocation owner on qualified cold-launch paths."""
    if not seed or not launch_seed_exec.supports_seed_command(
        command, record_path, environment=environment,
    ):
        return command, None
    if "\0" in seed:
        raise ValueError("Startup prompt cannot contain a NUL character")
    current = launch_seed_state.pending(record_path)
    if current is not None and current.handoff_id:
        raise ValueError("A launch-prompt handoff is in progress or unconfirmed; do not resend")
    if current is not None and current.kind != "new":
        raise ValueError("Typed Resume prompt belongs to its existing launch owner")
    if explicit:
        current = launch_seed_state.stage(
            record_path, kind="new", text=seed,
            creation_snapshot={"seed": dataclasses.asdict(current) if current is not None else None},
        )
    elif current is None or current.kind != "new" or current.text != seed:
        raise ValueError("Staged New prompt changed before launch; refresh and retry")
    if current is None:
        raise ValueError("Could not stage native startup prompt")
    return (
        launch_seed_exec.deferred_command(
            command, record_path, seed_id=current.seed_id, environment=environment, mux=True,
        ),
        current.seed_id,
    )


def complete_launch(
    args, *, wt_id, work_dir, record, acquisition, deferred, explicit_seed, seed,
    prepared_context, resume_target, driver, selection, result, cfg, sessions,
    output, launch_seed_state, tracking_write, profile_assignment, clock,
    native_seed_id: str | None = None,
) -> int:
    from . import agent_policy
    from repository_policy import AgentHostingError

    try:
        agent_policy.require(work_dir)
    except AgentHostingError as exc:
        return output._json_error(str(exc), exit_code=3)
    if result.get("created") is False:
        if acquisition:
            return output._json_error("a live mux holds the bridge acquisition target", exit_code=3)
        output._json_output({
            "ok": True, **deferred, "worktree_id": wt_id,
            "session": result["session"], "work_dir": work_dir,
            "created": False, "resumed": True, "new_pane": None,
            "seeded": False,
            "note": "a live mux session already embodies this worktree",
            **({
                "seed_id": native_seed_id, "seed_unconfirmed": True,
                "seed_reason": "joined-after-preparation",
            } if native_seed_id else {}),
        })
        return 0
    new_pane = result.get("new_pane")
    # Reserve only New intent at delivery; failed typing retains it.
    claimed_seed: str | None = None
    creation_receipt = {"seed": None}
    if record is not None and not acquisition and not deferred and not native_seed_id:
        try:
            creation_receipt = launch_seed_state.claim_creation(cfg.tracking_dir() / f"{wt_id}.yaml")
        except (ValueError, OSError, TimeoutError, tracking_write.AmbiguousWriteOutcome, tracking_write.ResidentRecoveryHeld) as exc:
            return output._json_error(f"Could not hand off staged New prompt: {exc}", exit_code=3)
        claimed_seed = creation_receipt["seed"]["text"] if creation_receipt["seed"] else None
        if not explicit_seed:
            seed = claimed_seed
    # A freshly-embodied session can be MCP/skill-heavy and take well over the
    # 20s handoff default to reach Copilot's input caret; seeding races that
    # load, and if it loses, the seed is never typed and the session sits idle
    # at an empty prompt. Give embody a generous, tunable seed-ready timeout so
    # a slow-loading autopilot is still driven autonomously.
    seed_ready_timeout = getattr(args, "seed_ready_timeout", None) or 180.0
    seed_result = (
        sessions.mux_seed_pane(new_pane, seed, session_name=result.get("session"),
                               ready_timeout=seed_ready_timeout)
        if (new_pane and seed and not deferred and not native_seed_id)
        else {}
    )
    settled = launch_seed_state.settle_creation(
        cfg.tracking_dir() / f"{wt_id}.yaml", creation_receipt, seed_result)

    verified = None
    verification_error = None
    verify_timeout = getattr(args, "verify_timeout", 0.0) or 0.0
    if verify_timeout > 0:
        deadline = clock.monotonic() + verify_timeout
        verified = False
        while (remaining := deadline - clock.monotonic()) > 0:
            try:
                if sessions.has_mux_session(wt_id, strict=True, timeout=min(5, remaining)):
                    verified = True
                    break
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                verified = None
                verification_error = f"Mux verification is unavailable: {exc}"
                break
            clock.sleep(min(0.3, max(0, deadline - clock.monotonic())))
        if verified is False:
            verification_error = "Mux session was not confirmed before the verification deadline"

    response = {
        "ok": verification_error is None,
        **deferred,
        **({"prepared_context": prepared_context} if prepared_context is not None else {}),
        "worktree_id": wt_id,
        "session": sessions.mux_session_name(wt_id),
        "work_dir": work_dir,
        "created": True,
        "resumed": False,
        "resume_session": resume_target,
        "new_pane": new_pane,
        "driven_by": driver,
        "seeded": bool(seed_result.get("sent")) if seed else False,
        "seed_ready": bool(seed_result.get("ready")) if seed else False,
        "seed_submitted": bool(seed_result.get("submitted")) if seed else False,
        "seed_reason": seed_result.get("reason") if seed else None,
        **({
            "seed_delivery": "native-startup",
            "seed_id": native_seed_id,
            "seed_deferred": True,
            "seed_reason": "native-startup-pending",
        } if native_seed_id else {}),
        **settled,  # a claimed pending seed kept for later, or a possible draft
        "mux_verified": verified,
        **({
            "error": (
                f"{verification_error}; creation may have occurred. Inspect the existing "
                "launch and prompt state before any retry; do not resend an unconfirmed prompt."
            ),
        } if verification_error else {}),
        "verify_hint": (
            f"agent-bridge live-sessions | grep {wt_id}  "
            "(the embodied Copilot auto-registers with the local bridge)"
        ),
    }
    if selection.assignment is not None:
        response["profile_assignment"] = profile_assignment.metadata(selection.assignment)
    output._json_output(response)
    return 3 if verification_error else 0

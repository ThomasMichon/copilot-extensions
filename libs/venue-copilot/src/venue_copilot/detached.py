"""Venue-neutral detached CLI-mode session launch/stop flow."""

from __future__ import annotations

import shlex
import subprocess
import sys
from typing import Any, Protocol

from . import (
    LIVE_SESSION_ALIAS_PROTOCOL,
    VenueCopilotError,
    await_claim,
    bridge_probe_script,
    build_copilot_remote_command,
    deregister_live_session,
    last_json,
    may_switch_session_id,
    observe_commands,
    registration_credentials_script,
    release_cli_mode,
    reserve_with_retry,
    resolve_daemon_port,
    resolve_local_auth_token,
    seed_delivery,
    seed_outcome,
    pending_seed_report,
    trust_folder_command,
    unstable_handle_warning,
    with_new_session,
)


class DetachAdapter(Protocol):
    def run(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        """Run a POSIX shell command on the venue."""
        ...

    def launch(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        """Launch the embody command on the venue."""
        ...

    def run_input(self, command: str, stdin: bytes, *, timeout: float) -> tuple[int, str, str]:
        """Run a POSIX shell command on the venue with ``stdin`` as its input."""
        ...

    def ensure_keeper(self, *, venue_port: int, mux: str) -> dict[str, Any]:
        """Ensure the venue's host bridge forward keeper is running."""
        ...

    def stop_keeper(self) -> bool:
        """Stop the venue's host bridge forward keeper."""
        ...

    def attach_command(self, plan: dict[str, Any]) -> str:
        """Human attach command for the running venue session."""
        ...

    def stop_command(self, plan: dict[str, Any]) -> str:
        """Verified stop command for the running venue session."""
        ...


# Runner configuration a venue passes in its plan: never part of a printed handle.
_RUNNER_CONFIG_KEYS = frozenset({
    "reservation_ttl", "reserve_retry_window", "registration_error", "bridge_probe_error",
    "missing_agent_worktrees_error", "old_agent_worktrees_error", "reservation_wait",
    "launch_detail",
})

# Mirrors agent-worktrees' mux seed hard cap. The remote launch timeout must
# stay above it because readiness can slide while Copilot is visibly busy.
_SEED_READY_HARD_CAP = 900.0


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """``plan`` without the runner-configuration keys (for handles and dry runs)."""
    return {k: v for k, v in plan.items() if k not in _RUNNER_CONFIG_KEYS}


def _payload(ok: bool, plan: dict[str, Any], **extra: Any) -> dict[str, Any]:
    shown = public_plan(plan)
    return {"ok": ok, **extra, **shown} if ok else {"ok": False, **shown, **extra}


def _venue_text(plan: dict[str, Any], key: str, default: str) -> str:
    value = plan.get(key)
    return str(value) if value else default


def _launch_command(
    plan: dict[str, Any],
    *,
    seed: str | None,
    seed_ready_timeout: float,
    driver: str | None,
    copilot_args: list[str],
    ensure_mux: bool,
) -> str:
    typed_seed, seed_prefix = seed_delivery(seed, plan["scope_id"])
    command = seed_prefix + build_copilot_remote_command(
        plan["identity"],
        anchor=bool(plan.get("anchor", True)),
        driver=driver,
        seed=typed_seed,
        seed_ready_timeout=seed_ready_timeout,
        ensure_mux=ensure_mux,
        detach=True,
        bridge_scope_id=plan["scope_id"],
        copilot_args=with_new_session(copilot_args),
        login_shell=False,
    )
    workspace = plan.get("workspace") or plan.get("workspace_folder")
    if workspace:
        command = (
            f"cd {shlex.quote(str(workspace))} && "
            f"{trust_folder_command(str(workspace))} && {command}"
        )
    return command


def stop_script(mux: str, *, verify: bool = True) -> str:
    mux_target = shlex.quote("=" + mux)
    if not verify:
        return f"tmux kill-session -t {mux_target} 2>/dev/null || true"
    return (
        f"tmux kill-session -t {mux_target} 2>/dev/null; sleep 1; "
        f"if tmux has-session -t {mux_target} 2>/dev/null; "
        "then echo STILL_RUNNING; exit 3; fi; echo STOPPED"
    )


def _bridge_path_ok(adapter: DetachAdapter, port: int) -> bool:
    import time

    for attempt in range(int(getattr(adapter, "probe_attempts", 2))):
        rc, _out, _err = adapter.run(bridge_probe_script(port), timeout=30.0)
        if rc == 0:
            return True
        if attempt + 1 < int(getattr(adapter, "probe_attempts", 2)):
            time.sleep(5.0)
    return False


def _stop_keeper_best_effort(adapter: DetachAdapter) -> bool:
    try:
        return adapter.stop_keeper()
    except (RuntimeError, OSError) as exc:
        print(f"[WARN] could not update the forward keeper: {exc}", file=sys.stderr)
        return False


def launch_detached(
    adapter: DetachAdapter,
    plan: dict[str, Any],
    *,
    seed: str | None,
    driver: str | None,
    copilot_args: list[str],
    ensure_mux: bool,
    register_timeout: float,
    progress: Any,
    refs: tuple[str, bytes, list[tuple[str, int]]] | None = None,
) -> tuple[int, dict[str, Any]]:
    reservation: dict[str, Any] | None = None
    created = False
    keeper_hold_acquired = False
    ok = False
    try:
        daemon_port = resolve_daemon_port()
        if not daemon_port:
            return 1, _payload(
                False,
                plan,
                error="the host agent-bridge daemon is not running (no routing table)",
            )
        token = resolve_local_auth_token()
        if not token:
            return 1, _payload(
                False,
                plan,
                error="the host agent-bridge daemon has no readable auth token",
            )
        handle_warning = unstable_handle_warning(daemon_port, copilot_args)
        if handle_warning:
            progress("handle", handle_warning)

        rc, _out, err = adapter.run(
            registration_credentials_script(token, daemon_port),
            timeout=60.0,
        )
        if rc != 0:
            return 1, _payload(
                False,
                plan,
                error=_venue_text(
                    plan,
                    "registration_error",
                    "could not provision registration credentials on the venue",
                ),
                detail=err.strip()[-1000:],
            )

        keeper = adapter.ensure_keeper(venue_port=daemon_port, mux=plan["mux_session"])
        keeper_hold_acquired = bool(keeper.get("hold_added"))
        if not _bridge_path_ok(adapter, daemon_port):
            return 1, _payload(
                False,
                plan,
                error=_venue_text(
                    plan,
                    "bridge_probe_error",
                    "the venue cannot reach the host bridge through the forward "
                    "(authenticated probe failed)",
                ),
            )

        launch_timeout = register_timeout + 300.0
        # refs become (part of) the seed; a worktree launch may also consume the
        # worktree's own pending seed with neither passed here, and wait as long.
        if seed or refs or not bool(plan.get("anchor", True)):
            launch_timeout = max(launch_timeout, _SEED_READY_HARD_CAP + 300.0)
        # The reservation must outlive the refs upload, the launch (with its seed
        # readiness wait) and registration, or a concurrent rejoin could replace it.
        ttl = max(float(plan.get("reservation_ttl", 900.0)),
                  (600.0 if refs else 0.0) + launch_timeout + register_timeout + 120.0)
        reservation = reserve_with_retry(
            plan["scope_id"],
            plan["venue"],
            ttl_seconds=ttl,
            retry_window=float(plan.get("reserve_retry_window", 90.0)),
            on_wait=lambda: progress(
                "waiting",
                _venue_text(plan, "reservation_wait", "another launch holds the reservation"),
            ),
        )
        progress("reserved", reservation.get("reservation_id", ""))
        notes: str | None = None
        if refs:
            from .refs import send_refs

            notes = send_refs(
                lambda command, stdin: adapter.run_input(command, stdin, timeout=600.0),
                refs,
                progress,
            )
            if notes is None:
                return 1, _payload(False, plan, error="could not copy the reference files to the venue")
            seed = f"{seed.rstrip()}\n\n{notes}" if seed else notes
        progress("launch", _venue_text(plan, "launch_detail", "`agent-worktrees embody` on the venue"))
        seed_ready_timeout = max(register_timeout, 180.0)
        rc, stdout, stderr = adapter.launch(
            _launch_command(
                plan,
                seed=seed,
                seed_ready_timeout=seed_ready_timeout,
                driver=driver,
                copilot_args=copilot_args,
                ensure_mux=ensure_mux,
            ),
            timeout=launch_timeout,
        )
        embodied = last_json(stdout)
        if "agent-worktrees: command not found" in stderr + stdout:
            return 1, _payload(
                False,
                plan,
                error=_venue_text(
                    plan,
                    "missing_agent_worktrees_error",
                    "agent-worktrees is not installed on the venue",
                ),
            )
        if "unrecognized arguments" in stderr or "unrecognized arguments" in stdout:
            return 1, _payload(
                False,
                plan,
                error=_venue_text(
                    plan,
                    "old_agent_worktrees_error",
                    "the venue's agent-worktrees is too old for detached launch "
                    "(--bridge-scope-id/--copilot-arg); update agent-worktrees",
                ),
                detail=stderr.strip()[-2000:],
            )
        if rc != 0 or not embodied.get("ok"):
            return 1, _payload(
                False,
                plan,
                error=(
                    f"remote embody failed: "
                    f"{embodied.get('error') or stderr.strip()[-2000:] or f'exit {rc}'}"
                ),
            )
        created = bool(embodied.get("created"))
        actual_mux = embodied.get("session")
        if actual_mux and actual_mux != plan["mux_session"]:
            plan["mux_session"] = actual_mux
            plan["venue"]["mux_session_name"] = actual_mux
        seed_delivery_status, seed_needs_bridge = seed_outcome(embodied, created=created, seed=seed)
        if seed_delivery_status == "failed":
            progress("seed-draft", f"typed seed was not submitted ({embodied.get('seed_reason')}); "
                     "not resending: the draft may remain in Copilot's input")
        progress("register", "waiting for the session to register with the host bridge")
        session_id = await_claim(plan["scope_id"], reservation["reservation_id"], register_timeout)
        if not session_id:
            return 1, _payload(
                False,
                plan,
                error="the session is running but never registered with the host bridge",
            )
        ok = True
        # A resume can re-register under a new id after the claim; only a daemon
        # with live-session aliases carries a bridge message (seed or note) across it.
        # A rejoin's own flags say nothing about how the running session was
        # launched (it may still be resuming), so it always needs that floor.
        alias_floor = ({"min_daemon_protocol": LIVE_SESSION_ALIAS_PROTOCOL}
                       if not created or may_switch_session_id(copilot_args) else {})
        if seed_needs_bridge:
            from .refs import deliver_note

            reason = embodied.get("seed_reason")
            detail = f"seed was never typed ({reason}); delivering over bridge" if reason else (
                "seed was never typed; delivering over bridge"
            )
            progress("seed-bridge", detail)
            seed_delivery_status = "bridge" if deliver_note(session_id, seed, operation=reservation["reservation_id"], **alias_floor) else "failed"
        refs_extra: dict[str, Any] = {}
        if notes:
            # A typed new session got the note in its seed; a running one (or a
            # new session whose typed seed missed readiness) is told by message.
            from .refs import deliver_note

            refs_delivered = "failed"
            if created and seed_delivery_status == "typed":
                refs_delivered = "seed"
            elif seed_needs_bridge and seed_delivery_status == "bridge":
                refs_delivered = "message"
            elif not created:
                refs_delivered = "message" if deliver_note(session_id, notes, operation=reservation["reservation_id"], **alias_floor) else "failed"
            refs_extra = {
                "ref_files": notes.splitlines()[1:],
                "refs_delivered": refs_delivered,
            }
        seed_extra = ({"seed_delivery": seed_delivery_status} if created and seed
                      else pending_seed_report(embodied, seed=seed))
        return 0, _payload(
            True,
            plan,
            session_id=session_id,
            created=created,
            resumed=not created,
            seeded=bool(created and seed and seed_delivery_status in {"typed", "bridge"})
            or seed_extra.get("seed_delivery") == "typed",
            keeper=keeper,
            **seed_extra,
            **refs_extra,
            **({"session_handle": "provisional", "handle_warning": handle_warning}
               if handle_warning else {}),
            commands={
                **observe_commands(session_id),
                "attach": adapter.attach_command(plan),
                "stop": adapter.stop_command(plan),
            },
        )
    except (RuntimeError, OSError, VenueCopilotError, subprocess.SubprocessError) as exc:
        return 1, _payload(False, plan, error=str(exc))
    finally:
        if reservation:
            release_cli_mode(plan["scope_id"], reservation_id=reservation.get("reservation_id"))
        if not ok:
            if created:
                progress("cleanup", f"stopping the unrepresented session {plan['mux_session']}")
                adapter.run(stop_script(plan["mux_session"], verify=False), timeout=60.0)
            if keeper_hold_acquired:
                _stop_keeper_best_effort(adapter)


def stop_detached(
    adapter: DetachAdapter,
    plan: dict[str, Any],
    *,
    session_row: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    row = session_row or {}
    session_id = (
        row.get("session_id")
        if (row.get("venue") or {}).get("target") == plan["venue"].get("target")
        else None
    )
    try:
        rc, out, err = adapter.run(stop_script(plan["mux_session"], verify=True), timeout=120.0)
    except (RuntimeError, subprocess.SubprocessError) as exc:
        return 1, _payload(False, plan, error=str(exc))
    if rc != 0 or "STOPPED" not in out:
        return 1, _payload(
            False,
            plan,
            error="could not verify the session stopped; nothing was released",
            detail=err,
        )
    keeper_stopped = _stop_keeper_best_effort(adapter)
    release_cli_mode(plan["scope_id"])
    deregistered = bool(session_id) and deregister_live_session(str(session_id))
    return 0, _payload(
        True,
        plan,
        stopped=True,
        keeper_stopped=keeper_stopped,
        deregistered=session_id if deregistered else None,
    )

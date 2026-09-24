"""Venue-neutral detached CLI-mode session launch/stop flow."""

from __future__ import annotations

import shlex
import subprocess
from typing import Any, Protocol

from . import (
    VenueCopilotError,
    await_claim,
    bridge_probe_script,
    build_copilot_remote_command,
    deregister_live_session,
    last_json,
    observe_commands,
    registration_credentials_script,
    release_cli_mode,
    reserve_with_retry,
    resolve_daemon_port,
    resolve_local_auth_token,
    seed_delivery,
    trust_folder_command,
    with_new_session,
)


class DetachAdapter(Protocol):
    def run(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        """Run a POSIX shell command on the venue."""
        ...

    def launch(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        """Launch the embody command on the venue."""
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


def _payload(ok: bool, plan: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"ok": ok, **extra, **plan} if ok else {"ok": False, **plan, **extra}


def _venue_text(plan: dict[str, Any], key: str, default: str) -> str:
    value = plan.get(key)
    return str(value) if value else default


def _launch_command(
    plan: dict[str, Any],
    *,
    seed: str | None,
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
) -> tuple[int, dict[str, Any]]:
    reservation: dict[str, Any] | None = None
    created = False
    keeper_started = False
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
        keeper_started = bool(keeper.get("started"))
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

        reservation = reserve_with_retry(
            plan["scope_id"],
            plan["venue"],
            ttl_seconds=float(plan.get("reservation_ttl", 900.0)),
            retry_window=float(plan.get("reserve_retry_window", 90.0)),
            on_wait=lambda: progress(
                "waiting",
                _venue_text(plan, "reservation_wait", "another launch holds the reservation"),
            ),
        )
        progress("reserved", reservation.get("reservation_id", ""))
        progress("launch", _venue_text(plan, "launch_detail", "`agent-worktrees embody` on the venue"))
        rc, stdout, stderr = adapter.launch(
            _launch_command(
                plan,
                seed=seed,
                driver=driver,
                copilot_args=copilot_args,
                ensure_mux=ensure_mux,
            ),
            timeout=register_timeout + 300.0,
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
        if created and seed and not embodied.get("seed_submitted"):
            reason = embodied.get("seed_reason")
            suffix = f" ({reason})" if reason else ""
            return 1, _payload(
                False,
                plan,
                error=(
                    "Copilot never reached a ready prompt, so the seed was not "
                    f"submitted{suffix}"
                ),
            )
        progress("register", "waiting for the session to register with the host bridge")
        session_id = await_claim(plan["scope_id"], reservation["reservation_id"], register_timeout)
        if not session_id:
            return 1, _payload(
                False,
                plan,
                error="the session is running but never registered with the host bridge",
            )
        ok = True
        return 0, _payload(
            True,
            plan,
            session_id=session_id,
            created=created,
            resumed=not created,
            seeded=bool(created and seed),
            keeper=keeper,
            commands={
                **observe_commands(session_id),
                "attach": adapter.attach_command(plan),
                "stop": adapter.stop_command(plan),
            },
        )
    except (RuntimeError, VenueCopilotError, subprocess.SubprocessError) as exc:
        return 1, _payload(False, plan, error=str(exc))
    finally:
        if reservation:
            release_cli_mode(plan["scope_id"], reservation_id=reservation.get("reservation_id"))
        if not ok:
            if created:
                progress("cleanup", f"stopping the unrepresented session {plan['mux_session']}")
                adapter.run(stop_script(plan["mux_session"], verify=False), timeout=60.0)
            if keeper_started:
                adapter.stop_keeper()


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
    keeper_stopped = adapter.stop_keeper()
    release_cli_mode(plan["scope_id"])
    deregistered = bool(session_id) and deregister_live_session(str(session_id))
    return 0, _payload(
        True,
        plan,
        stopped=True,
        keeper_stopped=keeper_stopped,
        deregistered=session_id if deregistered else None,
    )

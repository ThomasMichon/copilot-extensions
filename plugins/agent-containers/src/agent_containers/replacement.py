"""Active-session-safe destructive lifecycle for restricted fleet members."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass

from .config import ContainersConfig, FleetConfig
from .lease import (
    ProviderAdmissionError,
    active_session_admissions,
    deploy_hold,
    get_lease,
    mark_deploy_hold_uncertain,
    verify_deploy_hold,
)
from .lifecycle import (
    DockerContainerInfo,
    _docker,
    get_container,
    inspect_container,
    inspect_state,
    remove_container,
    restricted_policy_errors,
    stop_container,
    unpause_container,
)
from .rescue import (
    RescueError,
    capture_restricted_sessions,
    container_generation,
    pin_verified_capture,
    record_telemetry_loss,
    verified_capture_for_instance,
    verify_pinned_capture,
)
from .restricted_exec import (
    RestrictedExecError,
    resolve_executable,
    sanitized_exec_prefix,
)

_LOCK_LINE_RE = re.compile(
    r"^LOCK\t([0-9a-fA-F-]{36})\t([1-9][0-9]*)\t(live|stale)$"
)
_CONFIRMATION_AND_CLEANUP_GRACE = 45.0
_TERMINAL_STOPPED_STATES = {"exited", "created"}
_ALLOWED_REPLACEMENT_DRIFT = {
    "security policy fingerprint is stale",
    "container image differs from configured image",
    "container image ID differs from provisioned image ID",
    "configured image reference differs from running image ID",
}


@dataclass
class SessionLiveness:
    """Non-cooperative in-container session-state probe result."""

    state: str
    active_sessions: list[str]
    stale_sessions: list[str]
    reason: str | None = None
    session_state: str = "unknown"


@dataclass
class DestructiveResult:
    """One member's independent restricted destruction decision."""

    name: str
    status: str
    reason: str | None = None
    rescue: dict | None = None
    telemetry_abandoned: bool = False


def probe_session_liveness(
    info: DockerContainerInfo,
    *,
    user: str,
    bash_path: str,
    home: str,
) -> SessionLiveness:
    """Read Copilot lock markers from inside a running container.

    Docker executes the probe from the host. The session does not need to
    cooperate or publish provider/bridge state.
    """
    state = getattr(info, "state", "")
    if state == "paused":
        return SessionLiveness("unknown", [], [], "container is paused")
    if not bool(getattr(info, "is_running", state == "running")):
        return SessionLiveness(
            "unknown",
            [],
            [],
            "container is not running and tmpfs evidence is unavailable",
        )
    script = r"""
set -o pipefail
root="$HOME/.copilot/session-state"
test -d /proc
if [ ! -e "$root" ]; then
  printf 'ROOT\tabsent\n'
else
  test -d "$root"
  printf 'ROOT\tpresent\n'
  find "$root" -mindepth 2 -maxdepth 2 -type f -name 'inuse.*.lock' -print |
  while IFS= read -r path; do
    session="${path%/*}"
    session="${session##*/}"
    marker="${path##*/}"
    pid="${marker#inuse.}"
    pid="${pid%.lock}"
    case "$pid" in
      ''|*[!0-9]*) printf 'INVALID\t%s\t%s\n' "$session" "$marker"; continue ;;
    esac
    if [ -d "/proc/$pid" ]; then state=live; else state=stale; fi
    printf 'LOCK\t%s\t%s\t%s\n' "$session" "$pid" "$state"
  done
fi
scan=ok
for proc in /proc/[0-9]*; do
  pid="${proc##*/}"
  [ "$pid" = "$$" ] && continue
  [ "$pid" = "$PPID" ] && continue
  if [ ! -r "$proc/cmdline" ]; then
    [ -d "$proc" ] && scan=partial
    continue
  fi
  command=$(tr '\000' ' ' < "$proc/cmdline") || {
    [ -d "$proc" ] && scan=partial
    continue
  }
  case "$command" in
    *copilot*|*Copilot*|*--acp*) printf 'PROCESS\t%s\n' "$pid" ;;
  esac
done
printf 'PROCESS_SCAN\t%s\n' "$scan"
""".strip()
    try:
        result = _docker(
            [
                *sanitized_exec_prefix(info.container_id, user, home)[1:],
                bash_path,
                "--noprofile",
                "--norc",
                "-c",
                script,
            ],
            timeout=30,
        )
    except RuntimeError as exc:
        return SessionLiveness("unknown", [], [], str(exc))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return SessionLiveness(
            "unknown",
            [],
            [],
            detail or "session-state probe failed",
        )
    lines = result.stdout.splitlines()
    if not lines or lines[0] not in {"ROOT\tabsent", "ROOT\tpresent"}:
        return SessionLiveness(
            "unknown",
            [],
            [],
            "session-state probe returned an invalid header",
        )
    active = []
    stale = []
    processes = []
    scan_state = None
    for line in lines[1:]:
        if line.startswith("PROCESS\t"):
            pid = line.split("\t", 1)[1]
            if not pid.isdigit():
                return SessionLiveness(
                    "unknown",
                    active,
                    stale,
                    "process backstop returned an invalid pid",
                    lines[0].split("\t", 1)[1],
                )
            processes.append(pid)
            continue
        if line.startswith("PROCESS_SCAN\t"):
            scan_state = line.split("\t", 1)[1]
            if scan_state not in {"ok", "partial"}:
                return SessionLiveness(
                    "unknown",
                    active,
                    stale,
                    "process backstop returned invalid status",
                    lines[0].split("\t", 1)[1],
                )
            continue
        match = _LOCK_LINE_RE.fullmatch(line)
        if not match:
            return SessionLiveness(
                "unknown",
                active,
                stale,
                "session-state probe returned an invalid marker",
                lines[0].split("\t", 1)[1],
            )
        session_id, _pid, marker_state = match.groups()
        try:
            session_id = str(uuid.UUID(session_id))
        except ValueError:
            return SessionLiveness(
                "unknown",
                active,
                stale,
                "session-state probe found a non-UUID session marker",
                lines[0].split("\t", 1)[1],
            )
        if marker_state == "live":
            active.append(session_id)
        else:
            stale.append(session_id)
    session_state = lines[0].split("\t", 1)[1]
    if active:
        return SessionLiveness(
            "active",
            sorted(set(active)),
            sorted(set(stale)),
            session_state=session_state,
        )
    if processes:
        return SessionLiveness(
            "unknown",
            [],
            sorted(set(stale)),
            "Copilot-like process has no matching live session marker",
            session_state,
        )
    if scan_state != "ok":
        return SessionLiveness(
            "unknown",
            [],
            sorted(set(stale)),
            "process backstop was incomplete",
            session_state,
        )
    return SessionLiveness(
        "idle",
        [],
        sorted(set(stale)),
        session_state=session_state,
    )


def destroy_restricted_member(
    config: ContainersConfig,
    fleet: FleetConfig,
    info: DockerContainerInfo,
    *,
    operation: str,
    force_remove: bool,
    force_abandon: bool,
    timeout: float = 120.0,
) -> DestructiveResult:
    """Rescue and remove one restricted member only after confirmed idleness."""
    return _restricted_member_action(
        config,
        fleet,
        info,
        operation=operation,
        force_abandon=force_abandon,
        action=lambda current, action_timeout: remove_container(
            current.container_id,
            force=force_remove,
            timeout=action_timeout,
        ),
        confirm=lambda current: inspect_state(current.container_id) is None,
        action_timeout=timeout,
        success_status="removed",
    )


def stop_restricted_member(
    config: ContainersConfig,
    fleet: FleetConfig,
    info: DockerContainerInfo,
    *,
    force_abandon: bool,
    timeout: float = 60.0,
) -> DestructiveResult:
    """Rescue and stop one restricted member only after confirmed idleness."""
    return _restricted_member_action(
        config,
        fleet,
        info,
        operation="stop",
        force_abandon=force_abandon,
        action=lambda current, action_timeout: stop_container(
            current.container_id,
            timeout=action_timeout,
        ),
        confirm=lambda current: inspect_state(current.container_id) in {
            "exited",
            "created",
        },
        action_timeout=timeout,
        success_status="stopped",
    )


def _restricted_member_action(
    config: ContainersConfig,
    fleet: FleetConfig,
    info: DockerContainerInfo,
    *,
    operation: str,
    force_abandon: bool,
    action: Callable[[DockerContainerInfo, float], None],
    confirm: Callable[[DockerContainerInfo], bool],
    action_timeout: float,
    success_status: str,
) -> DestructiveResult:
    if not fleet.restricted:
        raise RuntimeError("restricted destructive lifecycle requires a restricted fleet")
    user = fleet.exec_user or config.exec_user
    rescue_timeout = config.rescue.operation_timeout_seconds
    deadline = time.monotonic() + rescue_timeout
    hold_lifetime = (
        rescue_timeout + action_timeout + _CONFIRMATION_AND_CLEANUP_GRACE
    )
    try:
        with deploy_hold(
            info.name,
            operation,
            max_lifetime=hold_lifetime,
        ) as hold:
            lease = get_lease(info.name)
            admissions = active_session_admissions(info.name)
            if admissions:
                return DestructiveResult(
                    info.name,
                    "deferred",
                    "provider session admission is active",
                )
            if lease is not None:
                return DestructiveResult(
                    info.name,
                    "deferred",
                    "container has an active effort lease",
                )

            current = get_container(config, info.name)
            if current is None or current.container_id.lower() != info.container_id.lower():
                return DestructiveResult(
                    info.name,
                    "deferred",
                    "container identity changed before lifecycle check",
                )
            if current.state == "paused":
                try:
                    unpause_container(current.container_id)
                except RuntimeError as exc:
                    return DestructiveResult(
                        info.name,
                        "deferred",
                        f"paused container could not be inspected: {exc}",
                    )
                current = get_container(config, info.name)
                if (
                    current is None
                    or current.container_id.lower() != info.container_id.lower()
                    or not current.is_running
                ):
                    return DestructiveResult(
                        info.name,
                        "deferred",
                        "container state is unknown after unpause",
                    )
            elif (
                not current.is_running
                and current.state not in _TERMINAL_STOPPED_STATES
            ):
                return DestructiveResult(
                    info.name,
                    "deferred",
                    f"container state {current.state!r} is transitional or unknown",
                )

            inspected = inspect_container(current.container_id)
            try:
                generation = container_generation(inspected)
            except RescueError as exc:
                return DestructiveResult(
                    info.name,
                    "deferred",
                    f"container execution generation is unknown: {exc}",
                )
            policy_errors = restricted_policy_errors(
                current,
                fleet,
                workspace_folder=fleet.workspace_folder or config.workspace_folder,
                exec_user=user,
                inspected=inspected,
            )
            unsafe_policy_errors = [
                error
                for error in policy_errors
                if error not in _ALLOWED_REPLACEMENT_DRIFT
            ]
            if unsafe_policy_errors:
                return DestructiveResult(
                    info.name,
                    "deferred",
                    "restricted policy validation failed: "
                    + "; ".join(unsafe_policy_errors),
                )

            if not current.is_running:
                existing_rescue = verified_capture_for_instance(
                    info.name,
                    info.container_id,
                    generation,
                )
                if existing_rescue is None and not force_abandon:
                    return DestructiveResult(
                        info.name,
                        "deferred",
                        "container is already stopped and tmpfs evidence is unavailable; "
                        "explicit telemetry abandonment is required",
                    )
                with _rescue_pin_context(
                    info,
                    existing_rescue,
                    generation,
                    hold.expires_at,
                ) as rescue_pin:
                    # Prove the loss marker still describes the held stopped instance.
                    verify_deploy_hold(info.name, hold.token)
                    latest = get_container(config, info.name)
                    if (
                        latest is None
                        or latest.container_id.lower() != info.container_id.lower()
                        or latest.state == "paused"
                    ):
                        return DestructiveResult(
                            info.name,
                            "deferred",
                            "container identity/state changed before destruction",
                        )
                    if existing_rescue is None:
                        record_telemetry_loss(
                            container=info.name,
                            container_instance=info.container_id,
                            container_generation=generation,
                            reason="container_not_running",
                        )
                    verify_deploy_hold(info.name, hold.token)
                    latest = get_container(config, info.name)
                    if (
                        latest is None
                        or latest.container_id.lower() != info.container_id.lower()
                        or latest.is_running
                        or latest.state == "paused"
                    ):
                        return DestructiveResult(
                            info.name,
                            "deferred",
                            "container identity/state changed before destruction",
                        )
                    if rescue_pin is not None:
                        verify_pinned_capture(rescue_pin)
                    _verify_generation(latest.container_id, generation)
                    _perform_action(
                        info.name,
                        hold.token,
                        hold.expires_at,
                        latest,
                        action=action,
                        confirm=confirm,
                        action_timeout=action_timeout,
                    )
                    return DestructiveResult(
                        info.name,
                        success_status,
                        rescue=existing_rescue,
                        telemetry_abandoned=existing_rescue is None,
                    )

            try:
                bash_path, home = resolve_executable(
                    current.container_id,
                    user,
                    inspected,
                    kind="bash",
                    deadline=deadline,
                )
            except RestrictedExecError as exc:
                return DestructiveResult(
                    info.name,
                    "deferred",
                    f"session liveness helper is unavailable: {exc}",
                )

            liveness = probe_session_liveness(
                current,
                user=user,
                bash_path=bash_path,
                home=home,
            )
            if liveness.state == "unknown":
                return DestructiveResult(
                    info.name,
                    "deferred",
                    f"session liveness is unknown: {liveness.reason}",
                )
            if liveness.state == "active":
                return DestructiveResult(
                    info.name,
                    "deferred",
                    "active Copilot session-state lock present",
                )

            rescue = None
            telemetry_abandoned = False
            abandon_reason = None
            try:
                rescue = capture_restricted_sessions(
                    config,
                    fleet,
                    container=info.name,
                    container_instance=info.container_id,
                    user=user,
                    deadline=deadline,
                )
            except (RescueError, OSError) as exc:
                if not force_abandon:
                    return DestructiveResult(
                        info.name,
                        "deferred",
                        f"session evidence rescue failed: {exc}",
                    )
                telemetry_abandoned = True
                abandon_reason = "rescue_failed"

            with _rescue_pin_context(
                info,
                rescue,
                generation,
                hold.expires_at,
            ) as rescue_pin:
                # This proof and identity read anchor the final probe to the instance
                # currently covered by our admission hold.
                verify_deploy_hold(info.name, hold.token)
                latest = get_container(config, info.name)
                if (
                    latest is None
                    or latest.container_id.lower() != info.container_id.lower()
                    or not latest.is_running
                ):
                    return DestructiveResult(
                        info.name,
                        "deferred",
                        "container identity/state changed before final liveness probe",
                        rescue=rescue,
                        telemetry_abandoned=telemetry_abandoned,
                    )
                final_liveness = probe_session_liveness(
                    latest,
                    user=user,
                    bash_path=bash_path,
                    home=home,
                )
                if final_liveness.state != "idle":
                    reason = (
                        "active Copilot session-state lock present"
                        if final_liveness.state == "active"
                        else f"session liveness is unknown: {final_liveness.reason}"
                    )
                    return DestructiveResult(
                        info.name,
                        "deferred",
                        reason,
                        rescue=rescue,
                        telemetry_abandoned=telemetry_abandoned,
                    )
                # Re-prove ownership and identity after the probe; the duplicate is
                # deliberate because the probe itself creates a check/action window.
                verify_deploy_hold(info.name, hold.token)
                latest = get_container(config, info.name)
                if (
                    latest is None
                    or latest.container_id.lower() != info.container_id.lower()
                    or not latest.is_running
                ):
                    return DestructiveResult(
                        info.name,
                        "deferred",
                        "container identity/state changed immediately before "
                        "lifecycle action",
                        rescue=rescue,
                        telemetry_abandoned=telemetry_abandoned,
                    )
                if abandon_reason is not None:
                    record_telemetry_loss(
                        container=info.name,
                        container_instance=info.container_id,
                        container_generation=generation,
                        reason=abandon_reason,
                    )
                    verify_deploy_hold(info.name, hold.token)
                if rescue_pin is not None:
                    verify_pinned_capture(rescue_pin)
                _verify_generation(latest.container_id, generation)
                _perform_action(
                    info.name,
                    hold.token,
                    hold.expires_at,
                    latest,
                    action=action,
                    confirm=confirm,
                    action_timeout=action_timeout,
                )
                return DestructiveResult(
                    info.name,
                    success_status,
                    rescue=rescue,
                    telemetry_abandoned=telemetry_abandoned,
                )
    except ProviderAdmissionError as exc:
        return DestructiveResult(
            info.name,
            "deferred",
            f"provider lifecycle hold unavailable: {exc}",
        )
    except RescueError as exc:
        return DestructiveResult(
            info.name,
            "deferred",
            f"rescue safety validation failed: {exc}",
        )


def _rescue_pin_context(
    info: DockerContainerInfo,
    rescue: dict | None,
    generation: str,
    expires_at: float,
) -> AbstractContextManager:
    if rescue is None:
        return nullcontext(None)
    capture_id = rescue.get("capture_id")
    if not isinstance(capture_id, str):
        raise RescueError("verified rescue metadata has no capture identity")
    return pin_verified_capture(
        info.name,
        info.container_id,
        generation,
        capture_id,
        expires_at=expires_at,
    )


def _verify_generation(container_id: str, expected: str) -> None:
    actual = container_generation(inspect_container(container_id))
    if actual != expected:
        raise RescueError(
            "container execution generation changed before lifecycle action"
        )


def _perform_action(
    name: str,
    hold_token: str,
    hold_expires_at: float,
    current: DockerContainerInfo,
    *,
    action: Callable[[DockerContainerInfo, float], None],
    confirm: Callable[[DockerContainerInfo], bool],
    action_timeout: float,
) -> None:
    """Run and confirm the destructive action before the admission hold expires."""
    remaining = (
        hold_expires_at
        - time.time()
        - _CONFIRMATION_AND_CLEANUP_GRACE
    )
    budget = min(action_timeout, remaining)
    if budget <= 0:
        raise ProviderAdmissionError(
            f"Provider lifecycle hold for '{name}' has no action time remaining"
        )
    verify_deploy_hold(name, hold_token)
    try:
        action(current, budget)
        verify_deploy_hold(name, hold_token)
        if not confirm(current):
            raise RuntimeError(
                f"Container '{name}' lifecycle action did not reach its confirmed state"
            )
    except Exception:
        mark_deploy_hold_uncertain(name, hold_token)
        raise
    verify_deploy_hold(name, hold_token)

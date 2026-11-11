"""Resolve the serving monitor's retained runtime without trusting a stale lock."""

from __future__ import annotations

import os
import time
import logging
from contextlib import nullcontext
from pathlib import Path

from zdd import diagnostics, routing

from . import locks, status_monitor_cutover as cutover


READ_ONLY_OBSERVATION_CAPABILITY = "worktrees-monitor-observation/read-only-v1"


class AsyncRetirementUnsupported(ValueError):
    def __init__(self, message: str, identity: tuple[int, str, str | None]):
        super().__init__(message)
        self.identity = identity


def is_monitor_commandline(cmdline: str) -> bool:
    tokens = cmdline.split()
    for index in range(len(tokens) - 2):
        if tokens[index] == "-m" and tokens[index + 1] == "agent_worktrees":
            return tokens[index + 2] == "status-monitor"
    return False


def _runtime_slot(root: Path, prefix: str | Path) -> Path:
    path = Path(prefix).resolve(strict=True)
    if path.name == ".venv" and path.parent.parent == (root / "versions").resolve():
        return path.parent
    return path


def candidate_serving(root: Path, slot: Path) -> bool:
    from .runtime_identity import slot_generation
    from .runtime_monitor_retirements import _exit_state

    directory = root / "status-monitor-routing"
    snapshot = routing.read_table(directory, strict=True)
    raw = (snapshot or {}).get("active")
    endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
    if endpoint is None or endpoint.process_start_time is None:
        return False
    if _exit_state({"pid": endpoint.pid, "start": endpoint.process_start_time}) is True:
        return False
    before = locks.process_start_time(endpoint.pid)
    if before is None or before != endpoint.process_start_time:
        return False
    generation = slot_generation(slot)
    token = cutover.read_control_token(root)
    if token is None:
        return False
    health = cutover.ControlClient(endpoint.base_url, token=token).health()
    prefix = health.get("runtime_prefix")
    return (
        health.get("status") == "ready"
        and health.get("published") is True
        and health.get("pid") == endpoint.pid
        and health.get("runtime_slot") == slot.name
        and isinstance(prefix, str) and Path(prefix).is_absolute()
        and _runtime_slot(root, prefix) == slot.resolve()
        and (generation is None or health.get("runtime_generation") == generation)
        and locks.process_start_time(endpoint.pid) == before
        and routing.read_table(directory, strict=True) == snapshot
    )


def routed_identity(
    runtime_home: Path | None = None, *, require_async_retirement: bool = False,
    require_capabilities: bool = False,
) -> tuple[int, str, str | None] | None:
    directory = cutover.routing_dir() if runtime_home is None else cutover.routing_dir(runtime_home)
    snapshot = routing.read_table(directory, strict=True)
    endpoints = []
    for key in ("active", "previous"):
        raw = (snapshot or {}).get(key)
        if raw is None:
            continue
        endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        if endpoint is None or type(endpoint.pid) is not int or endpoint.pid <= 0:
            raise ValueError(f"Routed monitor {key} identity is malformed")
        endpoints.append((key, endpoint))
    endpoint = None
    for key, candidate in endpoints:
        from .runtime_monitor_retirements import _exit_state, retirement_only

        if candidate.process_start_time is not None and _exit_state({
            "pid": candidate.pid, "start": candidate.process_start_time,
        }) is True:
            continue
        if diagnostics._pid_alive(candidate.pid):
            if key != "active":
                if retirement_only(directory.parent, candidate.to_dict()):
                    continue
                raise ValueError("Live fallback monitor requires owner-led route recovery")
            endpoint = candidate
    if endpoint is None:
        return None
    pid = endpoint.pid
    if type(pid) is not int or pid <= 0:
        raise ValueError("Routed monitor has no attributable PID")
    if not diagnostics._pid_alive(pid):
        return None
    before = locks.process_start_time(pid)
    if before is None or (
        endpoint.process_start_time is not None and endpoint.process_start_time != before
    ) or (
        require_async_retirement and endpoint.process_start_time != before
    ):
        raise ValueError("Routed monitor process identity is unconfirmed")
    token = cutover.read_control_token(directory.parent)
    if token is None and not require_async_retirement:
        raise ValueError("Serving monitor control token is unavailable")
    health = {}
    if token is not None:
        try:
            health = cutover.ControlClient(endpoint.base_url, token=token).health()
        except (OSError, ValueError, ConnectionError):
            if not require_async_retirement:
                raise
    if (
        (health and health.get("pid") != pid)
        or (not require_async_retirement and health.get("status") not in {"ready", "draining", "unavailable"})
        or locks.process_start_time(pid) != before
        or routing.read_table(directory, strict=True) != snapshot
    ):
        raise ValueError("Routed monitor identity changed during admission")
    slot = health.get("runtime_slot")
    prefix = health.get("runtime_prefix")
    authenticated_prefix = prefix
    if require_async_retirement and not prefix:
        record = locks.read_lock(directory.parent / "status-monitor.lock") or {}
        if (record.get("pid"), record.get("start_time")) == (pid, before):
            prefix = record.get("prefix")
    if isinstance(prefix, str) and Path(prefix).is_absolute():
        root = directory.parent if runtime_home is None else runtime_home
        try:
            path = _runtime_slot(root, prefix)
            slot = path.name if path.parent == (root / "versions").resolve() else None
        except OSError:
            if not require_async_retirement or require_capabilities:
                raise
            slot = None
    identity = pid, before, slot if isinstance(slot, str) else None
    if require_capabilities:
        if not isinstance(authenticated_prefix, str) or not Path(authenticated_prefix).is_absolute():
            raise ValueError("Serving monitor runtime prefix is unavailable")
        from .runtime_monitor_retirements import CAPABILITY
        from .status_monitor_recovery_contract import CAPABILITY as RECOVERY_CAPABILITY, ReceiverIdentity

        capabilities = health.get("capabilities")
        if not isinstance(capabilities, list) or CAPABILITY not in capabilities:
            raise AsyncRetirementUnsupported(
                "Serving monitor does not support safe asynchronous retirement", identity,
            )
        if RECOVERY_CAPABILITY not in capabilities:
            raise AsyncRetirementUnsupported(
                "Serving monitor does not support guarded preparation recovery", identity,
            )
        ReceiverIdentity.parse({
            "pid": pid, "processStartTime": before, "runtimeGeneration": health.get("runtime_generation"),
        })
    return identity


def activation_liveness(
    monitor_was_live: bool | None, retired: tuple[int, str] | None, *,
    asynchronous: bool, guard, handles, predecessor: dict | None,
) -> tuple[bool, bool]:
    """Ignore retained exited discovery only after exact installer-owned retirement."""
    from zdd import process_exit
    from . import status_monitor_runtime as runtime, runtime_monitor_retirements as retirements

    lock_live = cutover._monitor_lock_is_live()
    monitor_was_live = lock_live if monitor_was_live is None else bool(monitor_was_live)
    if retired is None:
        if asynchronous and predecessor is None and retirements.retirement_only(
            runtime._aw_runtime_home(), locks.read_lock(runtime._monitor_lock_path()),
        ):
            return False, False
        return lock_live, monitor_was_live
    if not asynchronous or guard is None or handles is None:
        raise ValueError("Cold startup requires the owning installer and cutover guard")
    record = locks.read_lock(runtime._monitor_lock_path()) or {}
    if lock_live and (record.get("pid"), record.get("start_time")) != retired:
        raise ValueError("A different monitor owns the lock after legacy retirement")
    if process_exit.observe_original_exit(*retired) is not True:
        raise ValueError("Legacy predecessor native exit is unconfirmed before startup")
    return False, False


def retire_legacy(root: Path, expected: tuple[int, str, str | None], *, guard) -> dict:
    """Retire only the authenticated incumbent while its installer owns cutover."""
    from agent_procutil.process_retirement import PinnedProcess, birth_identity
    from zdd import process_exit

    directory = root / "status-monitor-routing"
    if (
        not isinstance(guard, cutover.SingleInstance) or not guard.held
        or guard.lock_path.resolve() != (directory / "cutover.lock").resolve()
    ):
        raise ValueError("Legacy retirement requires the held installation cutover guard")
    pid, start, slot = expected
    if pid == os.getpid() or slot is None:
        raise ValueError("Legacy retirement requires an attributable predecessor runtime")
    with routing.routing_lock(directory, deadline=time.monotonic() + 5.0):
        snapshot = routing.read_table(directory, strict=True)
        try:
            observed = routed_identity(root, require_async_retirement=True, require_capabilities=True)
        except AsyncRetirementUnsupported as error:
            if error.identity != expected or monitor_slot(root, expected[:2]) != slot:
                raise ValueError("Legacy predecessor ownership changed before retirement") from error
        else:
            if observed is None and process_exit.observe_original_exit(pid, start):
                return {"pid": pid, "exit_confirmed": True, "already_exited": True}
            raise ValueError("Legacy predecessor no longer requires cold takeover")
        with PinnedProcess(pid, birth_identity(pid)) as process:
            if (
                locks.process_start_time(pid) != start
                or routing.read_table(directory, strict=True) != snapshot
            ):
                raise ValueError("Legacy predecessor identity or route changed before retirement")
            process.terminate(timeout=5.0)
            if not process.exited():
                raise RuntimeError("Legacy predecessor native exit is unconfirmed")
    return {"pid": pid, "exit_confirmed": True, "forced": True}


def monitor_slot(root: Path, identity: tuple[int, str]) -> str | None:
    record = locks.read_lock(root / "status-monitor.lock")
    if locks.lock_is_live(record) and record.get("pid") == identity[0]:
        if (
            locks.process_start_time(identity[0]) != identity[1]
            or not isinstance(record.get("prefix"), str)
            or not record["prefix"]
        ):
            raise ValueError("Serving monitor runtime identity changed before retention")
        prefix = _runtime_slot(root, record["prefix"])
        return prefix.name if prefix.parent == (root / "versions").resolve() else None
    else:
        routed = cutover.routed_monitor_identity(root)
        if (
            routed is None or routed[:2] != identity or not routed[2]
            or Path(routed[2]).name != routed[2]
        ):
            raise ValueError("Routed monitor runtime identity cannot be retained")
        prefix = (root / "versions" / routed[2]).resolve(strict=True)
    if prefix.parent == (root / "versions").resolve():
        return prefix.name
    raise ValueError("Serving monitor runtime is outside the retained runtime root")


def async_predecessor(
    root: Path, predecessor: dict | None, *,
    observed: tuple[int, str, str | None] | None, incumbent_expected: bool,
) -> tuple[dict | None, dict]:
    """Bind admitted predecessor custody to the exact route used by publication."""
    if observed is None:
        if incumbent_expected:
            raise ValueError("Live unrouted monitor cannot safely retire asynchronously")
        return None, {}
    native = {"pid": observed[0], "start": observed[1], "slot": observed[2]}
    if predecessor is not None and any(predecessor.get(key) != value for key, value in native.items()):
        raise ValueError("Serving predecessor changed before retirement reservation")
    from . import status_monitor_recovery_contract as recovery

    expected = recovery.active_endpoint(root / "status-monitor-routing")
    if (expected.pid, expected.process_start_time) != observed[:2]:
        raise ValueError("Serving predecessor route identity changed")
    options = {
        "expected_old": expected,
        "refuse_old": lambda _active: (
            None if locks.process_start_time(expected.pid) == expected.process_start_time
            else "asynchronous predecessor identity changed"
        ),
    }
    try:
        from .runtime_monitor_retirements import CAPABILITY

        health = cutover.ControlClient(
            expected.base_url, token=cutover.load_or_create_control_token(root),
        ).health()
        capabilities = health.get("capabilities")
        self_retiring = (
            health.get("pid") == expected.pid
            and isinstance(capabilities, list)
            and CAPABILITY in capabilities
            and recovery.CAPABILITY in capabilities
            and isinstance(health.get("runtime_generation"), str)
            and bool(health["runtime_generation"])
        )
    except (OSError, ValueError, ConnectionError):
        self_retiring = False
    if not self_retiring:
        predecessor_owner = recovery.observe(root / "status-monitor-routing", cutover.control_token_path(root))
        if predecessor_owner.route != expected:
            raise ValueError("Serving predecessor route identity changed")
        options["retire_failed_predecessor"] = lambda successor: recovery.retire_failed_predecessor(
            root / "status-monitor-routing", predecessor_owner, successor,
        )
    return {
        **native, "release_claim": (predecessor or {}).get("release_claim"),
    }, options


def publish_lock(path: Path, *, extra: dict, routing_held: bool = False) -> bool:
    """Renew discovery only while this exact native owner holds the route."""
    directory = path.parent / "status-monitor-routing"
    try:
        with nullcontext() if routing_held else routing.routing_lock(directory, deadline=time.monotonic() + 5.0):
            table = routing.read_table(directory, strict=True)
            raw = table.get("active") if isinstance(table, dict) else None
            endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
            if (
                endpoint is None or endpoint.pid != os.getpid()
                or not isinstance(endpoint.process_start_time, str) or not endpoint.process_start_time
                or endpoint.process_start_time != locks.process_start_time(os.getpid())
            ):
                return False
            written = locks.write_lock(path, extra=extra)
            if not written:
                logging.getLogger(__name__).error("Monitor discovery publication failed")
            return written
    except (OSError, ValueError, routing.RoutingLockTimeout, routing.ActivePublicationRefused) as error:
        logging.getLogger(__name__).error("Monitor discovery publication deferred: %s", error)
        return False

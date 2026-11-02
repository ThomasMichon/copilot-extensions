"""Guard-owned cancellation of an interrupted asynchronous publication."""

from __future__ import annotations

import time
import sys

from zdd import breadcrumb, diagnostics, process_exit, publication_history, routing

from . import runtime_monitor_retirements as retirements
from . import status_monitor_cutover as cutover
from . import status_monitor_recovery_contract as recovery


def _same_owner(left, right) -> bool:
    return left is not None and right is not None and (
        left.pid, left.process_start_time, left.bind, left.port, left.version,
    ) == (
        right.pid, right.process_start_time, right.bind, right.port, right.version,
    )


def settle_dead_committed(root, record) -> bool:
    """Retain irreversible retirement grants when their selected successor exits."""
    directory = root / "status-monitor-routing"
    if (
        record.get("self_retirement") is not True or record.get("state") != "committed"
        or breadcrumb.read_breadcrumb(directory) != record
    ):
        return False
    pid, start = record.get("new_pid"), record.get("new_process_start_time")
    if type(pid) is not int or not isinstance(start, str) or not start:
        return False
    if retirements._exit_state({"pid": pid, "start": start}) is not True:
        return False
    old = recovery.endpoint(record["old"]) if record.get("old") is not None else None
    records = [
        value for _path, value in retirements._records(root)
        if old is not None and (value["pid"], value["start"]) == (old.pid, old.process_start_time)
    ]
    if any(value["state"] != "retiring" for value in records) or (
        old is not None and not records
        and retirements._exit_state({"pid": old.pid, "start": old.process_start_time}) is not True
    ):
        return False
    table = routing.read_table(directory, strict=True) or {}
    routing._validate_endpoint_rows(table)
    active = routing.Endpoint.from_dict(table["active"]) if table.get("active") else None
    if active is not None:
        if (active.pid, active.process_start_time, active.port) != (pid, start, record.get("new_port")):
            raise ValueError("Committed dead successor has a foreign selected owner")
        routing.withdraw_active_guarded(
            directory, expected_active=active, refuse_current=lambda _raw: None,
            lock_deadline=time.monotonic() + 1,
        )
    if not breadcrumb.clear_breadcrumb(directory):
        raise OSError("Committed dead successor witness cleanup remains pending")
    return True


def abort(root, record) -> bool:
    """Caller holds cutover custody; only proven passive/exit outcomes may roll back."""
    directory = root / "status-monitor-routing"
    if (
        record.get("self_retirement") is not True
        or breadcrumb.read_breadcrumb(directory) != record
        or record.get("state") not in ("started", "flipped", "publication-uncertain", "committed", "rolled_back")
    ):
        return False
    table = routing.read_table(directory, strict=True) or {}
    routing._validate_endpoint_rows(table)
    active = routing.Endpoint.from_dict(table["active"]) if table.get("active") else None
    old = recovery.endpoint(record["old"]) if record.get("old") is not None else None
    pid, start, port = (
        record.get("new_pid"), record.get("new_process_start_time"), record.get("new_port"),
    )
    selected = active is not None and (
        active.pid, active.process_start_time, active.port,
    ) == (pid, start, port)
    if active is not None and not selected and not _same_owner(active, old):
        raise ValueError("Interrupted publication has a foreign selected owner")
    records = [
        value for _path, value in retirements._records(root)
        if old is not None and (value["pid"], value["start"]) == (old.pid, old.process_start_time)
    ]
    if any(value["state"] == "retiring" for value in records) or (
        record["state"] == "committed" and not records
    ):
        return False
    if pid is None:
        # The initial witness precedes spawn and every publication requires the
        # subsequent identity witness. It cannot authorize an unrecorded signal.
        history = publication_history.read(table)
        if (
            record["state"] not in {"started", "rolled_back"} or start is not None or selected
            or history is None or history["epoch"] != record.get("publication_witness")
        ):
            return False
        with routing.routing_lock(directory, deadline=time.monotonic() + 1):
            if breadcrumb.read_breadcrumb(directory) != record:
                return False
            record = breadcrumb.write_breadcrumb(
                directory, state="started", old=record.get("old"), new_port=port,
                started_at=record.get("started_at"), publication_witness=record.get("publication_witness"),
                self_retirement=True, pid=record.get("pid"), error="Passive startup cancelled",
                _routing_locked=True,
            )
    elif type(pid) is not int or pid <= 0 or not isinstance(start, str) or not start:
        raise ValueError("Interrupted candidate native identity is unavailable")
    else:
        exited = retirements._exit_state({"pid": pid, "start": start})
        if exited is None:
            raise ValueError("Interrupted candidate native exit is unavailable")
        if not exited:
            if selected or not publication_history.candidate_never_published(table, record, pid, start):
                return False
            token = cutover.control_token_path(root).read_text(encoding="utf-8").strip()
            if not token:
                raise ValueError("Interrupted candidate control identity is unavailable")
            candidate = cutover.ControlClient(
                f"http://127.0.0.1:{port}", token=token, config_dir=directory, timeout=1,
            )
            health = candidate.health()
            if (
                health.get("pid") != pid or health.get("published") is not False
                or health.get("status") != "ready"
            ):
                return False
            if sys.platform == "darwin":
                from .runtime_monitor_preparation import ACTION, CAPABILITY

                if CAPABILITY not in health.get("capabilities", []):
                    raise ValueError("Passive native cooperative cancellation is unsupported")
                with routing.routing_lock(directory, deadline=time.monotonic() + 1), process_exit.open_process(pid, start) as reference:
                    if breadcrumb.read_breadcrumb(directory) != record or routing.read_table(directory, strict=True) != table:
                        raise ValueError("Passive publication changed before cooperative cancellation")
                    accepted = candidate._request(ACTION, {
                        "pid": pid, "start": start, "port": port,
                        "started_at": record.get("started_at"),
                        "publication_witness": record.get("publication_witness"),
                    })
                    if accepted.get("shutdown") is not True or not reference.wait(1):
                        return False
                termination = {"identity_verified": True, "exit_confirmed": True}
            else:
                termination = _terminate_passive(directory, record, table, pid, start)
            if (
                termination.get("identity_verified") is not True
                or termination.get("exit_confirmed") is not True
            ):
                return False
    old_exit = retirements._exit_state({"pid": old.pid, "start": old.process_start_time}) if old else True
    if old_exit is None:
        raise ValueError("Interrupted predecessor native exit is unavailable")
    if old is not None and not old_exit:
        _resume_predecessor(root, directory, old, active)
    elif active is not None:
        routing.withdraw_active_guarded(
            directory, expected_active=active, lock_deadline=time.monotonic() + 1,
            refuse_current=lambda _raw: None,
        )
    breadcrumb.write_breadcrumb(
        directory, state="started", old=record.get("old"), new_port=port, new_pid=pid,
        new_process_start_time=start, publication_witness=record.get("publication_witness"),
        started_at=record.get("started_at"), self_retirement=True, pid=record.get("pid"),
        error="Precommit route restored; retirement cancellation pending",
    )
    for value in records:
        retirements.cancel(
            root, {key: value[key] for key in ("pid", "start", "slot", "release_claim")},
        )
    breadcrumb.write_breadcrumb(
        directory, state="rolled_back", old=record.get("old"), new_port=port, new_pid=pid,
        new_process_start_time=start, publication_witness=record.get("publication_witness"),
        started_at=record.get("started_at"), self_retirement=True, pid=record.get("pid"),
    )
    if not breadcrumb.clear_breadcrumb(directory):
        raise OSError("Interrupted publication witness cleanup remains pending")
    return True


def _terminate_passive(directory, record, table, pid, start):
    with routing.routing_lock(directory, deadline=time.monotonic() + 1):
        if (
            breadcrumb.read_breadcrumb(directory) != record
            or routing.read_table(directory, strict=True) != table
        ):
            raise ValueError("Passive publication changed before native termination")
        return diagnostics.terminate_pid_if_identity(pid, start, exit_timeout=1)


def _resume_predecessor(root, directory, old, active):
    token = cutover.control_token_path(root).read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("Interrupted predecessor control identity is unavailable")
    client = cutover.ControlClient(old.base_url, token=token, config_dir=directory, timeout=1)
    health = client.health()
    receiver = recovery.ReceiverIdentity.parse({
        "pid": health.get("pid"), "processStartTime": old.process_start_time,
        "runtimeGeneration": health.get("runtime_generation"),
    })
    if (
        not recovery.same_receiver(old, receiver)
        or recovery.CAPABILITY not in health.get("capabilities", [])
        or health.get("status") not in {"ready", "draining", "unavailable"}
    ):
        raise ValueError("Interrupted predecessor cannot safely resume")
    if _same_owner(active, old):
        routing.confirm_active(directory, active)
    else:
        routing.publish_active_with_previous_guarded(
            directory, bind=old.bind, port=old.port, pid=old.pid, version=old.version,
            process_start_time=old.process_start_time, expected_active=active,
            demote_existing=True,
        )
    client.guarded_predecessor = recovery.GuardedPredecessor(old, receiver, token)
    if client.undrain().get("draining") is not False or not recovery.serving_ready(client.health()):
        raise ValueError("Interrupted predecessor readiness remains pending")


def reconcile_retiring_route(root):
    """Caller owns cutover guards; never promote an already granted fallback."""
    directory = root / "status-monitor-routing"
    table = routing.read_table(directory, strict=True) or {}
    routing._validate_endpoint_rows(table)
    active = routing.Endpoint.from_dict(table["active"]) if table.get("active") else None
    previous = routing.Endpoint.from_dict(table["previous"]) if table.get("previous") else None
    if (
        active is None or previous is None
        or not retirements.retirement_only(root, previous.to_dict())
        or retirements._exit_state({"pid": active.pid, "start": active.process_start_time}) is not True
    ):
        return
    routing.withdraw_active_guarded(
        directory, expected_active=active, refuse_current=lambda _raw: None,
        lock_deadline=time.monotonic() + 1,
    )


def recover_abandoned(root) -> bool:
    """Under the caller's guard, settle only a departed installer's exact witness."""
    record = breadcrumb.read_breadcrumb(root / "status-monitor-routing")
    if record is None:
        if not breadcrumb.breadcrumb_path(root / "status-monitor-routing").exists():
            reconcile_retiring_route(root)
        return False
    if not isinstance(record, dict) or breadcrumb.owner_exited(record) is not True:
        return False
    return settle_dead_committed(root, record) or abort(root, record)

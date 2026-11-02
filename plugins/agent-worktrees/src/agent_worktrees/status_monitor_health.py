"""Compose the elected resident's health without exposing private work items."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from collections.abc import Callable
from typing import Protocol

from zdd.cutover import CutoverError
from zdd import breadcrumb, diagnostics

from .runtime_identity import PROCESS_GENERATION, PROCESS_VERSION
from .runtime_monitor_retirements import CAPABILITY


class HealthSurface(Protocol):
    def health(self) -> dict: ...


def env_timeout(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw:
        try:
            parsed = float(raw)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return default


def check_successor(
    host: str, port: int, client_factory: Callable[[str], HealthSurface], *,
    require_async_retirement: bool = False,
    expected_identity: tuple[int, str] | None = None,
) -> bool:
    try:
        status = client_factory(f"http://{host}:{port}").health()
        if expected_identity is not None and (
            (status.get("pid"), status.get("process_start_time")) != expected_identity
            or diagnostics.process_start_time(expected_identity[0]) != expected_identity[1]
        ):
            return False
        if status.get("status") != "ready":
            return False
        capabilities = status.get("capabilities")
        if require_async_retirement and (
            not isinstance(capabilities, list) or CAPABILITY not in capabilities
        ):
            raise CutoverError("Incoming monitor does not support asynchronous predecessor retirement")
        if require_async_retirement:
            from .status_monitor_recovery_contract import CAPABILITY as RECOVERY, ReceiverIdentity

            if RECOVERY not in capabilities:
                raise CutoverError("Incoming monitor does not support guarded preparation recovery")
            try:
                ReceiverIdentity.parse({
                    "pid": status.get("pid"), "processStartTime": status.get("process_start_time"),
                    "runtimeGeneration": status.get("runtime_generation"),
                })
            except ValueError as error:
                raise CutoverError("Incoming monitor preparation recovery identity is unavailable") from error
        roster = status.get("worktrees")
        if not isinstance(roster, dict):
            return True
        return roster.get("status") == "ready" or (
            status.get("published") is False and roster.get("status") == "unready"
        )
    except CutoverError:
        raise
    except Exception:
        return False


def prepared_check(handle, directory, client_factory):
    pid = getattr(handle, "pid", None)
    start = getattr(handle, "process_start_time", None) or (
        diagnostics.process_start_time(pid) if type(pid) is int and pid > 0 else None
    )

    def check(host, port):
        nonlocal start
        if type(pid) is not int or pid <= 0 or handle.poll() is not None:
            return False
        if start is None:
            record = breadcrumb.read_breadcrumb(directory)
            if isinstance(record, dict) and (
                record.get("self_retirement") is True
                and (record.get("new_pid"), record.get("new_port")) == (pid, port)
            ):
                start = record.get("new_process_start_time")
        if not isinstance(start, str) or not start:
            return False
        # The orchestrator rechecks this captured birth at its publication boundary.
        handle.process_start_time = start
        return check_successor(
            host, port, client_factory, require_async_retirement=True, expected_identity=(pid, start),
        )

    return check


def response(
    *, worktrees: HealthSurface, receipts: HealthSurface,
    repositories: HealthSurface, allocations: HealthSurface,
    admission_closed: bool, published: bool, shutdown: bool,
    retirements: dict | None = None,
) -> dict:
    roster = worktrees.health()
    receipt = receipts.health()
    repository = repositories.health()
    allocation = allocations.health()
    roster["capabilities"] = [
        *roster["capabilities"], *receipt["capabilities"],
        *repository["capabilities"], *allocation["capabilities"],
        CAPABILITY,
    ]
    if shutdown:
        roster["worktrees"]["status"] = "shutdown"
    ready = roster["worktrees"]["status"] in {"ready", "unready"}
    environment = Path(sys.prefix).resolve()
    slot = environment.parent if environment.name == ".venv" else environment
    return {
        "status": "draining" if admission_closed else ("ready" if ready else "unavailable"),
        "published": published, "pid": os.getpid(), **roster,
        "process_start_time": diagnostics.process_start_time(os.getpid()),
        "promptReceipt": receipt["promptReceipt"],
        "repositories": repository["repositories"],
        "allocationLinker": allocation["allocationLinker"],
        "runtime_generation": PROCESS_GENERATION, "version": PROCESS_VERSION,
        "runtime_slot": slot.name,
        "runtime_prefix": os.path.realpath(sys.prefix),
        "predecessorRetirement": retirements,
    }

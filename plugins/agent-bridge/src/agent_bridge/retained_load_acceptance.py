"""Journal the transport owner's accepted load before later registration."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from .acquisition_contract import AcquisitionRefused

EVENT = "retained_native_load_accepted"


def _refuse(message: str) -> AcquisitionRefused:
    return AcquisitionRefused("retained_recovery_held", message)


def _text(path: str) -> str:
    with open(path, encoding="ascii") as stream:
        value = stream.read(4097)
    if len(value) > 4096:
        raise ValueError("Hosted process metadata exceeds its budget")
    return value.strip()


def process(pid: int) -> dict[str, Any]:
    """Observe a live Linux process without reading its argv or environment."""
    fields = _text(f"/proc/{pid}/stat").rsplit(")", 1)[1].split()
    birth, parent = fields[19], int(fields[1])
    if fields[0] in {"Z", "X", "x"} or not birth.isdecimal() or len(birth) > 32 or parent < 0:
        raise ValueError("Hosted process identity is unavailable")
    return {"pid": pid, "start_time": birth, "parent_pid": parent}


def _namespaces() -> list[tuple[str, int, int]]:
    if os.readlink("/proc/self") != str(os.getpid()):
        raise ValueError("Hosted procfs observes another PID namespace")
    init = Path("/proc/1/ns/pid").stat()
    result = []
    for kind in ("pid", "mnt", "user"):
        observed = Path(f"/proc/self/ns/{kind}").stat()
        if observed.st_dev < 0 or observed.st_ino <= 0 or (
            kind == "pid" and (observed.st_dev, observed.st_ino) != (init.st_dev, init.st_ino)
        ):
            raise ValueError("Hosted namespace identity is unavailable")
        result.append((kind, observed.st_dev, observed.st_ino))
    return result


def epoch() -> dict[str, str]:
    """Capture the Linux/WSL epoch represented by the hosted writer contract."""
    if not sys.platform.startswith("linux"):
        raise ValueError("Hosted activation is unqualified on this platform")
    boot = str(UUID(_text("/proc/sys/kernel/random/boot_id")))
    cgroup = _text("/proc/1/cgroup")
    if (
        any(Path(marker).exists() for marker in ("/.dockerenv", "/run/.containerenv"))
        or any(tag in cgroup for tag in ("/docker", "/kubepods", "containerd", "/lxc", "libpod"))
    ):
        scope, init = _namespaces(), process(1)
        if init["parent_pid"] != 0 or _namespaces() != scope or process(1) != init:
            raise ValueError("Hosted container epoch changed")
        environment = hashlib.sha256(json.dumps(
            ["linux-container-v1", scope, init["start_time"]], separators=(",", ":"),
        ).encode("ascii")).hexdigest()[:32]
        platform = "linux"
    else:
        platform = "wsl" if "microsoft" in _text("/proc/sys/kernel/osrelease").lower() else "linux"
        environment = _text("/etc/machine-id")
        if len(environment) != 32 or any(c not in "0123456789abcdef" for c in environment):
            raise ValueError("Hosted environment identity is unavailable")
        if platform == "wsl":
            boot += ":" + process(1)["start_time"]
    return {"platform": platform, "environment_id": environment, "boot_id": boot}


def negotiate(support: dict[str, Any]) -> dict[str, str]:
    """Refuse unsupported evidence before a Host or native load can start."""
    if type(support.get("retained_hosted_activation")) is not int or support["retained_hosted_activation"] != 1:
        raise _refuse("Executing Worktrees owner lacks retained hosted ACP activation")
    try:
        return epoch()
    except (OSError, ValueError, IndexError) as exc:
        raise _refuse("Retained hosted ACP activation has no qualified local epoch") from exc


def host_binding(host: Any) -> dict[str, Any]:
    """Keep immutable transport addressing separate from frontend ownership."""
    value = {key: getattr(host, key) for key in (
        "session_id", "port", "host_pid", "child_pid", "protocol_version",
        "state_file", "created_at", "boundary", "endpoint",
    )}
    value["nonce_digest"] = hashlib.sha256(host.nonce.encode("utf-8")).hexdigest()
    return cast(dict[str, Any], json.loads(json.dumps(value)))


def prepare(owner: Any, client: Any) -> dict[str, Any]:
    """Bind the one impending RPC to its actual Host transport and writer."""
    from .acquisition_worktree_writer import current

    writer, host = current(), owner.host_record
    if (
        writer is None or host is None or host.boundary != "local" or host.endpoint
        or host.session_id != owner.session_id or not host.nonce
        or owner.host_process is None or owner.host_process.pid != host.host_pid
        or owner.host_process.poll() is not None
        or owner.socket is None or owner.streams is None
        or client.session_host_client is not owner.socket
        or owner.session.client is not client
    ):
        raise _refuse("Retained load lost its original local Host transport")
    try:
        observed_epoch = epoch()
        observed = {name: process(getattr(host, f"{name}_pid")) for name in ("host", "child")}
        if (
            observed_epoch != owner.hosted_epoch
            or observed["child"]["parent_pid"] != host.host_pid
            or any(observed[name]["start_time"] != host.extra.get(f"{name}_process_start")
                   for name in observed)
            or any(process(observed[name]["pid"]) != observed[name] for name in observed)
            or epoch() != observed_epoch
        ):
            raise ValueError("Original Host, child or epoch changed")
    except (OSError, ValueError, IndexError) as exc:
        raise _refuse("Retained load has unconfirmed physical Host custody") from exc
    return {
        "version": 1, "request": owner.request.model_dump(), "bridge_session_id": owner.session_id,
        "writer": writer.execution_requirement.model_dump(), "host": host_binding(host),
        "activation": {
            "version": 1, "operation_id": owner.request.operation_id,
            "session_id": owner.request.native_session_id,
            "head_revision": owner.request.head_revision, "epoch": observed_epoch,
            **{name: {key: observed[name][key] for key in ("pid", "start_time")}
               for name in observed},
        },
    }


def record(owner: Any, acceptance: dict[str, Any]) -> None:
    """Persist the RPC acknowledgement even if a stop arrived while awaiting it."""
    if owner.accepted_load is not None or owner.session.event_log is None:
        raise _refuse("Retained load acceptance cannot replace missing or prior evidence")
    owner.session.event_log.append(EVENT, acceptance)
    owner.manager.db.flush()
    owner.accepted_load = acceptance


def registration(owner: Any, client: Any) -> dict[str, Any]:
    """Convey only the acknowledged load on its unchanged original transport."""
    owner.require_current()
    accepted = owner.accepted_load
    if accepted is None or accepted != prepare(owner, client):
        raise _refuse("Retained registration has no matching accepted native load")
    return cast(dict[str, Any], json.loads(json.dumps(accepted["activation"])))

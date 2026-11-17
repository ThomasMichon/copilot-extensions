"""Shared record transactions for current-process and authenticated shell adapters.

An identity is not authorization. External adapters must retain and authenticate
the original process object before calling these private operations. No public
API accepts an identity override, and no environment field selects the caller.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from ._daemon_instance_identity import birth_identity, path_identity
from .daemon_instances import DaemonInstanceReceipt, DaemonInstanceSpec, DaemonLeasePolicy


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    birth: str

    def __post_init__(self) -> None:
        if type(self.pid) is not int or not 0 < self.pid < 2**32:
            raise ValueError("invalid instance process identity")
        if (
            not isinstance(self.birth, str) or not self.birth or len(self.birth) > 512
            or any(ord(character) < 32 for character in self.birth)
        ):
            raise ValueError("invalid instance process birth")

    @classmethod
    def current(cls) -> ProcessIdentity:
        pid = os.getpid()
        return cls(pid, birth_identity(pid))

    def matches(self, record: dict[str, str], prefix: str = "") -> bool:
        return (record.get(prefix + "pid"), record.get(prefix + "birth")) == (
            str(self.pid), self.birth,
        )


def reserve_for_process(
    spec: DaemonInstanceSpec, launcher: ProcessIdentity, *, environment=None,
) -> DaemonInstanceReceipt:
    from . import _daemon_instance_store as store

    context = store._test_context(environment)
    if context is not None:
        if spec.purpose == "interactive-test" and context["purpose"] != "interactive-test":
            raise ValueError("interactive daemon requires a pause-tolerant lifecycle owner")
        spec = replace(
            spec, purpose=context["purpose"], owner=context["owner"],
            registry_root=Path(context["registry_root"]),
            lease=DaemonLeasePolicy(float(context["ttl"]), float(context["max_lifetime"])),
        )
    source, source_member = store.source_provenance(spec.source)
    timing = store.lease_clock.fields(
        spec.lease.ttl_seconds, spec.lease.max_lifetime_seconds, store._monotonic_clock(),
    ) if sys.platform.startswith("linux") and spec.lease else {}
    root = store._root(spec.registry_root or store.registry_root(), create=True)
    runtime = store._runtime_anchor(spec.runtime_root)
    receipt = DaemonInstanceReceipt(uuid.uuid4().hex, root)
    now = store._wall_clock()
    device, inode = path_identity(runtime)
    record = {
        "schema": "1", "instance_id": receipt.instance_id, "state": "reserved",
        "service": spec.service, "purpose": spec.purpose, "lifecycle": spec.lifecycle,
        "owner": spec.owner or f"process:{launcher.pid}",
        "owner_pid": str(launcher.pid), "owner_birth": launcher.birth,
        "launcher_pid": str(launcher.pid), "launcher_birth": launcher.birth,
        "runtime_root": str(runtime), "runtime_device": device, "runtime_inode": inode,
        "source": str(source), "source_member": source_member,
        "created": str(now), "updated": str(now), "artifact_count": "0",
        "ttl": str(spec.lease.ttl_seconds if spec.lease else 0),
        "hard_deadline": str(now + spec.lease.max_lifetime_seconds if spec.lease else 0),
        "deadline": str(now + spec.lease.ttl_seconds if spec.lease else 0),
        "run_id": context["run_id"] if context else "", "protected": "0",
    }
    if context:
        record.update(owner_pid=str(context["pid"]), owner_birth=context["birth"])
    record.update(timing)
    with store._locked(receipt, wait_seconds=1) as path:
        if path.exists():
            raise OSError("daemon instance reservation already exists")
        store._write(path, record)
    return receipt


def bind_for_process(
    receipt: DaemonInstanceReceipt, process: ProcessIdentity, launcher: ProcessIdentity,
) -> None:
    from . import _daemon_instance_store as store

    with store._locked(receipt, wait_seconds=1) as path:
        record = store._read(path)
        if not launcher.matches(record, "launcher_"):
            raise ValueError("daemon launch belongs to a different process")
        if record["state"] in ("running", "finished") and process.matches(record):
            return
        if record["state"] not in ("reserved", "bound"):
            raise ValueError("daemon instance reservation already consumed")
        if record["state"] == "bound" and not process.matches(record):
            raise ValueError("daemon instance process changed")
        record.update(
            state="bound", pid=str(process.pid), birth=process.birth,
            updated=str(store._wall_clock()),
        )
        store._write(path, record)


def admit_for_process(
    receipt: DaemonInstanceReceipt, process: ProcessIdentity,
    *, before_admit: Callable[[dict[str, str]], None] | None = None,
) -> dict[str, str]:
    from . import _daemon_instance_store as store

    with store._locked(receipt, wait_seconds=1) as path:
        record = store._read(path)
        if record["state"] == "running":
            if not process.matches(record):
                raise ValueError("daemon instance reservation already admitted")
        elif record["state"] not in ("reserved", "bound"):
            raise ValueError("daemon instance admission refused")
        if record["purpose"] not in ("production", "unattended-test", "interactive-test"):
            raise ValueError("unknown daemon instance purpose")
        if store._expire(record, store._wall_clock()):
            raise ValueError("daemon instance admission refused")
        if record.get("pid") and not process.matches(record):
            raise ValueError("daemon instance admission process differs")
        if before_admit is not None:
            before_admit(record)
            if store._expire(record, store._wall_clock()):
                raise ValueError("daemon instance expired during lifetime admission")
        record.update(
            state="running", pid=str(process.pid), birth=process.birth,
            updated=str(store._wall_clock()),
        )
        store._write(path, record)
    return record


def finish_for_process(
    receipt: DaemonInstanceReceipt, process: ProcessIdentity,
    *, launcher: ProcessIdentity | None = None,
) -> None:
    from . import _daemon_instance_store as store

    with store._locked(receipt, wait_seconds=1) as path:
        record = store._read(path)
        if launcher is not None and not launcher.matches(record, "launcher_"):
            raise ValueError("daemon launch belongs to a different process")
        if not process.matches(record):
            raise ValueError("daemon instance custody belongs to another process")
        record.update(state="finished", updated=str(store._wall_clock()))
        store._write(path, record)

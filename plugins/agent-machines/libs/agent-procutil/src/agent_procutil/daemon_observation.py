"""Exact-process observations and producer-owned locators in daemon registrations."""

from __future__ import annotations

from pathlib import Path
import re

from .daemon_instances import DaemonInstanceReceipt, inspect_instances


def publish_file_observation(
    receipt: DaemonInstanceReceipt, name: str, path: str | Path,
) -> None:
    """Attribute a file to this process without granting artifact cleanup."""
    from . import _daemon_instance_store as store
    from ._daemon_instance_records import ProcessIdentity

    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
        raise ValueError("invalid daemon file observation name")
    target = Path(path).expanduser().resolve(strict=True)
    if not target.is_file():
        raise ValueError("daemon file observation is not a file")
    with store._locked(receipt, wait_seconds=1) as record_path:
        record = store._read(record_path)
        if not ProcessIdentity.current().matches(record) or record.get("state") != "running":
            raise ValueError("daemon file observation belongs to another process")
        key = f"observation_{name}_path"
        if key not in record and sum(
            item.startswith("observation_") and item.endswith("_path") for item in record
        ) >= 16:
            raise ValueError("daemon file observation limit exceeded")
        record[key] = str(target)
        store._write(record_path, record)


def observe_instances(
    registry_root: Path | None = None, *, service: str | None = None,
) -> list[dict[str, str]]:
    """Read registrations and observe birth identity without signaling or cleanup."""
    from ._daemon_instance_identity import identity_state

    observed = []
    for record in inspect_instances(registry_root):
        if (
            service is not None and record.get("service") != service
            and record.get("reason") != "invalid-record"
        ):
            continue
        value = dict(record)
        try:
            pid = int(record.get("pid", ""))
            birth = record.get("birth", "")
            status = identity_state(pid, birth) if pid > 0 and birth else "unknown"
        except (ValueError, OSError):
            status = "unknown"
        value["process_identity_state"] = status
        observed.append(value)
    return observed

"""OS-derived birth identity for the Connection Owner's liveness beacon.

A fresh beacon alone does not prove its writer is still running: on Windows
the Owner never probes ``pid`` (``os.kill`` would terminate it), and on any OS
the pid can be reused. The beacon records the writer's birth identity (process
creation time from the OS), and readers trust it only while the process now at
``pid`` has that same identity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def beacon_identity(raw: dict) -> str | None:
    """The writer identity recorded in a raw beacon payload, if any."""
    value = raw.get("process_identity")
    return value if isinstance(value, str) and value else None


def owner_process_identity(pid: int) -> str | None:
    """The OS birth identity of ``pid`` (None when it can't be determined)."""
    try:
        from ssh_manager.locks import process_identity

        return process_identity(pid)
    except Exception:
        return None


def _pid_gone(pid: int) -> bool:
    try:
        from ssh_manager.locks import pid_alive

        return not pid_alive(pid)
    except Exception:
        return False


def owner_identity_matches(live: Any, this_host: str) -> bool:
    """Whether the process at ``live.pid`` is still the beacon's writer.

    A beacon without an identity (written by an older Owner) or from another
    host can't be checked and keeps the previous freshness-only behavior. A
    dead pid, or a live one with a different birth identity (pid reuse), is not
    the writer. A live pid whose identity can't be read is not disproven.
    """
    recorded = getattr(live, "process_identity", None)
    if not recorded or getattr(live, "host", "") != this_host:
        return True
    current = owner_process_identity(live.pid)
    if current is None:
        return not _pid_gone(live.pid)
    return current == recorded


def predecessor_still_running(live: Any) -> bool:
    """Retain a legacy writer until its observed birth is positively gone.

    This only gates incoming *work*, never code application or passive startup.
    An unknown birth remains conservative; there is no signal, IPC dependency,
    metadata-based displacement or retargeting to a reused PID.
    """
    current = owner_process_identity(live.pid)
    if live.process_identity and current:
        return current == live.process_identity
    return not _pid_gone(live.pid)


_EXCLUSIONS_FILE = "connection-owner.predecessors.json"
_EXCLUSIONS_SCHEMA = "agent-codespaces.owner-predecessors"
_EXCLUSIONS_LIMIT = 65536


@dataclass(frozen=True)
class LegacyExclusion:
    pid: int
    process_identity: str | None


def _exclusions_path() -> Path:
    from agent_procutil._daemon_instance_store import _root
    from . import connection_owner as owner

    root = _root(Path(owner.RUNTIME_DIR))
    if os.name != "nt" and root.stat().st_mode & 0o022:
        raise OSError("Legacy exclusion root permits untrusted writes")
    return root / _EXCLUSIONS_FILE


def _exclusions_state(path: Path) -> os.stat_result | None:
    from agent_procutil._daemon_instance_store import _plain

    try:
        info = _plain(path)
    except FileNotFoundError:
        return None
    if info.st_nlink != 1 or (os.name != "nt" and info.st_mode & 0o022):
        raise ValueError("Legacy exclusion evidence must be a single-link owned file")
    return info


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate legacy exclusion field")
        result[key] = value
    return result


def _read_exclusions() -> list[LegacyExclusion]:
    from . import connection_owner as owner

    path = _exclusions_path()
    before = _exclusions_state(path)
    if before is None:
        return []
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not os.path.samestat(before, os.fstat(stream.fileno())):
            raise ValueError("Legacy exclusion evidence changed before reading")
        data = stream.read(_EXCLUSIONS_LIMIT + 1)
        after = _exclusions_state(path)
        if (after is None or not os.path.samestat(before, after)
                or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
            raise ValueError("Legacy exclusion evidence changed during reading")
    if len(data) > _EXCLUSIONS_LIMIT:
        raise ValueError("Legacy exclusion evidence exceeds its bound")
    raw = json.loads(data, object_pairs_hook=_unique_pairs)
    if (not isinstance(raw, dict)
            or set(raw) != {"schema", "version", "host", "predecessors"}
            or raw["schema"] != _EXCLUSIONS_SCHEMA or type(raw["version"]) is not int
            or raw["version"] != 1 or raw["host"] != owner._this_host()
            or not isinstance(raw["predecessors"], list)):
        raise ValueError("Unknown legacy exclusion evidence")
    entries = []
    for record in raw["predecessors"]:
        if (not isinstance(record, dict) or set(record) != {"pid", "process_identity"}
                or type(record["pid"]) is not int or record["pid"] <= 0
                or (record["process_identity"] is not None
                    and (not isinstance(record["process_identity"], str)
                         or not record["process_identity"] or len(record["process_identity"]) > 256))):
            raise ValueError("Invalid legacy process-birth exclusion")
        entries.append(LegacyExclusion(**record))
    return entries


def _write_exclusions(entries: list[LegacyExclusion]) -> None:
    from . import connection_owner as owner

    path = _exclusions_path()
    before = _exclusions_state(path)
    payload = json.dumps({
        "schema": _EXCLUSIONS_SCHEMA, "version": 1, "host": owner._this_host(),
        "predecessors": [asdict(entry) for entry in entries],
    }).encode("utf-8")
    if len(payload) > _EXCLUSIONS_LIMIT:
        raise ValueError("Legacy exclusion evidence exceeds its bound")
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    pending = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        current = _exclusions_state(path)
        if (before is None) != (current is None) or (
            before is not None and current is not None and (
                not os.path.samestat(before, current)
                or (before.st_size, before.st_mtime_ns) != (current.st_size, current.st_mtime_ns)
            )
        ):
            raise ValueError("Legacy exclusion evidence changed before publication")
        os.replace(pending, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        pending.unlink(missing_ok=True)


def retain_legacy_exclusion(recorded: Any) -> None:
    """Persist before replacing a legacy beacon, under the owner registry lock."""
    from . import connection_owner as owner

    if (recorded is None or recorded.pid <= 0 or recorded.pid == os.getpid()
            or recorded.host != owner._this_host() or recorded.work_lease_version == 1):
        return
    try:
        entries = _read_exclusions()
    except (OSError, ValueError):
        # Existing unreadable/foreign/corrupt evidence is itself a durable WORK
        # barrier. Preserve it; it does not prohibit incoming passive readiness.
        if os.path.lexists(Path(owner.RUNTIME_DIR) / _EXCLUSIONS_FILE):
            return
        raise
    entry = LegacyExclusion(
        recorded.pid, recorded.process_identity or owner_process_identity(recorded.pid),
    )
    if entry not in entries:
        _write_exclusions([*entries, entry])


def legacy_work_blocked() -> bool:
    """Retire only positively settled native births; caller holds registry lock.

    The separate durable record is never withdrawn with a process's beacon.
    Unknown evidence or an unconfirmed prune leaves work excluded, not startup.
    """
    try:
        entries = _read_exclusions()
        remaining = [entry for entry in entries if predecessor_still_running(entry)]
        if remaining != entries:
            _write_exclusions(remaining)
        return bool(remaining)
    except (OSError, ValueError):
        return True

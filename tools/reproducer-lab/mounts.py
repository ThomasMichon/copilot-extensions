"""Finite volatile-mount evidence; not full guest or experiment admission."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from boundary import Experiment
from guest_mounts import MAX_TABLE_BYTES

_ESCAPE = re.compile(r"\\(040|011|012|134)")


@dataclass(frozen=True)
class VolatileMounts:
    root_id: int
    tmp_id: int
    run_id: int
    tmp_bytes: int
    run_bytes: int


@dataclass(frozen=True)
class _Mount:
    mount_id: int
    device: str
    root: str
    options: frozenset[str]
    filesystem: str


@dataclass(frozen=True)
class StateMount:
    mount_id: int
    device: str
    root: str
    filesystem: str


def _path(value: str) -> str:
    decoded = _ESCAPE.sub(lambda match: chr(int(match[1], 8)), value)
    path = PurePosixPath(decoded)
    if (
        not path.is_absolute() or decoded.startswith("//") or str(path) != decoded or ".." in path.parts
        or any(part == "." for part in decoded.split("/"))
        or "\\" in _ESCAPE.sub("", value)
    ):
        raise ValueError("guest mount path is unsupported")
    return decoded


def _inventory(table: object) -> dict[str, _Mount]:
    if (
        not isinstance(table, str) or len(table) > MAX_TABLE_BYTES
        or len(table.encode("utf-8")) > MAX_TABLE_BYTES or not table.endswith("\n")
    ):
        raise ValueError("guest mount table is unavailable or exceeds its ceiling")
    lines = table.splitlines()
    if not 1 <= len(lines) <= 256:
        raise ValueError("guest mount table record ceiling exceeded")
    inventory: dict[str, _Mount] = {}
    ids: set[int] = set()
    for line in lines:
        left, separator, right = line.partition(" - ")
        fields, filesystem = left.split(), right.split()
        if (
            not separator or len(fields) < 6 or len(filesystem) != 3
            or re.fullmatch(r"[1-9][0-9]{0,19}", fields[0]) is None
            or re.fullmatch(r"[1-9][0-9]{0,19}", fields[1]) is None
            or re.fullmatch(r"[0-9]{1,10}:[0-9]{1,10}", fields[2]) is None
        ):
            raise ValueError("guest mount table record is malformed")
        mount_id, destination = int(fields[0]), _path(fields[4])
        root = _path(fields[3])
        options = fields[5].split(",")
        if (
            mount_id in ids or destination in inventory
            or not all(options) or len(set(options)) != len(options)
        ):
            raise ValueError("guest mount identities or options are ambiguous")
        ids.add(mount_id)
        inventory[destination] = _Mount(mount_id, fields[2], root, frozenset(options), filesystem[0])
    return inventory


def inspect_state_mount(table: object, device: str) -> StateMount:
    """Check the fixed durable-state mount, not Docker volume association."""
    inventory = _inventory(table)
    root, state = inventory.get("/"), inventory.get("/lab/state")
    if (
        root is None or "ro" not in root.options or "rw" in root.options
        or state is None or "rw" not in state.options or "ro" in state.options
        or state.filesystem not in {"ext4", "xfs", "btrfs", "zfs"}
        or state.device != device or state.device == root.device
        or "/lab" in inventory
        or any(path.startswith("/lab/state/") for path in inventory)
    ):
        raise ValueError("effective durable state mount is unavailable or unsupported")
    return StateMount(state.mount_id, state.device, state.root, state.filesystem)


def inspect_volatile_mounts(experiment: Experiment, value: object) -> VolatileMounts:
    if (
        not isinstance(value, dict) or set(value) != {"uid", "gid", "mountinfo", "roots"}
        or type(value["uid"]) is not int or value["uid"] != 1000
        or type(value["gid"]) is not int or value["gid"] != 1000
    ):
        raise ValueError("guest mount observation identity or fields differ")
    inventory = _inventory(value["mountinfo"])
    if "/" not in inventory or "ro" not in inventory["/"].options or "rw" in inventory["/"].options:
        raise ValueError("effective guest root is not read-only")
    roots = value["roots"]
    if not isinstance(roots, list) or len(roots) != 2:
        raise ValueError("guest volatile root inventory is incomplete")
    sizes = {"/tmp": experiment.budgets.tmp_bytes, "/run": experiment.budgets.run_bytes}
    seen: set[str] = set()
    for row in roots:
        if not isinstance(row, dict) or set(row) != {
            "path", "uid", "gid", "mode", "device", "bytes", "readonly",
        }:
            raise ValueError("guest volatile root fields differ")
        path = row["path"]
        if not isinstance(path, str) or path not in sizes or path in seen or path not in inventory:
            raise ValueError("guest volatile mount is missing, shared or duplicated")
        seen.add(path)
        mount = inventory[path]
        if (
            mount.filesystem != "tmpfs" or mount.root != "/"
            or not {"rw", "nosuid", "nodev"} <= mount.options or "ro" in mount.options
            or bool({"suid", "dev"} & mount.options) or row["readonly"] is not False
            or row["device"] != mount.device
            or any(type(row[field]) is not int for field in ("uid", "gid", "mode", "bytes"))
            or (row["uid"], row["gid"], row["mode"]) != (1000, 1000, 0o700)
            or row["bytes"] != sizes[path]
            or any(destination.startswith(path + "/") for destination in inventory)
        ):
            raise ValueError("effective volatile mount differs from its declared posture")
    if len({inventory[path].device for path in ("/", "/tmp", "/run")}) != 3:
        raise ValueError("root and volatile mounts share the same filesystem")
    return VolatileMounts(
        inventory["/"].mount_id, inventory["/tmp"].mount_id, inventory["/run"].mount_id,
        sizes["/tmp"], sizes["/run"],
    )

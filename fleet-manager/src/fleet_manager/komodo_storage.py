"""Code-registered observations of otherwise inaccessible original Docker volumes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import stat
import time

from .planning import PlanError, canonical, decode, digest, identifier


def storage_path(path: Path) -> None:
    if (not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts
            or any(ord(char) < 32 or char == "$" for char in str(path))):
        raise PlanError("Komodo storage requires canonical absolute original paths")


@dataclass(frozen=True)
class KomodoIncumbentStorage:
    """The original daemon/storage authority supplies fresh, bounded observations.

    The callback must authenticate ordinary paths and coherent opened-file reads
    through the original containers and mounts. It returns metadata, digests and
    public keys only, never private content. Construction authenticates nothing.
    """

    daemon_id: str
    file_owners: tuple[tuple[Path, int, int], ...]
    read: Callable[[bytes], bytes] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        identifier(self.daemon_id, "Komodo storage daemon")
        if (type(self.file_owners) is not tuple or not 1 <= len(self.file_owners) <= 36
                or not callable(self.read)):
            raise PlanError("Komodo storage requires its original file owners")
        paths = []
        for item in self.file_owners:
            if (type(item) is not tuple or len(item) != 3
                    or any(type(value) is not int or not 0 <= value < 2**32 for value in item[1:])):
                raise PlanError("Komodo storage file ownership is invalid")
            storage_path(item[0])
            paths.append(item[0])
        if len(set(paths)) != len(paths):
            raise PlanError("Komodo storage file owners cannot repeat")

    def observe(self, *, mounts: dict, volumes: list, files: set[Path],
                private_files: set[Path], keys: Path) -> dict:
        roots = {Path(volume["Mountpoint"]) for volume in volumes}
        for path in roots | files | {keys}:
            storage_path(path)
        if (len(roots) != len(volumes)
                or any(first in second.parents or second in first.parents
                for first in roots for second in roots if first != second)
                or keys not in roots):
            raise PlanError("Komodo observed storage requires disjoint original volumes and key root")
        public = {keys / (name + ".pub") for name in ("core", "periphery")}
        files = files | public
        owners = {str(path): [uid, gid] for path, uid, gid in self.file_owners}
        if (set(owners) != {str(path) for path in files}
                or any(not any(root in path.parents for root in roots) for path in files)):
            raise PlanError("Komodo observed files differ from original volume owners")
        request = canonical({
            "schema": "fleet-manager.komodo-storage-request", "version": 1,
            "daemon": self.daemon_id, "mounts": mounts, "volumes": volumes,
            "files": owners, "private": sorted(str(path) for path in private_files & files),
        })
        try:
            encoded = self.read(request)
        except Exception:
            raise PlanError("Komodo original storage observation is unavailable") from None
        if type(encoded) is not bytes or len(encoded) > 128 * 1024:
            raise PlanError("Komodo original storage observation exceeds its byte bound")
        value = decode(encoded)
        if (type(value) is not dict or set(value) != {"request", "valid_until", "entries", "public"}
                or value["request"] != digest(decode(request))
                or type(value["valid_until"]) is not int
                or not time.time() < value["valid_until"] <= time.time() + 300
                or type(value["entries"]) is not dict
                or set(value["entries"]) != {str(path) for path in roots | files}
                or type(value["public"]) is not dict or set(value["public"]) != {"core", "periphery"}
                or canonical(value) != encoded):
            raise PlanError("Komodo original storage observation is stale or differs from its request")
        identities = set()
        for path in roots | files:
            item = value["entries"][str(path)]
            is_file = path in files
            required = {"device", "inode", "uid", "gid", "mode", "nlink"} | (
                {"sha256", "size"} if is_file else set())
            if (type(item) is not dict or set(item) != required
                    or any(type(item.get(name)) is not int or item[name] < 0
                           for name in required - {"sha256"})
                    or item["inode"] == 0
                    or (not stat.S_ISREG(item["mode"]) if is_file else not stat.S_ISDIR(item["mode"]))):
                raise PlanError("Komodo original storage metadata is invalid")
            identity = (item["device"], item["inode"])
            if identity in identities:
                raise PlanError("Komodo original storage identities cannot alias")
            identities.add(identity)
            if is_file:
                if (item["nlink"] != 1 or [item["uid"], item["gid"]] != owners[str(path)]
                        or item["mode"] & (0o077 if path in private_files else 0o022)
                        or item["size"] > 1024 * 1024
                        or (path in {keys / "core.key", keys / "periphery.key"} and not item["size"])
                        or type(item["sha256"]) is not str or len(item["sha256"]) != 64
                        or any(char not in "0123456789abcdef" for char in item["sha256"])):
                    raise PlanError("Komodo original storage file custody differs")
        for name in ("core", "periphery"):
            raw = value["public"][name]
            if (type(raw) is not str or not 1 <= len(raw.encode("utf-8")) <= 4096
                    or len(raw.encode("utf-8")) != value["entries"][str(keys / (name + ".pub"))]["size"]
                    or hashlib.sha256(raw.encode("utf-8")).hexdigest()
                    != value["entries"][str(keys / (name + ".pub"))]["sha256"]):
                raise PlanError("Komodo original storage public identity differs from its file")
        return value

"""Exact existing storage observations; never an executable deployment profile."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import stat
import tomllib

from .artifact_staging import _bytes
from .komodo_declaration import SERVICES
from .komodo_profile import KomodoMaterialization, ordinary_path
from .planning import PlanError, canonical, decode, digest
from .private_state import read_private_file


@dataclass(frozen=True)
class KomodoConfigUsername:
    """The existing Core TOML is the username source, not a generated secret file."""

    config: Path

    def read(self) -> bytes:
        ordinary_path(self.config)
        try:
            raw = read_private_file(self.config, 1024 * 1024)
        except PlanError:
            raise PlanError("Komodo incumbent configured username is unavailable") from None
        return self.value(raw)

    @staticmethod
    def value(raw: bytes) -> bytes:
        try:
            settings = tomllib.loads(raw.decode("utf-8"))
            value = settings["init_admin_username"]
        except (KeyError, UnicodeError, tomllib.TOMLDecodeError):
            raise PlanError("Komodo incumbent configured username is unavailable") from None
        if type(value) is not str:
            raise PlanError("Komodo incumbent configured username is unsupported")
        return value.encode("utf-8")


def incumbent_topology(encoded: bytes) -> dict:
    if type(encoded) is not bytes:
        raise PlanError("Komodo incumbent topology requires immutable owner evidence")
    value = decode(encoded)
    if (canonical(value) != encoded or type(value) is not dict
            or set(value) != {"networks", "modes", "attachments"}
            or type(value["networks"]) is not dict or not 1 <= len(value["networks"]) <= 8
            or type(value["modes"]) is not dict or set(value["modes"]) != set(SERVICES)
            or type(value["attachments"]) is not dict or set(value["attachments"]) != set(SERVICES)):
        raise PlanError("Komodo incumbent topology requires bounded exact owner evidence")
    networks = value["networks"]
    identities = set()
    used = set()
    for name, network in networks.items():
        if (type(name) is not str or not name or len(name) > 128 or not name[0].isalnum()
                or any(not (char.isascii() and (char.isalnum() or char in "_.-")) for char in name)
                or type(network) is not dict or network.get("Name") != name
                or network.get("Driver") != "bridge" or network.get("Scope") != "local"
                or type(network.get("Internal")) is not bool
                or network.get("Attachable") is not False or network.get("Ingress") is not False
                or network.get("ConfigOnly") is not False or network.get("Options") not in (None, {})
                or network.get("ConfigFrom") not in (None, {"Network": ""})
                or type(network.get("Containers")) is not dict
                or type(network.get("Id")) is not str or len(network["Id"]) != 64
                or any(char not in "0123456789abcdef" for char in network["Id"])
                or network["Id"] in identities):
            raise PlanError("Komodo incumbent topology has unsupported or ambiguous bridges")
        identities.add(network["Id"])
    for service in SERVICES:
        names = value["attachments"][service]
        if (type(names) is not list or not names or any(type(name) is not str for name in names)
                or len(set(names)) != len(names) or not set(names) <= set(networks)
                or value["modes"][service] not in names):
            raise PlanError("Komodo incumbent topology has ambiguous service attachments")
        value["attachments"][service] = sorted(names)
        used.update(names)
    if used != set(networks):
        raise PlanError("Komodo incumbent topology includes unrelated bridges")
    return value


def mount_evidence(mounts: object) -> str:
    if not isinstance(mounts, list) or not mounts or len(mounts) > 32:
        raise PlanError("Komodo incumbent mounts are unavailable or unbounded")
    destinations = []
    for mount in mounts:
        if (not isinstance(mount, dict) or mount.get("Type") not in ("bind", "volume")
                or type(mount.get("RW")) is not bool
                or type(mount.get("Source")) is not str
                or type(mount.get("Destination")) is not str
                or not mount["Destination"].startswith("/")):
            raise PlanError("Komodo incumbent mount evidence is invalid")
        destination = mount["Destination"]
        if (destination.startswith("//") or ".." in PurePosixPath(destination).parts
                or str(PurePosixPath(destination)) != destination):
            raise PlanError("Komodo incumbent mount destination is not canonical")
        destinations.append(mount["Destination"])
    if len(set(destinations)) != len(destinations):
        raise PlanError("Komodo incumbent mount destinations are ambiguous")
    return digest(sorted(mounts, key=lambda item: item["Destination"]))


@dataclass(frozen=True)
class KomodoIncumbentMaterialization:
    """A current existing-owner observation, not custody or permission to adopt.

    Native named volumes remain volumes. Their mountpoints are used only to
    read retained identity and credential files, never as replacement bind mounts.
    The producer authenticates the complete native mount/volume snapshots and
    supplies all configuration and credential files its incumbent contract uses.
    """

    docker_socket: Path
    keys: Path
    admin_username: Path | KomodoConfigUsername
    admin_password: Path
    retained_files: tuple[Path, ...]
    encoded: bytes
    topology: bytes | None = None

    def __post_init__(self) -> None:
        if type(self.encoded) is not bytes or type(self.retained_files) is not tuple:
            raise PlanError("Komodo incumbent requires immutable existing-owner evidence")
        value = decode(self.encoded)
        if (not isinstance(value, dict) or set(value) != {"schema", "version", "mounts", "volumes"}
                or value["schema"] != "fleet-manager.komodo-incumbent-material"
                or type(value["version"]) is not int or value["version"] != 1
                or canonical(value) != self.encoded
                or not isinstance(value["mounts"], dict) or set(value["mounts"]) != set(SERVICES)
                or not isinstance(value["volumes"], list) or len(value["volumes"]) > 16
                or not 1 <= len(self.retained_files) <= 32):
            raise PlanError("Komodo incumbent material evidence is invalid")
        ordinary_path(self.docker_socket)
        if not stat.S_ISSOCK(self.docker_socket.stat().st_mode):
            raise PlanError("Komodo incumbent requires its existing native Docker socket")
        volumes = {}
        for volume in value["volumes"]:
            if (not isinstance(volume, dict) or volume.get("Driver") != "local"
                    or volume.get("Scope") != "local" or volume.get("Options") not in (None, {})
                    or any(type(volume.get(key)) is not str or not volume[key]
                           for key in ("Name", "Mountpoint", "CreatedAt"))
                    or volume["Name"] in volumes):
                raise PlanError("Komodo incumbent volume identity is invalid")
            if (not volume["Name"][0].isalnum()
                    or any(not (char.isascii() and (char.isalnum() or char in "_.-"))
                           for char in volume["Name"])):
                raise PlanError("Komodo incumbent volume name is invalid")
            path = Path(volume["Mountpoint"])
            ordinary_path(path)
            if not path.is_dir():
                raise PlanError("Komodo incumbent volume storage is unavailable")
            volumes[volume["Name"]] = volume
        used = set()
        mounted_files = set()
        for service in SERVICES:
            mounts = value["mounts"][service]
            mount_evidence(mounts)
            for mount in mounts:
                source = Path(mount["Source"])
                ordinary_path(source)
                if not source.exists():
                    raise PlanError("Komodo incumbent mount source is unavailable")
                if mount["Type"] == "volume":
                    name = mount.get("Name")
                    if (type(name) is not str or name not in volumes
                            or mount["Source"] != volumes[name]["Mountpoint"]
                            or mount.get("Driver") != volumes[name]["Driver"]):
                        raise PlanError("Komodo incumbent mount differs from its original volume")
                    used.add(name)
                elif source.is_file():
                    mounted_files.add(source)
            if service in ("core", "periphery"):
                keys = [mount for mount in mounts if mount["Destination"] == "/config/keys"]
                if len(keys) != 1 or keys[0]["Source"] != str(self.keys):
                    raise PlanError("Komodo incumbent public identity lacks its original mount")
                if any(mount["Destination"] in {
                    "/config/keys/" + name + suffix
                    for name in ("core", "periphery") for suffix in (".key", ".pub")
                } for mount in mounts):
                    raise PlanError("Komodo incumbent required key-file overlays are unsupported")
        if used != set(volumes):
            raise PlanError("Komodo incumbent volume evidence contains unrelated storage")
        if (len(set(self.retained_files)) != len(self.retained_files)
                or not (mounted_files | {self.keys / "core.key", self.keys / "periphery.key"})
                <= set(self.retained_files)):
            raise PlanError("Komodo incumbent must retain mounted configuration and original private keys")
        if self.admin_username == self.admin_password:
            raise PlanError("Komodo incumbent login files cannot alias")
        if type(self.admin_username) is KomodoConfigUsername:
            if (self.admin_username.config not in self.retained_files
                    or not any(mount["Source"] == str(self.admin_username.config)
                               and mount["Destination"] == "/config/config.toml"
                               for mount in value["mounts"]["core"])):
                raise PlanError("Komodo incumbent configured username requires retained Core configuration")
        if self.topology is not None:
            incumbent_topology(self.topology)
        ordinary_path(self.keys)
        if not self.keys.is_dir():
            raise PlanError("Komodo incumbent retained identity is unavailable")
        self.evidence()

    def credential_bytes(self) -> dict[str, bytes]:
        values = {}
        for name in ("admin_username", "admin_password"):
            path = getattr(self, name)
            if type(path) is KomodoConfigUsername:
                value = path.read()
            else:
                ordinary_path(path)
                value = read_private_file(path, 4096)
            if (not value or len(value) > 4096 or b"\0" in value or b"\n" in value or b"\r" in value
                    or (name == "admin_password" and len(value) < 16)):
                raise PlanError("Komodo incumbent login material is unsupported")
            try:
                value.decode("utf-8")
            except UnicodeError:
                raise PlanError("Komodo incumbent login material is unsupported") from None
            values[name] = value
        return values

    def evidence(self) -> dict:
        credentials = self.credential_bytes()
        selected = decode(self.encoded)
        paths = {}
        for mounts in selected["mounts"].values():
            for mount in mounts:
                path = Path(mount["Source"])
                ordinary_path(path)
                info = path.stat()
                paths[str(path)] = {"device": info.st_dev, "inode": info.st_ino}
        files = {}
        username_path = (self.admin_username.config if type(self.admin_username) is KomodoConfigUsername
                         else self.admin_username)
        private_files = {
            self.keys / "core.key", self.keys / "periphery.key",
            username_path, self.admin_password,
        }
        for path in (*self.retained_files, username_path, self.admin_password):
            ordinary_path(path)
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PlanError("Komodo incumbent retained file custody is ambiguous")
            content = (read_private_file(path, 1024 * 1024) if path in private_files
                       else _bytes(path, 1024 * 1024, allow_empty=True))
            if (path == username_path and (
                    KomodoConfigUsername.value(content) if type(self.admin_username) is KomodoConfigUsername
                    else content) != credentials["admin_username"]
                    or path == self.admin_password and content != credentials["admin_password"]):
                raise PlanError("Komodo incumbent login changed during observation")
            try:
                after = path.stat()
            except OSError:
                raise PlanError("Komodo incumbent retained file changed during observation") from None
            if ((info.st_dev, info.st_ino) != (after.st_dev, after.st_ino)
                    or not stat.S_ISREG(after.st_mode) or after.st_nlink != 1):
                raise PlanError("Komodo incumbent retained file changed during observation")
            files[str(path)] = {
                "device": info.st_dev, "inode": info.st_ino,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        info = self.keys.stat()
        result = {
            "incumbent": digest({
                "schema": selected["schema"], "version": selected["version"],
                "mounts": {name: mount_evidence(mounts) for name, mounts in selected["mounts"].items()},
                "volumes": {volume["Name"]: volume for volume in selected["volumes"]},
            }),
            "paths": paths, "files": files,
            "keys": {"path": str(self.keys), "device": info.st_dev, "inode": info.st_ino},
            "credentials": digest({
                name: hashlib.sha256(raw).hexdigest() for name, raw in credentials.items()
            }),
        }
        if self.topology is not None:
            result["topology"] = digest(incumbent_topology(self.topology))
        if type(self.admin_username) is KomodoConfigUsername:
            result["username_source"] = {"config": str(username_path), "field": "init_admin_username"}
        return result

    def network_mode(self, service: str, project: str) -> str:
        return (incumbent_topology(self.topology)["modes"][service] if self.topology is not None
                else project + "_default")

    def profile(self) -> bytes:
        return canonical({
            "schema": "fleet-manager.komodo-incumbent-observation", "version": 1,
            "material": self.evidence(),
        })

    def check_mounts(self, service: str, mounts: list[dict]) -> None:
        selected = decode(self.encoded)["mounts"][service]
        if mount_evidence(mounts) != mount_evidence(selected):
            raise PlanError("Komodo incumbent native mounts changed")

    def volumes(self) -> list[dict]:
        return decode(self.encoded)["volumes"]


KomodoMaterial = KomodoMaterialization | KomodoIncumbentMaterialization

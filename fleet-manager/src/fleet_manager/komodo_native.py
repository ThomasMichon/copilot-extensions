"""Finite native Docker/Compose boundary for the fixed Komodo profile."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from threading import Event
import time
from typing import Any

from .komodo_declaration import KomodoDeclaration, SERVICES
from .komodo_profile import KomodoMaterialization, ordinary_path
from .planning import PlanError, decode, identifier
from .private_state import read_private_file

_ID = re.compile(r"[0-9a-f]{64}\Z")
_INFO = '{"id":{{json .ID}},"os":{{json .OSType}},"architecture":{{json .Architecture}}}'


def _digest_spellings(image: str) -> set[str]:
    """Docker Hub familiar names retain the exact official repository and digest."""
    prefix = "docker.io/library/mongo@"
    if image.startswith(prefix):
        suffix = image[len(prefix):]
        return {repository + "@" + suffix for repository in (
            "docker.io/library/mongo", "docker.io/mongo", "library/mongo", "mongo",
            "index.docker.io/library/mongo",
        )}
    return {image}


@dataclass(frozen=True)
class KomodoImage:
    identity: str
    environment: tuple[str, ...]
    entrypoint: tuple[str, ...]
    command: tuple[str, ...]
    user: str


@dataclass(frozen=True)
class KomodoTooling:
    """Current admitted host toolchain; never selected by a wire request."""

    docker: Path
    client_config: Path
    daemon_id: str
    run: Callable[..., subprocess.CompletedProcess] = field(repr=False, compare=False)
    client_identity: tuple[int, int] = field(init=False)

    def __post_init__(self) -> None:
        try:
            for path in (self.docker, self.client_config):
                ordinary_path(path)
            if (not self.docker.is_file() or not os.access(self.docker, os.X_OK)
                    or not self.client_config.is_dir() or not callable(self.run)):
                raise PlanError("Komodo admitted native toolchain is unavailable")
            info = self.client_config.stat()
        except OSError:
            raise PlanError("Komodo admitted native toolchain is unavailable") from None
        identifier(self.daemon_id, "Komodo physical Docker daemon")
        object.__setattr__(self, "client_identity", (info.st_dev, info.st_ino))
        self.check_client()

    def check_client(self) -> None:
        try:
            ordinary_path(self.client_config)
            info = self.client_config.stat()
            with os.scandir(self.client_config) as entries:
                first = next(entries, None)
                inert = first is not None and first.name == "config.json" and next(entries, None) is None
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                    or (info.st_dev, info.st_ino) != self.client_identity or not inert):
                raise PlanError("Komodo requires private inert native client configuration")
            if read_private_file(self.client_config / "config.json", 4096) != b"{}":
                raise PlanError("Komodo requires its owner's inert Docker client configuration")
        except OSError:
            raise PlanError("Komodo native client configuration is unavailable") from None


class KomodoNative:
    """Internal effect adapter. Its caller supplies admission and durable custody."""

    def __init__(self, tooling: KomodoTooling) -> None:
        if sys.platform != "linux":
            raise PlanError("Komodo substrate owner requires an admitted Linux host")
        self.tooling = tooling
        self.native_failure: subprocess.SubprocessError | OSError | UnicodeError | None = None

    def _call(self, material: KomodoMaterialization, arguments: list[str], *,
              deadline: float) -> str:
        if self.native_failure is not None:
            raise PlanError("Komodo native process custody remains unsettled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PlanError("Komodo native deadline exceeded")
        self.tooling.check_client()
        settled = Event()
        environment = {name: os.environ[name] for name in ("LANG", "LC_ALL")
                       if name in os.environ}
        environment.update(PATH=os.defpath, HOME=str(self.tooling.client_config), DOCKER_CLI_HINTS="false")
        try:
            result = self.tooling.run([
                str(self.tooling.docker), "--config", str(self.tooling.client_config),
                "--host", "unix://" + str(material.docker_socket), *arguments,
            ], cwd=str(self.tooling.client_config), env=environment,
                timeout=remaining, max_stdout_bytes=1024 * 1024, max_stderr_bytes=4096,
                on_settled=settled.set)
        except subprocess.SubprocessError as error:
            if not settled.is_set():
                self.native_failure = error
                raise PlanError("Komodo native process custody remains unsettled") from None
            raise PlanError("Komodo native operation failed or is uncertain") from None
        except (OSError, UnicodeError) as error:
            if not settled.is_set():
                self.native_failure = error
            raise PlanError("Komodo native operation is unavailable or uncertain") from None
        if not settled.is_set():
            self.native_failure = subprocess.SubprocessError("native settlement is unconfirmed")
            raise PlanError("Komodo native adapter did not confirm whole-process settlement")
        if result.returncode != 0 or type(result.stdout) is not str:
            raise PlanError("Komodo native operation failed or is uncertain")
        return result.stdout

    def verify_host(self, declaration: KomodoDeclaration, material: KomodoMaterialization,
                    *, deadline: float) -> None:
        value = declaration.to_dict()
        info = decode(self._call(material, ["info", "--format", _INFO], deadline=deadline).encode())
        architecture = {"x86_64": "amd64", "aarch64": "arm64", "amd64": "amd64", "arm64": "arm64"}
        if (not isinstance(info, dict) or set(info) != {"id", "os", "architecture"}
                or info["id"] != self.tooling.daemon_id or info["os"] != "linux"
                or type(info["architecture"]) is not str
                or architecture.get(info["architecture"]) != value["architecture"]):
            raise PlanError("Komodo native daemon differs from admitted physical host")

    def prerequisites(self, declaration: KomodoDeclaration, material: KomodoMaterialization,
                      *, deadline: float) -> dict[str, KomodoImage]:
        self.verify_host(declaration, material, deadline=deadline)
        version = self._call(material, ["compose", "version", "--short"], deadline=deadline).strip()
        if not re.fullmatch(r"v?2\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?", version):
            raise PlanError("Komodo requires an admitted Compose version 2 toolchain")
        return self.selected_images(declaration, material, deadline=deadline)

    def selected_images(self, declaration: KomodoDeclaration, material: KomodoMaterialization,
                        *, deadline: float) -> dict[str, KomodoImage]:
        value = declaration.to_dict()
        selected = {}
        for service in SERVICES:
            image = value["images"][service]["image"]
            result = decode(self._call(material, [
                "image", "inspect", "--format",
                '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}},'
                '"digests":{{json .RepoDigests}},"environment":{{json .Config.Env}},'
                '"entrypoint":{{json .Config.Entrypoint}},"command":{{json .Config.Cmd}},'
                '"user":{{json .Config.User}}}', image,
            ], deadline=deadline).encode())
            if (not isinstance(result, dict) or set(result) != {
                    "id", "os", "architecture", "digests", "environment", "entrypoint", "command", "user"}
                    or type(result["id"]) is not str
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", result["id"]) is None
                    or result["os"] != "linux" or result["architecture"] != value["architecture"]
                    or not isinstance(result["digests"], list)
                    or not any(type(item) is str and item in _digest_spellings(image)
                               for item in result["digests"])):
                raise PlanError("Komodo immutable image is absent or differs from admitted platform")
            values = []
            for name in ("environment", "entrypoint", "command"):
                item = result[name]
                if item is not None and (
                    not isinstance(item, list) or any(type(value) is not str for value in item)
                ):
                    raise PlanError("Komodo immutable image configuration is invalid")
                values.append(tuple(item or ()))
            if type(result["user"]) is not str:
                raise PlanError("Komodo immutable image configuration is invalid")
            selected[service] = KomodoImage(result["id"], *values, result["user"])
        return selected

    def inspect(self, declaration: KomodoDeclaration, material: KomodoMaterialization,
                *, deadline: float) -> dict[str, dict[str, Any]]:
        project = declaration.to_dict()["project"]
        listed = self._call(material, [
            "container", "ls", "--all", "--no-trunc", "--filter",
            "label=com.docker.compose.project=" + project, "--format", "{{.ID}}",
        ], deadline=deadline).splitlines()
        if len(listed) > 3 or len(set(listed)) != len(listed) or any(
            _ID.fullmatch(item) is None for item in listed
        ):
            raise PlanError("Komodo native project contains unexpected or ambiguous resources")
        if not listed:
            return {}
        # Native output is captured privately and never reported; Config can hold secrets.
        raw = self._call(material, ["container", "inspect", *listed], deadline=deadline)
        containers = decode(raw.encode())
        if not isinstance(containers, list) or len(containers) != len(listed):
            raise PlanError("Komodo native container evidence is incomplete")
        result = {}
        for item in containers:
            if not isinstance(item, dict):
                raise PlanError("Komodo native container evidence is invalid")
            config = item.get("Config")
            labels = config.get("Labels") if isinstance(config, dict) else None
            service = labels.get("com.docker.compose.service") if isinstance(labels, dict) else None
            if (service not in SERVICES or service in result or item.get("Id") not in listed
                    or labels.get("com.docker.compose.project") != project
                    or type(labels.get("com.docker.compose.oneoff")) is not str
                    or labels["com.docker.compose.oneoff"].lower() != "false"
                    or not isinstance(item.get("HostConfig"), dict)
                    or not isinstance(item.get("Mounts"), list)
                    or not isinstance(item.get("State"), dict)):
                raise PlanError("Komodo native project has foreign or ambiguous resources")
            result[service] = item
        return result

    def _apply(self, declaration: KomodoDeclaration, material: KomodoMaterialization,
               *, profile: Path, expected: bytes, deadline: float,
               checkpoint: Callable[[], None] | None = None) -> None:
        """Only the admitted owner calls this after persisting UNKNOWN."""
        ordinary_path(profile)
        if read_private_file(profile, 1024 * 1024) != expected:
            raise PlanError("Komodo generated deployment slot changed before native effect")
        if checkpoint is not None:
            checkpoint()
        self._call(material, [
            "compose", "--project-name", declaration.to_dict()["project"],
            "--project-directory", str(profile.parent), "--env-file", "/dev/null",
            "--file", str(profile), "up", "--detach", "--no-build", "--pull", "never",
            "--wait", "--wait-timeout", str(max(1, int(deadline - time.monotonic()))),
            *SERVICES,
        ], deadline=deadline)
        if read_private_file(profile, 1024 * 1024) != expected:
            raise PlanError("Komodo generated deployment slot changed during native effect")

    def network(self, declaration: KomodoDeclaration, material: KomodoMaterialization, *,
                deadline: float) -> dict[str, Any] | None:
        name = declaration.to_dict()["project"] + "_default"
        identities = self._call(material, [
            "network", "ls", "--no-trunc", "--filter", "name=^" + re.escape(name) + "$",
            "--format", "{{.ID}}",
        ], deadline=deadline).splitlines()
        if not identities:
            return None
        if len(identities) != 1 or _ID.fullmatch(identities[0]) is None:
            raise PlanError("Komodo native network evidence is ambiguous")
        raw = decode(self._call(material, ["network", "inspect", identities[0]],
                                deadline=deadline).encode())
        if (not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict)
                or raw[0].get("Id") != identities[0] or raw[0].get("Name") != name):
            raise PlanError("Komodo native network identity differs from selection")
        return raw[0]

    def _retire(self, material: KomodoMaterialization, identities: list[str], *,
                deadline: float, checkpoint: Callable[[], None]) -> None:
        if not identities or len(identities) > 3 or any(_ID.fullmatch(item) is None for item in identities):
            raise PlanError("Komodo retirement requires exact retained container identities")
        checkpoint()
        self._call(material, ["container", "stop", "--time", "20", *identities], deadline=deadline)
        checkpoint()
        self._call(material, ["container", "rm", *identities], deadline=deadline)

    def identities_absent(self, material: KomodoMaterialization, identities: list[str], *,
                          deadline: float) -> bool:
        if len(identities) > 3 or any(_ID.fullmatch(item) is None for item in identities):
            raise PlanError("Komodo retirement requires exact retained container identities")
        for identity in identities:
            result = self._call(material, [
                "container", "ls", "--all", "--no-trunc", "--filter", "id=" + identity,
                "--format", "{{.ID}}",
            ], deadline=deadline)
            if result.strip():
                return False
        return True

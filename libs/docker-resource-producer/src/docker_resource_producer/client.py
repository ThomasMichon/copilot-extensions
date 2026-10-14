"""Bounded Docker CLI probes against an explicit, daemon-pinned endpoint."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import (
    LABEL_CREATOR, LABEL_GENERATION, LABEL_TOKEN, DiagnosticObservation, Endpoint, Identity,
    VerificationError, birth_labels, daemon_id, generation, text,
)

_ROUTING_ENV = (
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH",
)
_FORMATS = {"container": "{{json .ID}}", "volume": "{{json .Name}}"}


def _json(raw: str) -> Any:
    def constant(_value: str) -> Any:
        raise VerificationError("Non-finite Docker JSON value")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise VerificationError("Duplicate Docker response field")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError) as error:
        raise VerificationError("Malformed Docker JSON response") from error


def _optional_client_file(path: Path) -> str | None:
    try:
        path.stat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise VerificationError("Cannot inspect optional TLS client identity file") from error
    return str(path)


def _default_tls_paths(environment: Mapping[str, str]) -> dict[str, str | None]:
    directory = environment.get("DOCKER_CERT_PATH") or environment.get("DOCKER_CONFIG")
    if not directory:
        home_key = "USERPROFILE" if os.name == "nt" else "HOME"
        home = environment.get(home_key)
        if not home:
            raise VerificationError(f"Docker TLS directory requires supplied {home_key} or explicit config")
        directory = str(Path(home) / ".docker")
    certs = Path(directory).resolve()
    paths = {
        "tls_ca": str(certs / "ca.pem"),
        "tls_cert": _optional_client_file(certs / "cert.pem"),
        "tls_key": _optional_client_file(certs / "key.pem"),
    }
    if bool(paths["tls_cert"]) != bool(paths["tls_key"]):
        raise VerificationError("TLS certificate and key must be paired")
    return paths


class DockerClient:
    """No installer, authentication bootstrap, SDK or ledger dependency."""

    def __init__(
        self, endpoint: Endpoint, *, docker_argv: Sequence[str] = ("docker",),
        environment: Mapping[str, str] | None = None, timeout: float = 8,
    ) -> None:
        if not docker_argv or any(not isinstance(item, str) or not item or "\0" in item for item in docker_argv):
            raise VerificationError("Invalid Docker command prefix")
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not 0 < timeout <= 30:
            raise VerificationError("Docker probe timeout must be positive and at most 30 seconds")
        self.endpoint = endpoint
        self.docker_argv = tuple(docker_argv)
        self.environment = dict(os.environ if environment is None else environment)
        self.timeout = timeout

    def _run(self, args: Sequence[str], *, pinned: bool = True) -> subprocess.CompletedProcess[str]:
        environment = dict(self.environment)
        transport: list[str] = []
        if pinned:
            for key in _ROUTING_ENV:
                environment.pop(key, None)
            transport = ["--host", self.endpoint.host, f"--tls={'true' if self.endpoint.tls else 'false'}"]
            # Docker enables TLS whenever --tlsverify is present, even =false.
            if self.endpoint.tls:
                transport.append(f"--tlsverify={'true' if self.endpoint.tls_verify else 'false'}")
            for option, value in (
                ("--tlscacert", self.endpoint.tls_ca), ("--tlscert", self.endpoint.tls_cert),
                ("--tlskey", self.endpoint.tls_key),
            ):
                transport += [option, value if value is not None else ""]
        kwargs: dict[str, int] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # headless-guard: allow dependency-free Docker primitive
        try:
            return subprocess.run(
                [*self.docker_argv, *transport, *args], env=environment,
                stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                errors="strict", timeout=self.timeout, **kwargs,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as error:
            raise VerificationError(f"Docker invocation failed: {type(error).__name__}") from error

    def _checked(self, args: Sequence[str], *, pinned: bool = True) -> str:
        result = self._run(args, pinned=pinned)
        if result.returncode != 0:
            # Do not expose arbitrary daemon output, which can contain auth data.
            raise VerificationError(f"Docker {args[0]} failed (exit {result.returncode})")
        return result.stdout

    def _daemon_id(self) -> str:
        return daemon_id(_json(self._checked(["info", "--format", "{{json .ID}}"])))

    def _verify_daemon(self) -> None:
        if self._daemon_id() != self.endpoint.daemon_id:
            raise VerificationError("Docker daemon identity changed")

    @classmethod
    def pin(
        cls, *, docker_argv: Sequence[str] = ("docker",),
        environment: Mapping[str, str] | None = None, timeout: float = 8,
    ) -> DockerClient:
        """Resolve current CLI transport once, then verify only the pinned host."""
        env = dict(os.environ if environment is None else environment)
        probe = cls(Endpoint("unix:///unresolved", "unresolved"), docker_argv=docker_argv,
                    environment=env, timeout=timeout)
        host = env.get("DOCKER_HOST")
        context = "default" if host else env.get("DOCKER_CONTEXT")
        tls = bool(env.get("DOCKER_TLS") or env.get("DOCKER_TLS_VERIFY"))
        verify = bool(env.get("DOCKER_TLS_VERIFY"))
        paths: dict[str, str | None] = {"tls_ca": None, "tls_cert": None, "tls_key": None}
        if host and tls:
            _default_tls_paths(env)
        context = context or probe._checked(["context", "show"], pinned=False).strip()
        text(context, "Docker context", limit=256)
        entries = _json(probe._checked(["context", "inspect", "--", context], pinned=False))
        if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
            raise VerificationError("Malformed Docker context inspection")
        entry = entries[0]
        if entry.get("Name") != context:
            raise VerificationError("Docker context identity changed")
        endpoints = entry.get("Endpoints")
        docker = endpoints.get("docker") if isinstance(endpoints, dict) else None
        if not isinstance(docker, dict) or type(docker.get("SkipTLSVerify")) is not bool:
            raise VerificationError("Malformed Docker context endpoint")
        host = text(docker.get("Host"), "Docker endpoint")
        material = entry.get("TLSMaterial", {})
        if not isinstance(material, dict):
            raise VerificationError("Malformed Docker TLS material")
        names = material.get("docker", [])
        if not isinstance(names, list) or any(
            not isinstance(name, str) or name not in {"ca.pem", "cert.pem", "key.pem"}
            for name in names
        ):
            raise VerificationError("Unsupported Docker TLS material")
        tls = (bool(names) or docker["SkipTLSVerify"]) and host.startswith("tcp://")
        verify = tls and not docker["SkipTLSVerify"]
        if context == "default" and tls:
            paths = _default_tls_paths(env)
        elif names and tls:
            storage = entry.get("Storage")
            root = storage.get("TLSPath") if isinstance(storage, dict) else None
            text(root, "Docker TLS root")
            if not Path(root).is_absolute():
                raise VerificationError("Docker TLS root must be absolute")
            for field, name in zip(paths, ("ca.pem", "cert.pem", "key.pem")):
                paths[field] = str(Path(root) / "docker" / name) if name in names else None
        probe.endpoint = Endpoint(host, "unresolved", context=context, tls=tls, tls_verify=verify, **paths)
        probe.endpoint = replace(probe.endpoint, daemon_id=probe._daemon_id())
        return probe

    def _inventory(self, resource_type: str, *, token: str | None = None) -> list[str]:
        if resource_type not in _FORMATS:
            raise VerificationError("Unsupported Docker inventory type")
        args = [resource_type, "ls"]
        if resource_type == "container":
            args += ["--all", "--no-trunc"]
        if token is not None:
            args += ["--filter", f"label={LABEL_TOKEN}={token}"]
        args += ["--format", _FORMATS[resource_type]]
        identifiers = [_json(line) for line in self._checked(args).splitlines()]
        pattern = r"[0-9a-f]{64}" if resource_type == "container" else r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}"
        if any(not isinstance(value, str) or re.fullmatch(pattern, value) is None for value in identifiers):
            raise VerificationError("Malformed Docker inventory identity")
        if len(set(identifiers)) != len(identifiers):
            raise VerificationError("Duplicate Docker inventory identity")
        return identifiers

    def _inspect(self, resource_type: str, identifier: str) -> dict[str, Any]:
        result = _json(self._checked([resource_type, "inspect", identifier]))
        if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
            raise VerificationError("Malformed Docker resource inspection")
        return result[0]

    def _identity(self, resource_type: str, identifier: str, token: str, creator: str) -> Identity:
        result = self._inspect(resource_type, identifier)
        actual = result.get("Id" if resource_type == "container" else "Name")
        if actual != identifier:
            raise VerificationError("Docker inspection returned a different resource")
        config = result.get("Config") if resource_type == "container" else result
        labels = config.get("Labels") if isinstance(config, dict) else None
        if not isinstance(labels, dict) or labels.get(LABEL_TOKEN) != token or labels.get(LABEL_CREATOR) != creator:
            raise VerificationError("Docker resource ownership labels do not match")
        epoch = labels.get(LABEL_GENERATION)
        if not isinstance(epoch, str) or re.fullmatch(r"[1-9][0-9]{0,18}", epoch) is None:
            raise VerificationError("Docker resource allocation label is invalid")
        return Identity(resource_type, identifier, result.get("Created" if resource_type == "container" else "CreatedAt"),
                        self.endpoint.daemon_id, token, creator, generation(int(epoch)))

    def diagnostic_observation(self, identity: Identity) -> DiagnosticObservation:
        """Read a non-authoritative snapshot; CLI connections can observe ABA."""
        if identity.daemon_id != self.endpoint.daemon_id:
            raise VerificationError("Resource belongs to a different Docker daemon")
        self._verify_daemon()
        exists = identity.resource_id in self._inventory(identity.resource_type)
        if exists and self._identity(identity.resource_type, identity.resource_id, identity.token, identity.creator) != identity:
            raise VerificationError("Docker birth identity changed")
        self._verify_daemon()
        return DiagnosticObservation(identity, exists)

    def diagnostic_reservations(self, token: str, creator: str) -> list[DiagnosticObservation]:
        """Read labeled candidates without claiming authoritative coverage."""
        birth_labels(token, creator, 1)
        self._verify_daemon()
        identities = [
            self._identity(kind, identifier, token, creator)
            for kind in _FORMATS for identifier in self._inventory(kind, token=token)
        ]
        self._verify_daemon()
        return [DiagnosticObservation(identity, True) for identity in identities]

    def probe(self, identity: Identity) -> bool:
        """Refuse authoritative proof until a daemon-bound session is admitted."""
        raise VerificationError("Authoritative daemon-bound session unavailable; CLI snapshots are diagnostic only")

    def find_reservation(self, token: str, creator: str) -> list[Identity]:
        """Refuse authoritative reservation coverage without daemon binding."""
        raise VerificationError("Authoritative daemon-bound session unavailable; CLI snapshots are diagnostic only")

    def remove(self, identity: Identity, *, authorized: bool = False) -> bool:
        """Refuse mutation when no authoritative daemon-bound session exists."""
        if authorized is not True:
            raise VerificationError("Exact resource cleanup is not authorized")
        if identity.resource_type == "volume":
            raise VerificationError(
                "Docker has no birth-conditional volume deletion; automatic cleanup remains blocked"
            )
        raise VerificationError("Authoritative daemon-bound session unavailable; automatic removal remains blocked")

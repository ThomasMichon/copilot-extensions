"""Strict selected Komodo substrate configuration; parsing never authorizes effects."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from .planning import PlanError, canonical, decode, identifier, integer, record
from .role_bindings import ProviderInstance, fields, parse_bindings, reference

SERVICES = ("core", "mongo", "periphery")
REFERENCES = ("artifact", "credentials", "host", "runtime", "storage")
_PROJECT = re.compile(r"[a-z][a-z0-9-]{0,47}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_REPOSITORIES = {
    "core": "ghcr.io/moghtech/komodo-core",
    "periphery": "ghcr.io/moghtech/komodo-periphery",
    "mongo": "docker.io/library/mongo",
}


def fingerprint(value: object) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise PlanError("Komodo requires a lowercase SHA-256 reference")
    return value


@dataclass(frozen=True)
class KomodoDeclaration:
    """Immutable desired configuration, not artifact provenance or native custody."""

    encoded: bytes

    def __post_init__(self) -> None:
        if type(self.encoded) is not bytes:
            raise PlanError("Komodo declaration must retain immutable bytes")
        value = decode(self.encoded)
        record(value, "fleet-manager.komodo-substrate", {
            "provider", "instance", "target", "configuration_ref", "revision",
            "project", "architecture", "references", "images", "resources",
            "core_port", "server_name", "outfitting_seconds",
        }, {"desired_state"})
        if value.get("desired_state", "present") not in ("present", "absent"):
            raise PlanError("Komodo desired state is unsupported")
        if canonical(value) != self.encoded:
            raise PlanError("Komodo declaration must be canonical")
        for name in ("provider", "instance", "target", "configuration_ref", "revision", "server_name"):
            identifier(value[name], "Komodo declaration reference")
        if type(value["project"]) is not str or _PROJECT.fullmatch(value["project"]) is None:
            raise PlanError("Komodo requires a bounded native project name")
        if value["architecture"] not in ("amd64", "arm64"):
            raise PlanError("Komodo architecture is unsupported")
        integer(value["core_port"], "Komodo loopback port", minimum=1024, maximum=65535)
        integer(value["outfitting_seconds"], "Komodo outfitting duration", minimum=1, maximum=1800)
        refs = fields(value["references"], set(REFERENCES))
        for item in refs.values():
            reference(item, "Komodo owner reference")
        images = fields(value["images"], set(SERVICES))
        versions = {}
        for service in SERVICES:
            image = fields(images[service], {"image", "version"})
            raw, version = image["image"], image["version"]
            prefix = _REPOSITORIES[service] + "@sha256:"
            if type(raw) is not str or not raw.startswith(prefix):
                raise PlanError("Komodo requires selected official immutable images")
            fingerprint(raw[len(prefix):])
            if type(version) is not str or _VERSION.fullmatch(version) is None:
                raise PlanError("Komodo requires explicit supported image versions")
            versions[service] = version
        if (versions["core"] != versions["periphery"] or not versions["core"].startswith("2.")
                or not versions["mongo"].startswith("8.0.")):
            raise PlanError("Komodo profile requires matching version 2 and MongoDB 8.0")
        resources = fields(value["resources"], set(SERVICES))
        for limits in resources.values():
            fields(limits, {"cpu_millis", "memory_bytes"})
            integer(limits["cpu_millis"], "Komodo CPU limit", minimum=100, maximum=64000)
            integer(limits["memory_bytes"], "Komodo memory limit",
                    minimum=128 * 1024**2, maximum=128 * 1024**3)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> KomodoDeclaration:
        return cls(canonical(value))

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self.encoded)

    @property
    def deployment_digest(self) -> str:
        return hashlib.sha256(self.encoded).hexdigest()

    def check_provider(self, instance: ProviderInstance) -> None:
        value = self.to_dict()
        if (instance.implementation != "komodo"
                or (value["provider"], value["instance"], value["target"],
                    value["configuration_ref"]) != (
                        instance.name, instance.instance_ref, instance.target,
                        instance.configuration_ref)):
            raise PlanError("Komodo declaration differs from selected provider")


def select_substrate(config: dict[str, Any], name: str) -> KomodoDeclaration:
    """Select exactly one declared substrate; ordinary role planning remains inert."""
    identifier(name, "Komodo substrate selection")
    if not isinstance(config, dict) or not isinstance(config.get("extensions"), dict):
        raise PlanError("Komodo substrate namespace must be explicitly selected")
    namespace = config["extensions"].get("role_bindings")
    instances, _ = parse_bindings(namespace)
    substrates = namespace.get("substrates")
    if not isinstance(substrates, dict) or name not in substrates:
        raise PlanError("Komodo substrate selection is unavailable")
    selected = KomodoDeclaration.from_dict(substrates[name])
    matching = [instance for instance in instances if instance.name == selected.to_dict()["provider"]]
    if len(matching) != 1:
        raise PlanError("Komodo substrate provider is unavailable")
    selected.check_provider(matching[0])
    return selected

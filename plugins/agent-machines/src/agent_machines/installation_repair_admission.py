"""Read-only host admission for manifest-owned installation repair."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import installation_context as context
from installer_readiness import discover_modules
from installer_readiness.model import (
    DiscoveryReport,
    MarketplaceProvenance,
    PluginInstallation,
)
from plugin_activation.resolver import resolve_active_plugins

_ENVIRONMENT = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_LEGACY = re.compile(r"\.[a-z0-9][a-z0-9-]{0,127}\Z")
_PRESERVE = {
    "COPILOT_EXTENSIONS_TEST_CONTAINED",
    "COPILOT_EXTENSIONS_TEST_SANDBOX",
    "COPILOT_EXTENSIONS_ALLOW_HOST_STATE",
}


class RepairAdmissionError(ValueError):
    """Host ownership or reviewed inputs could not be established."""


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def ordinary(path: Path, *, directory: bool = False) -> None:
    """Reject linked/reparse ancestors before consuming an execution target."""
    if not path.is_absolute() or ".." in path.parts:
        raise RepairAdmissionError("repair paths must be absolute without parent traversal")
    for entry in (*reversed(path.parents), path):
        try:
            info = entry.lstat()
        except OSError:
            raise RepairAdmissionError("repair owner entry is unavailable") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RepairAdmissionError("repair refuses linked or reparse entries")
        expected_directory = entry != path or directory
        if not (stat.S_ISDIR(info.st_mode) if expected_directory else stat.S_ISREG(info.st_mode)):
            raise RepairAdmissionError("repair owner entry has an unsupported type")


def payload_identity(root: Path) -> str:
    """Bind the admitted payload, not only a mutable version label."""
    ordinary(root, directory=True)
    rows: list[tuple[str, str]] = []
    total = 0
    pending = [root]
    ignored = {".git", "__pycache__", ".pytest_cache", ".ruff_cache"}
    while pending:
        directory = pending.pop()
        for entry in sorted(directory.iterdir()):
            if entry.name in ignored:
                continue
            info = entry.lstat()
            if stat.S_ISDIR(info.st_mode):
                ordinary(entry, directory=True)
                pending.append(entry)
                continue
            ordinary(entry)
            if len(rows) >= 8192 or info.st_size > 8 * 1024 * 1024:
                raise RepairAdmissionError("repair payload exceeds its file bound")
            with entry.open("rb") as stream:
                content = stream.read(8 * 1024 * 1024 + 1)
            total += len(content)
            if len(content) != info.st_size or total > 64 * 1024 * 1024:
                raise RepairAdmissionError("repair payload exceeds its content bound or changed")
            rows.append((entry.relative_to(root).as_posix(), hashlib.sha256(content).hexdigest()))
    return digest(sorted(rows))


def ordinary_environment(
    source: Mapping[str, str], *, home: Path, payload: Path,
) -> dict[str, str]:
    """Do not pass a parent's core-cell action or root into an ordinary owner."""
    result = {
        key: value for key, value in source.items()
        if not (
            key.startswith("COPILOT_EXTENSIONS_") and key not in _PRESERVE
            or key in {"COPILOT_PLUGIN_ROOT", "COPILOT_PLUGIN_STAGED_FROM",
                       "COPILOT_PLUGIN_INSTALL_STAGED"}
            or "_CELL_" in key and key.startswith("AGENT_")
        )
    }
    result.update(HOME=str(home), USERPROFILE=str(home), COPILOT_PLUGIN_ROOT=str(payload))
    return result


@dataclass(frozen=True)
class OwnerBinding:
    installation: PluginInstallation
    source: str
    mode: str
    version: str
    content_digest: str
    mode_binding: str
    no_self_provision: str | None
    payload_root_environment: str | None
    environment: Mapping[str, str] = field(repr=False, compare=False)
    legacy_root: Path | None = field(default=None, repr=False)
    refusal: str | None = None

    def evidence(self) -> dict[str, object]:
        return {
            "owner": self.installation.owner_id,
            "source": self.source,
            "sourceFingerprint": self.installation.provenance.source_fingerprint,
            "mode": self.mode,
            "payloadVersion": self.version,
            "payloadDigest": self.content_digest,
            "modeDigest": self.mode_binding,
            "refusal": self.refusal,
        }


@dataclass(frozen=True)
class HostAdmission:
    report: DiscoveryReport
    bindings: Mapping[str, OwnerBinding]
    refused: Mapping[str, str]
    activation_digest: str


def _load(path: Path) -> dict[str, object]:
    ordinary(path)
    if path.stat().st_size > 1024 * 1024:
        raise RepairAdmissionError("repair declaration exceeds its byte bound")
    try:
        value = context.read_json(path)
    except context.InstallationContextError:
        raise RepairAdmissionError("repair declaration is invalid") from None
    if not isinstance(value, dict):
        raise RepairAdmissionError("repair declaration must be an object")
    return value


def _binding(
    installation: PluginInstallation, *, source: str, home: Path,
    project_root: Path, environment: Mapping[str, str],
) -> OwnerBinding:
    root = installation.payload_root
    manifest_path = root / "plugin.json"
    if not manifest_path.exists():
        manifest_path = root / ".claude-plugin" / "plugin.json"
    manifest = _load(manifest_path)
    version = manifest.get("version")
    if not isinstance(version, str) or not version or len(version) > 128:
        raise RepairAdmissionError("repair payload version is missing or invalid")
    clean = ordinary_environment(environment, home=home, payload=root)
    invocation_path = root / "payload-invocation.json"
    no_provision = payload_environment = None
    mode, refusal, mode_data = "ordinary", "owner-readonly-protocol-not-declared", {}
    legacy_root = None
    if invocation_path.exists():
        invocation = _load(invocation_path)
        no_provision = invocation.get("noSelfProvisionEnv")
        payload_environment = invocation.get("payloadRootEnv")
        if not isinstance(no_provision, str) or not _ENVIRONMENT.fullmatch(no_provision):
            raise RepairAdmissionError("repair owner has no declared no-self-provision protocol")
        if payload_environment is not None and (
            not isinstance(payload_environment, str) or not _ENVIRONMENT.fullmatch(payload_environment)
        ):
            raise RepairAdmissionError("repair owner payload-root environment is invalid")
        legacy = invocation.get("legacyRuntimeRoot", invocation.get("runtimeRoot"))
        if not isinstance(legacy, str) or not _LEGACY.fullmatch(legacy):
            raise RepairAdmissionError("repair owner has no supported declared legacy root")
        legacy_root = home / legacy
        mode_data = context.probe_legacy_entrypoint(
            legacy_root=legacy_root, payload_root=root, plugin_id=installation.plugin_id,
            copilot_home=home / ".copilot", project_root=project_root,
            environment=clean, os_profile=home,
        )
        if mode_data["actualMode"] == "namespaced":
            mode = "core-active"
            if mode_data["status"] != "ready":
                refusal = "core-ownership-revalidation-required"
            elif mode_data.get("context"):
                refusal = None
                clean["COPILOT_EXTENSIONS_CONTEXT"] = str(mode_data["context"])
            else:
                refusal = "core-ownership-revalidation-required"
        elif (
            mode_data["status"] != "ready"
            or mode_data["actualMode"] != "legacy"
            or mode_data["desiredMode"] != "legacy"
            or not mode_data["allowMutation"]
        ):
            refusal = "ordinary-owner-mutation-not-admitted"
        else:
            refusal = None
    return OwnerBinding(
        installation=installation, source=source, mode=mode, version=version,
        content_digest=payload_identity(root), mode_binding=digest(mode_data),
        no_self_provision=no_provision, payload_root_environment=payload_environment,
        environment=clean, legacy_root=legacy_root, refusal=refusal,
    )


def admit_host(
    project_root: Path, *, home: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> HostAdmission:
    """Resolve enabled owners through the existing host authority, without installing anything."""
    home = (home or Path.home()).absolute()
    project_root = project_root.absolute()
    ordinary(project_root, directory=True)
    environment = environment if environment is not None else os.environ
    activation = resolve_active_plugins(home=home, include_projects=False, project_root=project_root)
    active_owners = activation.active
    installations: list[PluginInstallation] = []
    bindings: dict[str, OwnerBinding] = {}
    refused: dict[str, str] = {}
    for source in sorted(activation.decisions):
        active = active_owners.get(source)
        if active is None:
            refused[source] = "enabled-payload-not-attributable"
            continue
        roots = {entry.root for entry in active.live_roots}
        if len(roots) != 1:
            refused[source] = "enabled-payload-root-ambiguous"
            continue
        root = next(iter(roots)).absolute()
        clean = ordinary_environment(environment, home=home, payload=root)
        try:
            ordinary(root, directory=True)
            resolved = context.resolve_payload_source(
                payload_root=root, plugin_id=active.name, copilot_home=home / ".copilot",
                project_root=project_root, environment=clean,
            )
            provenance = resolved["source"]
            installation = PluginInstallation(
                plugin_id=active.name, payload_root=root, scopes=active.scopes,
                provenance=MarketplaceProvenance(
                    marketplace_id=resolved["marketplaceId"],
                    source_fingerprint=resolved["sourceFingerprint"],
                    source_kind=provenance["kind"], source_canonical=provenance["canonical"],
                    source_ref=provenance["ref"],
                ),
            )
            installations.append(installation)
            # Non-runtime payloads need discovery, not a new executable owner binding.
            if not (root / "installer-readiness.json").exists():
                continue
            binding = _binding(
                installation, source=source, home=home, project_root=project_root,
                environment=environment,
            )
            previous = bindings.get(installation.owner_id)
            if previous is not None and previous.evidence() != binding.evidence():
                refused[source] = "enabled-owner-binding-ambiguous"
                bindings.pop(installation.owner_id, None)
            else:
                bindings[installation.owner_id] = binding
        except (RepairAdmissionError, context.InstallationContextError, OSError):
            refused[source] = "enabled-owner-admission-refused"
    for owner_id, binding in tuple(bindings.items()):
        if binding.mode != "ordinary" or binding.legacy_root is None:
            continue
        fingerprints = {
            peer.installation.provenance.source_fingerprint
            for peer in bindings.values()
            if peer.mode == "ordinary" and peer.legacy_root == binding.legacy_root
        }
        if len(fingerprints) > 1:
            bindings[owner_id] = replace(binding, refusal="ordinary-legacy-writer-ambiguous")
    return HostAdmission(
        report=discover_modules(installations), bindings=bindings, refused=refused,
        activation_digest=digest({
            "decisions": {
                source: decision.status.value
                for source, decision in sorted(activation.decisions.items())
            },
            "owners": [bindings[key].evidence() for key in sorted(bindings)],
            "refused": refused,
        }),
    )

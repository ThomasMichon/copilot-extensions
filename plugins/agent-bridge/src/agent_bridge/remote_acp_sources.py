"""Inert remote-target contributions from identity-verified enabled payloads."""

from __future__ import annotations

import json
import logging
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml
from dropin_registry import ScanAuthority
from plugin_activation import ActivationReport, resolve_active_plugins
from plugin_resolve import PLUGIN_MANIFEST_RELS
from pydantic import ValidationError

from .remote_acp_config import RemoteAcpRequirements, TARGET_NAME_PATTERN

log = logging.getLogger("agent-bridge")


@dataclass(frozen=True)
class RemoteContribution:
    name: str
    plugin: str
    scopes: tuple[str, ...]
    requirements: RemoteAcpRequirements | None
    reason: str = ""


@dataclass
class RemoteContributionReport:
    targets: dict[str, RemoteContribution] = field(default_factory=dict)
    unavailable_owners: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


def owned_path(root: Path, declared: str, *, directory: bool = False) -> Path:
    """Refuse cross-platform escapes and every symlink/reparse component."""
    posix, windows = PurePosixPath(declared), PureWindowsPath(declared)
    if (
        not declared or "\\" in declared or ":" in declared or posix.is_absolute()
        or windows.drive or ".." in posix.parts or "." in posix.parts
    ):
        raise ValueError("invalid-owned-path")
    root = root.resolve(strict=True)
    path = root
    for part in posix.parts:
        path = path / part
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("reparse-owned-path")
    info = path.stat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or not path.resolve(strict=True).is_relative_to(root):
        raise ValueError("invalid-owned-target")
    return path


def _add(
    report: RemoteContributionReport, name: object, raw: object,
    plugin: str, scopes: tuple[str, ...],
) -> None:
    if not isinstance(name, str) or not re.fullmatch(TARGET_NAME_PATTERN, name):
        raise ValueError("invalid-target-name")
    try:
        requirements = RemoteAcpRequirements.model_validate(raw)
        contribution = RemoteContribution(name, plugin, scopes, requirements)
    except ValidationError:
        contribution = RemoteContribution(name, plugin, scopes, None, "invalid-requirements")
    previous = report.targets.get(name)
    if previous is not None:
        if (
            previous.plugin == plugin and previous.requirements == contribution.requirements
            and previous.reason == contribution.reason
        ):
            contribution = RemoteContribution(
                name, plugin, tuple(sorted(set(previous.scopes) | set(scopes))),
                contribution.requirements, contribution.reason,
            )
        else:
            contribution = RemoteContribution(
                name, "", tuple(sorted(set(previous.scopes) | set(scopes))),
                None, "conflicting-contributions",
            )
    report.targets[name] = contribution


def _manifest_path(root: Path) -> Path:
    for parts in PLUGIN_MANIFEST_RELS:
        try:
            return owned_path(root, "/".join(parts))
        except FileNotFoundError:
            continue
    raise ValueError("missing-plugin-manifest")


def discover_remote_contributions(
    activation: ActivationReport | None = None,
) -> RemoteContributionReport:
    """Read metadata only; never execute a provider or retrieve credentials."""
    activation = activation if activation is not None else resolve_active_plugins()
    report = RemoteContributionReport()
    if activation.authority is ScanAuthority.INDETERMINATE:
        report.unavailable_owners.update(activation.decisions)
        report.errors.append("remote-acp:activation-indeterminate")
        return report
    for source, decision in activation.decisions.items():
        if source not in activation.active:
            report.unavailable_owners.add(source)
    if any(finding.status == "indeterminate" for finding in activation.findings):
        report.errors.append("remote-acp:activation-incomplete")
    for source, active in sorted(activation.active.items()):
        for selected in active.live_roots:
            try:
                manifest_path = _manifest_path(selected.root)
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("invalid-plugin-manifest")
                if (
                    ("bridgeConfig" in manifest or "bridgeProviders" in manifest)
                    and manifest.get("name") != active.name
                ):
                    raise ValueError("plugin-owner-mismatch")
                targets: dict = {}
                declared = manifest.get("bridgeConfig")
                if declared is not None:
                    if not isinstance(declared, str):
                        raise ValueError("invalid-config-declaration")
                    path = owned_path(selected.root, declared)
                    data = yaml.safe_load(path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict) or set(data) - {"remote_acp"}:
                        raise ValueError("invalid-contribution-config")
                    targets = data.get("remote_acp", {})
                    if not isinstance(targets, dict):
                        raise ValueError("invalid-target-map")
                for name, raw in targets.items():
                    _add(report, name, raw, source, selected.scopes)
                declared = manifest.get("bridgeProviders")
                if declared is not None:
                    if not isinstance(declared, str):
                        raise ValueError("invalid-provider-declaration")
                    directory = owned_path(selected.root, declared, directory=True)
                    for entry in sorted(directory.iterdir()):
                        if entry.suffix != ".json":
                            continue
                        try:
                            path = owned_path(
                                selected.root, entry.relative_to(selected.root).as_posix(),
                            )
                            item = json.loads(path.read_text(encoding="utf-8"))
                            if (
                                not isinstance(item, dict)
                                or item.get("schema_version") != 1
                                or type(item.get("schema_version")) is not int
                                or item.get("kind") != "remote-acp"
                                or set(item) - {"schema_version", "kind", "name", "target"}
                            ):
                                raise ValueError("invalid-remote-manifest")
                            raw = item.get("target")
                            if isinstance(raw, str):
                                raw = targets[raw]
                            _add(report, item.get("name"), raw, source, selected.scopes)
                        except (OSError, UnicodeError, ValueError, KeyError):
                            report.errors.append(f"{source}:provider-entry-unavailable")
                            report.unavailable_owners.add(source)
            except (OSError, UnicodeError, ValueError, yaml.YAMLError):
                report.errors.append(f"{source}:contribution-unavailable")
                report.unavailable_owners.add(source)
    for error in report.errors:
        log.warning("Remote ACP contribution: %s", error)
    return report

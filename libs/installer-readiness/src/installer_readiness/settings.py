"""Resolve enabled-plugin settings into validated installation-cell receipts.

Reads user/project ``settings.json`` layers for enabled plugins and their
``extraKnownMarketplaces`` declarations, then joins each enabled plugin to an
active installation-cell receipt to produce ``PluginInstallation`` records
that :mod:`installer_readiness.graph` can discover modules from.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from installation_context import (
    InstallationContextError,
    normalize_source,
    source_identity,
    validate_context_receipt,
    validate_namespace_receipt,
)

from .graph import discover_modules
from .manifest import _ID, _MARKETPLACE_ID, _ContractProblem, _finding, _object, _strict_json
from .model import DiscoveryReport, Finding, MarketplaceProvenance, PluginInstallation

PROJECT_SETTINGS_PATHS = (
    (".claude", "settings.json"),
    (".claude", "settings.local.json"),
    (".github", "copilot", "settings.json"),
    (".github", "copilot", "settings.local.json"),
)
USER_SETTINGS_PATHS = (("settings.json",), ("settings.local.json",))


class SettingsLayer(str, Enum):
    """Settings path family and merge precedence."""

    USER = "user"
    PROJECT = "project"


@dataclass(frozen=True)
class SettingsGroup:
    """One explicitly typed settings scope."""

    root: Path
    scope: str
    layer: SettingsLayer = SettingsLayer.PROJECT


@dataclass(frozen=True)
class _Enabled:
    plugin_id: str
    marketplace_key: str
    fingerprint: str
    scope: str
    source: str


@dataclass
class _SettingsValues:
    enabled: dict[str, tuple[bool, str]]
    marketplaces: dict[str, tuple[dict[str, Any], Path, Path]]
    findings: list[Finding]


def _settings_group(group: SettingsGroup) -> _SettingsValues:
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, tuple[dict[str, Any], Path, Path]] = {}
    findings: list[Finding] = []
    try:
        layer = SettingsLayer(group.layer)
    except ValueError:
        return _SettingsValues(
            enabled={},
            marketplaces={},
            findings=[
                _finding(
                    "invalid-settings-layer",
                    f"settings layer must be user or project, got {group.layer!r}",
                    group.root,
                )
            ],
        )
    paths = (
        USER_SETTINGS_PATHS
        if layer is SettingsLayer.USER
        else PROJECT_SETTINGS_PATHS
    )
    for parts in paths:
        path = group.root.joinpath(*parts)
        if not path.exists():
            continue
        try:
            data = _object(_strict_json(path), "settings")
        except _ContractProblem as error:
            findings.append(
                _finding(
                    "invalid-settings",
                    str(error),
                    path,
                    remedy="Correct the settings JSON before discovering modules.",
                )
            )
            continue
        raw_enabled = data.get("enabledPlugins", {})
        if not isinstance(raw_enabled, dict):
            findings.append(
                _finding(
                    "invalid-settings",
                    "enabledPlugins must be an object",
                    path,
                )
            )
        else:
            for source, value in raw_enabled.items():
                if not isinstance(source, str) or not isinstance(value, bool):
                    findings.append(
                        _finding(
                            "invalid-settings",
                            "enabledPlugins entries require string keys and booleans",
                            path,
                        )
                    )
                    continue
                enabled[source] = value
        raw_marketplaces = data.get("extraKnownMarketplaces", {})
        if not isinstance(raw_marketplaces, dict):
            findings.append(
                _finding(
                    "invalid-settings",
                    "extraKnownMarketplaces must be an object",
                    path,
                )
            )
        else:
            for key, declaration in raw_marketplaces.items():
                if not isinstance(key, str) or not isinstance(declaration, dict):
                    findings.append(
                        _finding(
                            "invalid-settings",
                            "marketplace entries require string keys and objects",
                            path,
                        )
                    )
                    continue
                marketplaces[key] = (declaration, path, group.root)

    return _SettingsValues(
        enabled={source: (value, group.scope) for source, value in enabled.items()},
        marketplaces=marketplaces,
        findings=findings,
    )


def _enabled_from_settings_groups(
    settings_groups: Iterable[SettingsGroup],
) -> tuple[list[_Enabled], list[Finding]]:
    indexed_groups = list(enumerate(settings_groups))
    layer_order = {SettingsLayer.USER: 0, SettingsLayer.PROJECT: 1}
    try:
        ordered = sorted(
            indexed_groups,
            key=lambda item: (layer_order[SettingsLayer(item[1].layer)], item[0]),
        )
    except ValueError:
        ordered = indexed_groups

    enabled: dict[str, tuple[bool, str]] = {}
    marketplaces: dict[str, tuple[dict[str, Any], Path, Path]] = {}
    findings: list[Finding] = []
    for _index, group in ordered:
        values = _settings_group(group)
        enabled.update(values.enabled)
        marketplaces.update(values.marketplaces)
        findings.extend(values.findings)

    result: list[_Enabled] = []
    for source in sorted(
        name for name, (value, _scope) in enabled.items() if value
    ):
        plugin_id, separator, marketplace_key = source.partition("@")
        if not separator or not _ID.fullmatch(plugin_id) or not marketplace_key:
            findings.append(
                _finding(
                    "invalid-enabled-plugin",
                    f"enabled plugin key is not '<plugin>@<marketplace>': {source}",
                    group.root,
                )
            )
            continue
        declaration = marketplaces.get(marketplace_key)
        if declaration is None:
            findings.append(
                _finding(
                    "missing-marketplace-provenance",
                    f"enabled plugin '{source}' has no marketplace source declaration",
                    group.root,
                    remedy=(
                        "Declare extraKnownMarketplaces for every enabled runtime "
                        "so its installation cell can be selected by provenance."
                    ),
                )
            )
            continue
        value, path, declaration_root = declaration
        descriptor = value.get("source")
        if not isinstance(descriptor, dict):
            findings.append(
                _finding(
                    "invalid-marketplace-provenance",
                    f"marketplace '{marketplace_key}' has no source descriptor",
                    path,
                )
            )
            continue
        try:
            normalized = normalize_source(descriptor, declaration_root)
            identity = source_identity(normalized, marketplace_key)
        except InstallationContextError as error:
            findings.append(
                _finding(
                    "invalid-marketplace-provenance",
                    str(error),
                    path,
                )
            )
            continue
        result.append(
            _Enabled(
                plugin_id=plugin_id,
                marketplace_key=marketplace_key,
                fingerprint=identity["fingerprint"],
                scope=enabled[source][1],
                source=source,
            )
        )
    return result, findings


def installations_from_settings(
    settings_groups: Iterable[SettingsGroup],
    durable_home: str | Path,
) -> tuple[tuple[PluginInstallation, ...], tuple[Finding, ...]]:
    """Join enabled settings to active, validated installation-cell receipts."""
    durable = Path(durable_home)
    findings: list[Finding] = []
    enabled, settings_findings = _enabled_from_settings_groups(settings_groups)
    findings.extend(settings_findings)

    cells_by_fingerprint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    marketplaces = durable / "marketplaces"
    enabled_fingerprints = {item.fingerprint for item in enabled}
    if marketplaces.is_dir():
        try:
            cells = sorted(path for path in marketplaces.iterdir() if path.is_dir())
        except OSError as error:
            findings.append(
                _finding(
                    "installation-registry-indeterminate",
                    str(error),
                    marketplaces,
                    remedy="Restore read access to the installation-cell registry.",
                )
            )
            cells = []
        for cell in cells:
            receipt = cell / "namespace.json"
            if not receipt.is_file():
                continue
            try:
                validated = validate_namespace_receipt(receipt, durable)
            except InstallationContextError as error:
                match = _MARKETPLACE_ID.fullmatch(cell.name)
                suffix = cell.name.rpartition("--")[2] if match else ""
                if suffix and any(
                    fingerprint.removeprefix("sha256:").startswith(suffix)
                    for fingerprint in enabled_fingerprints
                ):
                    findings.append(
                        _finding(
                            "invalid-installation-cell",
                            str(error),
                            receipt,
                            remedy=(
                                "Repair or remove the invalid installation-cell receipt."
                            ),
                        )
                    )
                continue
            if validated["receipt"].get("state") == "active":
                cells_by_fingerprint[validated["identity"]["fingerprint"]].append(
                    validated
                )

    installations: dict[tuple[str, str], PluginInstallation] = {}
    scopes: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in enabled:
        cells = cells_by_fingerprint.get(item.fingerprint, [])
        if len(cells) != 1:
            code = (
                "installation-not-found"
                if not cells
                else "ambiguous-installation-owner"
            )
            findings.append(
                _finding(
                    code,
                    (
                        f"enabled plugin '{item.source}' matches "
                        f"{len(cells)} active installation cells"
                    ),
                    item.source,
                    remedy=(
                        "Stamp exactly one active cell for this marketplace "
                        "provenance before planning installers."
                    ),
                )
            )
            continue
        cell = cells[0]
        receipt = (
            Path(cell["cellRoot"])
            / "plugins"
            / item.plugin_id
            / "install.json"
        )
        try:
            validated = validate_context_receipt(
                receipt,
                durable,
                expected_marketplace_id=cell["marketplaceId"],
                expected_plugin_id=item.plugin_id,
                environment={},
            )
        except InstallationContextError as error:
            findings.append(
                _finding(
                    "invalid-installation-owner",
                    str(error),
                    receipt,
                    remedy="Stamp or repair the plugin installation receipt.",
                )
            )
            continue
        if validated["state"] != "active":
            findings.append(
                _finding(
                    "inactive-installation",
                    f"enabled plugin installation is {validated['state']}",
                    receipt,
                    owner=f"{validated['marketplaceId']}::{item.plugin_id}",
                )
            )
            continue
        source = validated["source"]
        provenance = MarketplaceProvenance(
            marketplace_id=validated["marketplaceId"],
            source_fingerprint=validated["sourceFingerprint"],
            source_kind=source["kind"],
            source_canonical=source["canonical"],
            source_ref=source["ref"],
        )
        key = (provenance.marketplace_id, item.plugin_id)
        candidate = PluginInstallation(
            plugin_id=item.plugin_id,
            payload_root=Path(validated["payloadRoot"]),
            provenance=provenance,
            scopes=(),
            install_receipt=Path(validated["installReceipt"]),
        )
        prior = installations.get(key)
        if prior is not None and prior.payload_root.resolve() != candidate.payload_root.resolve():
            findings.append(
                _finding(
                    "ambiguous-installation-owner",
                    "settings resolve one installation identity to multiple payload roots",
                    receipt,
                    owner=candidate.owner_id,
                )
            )
            continue
        installations[key] = candidate
        scopes[key].add(item.scope)

    resolved = tuple(
        PluginInstallation(
            plugin_id=installation.plugin_id,
            payload_root=installation.payload_root,
            provenance=installation.provenance,
            scopes=tuple(sorted(scopes[key])),
            install_receipt=installation.install_receipt,
        )
        for key, installation in sorted(installations.items())
    )
    return resolved, tuple(findings)


def discover_from_settings(
    settings_groups: Iterable[SettingsGroup],
    durable_home: str | Path,
) -> DiscoveryReport:
    """Discover modules by joining enabled settings to installation cells."""
    installations, findings = installations_from_settings(
        settings_groups,
        durable_home,
    )
    report = discover_modules(installations)
    return DiscoveryReport(
        modules=report.modules,
        declines=report.declines,
        findings=tuple(findings) + report.findings,
        machine_gated_owners=report.machine_gated_owners,
    )

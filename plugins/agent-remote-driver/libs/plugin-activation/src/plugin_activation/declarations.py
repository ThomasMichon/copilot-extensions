"""Strict declarations for one explicit checkout and the current user."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dropin_registry import Finding, ScanAuthority
from plugin_resolve import (
    SETTINGS_RELS, MarketplaceSourceKind, RepoPluginSettings,
    marketplace_source_kind, split_source,
)

from ._config_io import _SettingsLoad, _read_settings, _user_settings
from ._findings import _combine_authority
from .state import PluginStateError, validate_identity


@dataclass(frozen=True)
class PluginDeclarations:
    settings: RepoPluginSettings
    marketplace_bases: dict[str, Path]
    authority: ScanAuthority
    findings: tuple[Finding, ...]


def current_declarations(*, home: Path, project_root: Path) -> PluginDeclarations:
    """Read the same user/current-checkout layers without resolving payloads."""
    user_root = home / ".copilot"
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, dict[str, Any]] = {}
    bases: dict[str, Path] = {}
    findings: list[Finding] = []
    authorities = []
    for base, loaded in (
        (user_root, _user_settings(user_root)),
        (project_root, _read_settings(project_root, SETTINGS_RELS)),
    ):
        enabled.update(loaded.settings.enabled)
        marketplaces.update(loaded.settings.marketplaces)
        bases.update({name: base for name in loaded.settings.marketplaces})
        findings.extend(loaded.findings)
        authorities.append(loaded.authority)
    return PluginDeclarations(
        settings=RepoPluginSettings(enabled=enabled, marketplaces=marketplaces),
        marketplace_bases=bases,
        authority=_combine_authority(*authorities),
        findings=tuple(findings),
    )


def declared_payload_roots(
    declared: PluginDeclarations, *, home: Path,
) -> dict[str, Path | None]:
    """Resolve the effective declarations without an authored-local cache fallback."""
    from ._installed_root import _installed_root
    from ._marketplace_root import _local_root
    from ._memo import MarketplaceMemo

    if declared.authority is ScanAuthority.INDETERMINATE:
        raise PluginStateError("Plugin declaration authority is indeterminate.")
    loaded = _SettingsLoad(
        settings=declared.settings, authority=declared.authority,
        marketplace_origins=declared.marketplace_bases,
    )
    roots = {}
    memo = MarketplaceMemo()
    for source in sorted(declared.settings.enabled_sources()):
        validate_identity(source)
        _, marketplace = split_source(source)
        kind = marketplace_source_kind(marketplace, declared.settings)
        if kind is MarketplaceSourceKind.LOCAL:
            candidate = _local_root(
                source, loaded, base=declared.marketplace_bases[marketplace], _memo=memo,
            )
        elif kind is MarketplaceSourceKind.REMOTE:
            candidate = _installed_root(home / ".copilot", source)
        else:
            raise PluginStateError("Enabled payload has no valid marketplace declaration.")
        if candidate.indeterminate or any(
            item.reason != "missing-target" for item in candidate.findings
        ):
            raise PluginStateError("Enabled payload source is invalid or indeterminate.")
        roots[source] = candidate.root
    return roots

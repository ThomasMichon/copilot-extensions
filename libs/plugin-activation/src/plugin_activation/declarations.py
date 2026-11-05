"""Strict declarations for one explicit checkout and the current user."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dropin_registry import Finding, ScanAuthority
from plugin_resolve import SETTINGS_RELS, RepoPluginSettings

from ._config_io import _read_settings, _user_settings
from ._findings import _combine_authority


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

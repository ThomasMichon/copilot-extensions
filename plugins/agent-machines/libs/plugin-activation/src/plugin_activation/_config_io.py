"""Read repo/user plugin-activation settings from JSON and YAML config files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dropin_registry import Finding, ScanAuthority
from plugin_resolve import RepoPluginSettings

from ._findings import _combine_authority, _finding
from .state import PluginStateError, read_json_object


@dataclass
class _SettingsLoad:
    settings: RepoPluginSettings = field(default_factory=RepoPluginSettings)
    authority: ScanAuthority = ScanAuthority.ABSENT
    findings: list[Finding] = field(default_factory=list)
    enabled_origins: dict[str, Path] = field(default_factory=dict)
    marketplace_origins: dict[str, Path] = field(default_factory=dict)


def _load_json_object(path: Path) -> tuple[ScanAuthority, dict, list[Finding]]:
    if not path.exists():
        return ScanAuthority.ABSENT, {}, []
    try:
        _, data = read_json_object(path)
    except PluginStateError as exc:
        reason = (
            "entry-indeterminate"
            if isinstance(exc.__cause__, (OSError, UnicodeError))
            else "invalid-entry"
        )
        return (
            ScanAuthority.INDETERMINATE,
            {},
            [
                _finding(
                    path,
                    reason,
                    status="indeterminate",
                    detail=str(exc),
                )
            ],
        )
    return ScanAuthority.COMPLETE, data, []


def _read_settings(base: Path, rels: tuple[tuple[str, ...], ...]) -> _SettingsLoad:
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, dict] = {}
    enabled_origins: dict[str, Path] = {}
    marketplace_origins: dict[str, Path] = {}
    findings: list[Finding] = []
    authorities: list[ScanAuthority] = []

    for rel in rels:
        path = base.joinpath(*rel)
        authority, data, read_findings = _load_json_object(path)
        authorities.append(authority)
        findings.extend(read_findings)
        if authority is not ScanAuthority.COMPLETE:
            continue

        raw_enabled = data.get("enabledPlugins")
        if raw_enabled is not None and not isinstance(raw_enabled, dict):
            authorities.append(ScanAuthority.INDETERMINATE)
            findings.append(
                _finding(
                    path,
                    "invalid-entry",
                    status="indeterminate",
                    detail="enabledPlugins must be an object",
                )
            )
        elif isinstance(raw_enabled, dict):
            for source, value in raw_enabled.items():
                if not isinstance(source, str) or not isinstance(value, bool):
                    authorities.append(ScanAuthority.INDETERMINATE)
                    findings.append(
                        _finding(
                            path,
                            "invalid-entry",
                            status="indeterminate",
                            target=str(source),
                            detail="enabledPlugins entries require string keys and booleans",
                        )
                    )
                    continue
                enabled[source] = value
                enabled_origins[source] = path

        raw_marketplaces = data.get("extraKnownMarketplaces")
        if raw_marketplaces is not None and not isinstance(raw_marketplaces, dict):
            authorities.append(ScanAuthority.INDETERMINATE)
            findings.append(
                _finding(
                    path,
                    "invalid-entry",
                    status="indeterminate",
                    detail="extraKnownMarketplaces must be an object",
                )
            )
        elif isinstance(raw_marketplaces, dict):
            for marketplace, definition in raw_marketplaces.items():
                if not isinstance(marketplace, str) or not isinstance(definition, dict):
                    authorities.append(ScanAuthority.INDETERMINATE)
                    findings.append(
                        _finding(
                            path,
                            "invalid-entry",
                            status="indeterminate",
                            target=str(marketplace),
                            detail="marketplace entries require string keys and objects",
                        )
                    )
                    continue
                marketplaces[marketplace] = definition
                marketplace_origins[marketplace] = path

    authority = (
        _combine_authority(*authorities)
        if authorities
        else ScanAuthority.ABSENT
    )
    return _SettingsLoad(
        settings=RepoPluginSettings(enabled=enabled, marketplaces=marketplaces),
        authority=authority,
        findings=findings,
        enabled_origins=enabled_origins,
        marketplace_origins=marketplace_origins,
    )


def _user_settings(copilot_home: Path) -> _SettingsLoad:
    return _read_settings(
        copilot_home,
        (("settings.json",), ("settings.local.json",)),
    )


def _load_yaml_object(path: Path) -> tuple[ScanAuthority, dict, list[Finding]]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ScanAuthority.ABSENT, {}, []
    except (OSError, UnicodeDecodeError) as exc:
        return (
            ScanAuthority.INDETERMINATE,
            {},
            [
                _finding(
                    path,
                    "registry-indeterminate",
                    status="indeterminate",
                    detail=str(exc),
                )
            ],
        )
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return (
            ScanAuthority.INDETERMINATE,
            {},
            [
                _finding(
                    path,
                    "invalid-entry",
                    status="indeterminate",
                    detail=str(exc),
                )
            ],
        )
    if not isinstance(data, dict):
        return (
            ScanAuthority.INDETERMINATE,
            {},
            [
                _finding(
                    path,
                    "invalid-entry",
                    status="indeterminate",
                    detail="registry document must be a mapping",
                )
            ],
        )
    return ScanAuthority.COMPLETE, data, []

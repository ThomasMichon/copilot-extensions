"""Strict, read-only projection of the native Copilot preference tiers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

import yaml

from . import config as cfg
from .config_dropins import scan_config_dropin_registry
from .repos_registry_reader import _UniqueRegistryLoader
from .repos_registry_transaction import read_bytes, revision


class ConfigurationError(ValueError):
    """A sanitized provider error: never attach raw configuration or exceptions."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def identity(value: object, *, allow_empty: bool = False) -> str:
    """Validate a *login* value supplied as a Copilot account preference.

    Rejects secret-shaped tokens (PATs, ``sk-`` keys): a login is the only
    value here a caller could mistakenly paste a credential into.
    """
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str):
        raise ConfigurationError("invalid-identity", "The preference must be a non-secret login.")
    value = value.strip()
    if not value and allow_empty:
        return ""
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value)
        or re.match(r"(?:gh[pousr]_|github_pat_|sk-)", value, re.IGNORECASE)
    ):
        raise ConfigurationError("invalid-identity", "The preference must be a non-secret login.")
    return value


def resource_name(value: object, *, allow_empty: bool = False) -> str:
    """Validate a repository/project/machine *name* (never a credential).

    Unlike :func:`identity`, legitimate resource names are not screened
    against secret-token shapes (e.g. a repository literally named
    ``sk-ui``) -- only the shared character-set and length constraints
    apply.
    """
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str):
        raise ConfigurationError("invalid-identity", "The value must be a valid resource name.")
    value = value.strip()
    if not value and allow_empty:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value):
        raise ConfigurationError("invalid-identity", "The value must be a valid resource name.")
    return value


def parse_mapping(text: str) -> dict:
    try:
        result = yaml.load(text, Loader=_UniqueRegistryLoader)
    except (UnicodeError, ValueError, TypeError, yaml.YAMLError) as exc:
        raise ConfigurationError(
            "invalid-composition", "A configuration source is invalid; repair it with its owner."
        ) from exc
    if result is None:
        return {}
    if not isinstance(result, dict):
        raise ConfigurationError("invalid-composition", "A configuration source must be a mapping.")
    return result


def mapping(path: Path) -> dict:
    content = read_bytes(path)
    if content is None:
        return {}
    try:
        text = content.decode("utf-8")
    except UnicodeError as exc:
        raise ConfigurationError("invalid-composition", "A configuration source is invalid.") from exc
    return parse_mapping(text)


def fingerprint(paths: list[Path]) -> str:
    """Include absence, directory membership and full bytes, not display values."""
    entries = {}
    for path in paths:
        candidates = [path, *sorted(path.rglob("*"))] if path.is_dir() else [path]
        for item in candidates:
            if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
                raise ConfigurationError(
                    "indirect-source", "Configuration source indirection requires owner inspection."
                )
            key = str(item.absolute())
            entries[key] = "directory" if item.is_dir() else revision(read_bytes(item))
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class PreferenceDefaults:
    login: str
    switch_enabled: bool
    sources: list[dict]
    revision: str


def defaults(project: str) -> PreferenceDefaults:
    project = resource_name(project)
    global_path = cfg.global_config_path()
    machine_path = cfg.project_dir(project) / "config.yaml"
    directory = machine_path.parent / "config.d"
    initial = scan_config_dropin_registry(directory, project_name=project, previous={})
    paths = [global_path, machine_path, directory, *[c.target for c in initial.active_configs]]
    before = fingerprint(paths)
    report = scan_config_dropin_registry(directory, project_name=project, previous={})
    invalid = [
        finding for finding in report.findings
        if not (finding.status == "inactive" and finding.reason == "not-enabled" and finding.owner)
    ]
    if invalid or report.authority.value == "indeterminate":
        raise ConfigurationError(
            "invalid-composition", "Configuration drop-ins need owner repair before editing preferences."
        )
    if [c.target for c in report.active_configs] != [c.target for c in initial.active_configs]:
        raise ConfigurationError("source-conflict", "Configuration changed; refresh and preview again.")
    layers = [
        ("global-default", "global", global_path, mapping(global_path)),
        *[
            ("drop-in-" + hashlib.sha256(str(c.target).encode()).hexdigest()[:16],
             "plugin" if c.owner else "machine-local", c.target, mapping(c.target))
            for c in report.active_configs
        ],
        ("project-local-default", "machine-local", machine_path, mapping(machine_path)),
    ]
    sources = []
    login = ""
    enabled = False
    for source_id, kind, path, raw in layers:
        if "default_copilot_account" in raw:
            login = identity(raw["default_copilot_account"], allow_empty=True)
            sources.append({
                "id": source_id, "kind": kind, "value": login or "(ambient)",
                "revision": revision(read_bytes(path)), "precedence": len(sources),
                "setting": "default_copilot_account",
            })
        if "copilot_identity_switch_enabled" in raw:
            if not isinstance(raw["copilot_identity_switch_enabled"], bool):
                raise ConfigurationError(
                    "invalid-composition", "Identity-switch policy must be a boolean."
                )
            enabled = raw["copilot_identity_switch_enabled"]
    if fingerprint(paths) != before:
        raise ConfigurationError("source-conflict", "Configuration changed; refresh and preview again.")
    return PreferenceDefaults(login, enabled, sources, before)

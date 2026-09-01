"""Strict Copilot plugin inventory and activation state operations."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PluginStateError(ValueError):
    """Raised when Copilot plugin state cannot be handled safely."""


@dataclass(frozen=True)
class ActivationSnapshot:
    """Activation fields that an inventory bootstrap may promote."""

    identity: str
    settings_existed: bool
    enabled_plugins_existed: bool
    user_value_existed: bool
    user_value: bool | None
    inventory_enabled: bool | None


@dataclass
class _StateDocuments:
    home: Path
    config_path: Path
    config_header: str
    config: dict[str, Any]
    settings_path: Path
    settings: dict[str, Any]
    record: dict[str, Any] | None
    user_activation: bool | None


def copilot_home() -> Path:
    """Return the current user's Copilot state directory."""
    return Path.home() / ".copilot"


def validate_identity(identity: str) -> None:
    """Require a source-qualified ``name@marketplace`` plugin identity."""
    if (
        not isinstance(identity, str)
        or identity.count("@") != 1
        or identity.startswith("@")
        or identity.endswith("@")
    ):
        raise PluginStateError("plugin identity must be <name>@<marketplace>")


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PluginStateError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _split_jsonc_header(raw: str) -> tuple[str, str]:
    lines = raw.splitlines(keepends=True)
    body_start = 0
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("//") or not stripped.strip():
            body_start = index + 1
            continue
        break
    return "".join(lines[:body_start]), "".join(lines[body_start:])


def read_json_object(
    path: Path,
    *,
    jsonc_header: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Read one JSON object, rejecting malformed input and duplicate keys."""
    if not path.exists():
        return "", {}
    try:
        raw = path.read_text(encoding="utf-8")
        header, body = _split_jsonc_header(raw) if jsonc_header else ("", raw)
        value = json.loads(body, object_pairs_hook=_reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError, PluginStateError) as exc:
        raise PluginStateError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PluginStateError(f"{path} must contain a JSON object")
    return header, value


def write_json_object_atomic(
    path: Path,
    value: dict[str, Any],
    header: str = "",
) -> None:
    """Atomically write one JSON object while preserving a leading JSONC header."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(header)
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def inventory_identity(record: dict[str, Any]) -> str | None:
    """Return a source-qualified identity from one installed inventory record."""
    name = record.get("name")
    marketplace = record.get("marketplace")
    if not isinstance(name, str) or not name:
        return None
    identity = (
        name
        if "@" in name
        else f"{name}@{marketplace}"
        if isinstance(marketplace, str) and marketplace
        else None
    )
    if identity is not None:
        validate_identity(identity)
    return identity


def inventory_records(
    config: dict[str, Any],
    path: Path,
) -> list[dict[str, Any]]:
    """Validate and return installed plugin inventory records."""
    raw = config.get("installedPlugins", [])
    if not isinstance(raw, list):
        raise PluginStateError(f"{path}: installedPlugins must be an array")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise PluginStateError(
                f"{path}: installedPlugins[{index}] must be an object"
            )
        identity = inventory_identity(item)
        if identity is not None:
            if identity in seen:
                raise PluginStateError(
                    f"{path}: duplicate installed plugin identity: {identity}"
                )
            seen.add(identity)
        enabled = item.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            label = identity or str(index)
            raise PluginStateError(
                f"{path}: installed plugin {label} enabled must be boolean"
            )
        records.append(item)
    return records


def activation_value(
    data: dict[str, Any],
    path: Path,
    identity: str,
) -> bool | None:
    """Return one activation value, distinguishing absence from ``false``."""
    validate_identity(identity)
    enabled = data.get("enabledPlugins")
    if enabled is None and "enabledPlugins" not in data:
        return None
    if not isinstance(enabled, dict):
        raise PluginStateError(f"{path}: enabledPlugins must be an object")
    for key, value in enabled.items():
        validate_identity(key)
        if not isinstance(value, bool):
            raise PluginStateError(f"{path}: enabledPlugins.{key} must be boolean")
    return enabled.get(identity)


def remove_activation_entries(
    settings: dict[str, Any],
    identities: Iterable[str],
    *,
    path: Path,
) -> tuple[dict[str, Any], list[str]]:
    """Remove exact user activation keys while preserving all other settings."""
    requested = sorted(set(identities))
    for identity in requested:
        validate_identity(identity)
    enabled = settings.get("enabledPlugins")
    if enabled is None and "enabledPlugins" not in settings:
        return dict(settings), []
    if not isinstance(enabled, dict):
        raise PluginStateError(f"{path}: enabledPlugins must be an object")
    for key, value in enabled.items():
        validate_identity(key)
        if not isinstance(value, bool):
            raise PluginStateError(f"{path}: enabledPlugins.{key} must be boolean")

    new = dict(settings)
    new_enabled = dict(enabled)
    removed = [identity for identity in requested if identity in new_enabled]
    for identity in removed:
        del new_enabled[identity]
    new["enabledPlugins"] = new_enabled
    return new, removed


def installed_plugin_identities(home: Path | None = None) -> list[str]:
    """Return source-qualified identities from persistent installed inventory."""
    home = home or copilot_home()
    config_path = home / "config.json"
    _, config = read_json_object(config_path, jsonc_header=True)
    return sorted(
        identity
        for record in inventory_records(config, config_path)
        if (identity := inventory_identity(record)) is not None
    )


def _load_state(
    identity: str,
    home: Path | None = None,
) -> _StateDocuments:
    validate_identity(identity)
    home = home or copilot_home()
    config_path = home / "config.json"
    config_header, config = read_json_object(config_path, jsonc_header=True)
    records = inventory_records(config, config_path)
    matching = [
        record for record in records if inventory_identity(record) == identity
    ]
    settings_path = home / "settings.json"
    _, settings = read_json_object(settings_path)
    user_activation = activation_value(settings, settings_path, identity)
    return _StateDocuments(
        home=home,
        config_path=config_path,
        config_header=config_header,
        config=config,
        settings_path=settings_path,
        settings=settings,
        record=matching[0] if matching else None,
        user_activation=user_activation,
    )


def inspect_plugin_state(
    identity: str,
    home: Path | None = None,
    repo: Path | None = None,
) -> dict[str, Any]:
    """Inspect inventory, user activation, repository activation, and trust."""
    documents = _load_state(identity, home)
    repository_activation: bool | None = None
    repository_trusted: bool | None = None
    if repo is not None:
        repo = repo.expanduser().resolve()
        repo_settings_path = repo / ".github" / "copilot" / "settings.json"
        _, repo_settings = read_json_object(repo_settings_path)
        repository_activation = activation_value(
            repo_settings,
            repo_settings_path,
            identity,
        )
        trusted_folders = documents.config.get("trustedFolders", [])
        if not isinstance(trusted_folders, list) or not all(
            isinstance(item, str) for item in trusted_folders
        ):
            raise PluginStateError(
                f"{documents.config_path}: trustedFolders must be an array of paths"
            )
        repo_key = os.path.normcase(os.path.normpath(str(repo)))
        repository_trusted = any(
            os.path.normcase(
                os.path.normpath(str(Path(item).expanduser().resolve()))
            )
            == repo_key
            for item in trusted_folders
        )

    record = documents.record
    return {
        "identity": identity,
        "installed": record is not None,
        "inventoryEnabled": record.get("enabled") if record else None,
        "userActivation": (
            "absent"
            if documents.user_activation is None
            else str(documents.user_activation).lower()
        ),
        "repositoryActivation": (
            None
            if repo is None
            else "absent"
            if repository_activation is None
            else str(repository_activation).lower()
        ),
        "repositoryTrusted": repository_trusted,
        "installedButNotUserEnabled": (
            record is not None and documents.user_activation is not True
        ),
    }


def remove_user_activation(
    identity: str,
    home: Path | None = None,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Preview or remove user activation without uninstalling inventory."""
    documents = _load_state(identity, home)
    if documents.record is None:
        raise PluginStateError(
            f"{identity} is not present in installed inventory; refusing mutation"
        )
    state = inspect_plugin_state(identity, documents.home)
    settings, removed = remove_activation_entries(
        documents.settings,
        [identity],
        path=documents.settings_path,
    )
    changes = [
        f"remove enabledPlugins.{removed_identity}"
        for removed_identity in removed
    ]
    inventory_changed = documents.record.get("enabled") is not False
    if inventory_changed:
        changes.append(f"set installedPlugins[{identity}].enabled=false")
        if apply:
            documents.record["enabled"] = False
    if apply and changes:
        if removed:
            write_json_object_atomic(documents.settings_path, settings)
        if inventory_changed:
            write_json_object_atomic(
                documents.config_path,
                documents.config,
                documents.config_header,
            )
    return {
        **state,
        "mode": "apply" if apply else "dry-run",
        "changes": changes,
        "changed": bool(changes),
    }


def capture(identity: str, home: Path | None = None) -> ActivationSnapshot:
    """Capture exact user activation plus the inventory activation flag."""
    documents = _load_state(identity, home)
    enabled = documents.settings.get("enabledPlugins")
    enabled_existed = "enabledPlugins" in documents.settings
    user_existed = isinstance(enabled, dict) and identity in enabled
    return ActivationSnapshot(
        identity=identity,
        settings_existed=documents.settings_path.exists(),
        enabled_plugins_existed=enabled_existed,
        user_value_existed=user_existed,
        user_value=documents.user_activation,
        inventory_enabled=(
            documents.record.get("enabled")
            if documents.record is not None
            else None
        ),
    )


def restore(snapshot: ActivationSnapshot, home: Path | None = None) -> None:
    """Restore activation fields without rolling back refreshed inventory."""
    home = home or copilot_home()
    settings_path = home / "settings.json"
    _, settings = read_json_object(settings_path)
    activation_value(settings, settings_path, snapshot.identity)
    enabled = settings.get("enabledPlugins")
    if enabled is None and "enabledPlugins" not in settings:
        enabled = {}
    if not isinstance(enabled, dict):  # validated above; narrows the type
        raise PluginStateError(f"{settings_path}: enabledPlugins must be an object")
    if snapshot.user_value_existed:
        enabled[snapshot.identity] = snapshot.user_value
    else:
        enabled.pop(snapshot.identity, None)
    if enabled or snapshot.enabled_plugins_existed:
        settings["enabledPlugins"] = enabled
    else:
        settings.pop("enabledPlugins", None)
    if settings or snapshot.settings_existed:
        write_json_object_atomic(settings_path, settings)
    elif settings_path.exists():
        settings_path.unlink()

    config_path = home / "config.json"
    header, config = read_json_object(config_path, jsonc_header=True)
    for record in inventory_records(config, config_path):
        if inventory_identity(record) != snapshot.identity:
            continue
        record["enabled"] = (
            snapshot.inventory_enabled
            if snapshot.inventory_enabled is not None
            else bool(snapshot.user_value)
            if snapshot.user_value_existed
            else False
        )
        write_json_object_atomic(config_path, config, header)
        return


def run_install_preserving_activation(
    argv: Sequence[str],
    identity: str,
    *,
    home: Path | None = None,
    **run_kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run an install command and restore activation on every exit path."""
    snapshot = capture(identity, home)
    try:
        return subprocess.run(argv, **run_kwargs)
    finally:
        restore(snapshot, home)

"""Compose a paired knowledge repo's Copilot plugins into its harness.

The launch repo owns Copilot's settings lookup, while operator-specific plugin
settings live in the private knowledge repo.  This module bridges that boundary
without making launch code depend on the ``harness-knowledge`` skill payload.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plugin_resolve import RepoPluginSettings, split_source
from plugin_resolve.conventions import (
    LOCAL_MARKETPLACE_SOURCE_KINDS,
    SETTINGS_RELS,
)

from . import config as cfg
from . import repos as repos_mod
from . import state_root, tracking

_OVERLAY_KEY = "_agentWorktreesKnowledgePluginOverlay"
_OVERLAY_VERSION = 1


class KnowledgePluginError(RuntimeError):
    """A safe, user-facing knowledge-plugin composition failure."""


class KnowledgePairIntegrityError(KnowledgePluginError):
    """A pair defect that must fail without sanitizing either checkout."""


@dataclass(frozen=True)
class KnowledgePair:
    """The role-resolved paths and repo identity of a tracked pair."""

    harness_path: Path
    knowledge_path: Path
    knowledge_repo: str
    pair_id: str
    pair_kind: str


def _load_json_object(path: Path) -> dict[str, Any]:
    """Read an existing overlay strictly to protect unmanaged operator data."""
    try:
        if not path.is_file():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("root value is not an object")
        return value
    except (OSError, ValueError) as exc:
        raise KnowledgePluginError(f"cannot read existing settings {path}: {exc}") from exc


def _read_repo_settings_strict(
    repo_dir: Path,
    *,
    source_name: str,
    native_local: dict[str, Any] | None = None,
    ignored_native_marketplaces: set[str] | None = None,
    ignored_native_enabled: set[str] | None = None,
) -> RepoPluginSettings:
    """Read all settings tiers without hiding malformed or unreadable sources."""
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, dict] = {}
    ignored_marketplaces = ignored_native_marketplaces or set()
    ignored_enabled = ignored_native_enabled or set()
    for rel in SETTINGS_RELS:
        path = repo_dir.joinpath(*rel)
        is_native_local = rel == (".github", "copilot", "settings.local.json")
        if is_native_local and native_local is not None:
            data = native_local
        else:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("root value is not an object")
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                raise KnowledgePluginError(
                    f"cannot read {source_name} settings {path}: {exc}"
                ) from exc

        raw_enabled = data.get("enabledPlugins")
        if raw_enabled is not None and not isinstance(raw_enabled, dict):
            raise KnowledgePluginError(
                f"cannot read {source_name} settings {path}: "
                "'enabledPlugins' is not a JSON object"
            )
        if isinstance(raw_enabled, dict):
            for source, on in raw_enabled.items():
                if is_native_local and source in ignored_enabled:
                    continue
                enabled[source] = bool(on)

        raw_marketplaces = data.get("extraKnownMarketplaces")
        if raw_marketplaces is not None and not isinstance(
            raw_marketplaces, dict
        ):
            raise KnowledgePluginError(
                f"cannot read {source_name} settings {path}: "
                "'extraKnownMarketplaces' is not a JSON object"
            )
        if isinstance(raw_marketplaces, dict):
            for name, definition in raw_marketplaces.items():
                if not isinstance(definition, dict):
                    raise KnowledgePluginError(
                        f"cannot read {source_name} settings {path}: "
                        f"marketplace '{name}' is not a JSON object"
                    )
                if is_native_local and name in ignored_marketplaces:
                    continue
                _validate_marketplace_source(
                    definition,
                    marketplace=name,
                    source_name=source_name,
                    path=path,
                )
                marketplaces[name] = definition
    return RepoPluginSettings(enabled=enabled, marketplaces=marketplaces)


def _read_harness_settings(
    repo_dir: Path,
    native_local: dict[str, Any],
    *,
    ignored_native_marketplaces: set[str],
    ignored_native_enabled: set[str],
) -> RepoPluginSettings:
    """Read every harness tier while excluding generated overlay ownership.

    ``native_local`` has already had exact marker-owned values retired. For a
    markerless legacy overlay, only values whose ownership was independently
    proven are omitted so they can be adopted once; lower-precedence
    Claude/native tiers and all unproven local values still participate in
    conflict detection.
    """
    return _read_repo_settings_strict(
        repo_dir,
        source_name="harness source",
        native_local=native_local,
        ignored_native_marketplaces=ignored_native_marketplaces,
        ignored_native_enabled=ignored_native_enabled,
    )


def _normalized_checkout_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path.resolve())))


def _ensure_distinct_checkouts(harness: Path, knowledge: Path) -> None:
    if _normalized_checkout_path(harness) == _normalized_checkout_path(knowledge):
        raise KnowledgePairIntegrityError(
            "harness and knowledge checkouts resolve to the same path: "
            f"{harness.resolve()}"
        )


def _validate_marketplace_source(
    definition: dict,
    *,
    marketplace: object,
    source_name: str,
    path: Path,
) -> None:
    """Reject marketplace definitions that the generated overlay cannot use."""
    source = definition.get("source")
    if not isinstance(source, dict):
        raise KnowledgePluginError(
            f"cannot read {source_name} settings {path}: marketplace "
            f"{marketplace!r} 'source' is not a JSON object"
        )
    source_kind = source.get("source")
    if not isinstance(source_kind, str) or not source_kind.strip():
        raise KnowledgePluginError(
            f"cannot read {source_name} settings {path}: marketplace "
            f"{marketplace!r} 'source.source' is not a non-empty string"
        )
    if source_kind.strip() in LOCAL_MARKETPLACE_SOURCE_KINDS:
        raw_path = source.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise KnowledgePluginError(
                f"cannot read {source_name} settings {path}: local marketplace "
                f"{marketplace!r} 'source.path' is not a non-empty string"
            )


def _is_local_marketplace(definition: dict) -> bool:
    source = definition.get("source")
    return bool(
        isinstance(source, dict)
        and isinstance(source.get("source"), str)
        and source["source"].strip() in LOCAL_MARKETPLACE_SOURCE_KINDS
    )


def _localized_marketplace(definition: dict, knowledge_path: Path) -> dict:
    """Copy a local marketplace definition and absolutize its directory path."""
    source = definition.get("source")
    assert isinstance(source, dict)
    raw_path = source.get("path")
    assert isinstance(raw_path, str) and raw_path.strip()
    directory = Path(raw_path.strip())
    if not directory.is_absolute():
        directory = knowledge_path / directory
    localized_source = dict(source)
    localized_source["path"] = directory.resolve().as_posix()
    localized = dict(definition)
    localized["source"] = localized_source
    return localized


def _dict_setting(data: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise KnowledgePluginError(
            f"cannot compose {path}: '{key}' is not a JSON object"
        )
    return dict(value)


def _managed_marker(
    existing: dict[str, Any], path: Path
) -> dict[str, Any] | None:
    """Return a validated ownership marker, or ``None`` when none exists."""
    if _OVERLAY_KEY not in existing:
        return None
    marker = existing.get(_OVERLAY_KEY)
    if not isinstance(marker, dict):
        raise KnowledgePluginError(
            f"cannot safely manage {path}: '{_OVERLAY_KEY}' is not a JSON object"
        )
    if marker.get("version") != _OVERLAY_VERSION:
        raise KnowledgePluginError(
            f"cannot safely manage {path}: unsupported '{_OVERLAY_KEY}' version "
            f"{marker.get('version')!r}"
        )
    for key in ("marketplaces", "enabledPlugins"):
        if not isinstance(marker.get(key), dict):
            raise KnowledgePluginError(
                f"cannot safely manage {path}: '{_OVERLAY_KEY}.{key}' is not "
                "a JSON object"
            )
    for key in ("pairId", "pairKind"):
        if key in marker and not isinstance(marker[key], str):
            raise KnowledgePluginError(
                f"cannot safely manage {path}: '{_OVERLAY_KEY}.{key}' is not "
                "a string"
            )
    return marker


def _previous_managed(
    existing: dict[str, Any], path: Path
) -> tuple[dict, dict]:
    marker = _managed_marker(existing, path)
    if marker is None:
        return {}, {}
    marketplaces = marker.get("marketplaces")
    enabled = marker.get("enabledPlugins")
    return dict(marketplaces), dict(enabled)


def _retire_previous(current: dict, previous: dict) -> None:
    """Drop a previously managed value only when it remains unmodified."""
    for key, prior_value in previous.items():
        if key in current and current[key] == prior_value:
            del current[key]


def _write_overlay(path: Path, result: dict[str, Any]) -> bool:
    """Atomically write or safely remove a native local settings overlay."""
    try:
        before = path.read_text(encoding="utf-8") if path.is_file() else None
    except OSError as exc:
        raise KnowledgePluginError(f"cannot safely read settings {path}: {exc}") from exc
    if not result:
        if before is None:
            return False
        try:
            path.unlink()
        except OSError as exc:
            raise KnowledgePluginError(
                f"cannot safely remove empty managed settings {path}: {exc}"
            ) from exc
        return True

    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if before == rendered:
        return False
    staging: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            staging = Path(handle.name)
            handle.write(rendered)
        os.replace(staging, path)
    except OSError as exc:
        raise KnowledgePluginError(f"cannot safely update settings {path}: {exc}") from exc
    finally:
        if staging is not None:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
    return True


def _overlay_lock_target(path: Path) -> Path:
    """Return a stable machine-local lock target without touching the repo."""
    normalized = os.path.normcase(os.path.abspath(str(path)))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return (
        Path(tempfile.gettempdir())
        / "agent-worktrees"
        / "knowledge-plugin-overlays"
        / f"{digest}.json"
    )


@contextmanager
def _overlay_transaction(path: Path) -> Iterator[None]:
    """Serialize one complete overlay read/modify/write across processes."""
    lock_target = _overlay_lock_target(path)
    try:
        with tracking._RecordLock(
            lock_target,
            timeout=30.0,
            require_sidecar=True,
        ):
            yield
    except (OSError, TimeoutError) as exc:
        raise KnowledgePluginError(
            f"cannot lock knowledge plugin overlay {path}: {exc}"
        ) from exc


def _retire_invalid_pair_overlay_locked(
    harness_path: str | Path,
    *,
    pair_error: str,
) -> dict[str, Any]:
    """Retire exact marker-owned values for one invalid tracked harness pair."""
    harness = Path(harness_path).resolve()
    output_path = harness / ".github" / "copilot" / "settings.local.json"
    if not output_path.is_file():
        return {
            "action": "no-op",
            "paired": False,
            "retired": False,
            "changed": False,
            "settings_local": str(output_path),
            "harness_path": str(harness),
            "pair_error": pair_error,
        }

    existing = _load_json_object(output_path)
    marker = _managed_marker(existing, output_path)
    # Explicit anchor overlays have no pair identity. They remain legitimate
    # machine-local settings even when this launch has no valid worktree pair.
    if marker is None or not marker.get("pairId"):
        return {
            "action": "no-op",
            "paired": False,
            "retired": False,
            "changed": False,
            "settings_local": str(output_path),
            "harness_path": str(harness),
            "pair_error": pair_error,
        }

    marketplaces = _dict_setting(existing, "extraKnownMarketplaces", output_path)
    enabled = _dict_setting(existing, "enabledPlugins", output_path)
    previous_marketplaces = dict(marker["marketplaces"])
    previous_enabled = dict(marker["enabledPlugins"])
    retired_marketplaces = sorted(
        key
        for key, value in previous_marketplaces.items()
        if marketplaces.get(key) == value
    )
    retired_enabled = sorted(
        key for key, value in previous_enabled.items() if enabled.get(key) == value
    )
    preserved_marketplaces = sorted(
        key
        for key, value in previous_marketplaces.items()
        if key in marketplaces and marketplaces[key] != value
    )
    preserved_enabled = sorted(
        key
        for key, value in previous_enabled.items()
        if key in enabled and enabled[key] != value
    )
    _retire_previous(marketplaces, previous_marketplaces)
    _retire_previous(enabled, previous_enabled)

    result = dict(existing)
    result.pop(_OVERLAY_KEY, None)
    if marketplaces:
        result["extraKnownMarketplaces"] = marketplaces
    else:
        result.pop("extraKnownMarketplaces", None)
    if enabled:
        result["enabledPlugins"] = enabled
    else:
        result.pop("enabledPlugins", None)
    changed = _write_overlay(output_path, result)
    return {
        "action": "retired",
        "paired": False,
        "retired": True,
        "changed": changed,
        "settings_local": str(output_path),
        "harness_path": str(harness),
        "pair_error": pair_error,
        "retired_entries": {
            "marketplaces": retired_marketplaces,
            "enabled_plugins": retired_enabled,
        },
        "preserved_modified": {
            "marketplaces": preserved_marketplaces,
            "enabled_plugins": preserved_enabled,
        },
        "file_removed": not output_path.exists(),
    }


def _retire_invalid_pair_overlay(
    harness_path: str | Path,
    *,
    pair_error: str,
) -> dict[str, Any]:
    harness = Path(harness_path).resolve()
    output_path = harness / ".github" / "copilot" / "settings.local.json"
    with _overlay_transaction(output_path):
        return _retire_invalid_pair_overlay_locked(
            harness,
            pair_error=pair_error,
        )


def _legacy_local_marketplaces(
    *,
    existing: dict[str, Any],
    definitions: dict[str, dict],
    knowledge_path: Path,
    legacy_definitions: dict[str, dict],
    legacy_knowledge_path: Path | None,
) -> set[str]:
    """Identify markerless marketplaces that exactly match old assembler output."""
    return {
        name
        for name, definition in definitions.items()
        if name in existing
        and (
            existing[name] == _localized_marketplace(definition, knowledge_path)
            or (
                legacy_knowledge_path is not None
                and name in legacy_definitions
                and existing[name]
                == _localized_marketplace(
                    legacy_definitions[name], legacy_knowledge_path
                )
            )
        )
    }


def _compose_locked(
    harness_path: str | Path,
    knowledge_path: str | Path,
    *,
    pair_id: str | None = None,
    pair_kind: str | None = None,
    legacy_knowledge_path: str | Path | None = None,
) -> dict[str, Any]:
    """Compose knowledge plugin settings into the harness's local overlay.

    Local marketplaces are re-pointed at ``knowledge_path``.  Remote
    marketplaces and enabled plugins are carried when the committed harness
    base does not already provide them.  Existing unmanaged local settings are
    preserved.  A private ownership marker records exact managed values, so a
    later re-point/removal can retire stale entries without deleting operator
    edits.
    """
    harness = Path(harness_path).resolve()
    knowledge = Path(knowledge_path).resolve()
    _ensure_distinct_checkouts(harness, knowledge)
    legacy_knowledge = (
        Path(legacy_knowledge_path).resolve() if legacy_knowledge_path else None
    )
    if not harness.is_dir():
        raise KnowledgePluginError(f"harness checkout does not exist: {harness}")
    if not knowledge.is_dir():
        raise KnowledgePluginError(f"knowledge checkout does not exist: {knowledge}")

    knowledge_settings = _read_repo_settings_strict(
        knowledge, source_name="knowledge source"
    )
    output_path = harness / ".github" / "copilot" / "settings.local.json"
    existing = _load_json_object(output_path)
    marketplaces = _dict_setting(existing, "extraKnownMarketplaces", output_path)
    enabled = _dict_setting(existing, "enabledPlugins", output_path)
    markerless_legacy = _OVERLAY_KEY not in existing
    previous_marketplaces, previous_enabled = _previous_managed(existing, output_path)
    _retire_previous(marketplaces, previous_marketplaces)
    _retire_previous(enabled, previous_enabled)

    local_names: set[str] = set()
    local_definitions: dict[str, dict] = {}
    desired_marketplaces: dict[str, dict] = {}
    for name, definition in knowledge_settings.marketplaces.items():
        if _is_local_marketplace(definition):
            local_names.add(name)
            local_definitions[name] = definition
            desired_marketplaces[name] = _localized_marketplace(definition, knowledge)
        else:
            desired_marketplaces[name] = dict(definition)

    desired_local_enabled = {
        source
        for source, on in knowledge_settings.enabled.items()
        if on and split_source(source)[1] in local_names
    }
    legacy_local_definitions: dict[str, dict] = {}
    if (
        markerless_legacy
        and legacy_knowledge is not None
        and legacy_knowledge != knowledge
        and legacy_knowledge.is_dir()
    ):
        legacy_settings = _read_repo_settings_strict(
            legacy_knowledge, source_name="legacy knowledge source"
        )
        legacy_local_definitions = {
            name: definition
            for name, definition in legacy_settings.marketplaces.items()
            if _is_local_marketplace(definition)
        }
    proven_legacy_names = (
        _legacy_local_marketplaces(
            existing=marketplaces,
            definitions=local_definitions,
            knowledge_path=knowledge,
            legacy_definitions=legacy_local_definitions,
            legacy_knowledge_path=legacy_knowledge,
        )
        if markerless_legacy
        else set()
    )
    proven_legacy_enabled = {
        source
        for source in desired_local_enabled
        if split_source(source)[1] in proven_legacy_names
        and enabled.get(source) is True
    }
    native_local = {
        "extraKnownMarketplaces": marketplaces,
        "enabledPlugins": enabled,
    }
    harness_settings = _read_harness_settings(
        harness,
        native_local,
        ignored_native_marketplaces=proven_legacy_names,
        ignored_native_enabled=proven_legacy_enabled,
    )

    candidates: dict[str, dict] = {}
    conflicting_marketplaces: list[str] = []
    for name, definition in sorted(desired_marketplaces.items()):
        if name in local_names and name in marketplaces:
            if not markerless_legacy or name not in proven_legacy_names:
                conflicting_marketplaces.append(name)
                continue
        base_definition = harness_settings.marketplaces.get(name)
        if base_definition is None:
            candidates[name] = definition
        elif base_definition != definition:
            # The generic harness base remains authoritative on name collision.
            conflicting_marketplaces.append(name)

    managed_marketplaces: dict[str, dict] = {}
    for name, definition in candidates.items():
        if name in proven_legacy_names:
            # Exact old-assembler output is safe to adopt and, for a paired
            # worktree, re-point from the registered anchor to the sibling.
            marketplaces[name] = definition
            managed_marketplaces[name] = definition
            continue
        if name in marketplaces and marketplaces[name] != definition:
            conflicting_marketplaces.append(name)
            continue
        if name not in marketplaces:
            marketplaces[name] = definition
            managed_marketplaces[name] = definition

    managed_enabled: dict[str, bool] = {}
    conflicting_enabled: list[str] = []
    conflicting_names = set(conflicting_marketplaces)
    if markerless_legacy:
        for source, on in enabled.items():
            _, marketplace = split_source(source)
            if marketplace not in local_names:
                continue
            if (
                marketplace not in proven_legacy_names
                or source not in desired_local_enabled
                or on is not True
            ):
                conflicting_enabled.append(source)
    for source, on in sorted(knowledge_settings.enabled.items()):
        if not on:
            continue
        _, marketplace = split_source(source)
        if marketplace in conflicting_names:
            conflicting_enabled.append(source)
            continue
        if marketplace in local_names and not markerless_legacy and source in enabled:
            conflicting_enabled.append(source)
            continue
        if source in harness_settings.enabled:
            if harness_settings.enabled[source] is True:
                continue
            conflicting_enabled.append(source)
            continue
        if source in proven_legacy_enabled:
            managed_enabled[source] = True
            continue
        enabled[source] = True
        managed_enabled[source] = True

    result = dict(existing)
    if marketplaces or "extraKnownMarketplaces" in existing:
        result["extraKnownMarketplaces"] = marketplaces
    if enabled or "enabledPlugins" in existing:
        result["enabledPlugins"] = enabled
    marker: dict[str, Any] = {
        "version": _OVERLAY_VERSION,
        "marketplaces": managed_marketplaces,
        "enabledPlugins": managed_enabled,
    }
    if pair_id:
        marker["pairId"] = pair_id
    if pair_kind:
        marker["pairKind"] = pair_kind
    result[_OVERLAY_KEY] = marker

    changed = _write_overlay(output_path, result)

    return {
        "action": "composed",
        "paired": pair_id is not None,
        "settings_local": str(output_path),
        "harness_path": str(harness),
        "knowledge_path": str(knowledge),
        "changed": changed,
        "marketplaces": sorted(managed_marketplaces),
        "enabled_plugins": sorted(managed_enabled),
        "count": len(managed_enabled),
        "conflicts": {
            "marketplaces": sorted(set(conflicting_marketplaces)),
            "enabled_plugins": sorted(set(conflicting_enabled)),
        },
    }


def compose(
    harness_path: str | Path,
    knowledge_path: str | Path,
    *,
    pair_id: str | None = None,
    pair_kind: str | None = None,
    legacy_knowledge_path: str | Path | None = None,
) -> dict[str, Any]:
    """Compose settings under a cross-process overlay transaction lock."""
    harness = Path(harness_path).resolve()
    knowledge = Path(knowledge_path).resolve()
    _ensure_distinct_checkouts(harness, knowledge)
    output_path = harness / ".github" / "copilot" / "settings.local.json"
    with _overlay_transaction(output_path):
        return _compose_locked(
            harness,
            knowledge,
            pair_id=pair_id,
            pair_kind=pair_kind,
            legacy_knowledge_path=legacy_knowledge_path,
        )


def _resolve_pair_state(
    *,
    cwd: str | Path | None = None,
    config: cfg.Config | None = None,
) -> tuple[state_root.StatePair, cfg.Config | None, str | None]:
    """Resolve pair metadata plus the harness config needed for validation."""
    current_dir = str(Path(cwd or os.getcwd()).resolve())
    resolution = state_root.resolve_pair(config, cwd=current_dir)
    loaded_config = config
    config_error: str | None = None
    if loaded_config is None and resolution.current and resolution.current.repo:
        harness_entry = next(
            (
                entry
                for entry in (resolution.current, resolution.sibling)
                if entry is not None and entry.role == "harness"
            ),
            None,
        )
        repo = harness_entry.repo if harness_entry else resolution.current.repo
        try:
            loaded_config = cfg.load_config(cfg.project_dir(repo) / "config.yaml")
        except (OSError, RuntimeError, ValueError) as exc:
            config_error = f"cannot load project config for '{repo}': {exc}"
        else:
            # Anchor pairs need the binding-aware config to resolve their
            # sibling; worktree pairs are harmlessly revalidated.
            resolution = state_root.resolve_pair(loaded_config, cwd=current_dir)
    return resolution, loaded_config, config_error


def _validate_pair(
    resolution: state_root.StatePair,
    loaded_config: cfg.Config | None,
    config_error: str | None,
) -> KnowledgePair:
    """Validate resolved roles, checkouts, and the live knowledge binding."""
    if config_error:
        raise KnowledgePluginError(config_error)
    if resolution.error or not resolution.paired or not resolution.sibling:
        raise KnowledgePluginError(resolution.error or "current worktree is not paired")
    entries = [resolution.current, resolution.sibling]
    by_role = {
        entry.role: (Path(entry.path), entry.repo)
        for entry in entries
        if entry is not None and entry.role
    }
    if "harness" not in by_role or "knowledge" not in by_role:
        raise KnowledgePluginError(
            "pair does not contain both harness and knowledge roles "
            f"(got {sorted(by_role)})"
        )
    harness_path, _ = by_role["harness"]
    knowledge_path, knowledge_repo = by_role["knowledge"]
    harness_path = harness_path.resolve()
    knowledge_path = knowledge_path.resolve()
    _ensure_distinct_checkouts(harness_path, knowledge_path)
    if not harness_path.is_dir() or not knowledge_path.is_dir():
        raise KnowledgePluginError(
            "paired checkout is stale or missing "
            f"(harness={harness_path}, knowledge={knowledge_path})"
        )
    bound_repo = (loaded_config.knowledge_repo or "").strip() if loaded_config else ""
    if not bound_repo:
        raise KnowledgePluginError(
            "cannot compose paired knowledge plugins without a non-empty live "
            "knowledge_repo binding"
        )
    if not knowledge_repo:
        raise KnowledgePluginError(
            "paired knowledge checkout has no repo identity; create or select "
            "a current pair"
        )
    if bound_repo != knowledge_repo:
        raise KnowledgePluginError(
            f"pair knowledge repo '{knowledge_repo}' no longer matches the "
            f"current binding '{bound_repo}'; create or select a current pair"
        )
    return KnowledgePair(
        harness_path=harness_path,
        knowledge_path=knowledge_path,
        knowledge_repo=knowledge_repo,
        pair_id=resolution.pair_id or "",
        pair_kind=resolution.pair_kind or "worktree",
    )


def resolve_pair(
    *,
    cwd: str | Path | None = None,
    config: cfg.Config | None = None,
) -> KnowledgePair:
    """Resolve and validate the current tracked harness/knowledge pair."""
    resolution, loaded_config, config_error = _resolve_pair_state(
        cwd=cwd, config=config
    )
    return _validate_pair(resolution, loaded_config, config_error)


def _is_tracked_harness(
    resolution: state_root.StatePair,
    loaded_config: cfg.Config | None,
) -> bool:
    """Prove that the current tracked checkout owns a paired overlay."""
    current = resolution.current
    if current is None or current.role == "knowledge":
        return False
    if current.role == "harness":
        return True
    if loaded_config is None:
        return False
    repo_config = getattr(loaded_config, "repos", {}).get(current.repo)
    if repo_config is None:
        try:
            repo_config = loaded_config.default_repo
        except KeyError:
            return False
    return bool(repo_config.stateless)


def compose_from_pair(
    *,
    cwd: str | Path | None = None,
    config: cfg.Config | None = None,
) -> dict[str, Any]:
    """Compose a valid pair or safely sanitize its stale managed overlay."""
    resolution, loaded_config, config_error = _resolve_pair_state(
        cwd=cwd, config=config
    )
    if config_error:
        # A config read failure does not prove the pair is invalid. Preserve
        # the last known-good overlay and fail the launch preflight closed.
        raise KnowledgePluginError(config_error)
    try:
        pair = _validate_pair(resolution, loaded_config, config_error)
    except KnowledgePairIntegrityError:
        raise
    except KnowledgePluginError as exc:
        pair_error = str(exc)
        if _is_tracked_harness(resolution, loaded_config):
            assert resolution.current is not None
            return _retire_invalid_pair_overlay(
                resolution.current.path, pair_error=pair_error
            )
        return {
            "action": "no-op",
            "paired": False,
            "retired": False,
            "changed": False,
            "pair_error": pair_error,
        }

    legacy_anchor: str | None = None
    if pair.pair_kind == "worktree":
        candidate = repos_mod.resolve_path(pair.knowledge_repo)
        if candidate and Path(candidate).is_dir():
            legacy_anchor = candidate
    summary = compose(
        pair.harness_path,
        pair.knowledge_path,
        pair_id=pair.pair_id,
        pair_kind=pair.pair_kind,
        legacy_knowledge_path=legacy_anchor,
    )
    summary["knowledge_repo"] = pair.knowledge_repo
    return summary

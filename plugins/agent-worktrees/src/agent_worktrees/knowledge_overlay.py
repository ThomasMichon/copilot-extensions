"""Merge explicit checkout settings while the caller owns the overlay lease."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

from plugin_resolve import RepoPluginSettings, split_source
from plugin_resolve.conventions import (
    LOCAL_MARKETPLACE_SOURCE_KINDS,
    SETTINGS_RELS,
)

from . import knowledge_plugin_committed as committed_settings

# A knowledge repo's own `<repo>-harness` plugin is a self-referential
# maintenance surface for *that* repo (same convention as other named-repo
# `-harness` adapters), not a generically graftable capability -- the same
# reasoning that keeps a `*-agent` plugin venue-scoped and never loaded
# centrally. Composing it into a different harness's session would introduce
# an extra sessionStart/agent surface authored for a different repository.
SELF_HARNESS_SUFFIX = "-harness"


def is_self_referential_harness_plugin(source: str, local_names: set[str]) -> bool:
    """True when `source` names a knowledge-local `*-harness` plugin."""
    plugin, marketplace = split_source(source)
    return marketplace in local_names and plugin.endswith(SELF_HARNESS_SUFFIX)


KEY = "_agentWorktreesToolPluginOverlay"

def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Refuse ambiguous duplicate keys at every JSON object depth."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


_OVERLAY_KEY = "_agentWorktreesKnowledgePluginOverlay"
_OVERLAY_VERSION = 1


class KnowledgePluginError(RuntimeError):
    """A safe, user-facing knowledge-plugin composition failure."""


class KnowledgePairIntegrityError(KnowledgePluginError):
    """A pair defect that must fail without sanitizing either checkout."""


def _load_json_object(path: Path) -> dict[str, Any]:
    """Read an existing overlay strictly to protect unmanaged operator data."""
    try:
        if not path.is_file():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_json_object)
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
    existing: dict[str, Any], path: Path, *, validate_tool: Callable[[dict, Path], object] | None = None,
) -> dict[str, Any] | None:
    """Return a validated ownership marker, or ``None`` when none exists."""
    if KEY in existing:
        if validate_tool is None:
            raise KnowledgePluginError(f"tool plugin receipt requires registered Git family evidence: {path}")
        validate_tool(existing, path)
    if _OVERLAY_KEY not in existing:
        return None
    marker = existing.get(_OVERLAY_KEY)
    if not isinstance(marker, dict):
        raise KnowledgePluginError(
            f"cannot safely manage {path}: '{_OVERLAY_KEY}' is not a JSON object"
        )
    validate_personal_version(marker, path, existing)
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


def same_json_value(left: Any, right: Any) -> bool:
    """Compare exact JSON values without equating booleans and numbers."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same_json_value(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same_json_value(a, b) for a, b in zip(left, right))
    return left == right


def _retire_previous(current: dict, previous: dict) -> None:
    """Drop a previously managed value only when it remains unmodified."""
    for key, prior_value in previous.items():
        if key in current and same_json_value(current[key], prior_value):
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
    normalized = unicodedata.normalize("NFC", os.path.abspath(str(path))).casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return (
        Path(tempfile.gettempdir())
        / "agent-worktrees"
        / "knowledge-plugin-overlays"
        / f"{digest}.json"
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
            same_json_value(existing[name], _localized_marketplace(definition, knowledge_path))
            or (
                legacy_knowledge_path is not None
                and name in legacy_definitions
                and same_json_value(existing[name], _localized_marketplace(
                    legacy_definitions[name], legacy_knowledge_path))
            )
        )
    }


def _compose_locked(
    harness_path: str | Path,
    knowledge_path: str | Path,
    *,
    validate_tool: Callable[[dict, Path], object] | None = None,
    pair_id: str | None = None,
    pair_kind: str | None = None,
    legacy_knowledge_path: str | Path | None = None,
) -> dict[str, Any]:
    """Compose knowledge plugin settings into the harness's local overlay.

    Local marketplaces are re-pointed at ``knowledge_path``.  Remote
    marketplaces and enabled plugins are carried when the committed harness
    base does not already provide them, or when the base's own value is an
    explicit opt-in ``false`` (the operator's knowledge-bound ``true`` wins --
    that is exactly what an opt-in flag is for).  Existing unmanaged local settings are
    preserved.  A private ownership marker records exact managed values, so a
    later re-point/removal can retire stale entries without deleting operator
    edits.

    The knowledge repo's own local self-referential ``*-harness`` plugin is
    never composed in -- see
    :func:`knowledge_composition_is_self_referential_harness_plugin`.
    It is reported separately under ``excluded_enabled_plugins``.
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
    previous = _managed_marker(existing, output_path, validate_tool=validate_tool)
    markerless_legacy = previous is None or same_json_value(previous, {"version": 2, "marketplaces": {}, "enabledPlugins": {}})
    previous_marketplaces = dict(previous["marketplaces"]) if previous else {}
    previous_enabled = dict(previous["enabledPlugins"]) if previous else {}
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
    harness_owned_marketplaces: set[str] = set()
    harness_committed_marketplaces = (
        committed_settings.read_harness_committed_marketplaces(harness)
    )
    for name, definition in sorted(desired_marketplaces.items()):
        if name in local_names and name in marketplaces:
            if not markerless_legacy or name not in proven_legacy_names:
                conflicting_marketplaces.append(name)
                continue
        base_definition = harness_settings.marketplaces.get(name)
        if base_definition is None:
            candidates[name] = definition
        elif not same_json_value(base_definition, definition):
            committed_definition = harness_committed_marketplaces.get(name)
            if committed_definition is not None and same_json_value(committed_definition, base_definition):
                # The collision is purely with the harness's own *committed*
                # marketplace declaration -- e.g. the harness's own local
                # in-repo marketplace, incidentally redeclared elsewhere
                # (differently) by the knowledge repo. No operator/native
                # override participated, so resolution is well-defined (the
                # harness's committed value always wins): don't cascade this
                # into blocking `name@...` enabledPlugins overrides below,
                # unlike a genuine ambiguous collision.
                harness_owned_marketplaces.add(name)
            else:
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
        if name in marketplaces and not same_json_value(marketplaces[name], definition):
            conflicting_marketplaces.append(name)
            continue
        if name not in marketplaces:
            marketplaces[name] = definition
            managed_marketplaces[name] = definition

    managed_enabled: dict[str, bool] = {}
    conflicting_enabled: list[str] = []
    excluded_enabled: list[str] = []
    conflicting_names = set(conflicting_marketplaces)
    harness_committed_enabled = committed_settings.read_harness_committed_enabled(
        harness
    )
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
        if is_self_referential_harness_plugin(source, local_names):
            # Exact personal receipts retired above; preserve other contributors.
            excluded_enabled.append(source)
            continue
        _, marketplace = split_source(source)
        if marketplace in conflicting_names:
            conflicting_enabled.append(source)
            continue
        if source in enabled and not same_json_value(enabled[source], on):
            conflicting_enabled.append(source)
            continue
        if marketplace in local_names and not markerless_legacy and source in enabled:
            conflicting_enabled.append(source)
            continue
        if source in harness_settings.enabled:
            if harness_settings.enabled[source] is True:
                continue
            if (
                harness_committed_enabled.get(source) is False
                and source not in enabled
            ):
                # An explicit harness-*committed* `false` marks an opt-in
                # plugin. A local/operator `false` still wins as a deliberate
                # override and remains a conflict instead of being silently
                # re-enabled by the knowledge repo.
                enabled[source] = True
                managed_enabled[source] = True
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
    protect_tool_receipts(existing, result, marker, conflicting_marketplaces, conflicting_enabled)
    preserve_guard(result)

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
        "harness_owned_marketplaces": sorted(harness_owned_marketplaces),
        "excluded_enabled_plugins": sorted(set(excluded_enabled)),
    }


def preserve_guard(result: dict) -> None:
    """Older personal owners must refuse mixed contributor documents."""

    if KEY in result:
        marker = result.setdefault(_OVERLAY_KEY, {"marketplaces": {}, "enabledPlugins": {}})
        marker["version"] = 2
    elif same_json_value(result.get(_OVERLAY_KEY), {"version": 2, "marketplaces": {}, "enabledPlugins": {}}):
        result.pop(_OVERLAY_KEY)
    elif _OVERLAY_KEY in result:
        result[_OVERLAY_KEY]["version"] = 1


def protect_tool_receipts(existing: dict, result: dict, personal: dict,
                          marketplace_conflicts: list, enabled_conflicts: list) -> None:
    """A missing live tool value does not transfer its receipt to personal input."""
    tool = existing.get(KEY)
    if tool is None:
        return
    rejected_marketplaces = set()
    for setting, receipt, conflicts in (
        ("extraKnownMarketplaces", "marketplaces", marketplace_conflicts),
        ("enabledPlugins", "enabledPlugins", enabled_conflicts),
    ):
        authored = personal[receipt]
        overlaps = set(authored) & set(tool[setting])
        if setting == "extraKnownMarketplaces":
            rejected_marketplaces.update(overlaps)
        for name in overlaps:
            authored.pop(name)
            if name in existing.get(setting, {}):
                result[setting][name] = existing[setting][name]
            else:
                result.get(setting, {}).pop(name, None)
            conflicts.append(name)
        if not result.get(setting) and setting not in existing:
            result.pop(setting, None)

    prior_enabled = existing.get(_OVERLAY_KEY, {}).get("enabledPlugins", {})
    existing_enabled = existing.get("enabledPlugins", {})
    for source in tuple(personal["enabledPlugins"]):
        if split_source(source)[1] in rejected_marketplaces:
            personal["enabledPlugins"].pop(source)
            if source in existing_enabled and (
                source not in prior_enabled
                or not same_json_value(existing_enabled[source], prior_enabled[source])
            ):
                result.setdefault("enabledPlugins", {})[source] = existing_enabled[source]
            else:
                result.get("enabledPlugins", {}).pop(source, None)
            enabled_conflicts.append(source)
    if not result.get("enabledPlugins") and "enabledPlugins" not in existing:
        result.pop("enabledPlugins", None)


def validate_personal_version(marker: dict, path: Path, existing: dict) -> None:

    if marker.get("version") not in (1, 2) or (marker.get("version") == 2 and KEY not in existing):
        raise KnowledgePluginError(
            f"cannot safely manage {path}: unsupported '{_OVERLAY_KEY}' version {marker.get('version')!r}"
        )
    tool = existing.get(KEY)
    if isinstance(tool, dict):
        for key, personal_key in (("extraKnownMarketplaces", "marketplaces"), ("enabledPlugins", "enabledPlugins")):
            owned = marker.get(personal_key)
            if isinstance(owned, dict) and set(owned) & set(tool[key]):
                raise KnowledgePluginError(f"ambiguous tool/personal receipt ownership in {path}")

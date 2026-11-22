"""Own the settings lease/writer and compose paired personal plugin settings.

Project tools use a distinct contributor without depending on knowledge binding.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


from . import config as cfg
from . import knowledge_composition_policy as policy
from . import knowledge_overlay as overlay
from .knowledge_overlay import (
    KnowledgePluginError as KnowledgePluginError,
    KnowledgePairIntegrityError as KnowledgePairIntegrityError,
    _OVERLAY_KEY as _OVERLAY_KEY,
    _OVERLAY_VERSION as _OVERLAY_VERSION,
    _dict_setting as _dict_setting,
    _ensure_distinct_checkouts as _ensure_distinct_checkouts,
    _is_local_marketplace as _is_local_marketplace,
    _load_json_object as _load_json_object,
    _localized_marketplace as _localized_marketplace,
    _normalized_checkout_path as _normalized_checkout_path,
    _overlay_lock_target as _overlay_lock_target,
    _read_repo_settings_strict as _read_repo_settings_strict,
    _retire_previous as _retire_previous,
    _validate_marketplace_source as _validate_marketplace_source,
    _write_overlay as _write_overlay,
    same_json_value as same_json_value,
)
from . import repos as repos_mod
from . import state_root, tracking

@dataclass(frozen=True)
class KnowledgePair:
    """The role-resolved paths and repo identity of a tracked pair."""

    harness_path: Path
    knowledge_path: Path
    knowledge_repo: str
    pair_id: str
    pair_kind: str


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
        if key in marketplaces and same_json_value(marketplaces[key], value)
    )
    retired_enabled = sorted(
        key for key, value in previous_enabled.items() if key in enabled and same_json_value(enabled[key], value)
    )
    preserved_marketplaces = sorted(
        key
        for key, value in previous_marketplaces.items()
        if key in marketplaces and not same_json_value(marketplaces[key], value)
    )
    preserved_enabled = sorted(
        key
        for key, value in previous_enabled.items()
        if key in enabled and not same_json_value(enabled[key], value)
    )
    _retire_previous(marketplaces, previous_marketplaces)
    _retire_previous(enabled, previous_enabled)

    result = dict(existing)
    result.pop(_OVERLAY_KEY, None)
    from .copilot_plugin_profile import preserve_guard
    preserve_guard(result)
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
    pair_pending: bool = False,
) -> dict[str, Any]:
    harness = Path(harness_path).resolve()
    output_path = harness / ".github" / "copilot" / "settings.local.json"
    with _overlay_transaction(output_path):
        if pair_pending:
            from .local_cache_refresh import mark_pair_pending

            mark_pair_pending(harness)
        return _retire_invalid_pair_overlay_locked(
            harness,
            pair_error=pair_error,
        )


def _managed_marker(existing: dict[str, Any], path: Path, *, tool_family=None) -> dict[str, Any] | None:
    from .copilot_plugin_profile import validate_marker

    return overlay._managed_marker(existing, path, validate_tool=lambda data, target: validate_marker(data, target, family=tool_family))


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
    from . import local_cache_refresh as refresh

    with _overlay_transaction(output_path):
        if pair_id:
            refresh.mark_pair_pending(harness)
        if pair_id and pair_kind == "worktree":
            refresh.prepare_companion_instructions(knowledge)
        from .copilot_plugin_profile import validate_marker
        summary = overlay._compose_locked(
            harness,
            knowledge,
            pair_id=pair_id,
            pair_kind=pair_kind,
            legacy_knowledge_path=legacy_knowledge_path,
            validate_tool=validate_marker,
        )
        refresh.complete_pair_composition(harness, summary)
        return summary


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
    repo_config = policy.repo_config_for(loaded_config, current.repo)
    if repo_config is None:
        return False
    return bool(repo_config.stateless)


def _retire_disabled_overlay(harness_path: str | Path, *, knowledge_path: Path | None = None) -> dict[str, Any]:
    """Retire a marker-owned overlay when composition is config-disabled
    (pair stays valid -- only composition is off; see `_retire_invalid_pair_overlay`)."""
    harness = Path(harness_path).resolve()
    output_path = harness / ".github" / "copilot" / "settings.local.json"
    from . import local_cache_refresh as refresh

    def completed(changed: bool) -> dict[str, Any]:
        summary = {
            "action": "disabled", "paired": True, "changed": changed,
            "settings_local": str(output_path), "harness_path": str(harness),
        }
        refresh.complete_pair_composition(harness, summary)
        return summary

    with _overlay_transaction(output_path):
        refresh.mark_pair_pending(harness)
        if knowledge_path is not None:
            refresh.prepare_companion_instructions(knowledge_path)
        existing = _load_json_object(output_path)
        marker = _managed_marker(existing, output_path)
        if marker is None or not marker.get("pairId"):
            return completed(False)
        marketplaces = _dict_setting(existing, "extraKnownMarketplaces", output_path)
        enabled = _dict_setting(existing, "enabledPlugins", output_path)
        previous_marketplaces = dict(marker["marketplaces"])
        previous_enabled = dict(marker["enabledPlugins"])
        _retire_previous(marketplaces, previous_marketplaces)
        _retire_previous(enabled, previous_enabled)

        result = dict(existing)
        result.pop(_OVERLAY_KEY, None)
        from .copilot_plugin_profile import preserve_guard
        preserve_guard(result)
        if marketplaces:
            result["extraKnownMarketplaces"] = marketplaces
        else:
            result.pop("extraKnownMarketplaces", None)
        if enabled:
            result["enabledPlugins"] = enabled
        else:
            result.pop("enabledPlugins", None)
        changed = _write_overlay(output_path, result)
        return completed(changed)


def compose_from_pair(
    *,
    cwd: str | Path | None = None,
    config: cfg.Config | None = None,
) -> dict[str, Any]:
    """Compose a valid pair or safely sanitize its stale managed overlay."""
    resolution, loaded_config, config_error = _resolve_pair_state(
        cwd=cwd, config=config
    )
    from .local_cache_refresh import mark_failed_pair_composition

    if config_error:
        # A config read failure does not prove the pair is invalid. Preserve
        # the last known-good overlay and fail the launch preflight closed.
        mark_failed_pair_composition(resolution)
        raise KnowledgePluginError(config_error)
    try:
        pair = _validate_pair(resolution, loaded_config, config_error)
    except KnowledgePairIntegrityError:
        mark_failed_pair_composition(resolution)
        raise
    except KnowledgePluginError as exc:
        pair_error = str(exc)
        if _is_tracked_harness(resolution, loaded_config):
            assert resolution.current is not None
            return _retire_invalid_pair_overlay(
                resolution.current.path, pair_error=pair_error,
                pair_pending=resolution.paired,
            )
        mark_failed_pair_composition(resolution)
        return {
            "action": "no-op",
            "paired": False,
            "retired": False,
            "changed": False,
            "pair_error": pair_error,
        }

    assert resolution.current is not None
    repo_config = policy.repo_config_for(loaded_config, resolution.current.repo)
    if repo_config is not None and not repo_config.compose_knowledge_plugins:
        # Opted out via `RepoConfig.compose_knowledge_plugins`: state-root
        # pairing stays valid; only the plugin overlay is retired/withheld.
        summary = _retire_disabled_overlay(
            pair.harness_path,
            knowledge_path=pair.knowledge_path if pair.pair_kind == "worktree" else None,
        )
        summary["knowledge_repo"] = pair.knowledge_repo
        return summary

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

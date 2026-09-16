"""Public compatibility wrapper for picker pivot support."""

from __future__ import annotations

from dropin_registry import Finding
from plugin_activation import resolve_active_plugins

from . import pivot_manifest as _manifest
from . import pivot_registry_scan as _scan
from .pivot_actions import (
    ConfigSection,
    ManifestError,
    WorktreeAction,
    entry_matches,
    format_form_template,
    format_template,
    parse_config_sections,
    parse_worktree_actions,
    worktree_action_matches,
)
from .pivot_manifest import (
    Column,
    PivotAction,
    PivotContribution,
    PivotRegistryReport,
    RegisteredPivot,
    discover_config_sections,
    discover_worktree_actions,
    installed_plugins_dir,
    parse_list_payload,
    parse_manifest,
    pivots_dir,
    resolve_path,
)

_resolve_state_root_path = _manifest._resolve_state_root_path


def _sync_compat_seams() -> None:
    _manifest.resolve_active_plugins = resolve_active_plugins
    _manifest._resolve_state_root_path = _resolve_state_root_path
    _scan._resolve_state_root_path = _resolve_state_root_path
    _scan._LAST_KNOWN = _manifest._LAST_KNOWN


def _resolve_activation():
    _sync_compat_seams()
    return _manifest._resolve_activation()


def scan_pivot_registry(*args, **kwargs):
    _sync_compat_seams()
    return _scan.scan_pivot_registry(*args, **kwargs)


def warn_pivot_findings(*args, **kwargs):
    _sync_compat_seams()
    return _scan.warn_pivot_findings(*args, **kwargs)


def prunable_findings(*args, **kwargs):
    _sync_compat_seams()
    return _scan.prunable_findings(*args, **kwargs)


def prune_stale_entries(*args, **kwargs):
    _sync_compat_seams()
    return _scan.prune_stale_entries(*args, **kwargs)


def ensure_pivots(*args, **kwargs):
    _sync_compat_seams()
    return _scan.ensure_pivots(*args, **kwargs)


def discover_pivots(*args, **kwargs):
    _sync_compat_seams()
    return _scan.discover_pivots(*args, **kwargs)


def order_pivots(*args, **kwargs):
    _sync_compat_seams()
    return _scan.order_pivots(*args, **kwargs)


__all__ = [
    "Column",
    "ConfigSection",
    "Finding",
    "ManifestError",
    "PivotAction",
    "PivotContribution",
    "PivotRegistryReport",
    "RegisteredPivot",
    "WorktreeAction",
    "_resolve_activation",
    "_resolve_state_root_path",
    "discover_config_sections",
    "discover_pivots",
    "discover_worktree_actions",
    "ensure_pivots",
    "entry_matches",
    "format_form_template",
    "format_template",
    "installed_plugins_dir",
    "order_pivots",
    "parse_config_sections",
    "parse_list_payload",
    "parse_manifest",
    "parse_worktree_actions",
    "pivots_dir",
    "prunable_findings",
    "prune_stale_entries",
    "resolve_active_plugins",
    "resolve_path",
    "scan_pivot_registry",
    "warn_pivot_findings",
    "worktree_action_matches",
]

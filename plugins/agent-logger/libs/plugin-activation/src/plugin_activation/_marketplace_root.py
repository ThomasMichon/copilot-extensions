"""Resolve a plugin source to its local-marketplace root."""

from __future__ import annotations

import stat
from pathlib import Path

from dropin_registry import ScanAuthority
from plugin_resolve import (
    MARKETPLACE_MANIFEST_RELS,
    MarketplaceSourceKind,
    load_marketplace,
    local_marketplace_path,
    marketplace_source_kind,
    plugin_dir,
    split_source,
)

from ._config_io import _SettingsLoad, _load_json_object
from ._findings import _finding
from ._fs_candidates import _is_reparse, _manifest_name
from ._installed_root import _Candidate
from ._memo import MarketplaceMemo, memo_get


def _marketplace_manifest(
    root: Path,
) -> tuple[Path | None, dict | None, str | None, bool]:
    for rel in MARKETPLACE_MANIFEST_RELS:
        path = root.joinpath(*rel)
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            return path, None, str(exc), True
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or _is_reparse(info)
        ):
            return (
                path,
                None,
                "marketplace manifest must be a regular non-reparse file",
                False,
            )
        authority, data, findings = _load_json_object(path)
        if authority is ScanAuthority.INDETERMINATE:
            indeterminate = findings[0].reason == "entry-indeterminate"
            return (
                path,
                None,
                findings[0].detail or findings[0].reason,
                indeterminate,
            )
        return path, data, None, False
    return None, None, "marketplace manifest is missing", False


def _local_root(
    source: str,
    loaded_settings: _SettingsLoad,
    *,
    base: Path,
    _memo: MarketplaceMemo | None = None,
) -> _Candidate:
    name, marketplace = split_source(source)
    definition = loaded_settings.settings.marketplaces.get(marketplace)
    if definition is None:
        return _Candidate()
    origin = loaded_settings.marketplace_origins.get(marketplace, base)
    raw_source = definition.get("source") if isinstance(definition, dict) else None
    if not isinstance(raw_source, dict):
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "invalid-entry",
                    status="indeterminate",
                    target=marketplace,
                    owner=source,
                    detail="marketplace source must be an object",
                )
            ],
            indeterminate=True,
        )
    kind = raw_source.get("source")
    if not isinstance(kind, str):
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "invalid-entry",
                    status="indeterminate",
                    target=marketplace,
                    owner=source,
                    detail="marketplace source kind must be a string",
                )
            ],
            indeterminate=True,
        )
    source_kind = marketplace_source_kind(
        marketplace,
        loaded_settings.settings,
    )
    if source_kind is MarketplaceSourceKind.INVALID:
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "invalid-entry",
                    status="indeterminate",
                    target=marketplace,
                    owner=source,
                    detail=f"unsupported marketplace source kind: {kind!r}",
                )
            ],
            indeterminate=True,
        )
    if source_kind is MarketplaceSourceKind.REMOTE:
        return _Candidate()
    raw_path = raw_source.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "invalid-entry",
                    status="indeterminate",
                    target=marketplace,
                    owner=source,
                    detail="local marketplace requires a non-empty string path",
                )
            ],
            indeterminate=True,
        )

    marketplace_path = local_marketplace_path(
        marketplace,
        loaded_settings.settings,
        repo_dir=base,
    )
    if marketplace_path is None:
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "invalid-entry",
                    status="indeterminate",
                    target=marketplace,
                    owner=source,
                    detail="local marketplace requires a non-empty path",
                )
            ],
            indeterminate=True,
        )
    try:
        canonical_marketplace = marketplace_path.resolve(strict=True)
    except FileNotFoundError as exc:
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "missing-target",
                    target=str(marketplace_path),
                    owner=source,
                    detail=str(exc),
                )
            ]
        )
    except OSError as exc:
        return _Candidate(
            findings=[
                _finding(
                    origin,
                    "entry-indeterminate",
                    status="indeterminate",
                    target=str(marketplace_path),
                    owner=source,
                    detail=str(exc),
                )
            ],
            indeterminate=True,
        )

    manifest_path, manifest, manifest_error, manifest_indeterminate = memo_get(
        _memo.manifests if _memo else None, canonical_marketplace,
        lambda: _marketplace_manifest(canonical_marketplace),
    )
    if manifest_error is not None or manifest is None:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "entry-indeterminate"
                    if manifest_indeterminate
                    else "identity-mismatch",
                    status="indeterminate" if manifest_indeterminate else "inactive",
                    target=str(canonical_marketplace),
                    owner=source,
                    detail=manifest_error,
                )
            ],
            indeterminate=manifest_indeterminate,
        )
    if manifest.get("name") != marketplace:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "identity-mismatch",
                    target=str(canonical_marketplace),
                    owner=source,
                    detail="marketplace manifest name differs from source",
                )
            ]
        )
    loaded_marketplace = memo_get(
        _memo.marketplaces if _memo else None, canonical_marketplace,
        lambda: load_marketplace(canonical_marketplace),
    )
    if loaded_marketplace is None or loaded_marketplace.name != marketplace:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "identity-mismatch",
                    target=source,
                    owner=source,
                    detail="marketplace could not be loaded with the expected identity",
                )
            ]
        )
    if name in loaded_marketplace.duplicates:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "root-ambiguous",
                    target=source,
                    owner=source,
                    detail="marketplace declares the plugin more than once",
                )
            ]
        )
    entry = loaded_marketplace.plugins.get(name)
    if entry is None or not isinstance(entry.source, str):
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "missing-target",
                    target=source,
                    owner=source,
                    detail="marketplace has no relative source for this plugin",
                )
            ]
        )
    plugin_source = Path(entry.source.strip())
    plugin_root = Path(loaded_marketplace.plugin_root)
    if (
        not entry.source.strip()
        or plugin_source.is_absolute()
        or plugin_root.is_absolute()
    ):
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "identity-mismatch",
                    target=entry.source,
                    owner=source,
                    detail="local plugin source and pluginRoot must be relative",
                )
            ]
        )
    candidate = canonical_marketplace / plugin_root / plugin_source
    try:
        canonical = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "missing-target",
                    target=str(candidate),
                    owner=source,
                    detail=str(exc),
                )
            ]
        )
    except OSError as exc:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "entry-indeterminate",
                    status="indeterminate",
                    target=str(candidate),
                    owner=source,
                    detail=str(exc),
                )
            ],
            indeterminate=True,
        )
    try:
        canonical.relative_to(canonical_marketplace)
    except ValueError:
        return _Candidate(
            findings=[
                _finding(
                    manifest_path or canonical_marketplace,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=source,
                    detail="plugin source escapes its marketplace root",
                )
            ]
        )
    manifest_name, plugin_error, plugin_indeterminate = _manifest_name(canonical)
    if plugin_error is not None or manifest_name != name:
        return _Candidate(
            findings=[
                _finding(
                    canonical,
                    "entry-indeterminate"
                    if plugin_indeterminate
                    else "identity-mismatch",
                    status="indeterminate" if plugin_indeterminate else "inactive",
                    target=str(canonical),
                    owner=source,
                    detail=plugin_error
                    or "plugin manifest name differs from marketplace entry",
                )
            ],
            indeterminate=plugin_indeterminate,
        )
    if plugin_dir(loaded_marketplace, name) != canonical:
        return _Candidate(
            findings=[
                _finding(
                    canonical,
                    "identity-mismatch",
                    target=str(canonical),
                    owner=source,
                    detail="shared marketplace resolver rejected the plugin root",
                )
            ]
        )
    return _Candidate(root=canonical)

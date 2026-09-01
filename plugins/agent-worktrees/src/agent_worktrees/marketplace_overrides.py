"""Prefer trusted registered marketplace checkouts in repo-local settings.

Portable repositories keep Git-backed marketplace declarations in committed
settings. On a machine that has a same-named repository in the agent-worktrees
registry, this module writes a source-only ``directory`` override into
``.github/copilot/settings.local.json``. The committed source remains the
fallback everywhere else.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

from plugin_resolve import marketplace_manifest_path
from plugin_resolve.conventions import SETTINGS_RELS

from . import git_ops
from . import repos as repos_mod
from .knowledge_plugins import (
    KnowledgePluginError,
    _dict_setting,
    _load_json_object,
    _overlay_transaction,
    _retire_previous,
    _write_overlay,
)

MarketplaceOverrideError = KnowledgePluginError

_OVERLAY_KEY = "_agentWorktreesMarketplaceOverrides"
_OVERLAY_VERSION = 1
_REMOTE_SOURCE_KINDS = frozenset({"git", "github"})
_SETTINGS_REL = Path(".github") / "copilot" / "settings.local.json"


def _validate_marker(existing: dict[str, Any], path: Path) -> dict[str, dict]:
    if _OVERLAY_KEY not in existing:
        return {}
    marker = existing.get(_OVERLAY_KEY)
    if not isinstance(marker, dict):
        raise MarketplaceOverrideError(
            f"cannot safely manage {path}: '{_OVERLAY_KEY}' is not a JSON object"
        )
    if marker.get("version") != _OVERLAY_VERSION:
        raise MarketplaceOverrideError(
            f"cannot safely manage {path}: unsupported '{_OVERLAY_KEY}' version "
            f"{marker.get('version')!r}"
        )
    marketplaces = marker.get("marketplaces")
    if not isinstance(marketplaces, dict):
        raise MarketplaceOverrideError(
            f"cannot safely manage {path}: "
            f"'{_OVERLAY_KEY}.marketplaces' is not a JSON object"
        )
    for name, definition in marketplaces.items():
        if not isinstance(name, str) or not isinstance(definition, dict):
            raise MarketplaceOverrideError(
                f"cannot safely manage {path}: "
                f"'{_OVERLAY_KEY}.marketplaces' contains an invalid entry"
            )
    return dict(marketplaces)


def _read_marketplaces(
    repo: Path,
    *,
    native_local: dict[str, Any],
    ignored_native_marketplaces: set[str],
) -> dict[str, dict]:
    """Merge user-global then repo marketplace settings with strict validation."""
    paths = [
        Path.home() / ".copilot" / "settings.json",
        Path.home() / ".copilot" / "settings.local.json",
        *(repo.joinpath(*rel) for rel in SETTINGS_RELS),
    ]
    native_local_path = repo / ".github" / "copilot" / "settings.local.json"
    marketplaces: dict[str, dict] = {}
    for path in paths:
        if path == native_local_path:
            data = native_local
        else:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                raise MarketplaceOverrideError(
                    f"cannot read marketplace settings {path}: {exc}"
                ) from exc
            if not isinstance(data, dict):
                raise MarketplaceOverrideError(
                    f"cannot read marketplace settings {path}: "
                    "root value is not an object"
                )

        raw = data.get("extraKnownMarketplaces")
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise MarketplaceOverrideError(
                f"cannot read marketplace settings {path}: "
                "'extraKnownMarketplaces' is not a JSON object"
            )
        for name, definition in raw.items():
            if path == native_local_path and name in ignored_native_marketplaces:
                continue
            if not isinstance(name, str) or not isinstance(definition, dict):
                raise MarketplaceOverrideError(
                    f"cannot read marketplace settings {path}: "
                    f"marketplace {name!r} is not a JSON object"
                )
            source = definition.get("source")
            if not isinstance(source, dict):
                raise MarketplaceOverrideError(
                    f"cannot read marketplace settings {path}: marketplace "
                    f"{name!r} 'source' is not a JSON object"
                )
            source_kind = source.get("source")
            if not isinstance(source_kind, str) or not source_kind.strip():
                raise MarketplaceOverrideError(
                    f"cannot read marketplace settings {path}: marketplace "
                    f"{name!r} 'source.source' is not a non-empty string"
                )
            marketplaces[name] = definition
    return marketplaces


def _registered_marketplace_path(
    name: str,
    *,
    refresh_repositories: bool,
    fast_forward_repositories: bool,
    refresh_results: dict[str, dict[str, Any]],
) -> tuple[Path | None, str | None]:
    """Resolve one exact registry entry to a contained, exact-name ``.ai`` root."""
    entry = repos_mod.find_repo(name)
    if entry is None:
        return None, "unregistered"
    raw_checkout = entry.local_path()
    if not raw_checkout:
        return None, "no-local-path"
    try:
        checkout = Path(raw_checkout).resolve(strict=True)
    except OSError:
        return None, "checkout-missing"
    if not checkout.is_dir():
        return None, "checkout-missing"

    checkout_key = str(checkout)
    if refresh_repositories and checkout_key not in refresh_results:
        remote = git_ops.resolve_remote_name(
            getattr(entry, "remote", ""),
            cwd=checkout,
        )
        default_branch = getattr(entry, "default_branch", "") or "main"
        try:
            prepared = git_ops.prepare_worktree_base(
                checkout,
                remote=remote,
                default_branch=default_branch,
                fast_forward_anchor=fast_forward_repositories,
            )
        except Exception as exc:
            refresh_results[checkout_key] = {
                "fetched": False,
                "fetch_failed": True,
                "anchor": "refresh-error",
                "start_point": None,
                "error": str(exc)[:160],
            }
        else:
            refresh_results[checkout_key] = {
                "fetched": prepared.fetched,
                "fetch_failed": prepared.fetch_error is not None,
                "anchor": prepared.anchor.reason,
                "start_point": prepared.start_point,
            }

    try:
        marketplace = (checkout / ".ai").resolve(strict=True)
    except OSError:
        return None, "marketplace-missing"
    if not marketplace.is_dir():
        return None, "marketplace-missing"
    if marketplace == checkout or checkout not in marketplace.parents:
        return None, "marketplace-path-escape"

    manifest = marketplace_manifest_path(marketplace)
    if manifest is None:
        return None, "manifest-missing"
    try:
        resolved_manifest = manifest.resolve(strict=True)
    except OSError:
        return None, "manifest-missing"
    if marketplace not in resolved_manifest.parents:
        return None, "manifest-path-escape"
    try:
        data = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "manifest-invalid"
    if not isinstance(data, dict) or data.get("name") != name:
        return None, "name-mismatch"
    return marketplace, None


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _assert_safe_output_path(repo: Path, output_path: Path) -> None:
    """Reject repository-controlled links before any overlay read or write."""
    current = repo
    for part in _SETTINGS_REL.parts[:-1]:
        current = current / part
        if current.exists() or current.is_symlink():
            if _is_reparse_point(current):
                raise MarketplaceOverrideError(
                    f"cannot safely manage {output_path}: {current} is a link "
                    "or reparse point"
                )
            if not current.is_dir():
                raise MarketplaceOverrideError(
                    f"cannot safely manage {output_path}: {current} is not a directory"
                )
    if current.exists():
        resolved_parent = current.resolve()
        if resolved_parent != repo and repo not in resolved_parent.parents:
            raise MarketplaceOverrideError(
                f"cannot safely manage {output_path}: parent escapes repository"
            )

    if output_path.exists() or output_path.is_symlink():
        if _is_reparse_point(output_path):
            raise MarketplaceOverrideError(
                f"cannot safely manage {output_path}: output is a link or "
                "reparse point"
            )
        try:
            mode = os.lstat(output_path).st_mode
        except OSError as exc:
            raise MarketplaceOverrideError(
                f"cannot safely inspect {output_path}: {exc}"
            ) from exc
        if not stat.S_ISREG(mode):
            raise MarketplaceOverrideError(
                f"cannot safely manage {output_path}: output is not a regular file"
            )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", str(repo), *args]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise MarketplaceOverrideError(
            f"cannot inspect Git ownership for {repo}: {exc}"
        ) from exc


def _ensure_output_is_local(repo: Path, *, ensure_ignored: bool) -> None:
    """Reject tracked output and optionally add a shared Git exclude rule."""
    rel = _SETTINGS_REL.as_posix()
    tracked = _git(
        repo,
        "ls-files",
        "--error-unmatch",
        "--",
        f":(icase,literal){rel}",
    )
    if tracked.returncode == 0:
        raise MarketplaceOverrideError(
            f"cannot manage tracked repository file: {repo / _SETTINGS_REL}"
        )
    if tracked.returncode != 1:
        raise MarketplaceOverrideError(
            f"cannot determine whether local settings are tracked: "
            f"{tracked.stderr.strip() or tracked.stdout.strip() or 'git failed'}"
        )

    ignored = _git(repo, "check-ignore", "--quiet", "--", rel)
    if ignored.returncode == 0:
        return
    if ignored.returncode != 1:
        raise MarketplaceOverrideError(
            f"cannot determine whether local settings are ignored: "
            f"{ignored.stderr.strip() or ignored.stdout.strip() or 'git failed'}"
        )
    if not ensure_ignored:
        raise MarketplaceOverrideError(
            f"cannot manage unignored local settings: {repo / _SETTINGS_REL}"
        )

    git_dir_result = _git(repo, "rev-parse", "--git-path", "info/exclude")
    if git_dir_result.returncode != 0 or not git_dir_result.stdout.strip():
        raise MarketplaceOverrideError(
            f"cannot resolve Git exclude file for repository: {repo}"
        )
    exclude = Path(git_dir_result.stdout.strip())
    if not exclude.is_absolute():
        exclude = repo / exclude
    if exclude.exists() and _is_reparse_point(exclude):
        raise MarketplaceOverrideError(
            f"cannot safely update Git exclude file: {exclude}"
        )
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        before = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        rule = f"/{rel}"
        if rule not in {line.strip() for line in before.splitlines()}:
            separator = "" if not before or before.endswith(("\n", "\r")) else "\n"
            exclude.write_text(
                f"{before}{separator}{rule}\n",
                encoding="utf-8",
            )
    except OSError as exc:
        raise MarketplaceOverrideError(
            f"cannot update Git exclude file {exclude}: {exc}"
        ) from exc

    ignored = _git(repo, "check-ignore", "--quiet", "--", rel)
    if ignored.returncode == 1:
        raise MarketplaceOverrideError(
            f"Git exclude rule did not ignore local settings: {repo / _SETTINGS_REL}"
        )
    if ignored.returncode != 0:
        raise MarketplaceOverrideError(
            f"cannot verify Git exclude rule for local settings: "
            f"{ignored.stderr.strip() or ignored.stdout.strip() or 'git failed'}"
        )


def reconcile(
    repo_path: str | Path,
    *,
    ensure_ignored: bool = False,
    refresh_repositories: bool = False,
    fast_forward_repositories: bool = False,
) -> dict[str, Any]:
    """Reconcile source-only local marketplace overrides for one checkout."""
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise MarketplaceOverrideError(f"repository checkout does not exist: {repo}")
    output_path = repo / _SETTINGS_REL
    _assert_safe_output_path(repo, output_path)
    _ensure_output_is_local(repo, ensure_ignored=ensure_ignored)

    with _overlay_transaction(output_path):
        _assert_safe_output_path(repo, output_path)
        existing = _load_json_object(output_path)
        marketplaces = _dict_setting(
            existing, "extraKnownMarketplaces", output_path
        )
        previous = _validate_marker(existing, output_path)
        _retire_previous(marketplaces, previous)

        native_local = dict(existing)
        if marketplaces:
            native_local["extraKnownMarketplaces"] = marketplaces
        else:
            native_local.pop("extraKnownMarketplaces", None)
        native_local.pop(_OVERLAY_KEY, None)
        declared = _read_marketplaces(
            repo,
            native_local=native_local,
            ignored_native_marketplaces=set(previous),
        )

        desired: dict[str, dict] = {}
        skipped: dict[str, str] = {}
        refresh_results: dict[str, dict[str, Any]] = {}
        for name, definition in sorted(declared.items()):
            source = definition.get("source")
            source_kind = (
                source.get("source").strip()
                if isinstance(source, dict)
                and isinstance(source.get("source"), str)
                else ""
            )
            if source_kind not in _REMOTE_SOURCE_KINDS:
                continue
            local_path, reason = _registered_marketplace_path(
                name,
                refresh_repositories=refresh_repositories,
                fast_forward_repositories=fast_forward_repositories,
                refresh_results=refresh_results,
            )
            if local_path is None:
                skipped[name] = reason or "unavailable"
                continue
            desired[name] = {
                "source": {
                    "source": "directory",
                    "path": str(local_path),
                }
            }

        managed: dict[str, dict] = {}
        conflicts: list[str] = []
        for name, definition in desired.items():
            if name in marketplaces:
                if marketplaces[name] != definition:
                    conflicts.append(name)
                continue
            marketplaces[name] = definition
            managed[name] = definition

        result = dict(existing)
        result.pop(_OVERLAY_KEY, None)
        if marketplaces:
            result["extraKnownMarketplaces"] = marketplaces
        else:
            result.pop("extraKnownMarketplaces", None)
        if managed:
            result[_OVERLAY_KEY] = {
                "version": _OVERLAY_VERSION,
                "marketplaces": managed,
            }

        changed = _write_overlay(output_path, result)
        return {
            "action": "reconciled",
            "changed": changed,
            "settings_local": str(output_path),
            "repo_path": str(repo),
            "marketplaces": sorted(managed),
            "conflicts": sorted(conflicts),
            "skipped": skipped,
            "repository_refresh": refresh_results,
            "file_removed": not output_path.exists(),
        }

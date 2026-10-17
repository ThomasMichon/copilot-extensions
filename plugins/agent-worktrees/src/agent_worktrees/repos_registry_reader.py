"""Decode the machine-local repository registry, with optional strict validation."""

from __future__ import annotations

from pathlib import Path
import stat
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .repos import ReposRegistry


class RepositoryResolutionError(ValueError):
    """An authoritative registered repository could not be resolved."""


class _UniqueRegistryLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise RepositoryResolutionError(
                    f"repository registry has duplicate key {key!r}"
                )
            seen.add(key)
        self.flatten_mapping(node)
        return super().construct_mapping(node, deep=deep)


def read_registry(path: Path, *, strict: bool = False) -> ReposRegistry:
    from . import config_migrations
    from .repos import (
        VALID_CLASSES, _LEGACY_TYPE_MAP, RepoEntry, ReposRegistry,
        normalize_class, normalize_visibility,
    )

    try:
        if strict:
            try:
                path.lstat()
            except FileNotFoundError:
                for parent in path.parents:
                    try:
                        parent.lstat()
                    except FileNotFoundError:
                        continue
                    if not parent.is_dir():
                        raise RepositoryResolutionError(
                            f"repository registry ancestor is unavailable: {parent}"
                        )
                    break
                return ReposRegistry()
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode):
                raise RepositoryResolutionError("repository registry must be a regular file")
        elif not path.exists():
            return ReposRegistry()
        text = path.read_text(encoding="utf-8")
        data = yaml.load(text, Loader=_UniqueRegistryLoader) if strict else yaml.safe_load(text)
        if not isinstance(data, dict):
            if strict:
                raise RepositoryResolutionError("repository registry must be a mapping")
            return ReposRegistry()
        data = config_migrations.migrate_loaded(data, config_migrations.SCHEMA_REPOS)
        srcroot = data.get("srcroot", {})
        if not isinstance(srcroot, dict):
            srcroot = {}
        raw_map = data.get("account_map", {})
        account_map: dict[str, str] = {}
        if isinstance(raw_map, dict):
            for owner, login in raw_map.items():
                if owner and login:
                    account_map[str(owner)] = str(login)
        repos: dict[str, RepoEntry] = {}
        raw_repos = data.get("repos", {})
        if strict and not isinstance(raw_repos, dict):
            raise RepositoryResolutionError("repository registry 'repos' must be a mapping")
        if isinstance(raw_repos, dict):
            for name, entry in raw_repos.items():
                if not isinstance(entry, dict):
                    if strict:
                        raise RepositoryResolutionError(
                            f"registered repository {name!r} must be a mapping"
                        )
                    continue
                paths = {}
                for plat in ("windows", "wsl", "linux"):
                    if plat in entry:
                        if strict and (
                            not isinstance(entry[plat], str) or not entry[plat].strip()
                        ):
                            raise RepositoryResolutionError(
                                f"registered repository {name!r} has invalid {plat} path"
                            )
                        paths[plat] = str(entry[plat])
                raw_tags = entry.get("tags", [])
                tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []
                raw_class = entry.get("class", entry.get("type"))
                if strict and raw_class is not None and (
                    not isinstance(raw_class, str)
                    or raw_class.lower() not in (*VALID_CLASSES, *_LEGACY_TYPE_MAP)
                ):
                    raise RepositoryResolutionError(
                        f"registered repository {name!r} has invalid class {raw_class!r}"
                    )
                norm_class = normalize_class(raw_class)
                raw_agent = entry.get("agent")
                agent = (
                    bool(raw_agent) if raw_agent is not None
                    else norm_class != "reference"
                )
                remote = entry.get("remote", "")
                if strict and not isinstance(remote, str):
                    raise RepositoryResolutionError(
                        f"registered repository {name!r} has invalid remote"
                    )
                repos[name] = RepoEntry(
                    name=name,
                    repo_class=norm_class,
                    remote=remote,
                    default_branch=entry.get("default_branch", ""),
                    tags=tags,
                    contributing=entry.get("contributing", ""),
                    account=str(entry.get("account", "") or ""),
                    copilot_account=str(entry.get("copilot_account", "") or ""),
                    agent=agent,
                    visibility=normalize_visibility(entry.get("visibility")),
                    paths=paths,
                )
        return ReposRegistry(srcroot=srcroot, repos=repos, account_map=account_map)
    except Exception as exc:
        if strict:
            if isinstance(exc, RepositoryResolutionError):
                raise
            raise RepositoryResolutionError(
                f"repository registry could not be read: {exc}"
            ) from exc
        return ReposRegistry()

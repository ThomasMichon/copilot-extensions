"""Decode the machine-local repository registry, with optional strict validation."""

from __future__ import annotations

from pathlib import Path
import stat
from typing import TYPE_CHECKING

import yaml
from dropin_registry import ScanAuthority

from . import config_acquisition as acquisition
from .acquisition_report import AcquisitionReport, failed, observe

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


def read_registry_report(path: Path, *, strict: bool = False) -> AcquisitionReport[ReposRegistry]:
    """Report the original registry acquisition without replaying owner work."""
    with acquisition.capture_config_observations() as observations:
        value = read_registry(path, strict=strict)
        if not any(o.source == str(path) and o.stage == "repository-registry" for o in observations):
            acquisition.opaque_source("repository-registry")
    return AcquisitionReport("repository-registry", value, tuple(observations))


def read_registry(path: Path, *, strict: bool = False) -> ReposRegistry:
    observations = acquisition.current_observations()
    start = len(observations) if observations is not None else 0
    from . import config_migrations
    from .repos_registry_transaction import read_bytes, revision
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
                failed(observations, str(path), "repository-registry", "presence-provenance-unavailable", "strict pre-read ancestor selection has no read outcome")
                return ReposRegistry(_baseline=(str(path.resolve()), revision(None)))
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode):
                raise RepositoryResolutionError("repository registry must be a regular file")
        content = read_bytes(path)
        baseline = (str(path.resolve()), revision(content))
        if observations is not None and not any(
            o.source == str(path) and o.stage == "config-source-bytes" for o in observations[start:]
        ):
            acquisition.opaque_source("repository-registry-bytes")
        if content is None:
            observe(observations, str(path), "repository-registry", ScanAuthority.ABSENT)
            return ReposRegistry(_baseline=baseline)
        text = content.decode("utf-8")
        data = yaml.load(text, Loader=_UniqueRegistryLoader) if strict else yaml.safe_load(text)
        if not isinstance(data, dict):
            failed(observations, str(path), "repository-registry", "invalid-shape", "registry document must be a mapping")
            if strict:
                raise RepositoryResolutionError("repository registry must be a mapping")
            return ReposRegistry(_baseline=(str(path.resolve()), "invalid"))
        observe(observations, str(path), "registry-parser", ScanAuthority.COMPLETE)
        migration_start = len(observations) if observations is not None else 0
        data = config_migrations.migrate_loaded(data, config_migrations.SCHEMA_REPOS)
        if observations is not None and not any(
            o.stage == "config-migration" and o.source == config_migrations.SCHEMA_REPOS
            for o in observations[migration_start:]
        ):
            acquisition.opaque_source("repository-registry-migration")
        schema_version = data.get("schema_version")
        if "schema_version" in data and (type(schema_version) is not int or schema_version < 1):
            raise RepositoryResolutionError("repository registry has an invalid schema version")
        srcroot = data.get("srcroot", {})
        if not isinstance(srcroot, dict):
            failed(observations, str(path), "registry-fields", "invalid-shape", "srcroot")
            srcroot = {}
        raw_map = data.get("account_map", {})
        if strict and (
            not isinstance(raw_map, dict)
            or not all(
                isinstance(owner, str) and owner.strip()
                and isinstance(login, str) and login.strip()
                for owner, login in raw_map.items()
            )
        ):
            raise RepositoryResolutionError(
                "repository registry 'account_map' must map non-empty owners to logins"
            )
        if observations is not None and (not isinstance(raw_map, dict) or not all(
            isinstance(owner, str) and owner.strip() and isinstance(login, str) and login.strip()
            for owner, login in raw_map.items()
        )):
            failed(observations, str(path), "registry-fields", "invalid-shape", "account_map")
        account_map: dict[str, str] = {}
        if isinstance(raw_map, dict):
            for owner, login in raw_map.items():
                if owner and login:
                    account_map[str(owner)] = str(login)
        repos: dict[str, RepoEntry] = {}
        raw_repos = data.get("repos", {})
        if strict and not isinstance(raw_repos, dict):
            raise RepositoryResolutionError("repository registry 'repos' must be a mapping")
        if not isinstance(raw_repos, dict):
            failed(observations, str(path), "registry-fields", "invalid-shape", "repos")
        if isinstance(raw_repos, dict):
            for name, entry in raw_repos.items():
                if not isinstance(entry, dict):
                    if strict:
                        raise RepositoryResolutionError(
                            f"registered repository {name!r} must be a mapping"
                        )
                    failed(observations, str(path), "registry-fields", "invalid-entry", "repository entry must be a mapping")
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
                raw_agent = entry.get("agent", False)
                if strict and type(raw_agent) is not bool:
                    raise RepositoryResolutionError(
                        f"registered repository {name!r} agent must be a Boolean"
                    )
                agent = raw_agent is True
                remote = entry.get("remote", "")
                if strict and not isinstance(remote, str):
                    raise RepositoryResolutionError(
                        f"registered repository {name!r} has invalid remote"
                    )
                if strict and not isinstance(entry.get("copilot_account", ""), str):
                    raise RepositoryResolutionError("registered repository has invalid Copilot preference")
                if strict and not isinstance(entry.get("account", ""), str):
                    raise RepositoryResolutionError("registered repository has invalid account")
                if observations is not None and (
                    any(plat in entry and (not isinstance(entry[plat], str) or not entry[plat].strip())
                        for plat in ("windows", "wsl", "linux"))
                    or not isinstance(raw_tags, list)
                    or (raw_class is not None and (not isinstance(raw_class, str)
                        or raw_class.lower() not in (*VALID_CLASSES, *_LEGACY_TYPE_MAP)))
                    or type(raw_agent) is not bool or not isinstance(remote, str)
                    or not isinstance(entry.get("copilot_account", ""), str)
                    or not isinstance(entry.get("account", ""), str)
                ):
                    failed(observations, str(path), "registry-fields", "invalid-entry", "repository fields retain permissive normalization")
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
                if observations is not None and entry.get("visibility") not in (None, "") and not repos[name].visibility:
                    failed(observations, str(path), "registry-fields", "invalid-entry", "visibility normalization discarded a field")
        observe(observations, str(path), "repository-registry", ScanAuthority.COMPLETE)
        return ReposRegistry(
            srcroot=srcroot, repos=repos, account_map=account_map, _baseline=baseline,
            schema_version=schema_version,
        )
    except Exception as exc:
        failed(observations, str(path), "repository-registry", "invalid-registry-source", type(exc).__name__)
        if strict:
            if isinstance(exc, RepositoryResolutionError):
                raise
            raise RepositoryResolutionError(
                f"repository registry could not be read: {exc}"
            ) from exc
        return ReposRegistry(_baseline=(str(path.resolve()), "invalid"))

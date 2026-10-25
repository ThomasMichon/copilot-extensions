"""Read existing qualified payloads from an explicitly selected local inventory."""

from __future__ import annotations

import hashlib
import io
import logging
import math
import os
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from .conventions import (
    LOCAL_MARKETPLACE_SOURCE_KINDS, MARKETPLACE_MANIFEST_RELS, REMOTE_MARKETPLACE_SOURCE_KINDS,
)
from .execution import execution_environment, execution_executable
from .marketplace import _is_reparse, load_marketplace, marketplace_manifest_path, plugin_dir
from .payload import catalog_metadata, plugin_manifest, publication_metadata
from .remote_identity import normalize_remote
from .registration import _settings_document, strict_json
from .settings import split_source


def _require_unlinked_path(root: Path, path: Path, *, allow_missing: bool = False) -> None:
    relative = path.relative_to(root)
    if ".." in relative.parts:
        raise ValueError("Native cache evidence requires a contained path")
    member = root
    for part in ("", *relative.parts):
        member = member / part
        try:
            info = member.lstat()
        except FileNotFoundError:
            if allow_missing:
                break
            raise
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError("Linked native cache evidence path")


def inventory_paths(repo: Path, environment: dict[str, str] | None) -> tuple[Path, Path]:
    env = os.environ if environment is None else environment
    user_home = env.get("USERPROFILE") if os.name == "nt" else env.get("HOME")
    user_home = Path(user_home) if user_home else Path.home()
    home = Path(env["COPILOT_HOME"]) if env.get("COPILOT_HOME") else user_home / ".copilot"
    if env.get("COPILOT_CACHE_HOME"):
        cache = Path(env["COPILOT_CACHE_HOME"])
    elif os.name == "nt":
        cache = Path(env.get("LOCALAPPDATA") or user_home / "AppData" / "Local") / "copilot"
    elif sys.platform == "darwin":
        cache = user_home / "Library" / "Caches" / "copilot"
    else:
        cache = Path(env.get("XDG_CACHE_HOME") or user_home / ".cache") / "copilot"
    return (repo / home).resolve(), (repo / cache).resolve()


def installed_item(source: str, home: Path, cache: Path) -> dict:
    name, marketplace = split_source(source)
    registration = _settings_document(home / "settings.json", jsonc_header=True).get(
        "extraKnownMarketplaces", {},
    ).get(marketplace)
    expected_name = marketplace
    declared_catalog = None
    if registration is not None:
        definition = registration.get("source") if isinstance(registration, dict) else None
        kind = definition.get("source") if isinstance(definition, dict) else None
        if kind not in LOCAL_MARKETPLACE_SOURCE_KINDS | REMOTE_MARKETPLACE_SOURCE_KINDS:
            raise ValueError(f"Invalid installed marketplace registration: {marketplace}")
        field = "path" if kind in LOCAL_MARKETPLACE_SOURCE_KINDS else "repo" if kind == "github" else "url"
        if not isinstance(definition.get(field), str) or not definition[field].strip():
            raise ValueError(f"Invalid installed marketplace location: {marketplace}")
        if kind in LOCAL_MARKETPLACE_SOURCE_KINDS:
            expected_name = definition.get("manifestName", marketplace)
            if not isinstance(expected_name, str) or not expected_name.strip():
                raise ValueError(f"Invalid installed marketplace identity mapping: {marketplace}")
            root = Path(definition["path"])
            if root.is_absolute() and root.exists():
                declared_catalog = root.resolve(strict=True)
    matches = []
    catalogs = cache / "marketplaces"
    candidates = (
        [declared_catalog] if declared_catalog is not None
        else list(sorted(catalogs.iterdir())) if catalogs.exists() else []
    )
    visited = set()
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        candidate = candidate.resolve(strict=True)
        if candidate in visited:
            continue
        visited.add(candidate)
        if candidate != declared_catalog:
            candidate.relative_to(catalogs.resolve())
        path = marketplace_manifest_path(candidate)
        if path is None:
            if candidate == declared_catalog:
                raise ValueError(f"Registered installed marketplace catalog is missing: {source}")
            continue
        document = strict_json(path)
        if document.get("name") != expected_name:
            if candidate == declared_catalog:
                raise ValueError(f"Installed marketplace catalog identity differs: {source}")
            continue
        owner = document.get("owner")
        if not isinstance(owner, dict) or not isinstance(owner.get("name"), str) or not owner["name"].strip():
            raise ValueError(f"Installed marketplace owner metadata is missing: {source}")
        if not isinstance(document.get("plugins"), list):
            raise ValueError(f"Installed marketplace plugins must be a list: {source}")
        entries = [entry for entry in document.get("plugins", [])
                   if isinstance(entry, dict) and entry.get("name") == name]
        if len(entries) != 1:
            raise ValueError(f"Installed marketplace catalog does not uniquely declare: {source}")
        matches.append((candidate, *publication_metadata(candidate, source)))
    if len(matches) > 1:
        raise ValueError(f"Installed marketplace catalog is ambiguous: {source}")
    catalog_path, metadata, entry = matches[0] if matches else (None, {}, {})
    if declared_catalog is not None:
        catalog = load_marketplace(declared_catalog)
        payload = plugin_dir(catalog, name) if catalog is not None else None
        if payload is None:
            raise ValueError(f"Required registered local plugin is absent: {source}")
        definition = {
            **registration,
            "source": {**registration["source"], "path": str(declared_catalog.resolve())},
        }
        return {
            "reference": {
                "marketplace_root": str(declared_catalog.resolve()), "payload": str(payload),
                "definition": definition,
            },
            "catalog": metadata, "entry": entry,
            "inventory_origin": {"home": str(home), "cache": str(cache)},
        }
    root = home / "installed-plugins"
    payload = root / marketplace / name
    if not payload.is_dir():
        raise ValueError(f"Required installed plugin is absent and no repository marketplace origin is declared: {source}")
    payload = payload.resolve(strict=True)
    payload.relative_to(root.resolve())
    manifest = plugin_manifest(payload)
    if manifest.get("name") != name or manifest.get("marketplace", marketplace) != marketplace:
        raise ValueError(f"Installed payload identity differs from requested source: {source}")
    return {
        "inventory": {
            "home": str(home), "cache": str(cache), "payload": str(payload),
            "registration": registration,
            "catalog_root": str(catalog_path) if catalog_path is not None else None,
        },
        "catalog": metadata, "entry": entry,
    }


def inventory_item(
    source: str, home: Path, cache: Path, *, definition: dict | None = None,
    environment=None, execution_cwd: Path | None = None,
) -> dict | None:
    """Reuse installed authority or qualify an unchanged native Git-cache payload."""
    if definition is None:
        return installed_item(source, home, cache)
    name, marketplace = split_source(source)
    registration = _settings_document(home / "settings.json", jsonc_header=True).get(
        "extraKnownMarketplaces", {},
    ).get(marketplace)
    if registration == definition:
        if not (home / "installed-plugins" / marketplace / name).is_dir():
            return None
        return {**installed_item(source, home, cache), "marketplace": definition}
    if registration is not None:
        return None
    return native_git_inventory_item(
        source, home, cache, definition=definition,
        environment=environment, execution_cwd=execution_cwd,
    )


def native_git_inventory_item(
    source: str, home: Path, cache: Path, *, definition: dict,
    environment=None, execution_cwd: Path | None = None,
    deadline: float | None = None, strict: bool = False,
) -> dict | None:
    """Prove cache consistency, independently of registration or origin trust.

    Strict consumers refuse an ineligible candidate attributable to the selected
    catalog or remote. Ordinary inventory retains its availability-only policy.
    This operation never authenticates the repository behind a remote URL.
    """
    name, marketplace = split_source(source)
    registration = _settings_document(home / "settings.json", jsonc_header=True).get(
        "extraKnownMarketplaces", {},
    ).get(marketplace)

    def remaining():
        if deadline is None:
            return 30
        if (
            isinstance(deadline, bool) or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline) or deadline <= time.monotonic()
        ):
            raise ValueError("Native cache proof deadline exceeded or invalid")
        return min(30, deadline - time.monotonic())

    remaining()
    origin = definition.get("source", {})
    kind = origin.get("source")
    field = "repo" if kind == "github" else "url"
    if (
        kind not in {"github", "git"}
        or set(origin) - {"source", field, "ref", "sha", "path"}
        or not isinstance(origin.get(field), str) or not origin[field]
        or any(key in origin and (not isinstance(origin[key], str) or not origin[key])
               for key in ("ref", "sha", "path"))
    ):
        return None
    remote = normalize_remote(
        f"https://github.com/{origin[field]}.git" if kind == "github" else origin[field],
    )
    if remote is None or not remote.startswith("network:"):
        return None
    location = Path(origin.get("path", "."))
    if location.is_absolute() or ".." in location.parts:
        return None
    catalogs = cache / "marketplaces"
    try:
        if not catalogs.is_dir():
            return None
        candidates = sorted(catalogs.iterdir())
    except OSError:
        logging.getLogger("plugin-resolve").warning(
            "%s native cache inventory is unavailable (unreadable-cache); "
            "ordinary acquisition remains available", source,
        )
        return None
    cwd = execution_cwd or home
    env = execution_environment({
        key: value for key, value in (os.environ if environment is None else environment).items()
        if not key.startswith("GIT_")
    }, cwd)
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")
    executable = execution_executable("git", env, cwd)

    def git(root, *arguments):
        try:
            result = subprocess.run(
                [executable, "--literal-pathspecs", "-c", "core.fsmonitor=false",
                 "-c", "core.longpaths=true", "-C", str(root), *arguments],
                capture_output=True, text=True, encoding="utf-8", timeout=remaining(), env=env,
                errors="surrogateescape",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            remaining()
            logging.getLogger("plugin-resolve").warning(
                "%s native cache candidate is ineligible (probe-timeout); "
                "ordinary acquisition remains available", source,
            )
            return None
        remaining()
        return result.stdout.rstrip("\r\n") if result.returncode == 0 else None

    from .reflection import _allowed, subset

    matches = []
    attributable = set()
    for candidate in candidates:
        remaining()
        try:
            if not candidate.is_dir():
                continue
            if candidate.is_symlink() or getattr(candidate.lstat(), "st_reparse_tag", 0):
                continue
            if strict:
                candidate_remote = git(candidate, "config", "--get", "remote.origin.url")
                if candidate_remote is not None and normalize_remote(candidate_remote) == remote:
                    attributable.add(candidate)
            root = candidate / location
            canonical_candidate = candidate.resolve(strict=True)
            if not root.is_dir() or not root.resolve(strict=True).is_relative_to(canonical_candidate):
                continue
            path = marketplace_manifest_path(root)
            if path is None:
                continue
            _require_unlinked_path(candidate, path)
            document = strict_json(path)
        # Path.resolve() reports link loops as RuntimeError before Python 3.13.
        except (OSError, ValueError, RuntimeError):
            logging.getLogger("plugin-resolve").warning(
                "%s native cache candidate is ineligible (invalid-catalog); "
                "ordinary acquisition remains available", source,
            )
            continue
        if document.get("name") != marketplace:
            continue
        attributable.add(candidate)
        entries, metadata, owner = (
            document.get("plugins"), document.get("metadata", {}), document.get("owner"),
        )
        if (
            not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries)
            or not isinstance(metadata, dict) or not isinstance(metadata.get("pluginRoot", ""), str)
            or not isinstance(owner, dict) or not isinstance(owner.get("name"), str)
            or not owner["name"].strip()
        ):
            logging.getLogger("plugin-resolve").warning(
                "%s native cache candidate is ineligible (invalid-catalog); "
                "ordinary acquisition remains available", source,
            )
            continue
        try:
            catalog = load_marketplace(root)
            payload = plugin_dir(catalog, name) if catalog is not None else None
        except RuntimeError:
            logging.getLogger("plugin-resolve").warning(
                "%s native cache candidate is ineligible (invalid-payload); "
                "ordinary acquisition remains available", source,
            )
            continue
        if payload is None:
            continue
        top = git(candidate, "rev-parse", "--show-toplevel")
        if top is None or Path(top) != canonical_candidate:
            continue
        actual_remote = git(candidate, "config", "--get", "remote.origin.url")
        if (
            actual_remote is None
            or (normalize_remote(actual_remote) != remote if kind == "github"
                else actual_remote != origin[field])
        ):
            continue
        head = git(candidate, "rev-parse", "--verify", "HEAD^{commit}")
        selector = origin.get("sha") or origin.get("ref")
        if head is None or (selector and git(
            candidate, "rev-parse", "--verify", "--end-of-options", selector + "^{commit}",
        ) != head):
            continue
        tree_id = git(candidate, "rev-parse", "--verify", head + "^{tree}")
        if tree_id is None:
            continue
        references: set[Path] = set()
        try:
            if not _allowed(payload.relative_to(candidate)):
                raise ValueError("Native cache payload root is excluded from content proof")
            declared = Path(catalog.plugin_root) / catalog.plugins[name].source.strip()
            _require_unlinked_path(candidate, root / declared)
            content = subset(candidate, payload, referenced_paths=references)
            for member in references:
                _require_unlinked_path(candidate, member, allow_missing=True)
            paths = []
            for relative_catalog in MARKETPLACE_MANIFEST_RELS:
                catalog_path = root.joinpath(*relative_catalog)
                paths.append(str(catalog_path.relative_to(candidate)))
                if catalog_path == path:
                    break
            paths.append(str(payload.relative_to(candidate)))
            paths.extend(str(member.relative_to(candidate)) for member in sorted(references))
            ancestors: dict[int, set[str]] = {}
            for value in paths:
                relative = Path(value)
                if not _allowed(relative):
                    continue
                for parent in relative.parents:
                    if parent.parts:
                        ancestors.setdefault(len(parent.parts), set()).add(str(parent))
            for depth in sorted(ancestors):
                parents = git(
                    candidate, "ls-tree", "-z", "--full-tree", head, "--",
                    *sorted(ancestors[depth]),
                )
                if parents is None:
                    raise ValueError("Native cache ancestor proof is unavailable")
                for row in parents.split("\0"):
                    if row and row.split(None, 1)[0] != "040000":
                        raise ValueError("Native cache path traverses a non-tree committed ancestor")
            tree = git(candidate, "ls-tree", "-r", "-z", "--full-tree", head, "--", *paths)
            if tree is None:
                continue
            committed = set()
            for row in tree.split("\0"):
                remaining()
                if not row:
                    continue
                metadata, separator, filename = row.partition("\t")
                mode, object_type, object_id = metadata.split()
                relative = Path(filename)
                if not _allowed(relative):
                    continue
                if not separator or object_type != "blob" or mode not in {"100644", "100755"}:
                    raise ValueError("Native cache member is not an ordinary committed file")
                member = candidate / relative
                _require_unlinked_path(candidate, member)
                if sys.platform != "win32" and bool(
                    member.stat().st_mode & stat.S_IXUSR,
                ) != (mode == "100755"):
                    raise ValueError("Native cache executable mode differs from the selected commit")
                data = member.read_bytes()
                algorithm = {40: "sha1", 64: "sha256"}.get(len(object_id))
                if algorithm is None or hashlib.new(
                    algorithm, b"blob " + str(len(data)).encode("ascii") + b"\0" + data,
                ).hexdigest() != object_id:
                    raise ValueError("Native cache bytes differ from the selected commit")
                committed.add(relative)
            if path.relative_to(candidate) not in committed:
                raise ValueError("Native cache catalog is not in the selected commit")
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
                if any(Path(member.name) not in committed for member in archive):
                    raise ValueError("Native cache closure contains unverified files")
            metadata, entry = publication_metadata(root, source)
            catalog_metadata(source, plugin_manifest(payload), metadata, entry)
        except (OSError, ValueError, RuntimeError):
            logging.getLogger("plugin-resolve").warning(
                "%s native cache candidate is ineligible (invalid-payload); "
                "ordinary acquisition remains available", source,
            )
            continue
        status = git(candidate, "status", "--porcelain", "-z", "--untracked-files=all",
                     "--ignored=matching", "--", *paths)
        if status is None or any(_allowed(Path(row[3:])) for row in status.split("\0") if row):
            continue
        matches.append({
            "marketplace": definition,
            "inventory": {
                "home": str(home), "cache": str(cache), "payload": str(payload),
                "registration": registration, "catalog_root": str(root),
                "git_commit": head, "git_tree": tree_id,
                "git_root": str(canonical_candidate),
                "payload_digest": hashlib.sha256(content).hexdigest(),
            },
            "catalog": metadata, "entry": entry,
        })
    if len(matches) > 1:
        raise ValueError(f"Native Git marketplace inventory is ambiguous: {source}")
    if strict and len(matches) != len(attributable):
        raise ValueError(f"Native Git marketplace evidence is corrupt, foreign or changed: {source}")
    remaining()
    return matches[0] if matches else None

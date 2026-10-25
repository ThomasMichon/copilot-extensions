"""Read existing qualified payloads from an explicitly selected local inventory."""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

from .conventions import LOCAL_MARKETPLACE_SOURCE_KINDS, REMOTE_MARKETPLACE_SOURCE_KINDS
from .execution import execution_environment, execution_executable
from .marketplace import load_marketplace, marketplace_manifest_path, plugin_dir
from .payload import plugin_manifest, publication_metadata
from .remote_identity import normalize_remote
from .registration import _settings_document, strict_json
from .settings import split_source


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
        f"https://github.com/{origin[field]}" if kind == "github" else origin[field],
    )
    if remote is None:
        return None
    location = Path(origin.get("path", "."))
    if location.is_absolute() or ".." in location.parts:
        return None
    catalogs = cache / "marketplaces"
    if not catalogs.is_dir():
        return None
    cwd = execution_cwd or home
    env = execution_environment({
        key: value for key, value in (os.environ if environment is None else environment).items()
        if not key.startswith("GIT_")
    }, cwd)
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")
    executable = execution_executable("git", env, cwd)

    def git(root, *arguments):
        result = subprocess.run(
            [executable, "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
            capture_output=True, text=True, encoding="utf-8", timeout=30, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.stdout.rstrip("\r\n") if result.returncode == 0 else None

    from .reflection import _allowed, subset

    matches = []
    for candidate in sorted(catalogs.iterdir()):
        if not candidate.is_dir():
            continue
        if candidate.is_symlink() or getattr(candidate.lstat(), "st_reparse_tag", 0):
            continue
        root = candidate / location
        if not root.is_dir() or not root.resolve().is_relative_to(candidate.resolve()):
            continue
        path = marketplace_manifest_path(root)
        if path is None:
            continue
        member = path
        while member != candidate:
            if member.is_symlink() or getattr(member.lstat(), "st_reparse_tag", 0):
                break
            member = member.parent
        if member != candidate or strict_json(path).get("name") != marketplace:
            continue
        catalog = load_marketplace(root)
        payload = plugin_dir(catalog, name) if catalog is not None else None
        if payload is None:
            continue
        top = git(candidate, "rev-parse", "--show-toplevel")
        actual_remote = git(candidate, "config", "--get", "remote.origin.url")
        if (
            top is None or Path(top).resolve() != candidate.resolve()
            or actual_remote is None or normalize_remote(actual_remote) != remote
        ):
            continue
        head = git(candidate, "rev-parse", "--verify", "HEAD^{commit}")
        selector = origin.get("sha") or origin.get("ref")
        if head is None or (selector and git(
            candidate, "rev-parse", "--verify", "--end-of-options", selector + "^{commit}",
        ) != head):
            continue
        content = subset(candidate, payload)
        relative_payload = payload.relative_to(candidate)
        paths = [str(path.relative_to(candidate)), str(relative_payload)]
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            paths.extend(member.name for member in archive
                         if not Path(member.name).is_relative_to(relative_payload))
        status = git(candidate, "status", "--porcelain", "-z", "--untracked-files=all",
                     "--ignored=matching", "--", *paths)
        if status is None or any(_allowed(Path(row[3:])) for row in status.split("\0") if row):
            continue
        if git(candidate, "ls-files", "--error-unmatch", "--",
               str(path.relative_to(candidate))) is None:
            continue
        metadata, entry = publication_metadata(root, source)
        matches.append({
            "marketplace": definition,
            "inventory": {
                "home": str(home), "cache": str(cache), "payload": str(payload),
                "registration": registration, "catalog_root": str(root),
                "git_commit": head,
                "payload_digest": hashlib.sha256(content).hexdigest(),
            },
            "catalog": metadata, "entry": entry,
        })
    if len(matches) > 1:
        raise ValueError(f"Native Git marketplace inventory is ambiguous: {source}")
    return matches[0] if matches else None

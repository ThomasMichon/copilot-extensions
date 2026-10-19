"""Transactional, repository-local registration of prepared venue payloads."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from .marketplace import Marketplace, MarketplacePlugin, load_marketplace, plugin_dir
from .conventions import LOCAL_MARKETPLACE_SOURCE_KINDS, REMOTE_MARKETPLACE_SOURCE_KINDS, SETTINGS_RELS
from .payload import catalog_metadata, dependencies, plugin_manifest
from .settings import (
    RepoPluginSettings, local_marketplace_path, marketplace_manifest_name,
    split_source,
)


def strict_json(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _settings_document(path: Path) -> dict:
    value = strict_json(path)
    for key in ("enabledPlugins", "extraKnownMarketplaces"):
        if key in value and not isinstance(value[key], dict):
            raise ValueError(f"{key} must be an object in {path}")
    if any(not isinstance(enabled, bool) for enabled in value.get("enabledPlugins", {}).values()):
        raise ValueError(f"enabledPlugins values must be booleans in {path}")
    return value


def _operator_settings(repo: Path, receipt: dict) -> RepoPluginSettings:
    """Merge authored layers, peeling only unchanged operation-owned values."""
    enabled = {}
    marketplaces = {}
    for relative in SETTINGS_RELS:
        data = _settings_document(repo.joinpath(*relative))
        generated_layer = relative == (".github", "copilot", "settings.local.json")
        for source, value in data.get("enabledPlugins", {}).items():
            owned = receipt.get("enabledPlugins", {})
            if not (generated_layer and source in owned and value == owned[source]):
                enabled[source] = value
        for name, value in data.get("extraKnownMarketplaces", {}).items():
            if not isinstance(value, dict):
                continue
            owned = receipt.get("extraKnownMarketplaces", {})
            if generated_layer and name in owned and value == owned[name]:
                # Only authored local overrides were overwritten. Base/Claude
                # declarations remain on disk and are always read afresh above.
                saved = receipt.get("marketplaceOrigins", {})
                if name in saved and receipt.get("marketplaceOriginActive", {}).get(name, True):
                    marketplaces[name] = saved[name]
            else:
                marketplaces[name] = value
    return RepoPluginSettings(enabled=enabled, marketplaces=marketplaces)


def _installation_home(staging: Path, source: str, definition: dict) -> Path:
    _name, marketplace = split_source(source)
    digest = hashlib.sha256(json.dumps([source, definition], sort_keys=True).encode()).hexdigest()
    home = staging / marketplace / "installation" / digest
    home.resolve().relative_to(staging.resolve())
    return home


def _validate_effective_origin(
    source: str, candidate: Path, repo: Path, state_dir: Path,
    operator: RepoPluginSettings, effective: RepoPluginSettings, publications: dict,
) -> None:
    name, marketplace = split_source(source)
    authority = operator.marketplaces.get(marketplace)
    if authority is None:
        return
    kind = authority.get("source", {}).get("source")
    if kind in REMOTE_MARKETPLACE_SOURCE_KINDS:
        home = _installation_home(state_dir / "marketplaces", source, authority)
        expected = home / "installed-plugins" / marketplace / name
        if (
            candidate.resolve() == expected.resolve()
            and strict_json(home / ".installation-complete.json").get("source") == source
            and _settings_document(home / "settings.json").get("extraKnownMarketplaces", {}).get(marketplace)
            == authority
            and plugin_manifest(candidate).get("name") == name
        ):
            return
    elif kind in LOCAL_MARKETPLACE_SOURCE_KINDS:
        actual = local_marketplace_path(marketplace, effective, repo_dir=repo)
        declared = local_marketplace_path(marketplace, operator, repo_dir=repo)
        if (
            actual is not None and declared is not None and actual.resolve() == declared.resolve()
            and marketplace_manifest_name(marketplace, effective) == marketplace_manifest_name(marketplace, operator)
        ):
            return
        if publications.get(source, {}).get("native_marketplace") == authority:
            return
    raise ValueError(f"Enabled target-native marketplace origin was not prepared: {source}")


def atomic_json(path: Path, value: dict) -> None:
    pending = path.with_name(f".{path.name}.{uuid.uuid4().hex}.pending")
    try:
        with pending.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


@contextlib.contextmanager
def checkout_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".venue-plugins.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Target plugin registration lock timed out") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def register_prepared(
    repo: Path, payloads: dict[str, str], enabled: dict[str, bool], roots: dict[str, str],
    *, state_dir: Path, publications: dict[str, dict],
    diagnostics: list[dict], initial_owned_plugins: dict[str, bool],
) -> dict:
    """Merge only unclaimed or unchanged owned keys; return effective directories.

    Ownership is advisory and published first. A crash between the two atomic
    writes can relinquish ownership, never acquire an operator's differing value.
    """
    directory = repo / ".github" / "copilot"
    directory.resolve().relative_to(repo.resolve())
    with checkout_lock(state_dir):
        directory.mkdir(parents=True, exist_ok=True)
        for relative in (".github/copilot/settings.json", ".claude/settings.json",
                         ".claude/settings.local.json"):
            _settings_document(repo / relative)
        path = directory / "settings.local.json"
        receipt_path = state_dir / "ownership.json"
        if path.is_symlink() or receipt_path.is_symlink():
            raise ValueError("Linked registration files are not operation-owned")
        data = _settings_document(path)
        receipt = strict_json(receipt_path)
        operator = _operator_settings(repo, receipt)
        plugins = data.setdefault("enabledPlugins", {})
        marketplaces = data.setdefault("extraKnownMarketplaces", {})
        operator_marketplaces = dict(marketplaces)
        prior_plugins = receipt.get("enabledPlugins", {})
        prior_marketplaces = receipt.get("extraKnownMarketplaces", {})
        owned_plugins = {k: v for k, v in prior_plugins.items() if k in plugins and plugins[k] == v}
        owned_marketplaces = {
            k: v for k, v in prior_marketplaces.items() if k in marketplaces and marketplaces[k] == v
        }
        for source, value in list(owned_plugins.items()):
            if (
                source in initial_owned_plugins and initial_owned_plugins[source] == value
                and source not in payloads and source not in operator.enabled
            ):
                plugins.pop(source)
                owned_plugins.pop(source)
        origins = dict(receipt.get("marketplaceOrigins", {}))
        origin_active = dict(receipt.get("marketplaceOriginActive", {}))
        for source in owned_plugins:
            if source in operator.enabled:
                plugins[source] = owned_plugins[source] = operator.enabled[source]
        for marketplace in origins:
            if operator_marketplaces.get(marketplace) != prior_marketplaces.get(marketplace):
                origin_active[marketplace] = False
        catalogs: dict[str, dict] = {}
        reconciled_marketplaces: set[str] = set()
        for source, raw in payloads.items():
            name, marketplace = split_source(source)
            payload = Path(raw).resolve(strict=True)
            manifest = plugin_manifest(payload)
            if manifest.get("name") != name:
                raise ValueError(f"Prepared payload identity mismatch: {source}")
            root = Path(roots[marketplace]).resolve()
            payload.relative_to(root)
            root.mkdir(exist_ok=True)
            catalog = catalogs.setdefault(marketplace, strict_json(root / "marketplace.json"))
            entries = {item["name"]: item for item in catalog.get("plugins", [])}
            details = publications[source]
            metadata, entry = catalog_metadata(
                source, manifest, details.get("catalog", {}), details.get("entry", {}),
            )
            entries[name] = {**entry, "source": "./" + Path(os.path.relpath(payload, root)).as_posix()}
            catalog.update(metadata, plugins=list(entries.values()))
            definition = {"source": {"source": "directory", "path": str(root)}}
            current = operator_marketplaces.get(marketplace)
            authority = operator.marketplaces.get(marketplace)
            prepared_origin = details["native_marketplace"]
            if (
                any(isinstance(origin, dict) and origin.get("source", {}).get("source")
                    in LOCAL_MARKETPLACE_SOURCE_KINDS for origin in (prepared_origin, authority))
                and prepared_origin != authority
            ):
                raise ValueError(f"Target-native local marketplace origin changed during preparation: {marketplace}")
            if (
                isinstance(authority, dict)
                and authority.get("source", {}).get("source") in REMOTE_MARKETPLACE_SOURCE_KINDS
                and details.get("marketplace") != authority
            ):
                raise ValueError(f"Explicit target-native remote origin was not prepared: {marketplace}")
            native_remote = (
                isinstance(current, dict)
                and current.get("source", {}).get("source") in REMOTE_MARKETPLACE_SOURCE_KINDS
            )
            if native_remote and details.get("marketplace") != current:
                raise ValueError(f"Explicit target-native remote origin was not prepared: {marketplace}")
            if marketplace not in reconciled_marketplaces:
                if native_remote:
                    origins[marketplace] = current
                    origin_active[marketplace] = True
                    diagnostics.append({
                        "source": source, "marketplace": marketplace, "status": "native-origin-preserved",
                    })
                elif marketplace in origins and current != prior_marketplaces.get(marketplace):
                    origin_active[marketplace] = False
                if native_remote or current is None or marketplace in owned_marketplaces:
                    marketplaces[marketplace] = definition
                    owned_marketplaces[marketplace] = definition
                reconciled_marketplaces.add(marketplace)
            if source not in plugins or source in owned_plugins:
                plugins[source] = operator.enabled.get(source, enabled.get(source, True))
                owned_plugins[source] = plugins[source]
        effective = RepoPluginSettings(
            enabled={**operator.enabled, **plugins},
            marketplaces={**operator.marketplaces, **marketplaces},
        )
        resolved = {}
        for source in effective.enabled_sources():
            name, marketplace = split_source(source)
            local = local_marketplace_path(marketplace, effective, repo_dir=repo)
            if local is None:
                continue
            if marketplace in catalogs and local.resolve() == Path(roots[marketplace]).resolve():
                manifest = catalogs[marketplace]
                catalog = Marketplace(
                    root=local, name=manifest["name"],
                    plugins={entry["name"]: MarketplacePlugin(entry["name"], entry["source"])
                             for entry in manifest["plugins"]},
                )
            else:
                catalog = load_marketplace(local)
            if catalog is not None and catalog.name == marketplace_manifest_name(marketplace, effective):
                candidate = plugin_dir(catalog, name)
                if candidate is not None:
                    _validate_effective_origin(
                        source, candidate, repo, state_dir, operator, effective, publications,
                    )
                    resolved[source] = str(candidate)
        missing = [source for source in effective.enabled_sources() if source not in resolved]
        if missing:
            raise ValueError(f"Enabled plugins do not resolve: {', '.join(missing)}")
        for source, directory_path in resolved.items():
            _name, marketplace = split_source(source)
            for dependency in dependencies(plugin_manifest(Path(directory_path)), marketplace):
                if not effective.enabled.get(dependency) or dependency not in resolved:
                    diagnostics.append({
                        "source": source, "dependency": dependency,
                        "status": "disabled" if effective.enabled.get(dependency) is False
                        else "outside-selection",
                    })
        for marketplace, catalog in catalogs.items():
            atomic_json(Path(roots[marketplace]) / "marketplace.json", catalog)
        atomic_json(receipt_path, {
            "enabledPlugins": owned_plugins, "extraKnownMarketplaces": owned_marketplaces,
            "marketplaceOrigins": origins,
            "marketplaceOriginActive": origin_active,
        })
        atomic_json(path, data)
        return resolved

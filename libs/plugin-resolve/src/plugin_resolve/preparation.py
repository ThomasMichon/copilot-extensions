"""Target-side install/copy -> repository registration barrier."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from .conventions import REMOTE_MARKETPLACE_SOURCE_KINDS, LOCAL_MARKETPLACE_SOURCE_KINDS
from .marketplace import marketplace_manifest_path
from .payload import (
    publication_metadata, catalog_metadata, dependencies, plugin_manifest, report_diagnostic,
)
from .reflection import _plan_item, identity
from .registration import (
    _installation_home, _operator_settings, _settings_document,
    atomic_json, checkout_lock, register_prepared, strict_json,
)
from .settings import RepoPluginSettings, local_marketplace_path
from .inventory import inventory_paths, installed_item
from .execution import execution_environment, execution_executable, windows_batch_command
from .target import bind_target

_GIT_SELECTORS = {
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
}


def _environment(environment=None) -> dict[str, str]:
    source = os.environ if environment is None else environment
    return {key: value for key, value in source.items() if key not in _GIT_SELECTORS}


def _failure(operation: str, source: str, result) -> RuntimeError:
    lines = []
    for line in (result.stderr or result.stdout or "").splitlines()[:20]:
        line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
        if re.search(r"authorization|bearer|token|secret|password|credential|gh[pousr]_|github_pat_|eyJ",
                     line, re.IGNORECASE):
            lines.append("[credential-bearing diagnostic redacted]")
        else:
            lines.append(re.sub(r"https?://\S+", "[URL]", line))
    cause = " ".join(lines)[:500] or "no diagnostic was returned"
    return RuntimeError(f"{operation} failed: {source} (exit {result.returncode}): {cause}")


def _git(repo: Path, *args: str, environment=None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
        timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=_environment(environment),
    ).stdout.strip()


def _unpack(content: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts or not member.isfile():
                raise ValueError("Unsafe reflected payload member")
            path = destination.joinpath(*relative.parts)
            path.resolve().relative_to(destination.resolve())
            content = archive.extractfile(member).read()
            if path.exists() and path.read_bytes() == content:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            pending = path.with_name(f".{path.name}.{uuid.uuid4().hex}.pending")
            try:
                pending.write_bytes(content)
                pending.chmod(member.mode & 0o777)
                os.replace(pending, path)
            finally:
                pending.unlink(missing_ok=True)


def _installed_metadata(
    home: Path, source: str, *, required: bool = True, cache_root: Path | None = None,
) -> tuple[dict, dict] | None:
    name, marketplace = identity(source)
    if cache_root is None:
        cache_root = home / "cache"
        cache_root.resolve().relative_to(home.resolve())
    if cache_root.is_symlink() or (cache_root.exists() and getattr(cache_root.lstat(), "st_reparse_tag", 0)):
        raise ValueError("Linked native cache root is not operation-owned")
    cache = cache_root / "marketplaces"
    cache.resolve().relative_to(cache_root.resolve())
    if cache.is_symlink() or (cache.exists() and getattr(cache.lstat(), "st_reparse_tag", 0)):
        raise ValueError("Linked native marketplace cache is not operation-owned")
    matches = []
    for root in sorted(cache.iterdir()) if cache.exists() else ():
        if not root.is_dir():
            continue
        if root.is_symlink() or getattr(root.lstat(), "st_reparse_tag", 0):
            raise ValueError("Linked native marketplace clone is not operation-owned")
        root.resolve(strict=True).relative_to(cache.resolve())
        path = marketplace_manifest_path(root)
        if path is None:
            continue
        member = path
        while member != root:
            if member.is_symlink() or getattr(member.lstat(), "st_reparse_tag", 0):
                raise ValueError("Linked native marketplace manifest is not operation-owned")
            member = member.parent
        path.resolve(strict=True).relative_to(root.resolve())
        catalog = strict_json(path)
        if catalog.get("name") == marketplace and any(
            isinstance(item, dict) and item.get("name") == name for item in catalog.get("plugins", [])
        ):
            matches.append(publication_metadata(root, source))
    if not matches and not required:
        return None
    if len(matches) != 1:
        raise ValueError(f"Installed marketplace catalog is missing or ambiguous: {source}")
    metadata, entry = matches[0]
    if not metadata.get("owner"):
        raise ValueError(f"Marketplace owner metadata is missing: {marketplace}")
    return metadata, entry


def _install(source: str, definition: dict, home: Path, repo: Path,
             copilot_command: tuple[str, ...], *, environment=None,
             execution_cwd: Path | None = None,
             check_cancelled=lambda: None) -> tuple[Path, dict, dict]:
    check_cancelled()
    name, marketplace = identity(source)
    home.mkdir(parents=True, exist_ok=True)
    payload = home / "installed-plugins" / marketplace / name
    payload.resolve().relative_to(home.resolve())
    receipt = home / ".installation-complete.json"
    previous = strict_json(receipt)
    if previous and previous.get("source") != source:
        raise ValueError(f"Installation receipt source differs from requested source: {source}")
    settings_path = home / "settings.json"
    settings = _settings_document(settings_path)
    registrations = settings.get("extraKnownMarketplaces", {})
    registered = registrations.get(marketplace)
    if marketplace in registrations and registered != definition:
        raise ValueError(f"Registered marketplace origin differs from requested origin: {marketplace}")
    try:
        manifest = strict_json(payload / "plugin.json") or strict_json(
            payload / ".claude-plugin" / "plugin.json"
        )
    except json.JSONDecodeError:
        if previous:
            raise ValueError(f"Completed installation has an invalid payload manifest: {source}") from None
        manifest = {}
    if manifest.get("name") not in (None, name):
        raise ValueError(f"Installed payload identity differs from requested source: {source}")
    reusable = previous.get("source") == source and manifest.get("name") == name and registered == definition
    if reusable:
        metadata, entry = previous.get("catalog"), previous.get("entry")
        if (
            isinstance(metadata, dict) and metadata.get("name") == marketplace and metadata.get("owner")
            and isinstance(entry, dict) and entry.get("name") == name
        ):
            catalog_metadata(source, manifest, metadata, entry)
            return payload, metadata, entry
        publication = _installed_metadata(home, source, required=False)
        if publication is not None:
            metadata, entry = publication
            catalog_metadata(source, manifest, metadata, entry)
            if _settings_document(settings_path).get("extraKnownMarketplaces", {}).get(marketplace) != definition:
                raise ValueError(f"Registered marketplace origin changed during metadata hydration: {marketplace}")
            check_cancelled()
            atomic_json(receipt, {"source": source, "catalog": metadata, "entry": entry})
            return payload, metadata, entry
    origin = definition.get("source", {})
    location = origin.get("repo") if origin.get("source") == "github" else origin.get("url")
    if not isinstance(location, str) or not location:
        raise ValueError(f"Unsupported remote marketplace installation source: {marketplace}")
    if registered is None:
        settings.setdefault("extraKnownMarketplaces", {})[marketplace] = definition
        check_cancelled()
        atomic_json(settings_path, settings)
    with tempfile.TemporaryDirectory(prefix="copilot-venue-install-") as neutral:
        if Path(neutral).resolve().is_relative_to(repo.resolve()):
            raise ValueError("Native installation commands require a neutral cwd outside the target checkout")
        cache_root = Path(neutral) / "cache"
        env = {
            **_environment(environment), "COPILOT_HOME": str(home),
            "COPILOT_CACHE_HOME": str(cache_root), "COPILOT_AUTO_UPDATE": "false",
        }
        if not copilot_command:
            raise ValueError("Native installation requires an executable")
        command_prefix = list(copilot_command)
        if copilot_command == ("copilot",):
            command_prefix[0] = env.get("COPILOT_PATH") or "copilot"
        command_prefix[0] = execution_executable(
            command_prefix[0], env, execution_cwd or repo,
        )
        if os.name == "nt" and command_prefix[0].lower().endswith((".cmd", ".bat")):
            command_prefix = windows_batch_command(command_prefix, env)
            command_prefix[0] = execution_executable(command_prefix[0], env, execution_cwd or repo)
        def command(arguments, operation):
            check_cancelled()
            result = subprocess.run(
                [*command_prefix, *arguments], cwd=neutral, env=env,
                capture_output=True, text=True, timeout=240,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode:
                raise _failure(operation, source, result)
            check_cancelled()

        if reusable and _installed_metadata(home, source, required=False) is None:
            command(["plugin", "marketplace", "update", marketplace], "Required marketplace refresh")
        if not reusable:
            command(["plugin", "install", source], "Required plugin installation")
        metadata, entry = _installed_metadata(home, source, cache_root=cache_root)
    if _settings_document(settings_path).get("extraKnownMarketplaces", {}).get(marketplace) != definition:
        raise ValueError(f"Registered marketplace origin changed during installation: {marketplace}")
    manifest = strict_json(payload / "plugin.json") or strict_json(
        payload / ".claude-plugin" / "plugin.json"
    )
    if manifest.get("name") != name:
        raise RuntimeError(f"Installer did not produce the requested payload: {source}")
    catalog_metadata(source, manifest, metadata, entry)
    check_cancelled()
    atomic_json(receipt, {"source": source, "catalog": metadata, "entry": entry})
    return payload, metadata, entry


def prepare(
    request: dict, *, copilot_command: tuple[str, ...] = ("copilot",),
    diagnostics: list[dict] | None = None,
    environment: dict[str, str] | None = None, check_cancelled=lambda: None,
    prepared_context: dict | None = None,
) -> dict[str, str]:
    check_cancelled()
    local_mode = request.get("local_mode", "copy")
    if local_mode not in {"copy", "reference"}:
        raise ValueError("Unknown local payload preparation mode")
    repo = Path(request["repo"]).resolve(strict=True)
    if request.get("worktree_id"):
        raise ValueError("Provider must resolve the explicit execution checkout before preparation")
    if not repo.is_dir():
        raise ValueError("Target checkout is not a directory")
    execution_cwd = repo
    environment = execution_environment(_environment(environment), execution_cwd)
    repo = Path(_git(repo, "rev-parse", "--show-toplevel", environment=environment)).resolve(strict=True)
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir", environment=environment)).resolve(strict=True)
    state_dir = git_dir / "copilot-venue-plugins"
    state_dir.resolve().relative_to(git_dir)
    staging = state_dir / "marketplaces"
    with checkout_lock(state_dir):
        ownership = strict_json(state_dir / "ownership.json")
        native_settings = _operator_settings(repo, ownership)
    inventory = inventory_paths(repo, environment) if local_mode == "reference" else None
    host_sources = {item["source"] for item in request["plugins"]}
    marketplaces = {**request.get("marketplaces", {}), **native_settings.marketplaces}
    host_selection = []
    for item in request["plugins"]:
        _name, marketplace = identity(item["source"])
        origin = native_settings.marketplaces.get(marketplace)
        selected = None
        if "target_relative" in item and native_settings.enabled.get(item["source"]) is False:
            continue
        if origin is not None and not (
            item.get("local_source")
            and origin.get("source", {}).get("source") in REMOTE_MARKETPLACE_SOURCE_KINDS
            and item["source"] not in native_settings.enabled
        ):
            kind = origin.get("source", {}).get("source")
            if kind not in REMOTE_MARKETPLACE_SOURCE_KINDS | LOCAL_MARKETPLACE_SOURCE_KINDS:
                raise ValueError(f"Unsupported target marketplace source: {marketplace}")
            selected = _plan_item(
                item, (repo, native_settings), marketplaces,
                allow_external=True, existing_root=staging,
                required=(
                    native_settings.enabled.get(item["source"], False) if local_mode == "reference"
                    else item["source"] in native_settings.enabled
                ) or bool(item.get("resolve_on_target")),
                local_mode="reference" if kind in LOCAL_MARKETPLACE_SOURCE_KINDS else local_mode,
            )
            if selected is not None and kind in LOCAL_MARKETPLACE_SOURCE_KINDS:
                selected["native_local"] = True
        if selected is None and "target_relative" in item:
            item = bind_target(item, repo, request.get("target_repository"), environment)
        host_selection.append(selected if selected is not None else item)
    native_selection = []
    for source in native_settings.enabled_sources():
        if source in host_sources:
            continue
        _name, marketplace = identity(source)
        owner_settings = native_settings
        declarations = marketplaces
        if (
            marketplace not in native_settings.marketplaces
            and not (local_mode == "reference" and source in ownership.get("inventorySources", {}))
        ):
            projected = _settings_document(
                repo / ".github" / "copilot" / "settings.local.json"
            ).get("extraKnownMarketplaces", {}).get(marketplace)
            owned = ownership.get("extraKnownMarketplaces", {})
            if marketplace in owned and projected == owned[marketplace]:
                cache_root = Path(projected["source"]["path"]).resolve(strict=True)
                if ownership.get("referenceMarketplaces", {}).get(marketplace) != projected:
                    cache_root.relative_to(staging.resolve())
                owner_settings = RepoPluginSettings(marketplaces={marketplace: projected})
                declarations = {**marketplaces, marketplace: projected}
        native_selection.append(_plan_item(
            {"source": source, "enable": True}, (repo, owner_settings), declarations,
            allow_external=True, existing_root=staging,
            local_mode="reference" if marketplace in native_settings.marketplaces
            and native_settings.marketplaces[marketplace].get("source", {}).get("source")
            in LOCAL_MARKETPLACE_SOURCE_KINDS else local_mode,
        ))
        if native_selection[-1].get("reference") is not None:
            native_selection[-1]["native_local"] = True
    selected = [*host_selection, *native_selection]
    if selected:
        occupied = {}
        for item in selected:
            _name, marketplace = identity(item["source"])
            native_root = local_marketplace_path(marketplace, native_settings, repo_dir=repo)
            if native_root is not None:
                occupied[marketplace] = native_root.resolve()
            elif marketplace in native_settings.marketplaces:
                occupied[marketplace] = json.dumps(native_settings.marketplaces[marketplace], sort_keys=True)
            elif item.get("native_local") and item.get("reference"):
                occupied.setdefault(marketplace, Path(item["reference"]["marketplace_root"]).resolve())
        projected = []
        for item in selected:
            reference = item.get("reference")
            local_source = item.get("local_source")
            if (reference is None and local_source is None) or "local_path" in item:
                projected.append(item)
                continue
            logical = item["source"]
            name, marketplace = identity(logical)
            root = Path(
                reference["marketplace_root"] if reference else local_source["marketplace_root"],
            ).resolve()
            previous_root = occupied.setdefault(marketplace, root)
            if root != previous_root:
                digest = hashlib.sha256(f"{marketplace}\0{root}".encode()).hexdigest()[:16]
                alias = f"{marketplace[:40]}-venue-{digest}"
                source = f"{name}@{alias}"
                previous_alias = ownership.get("pluginIdentities", {}).get(logical)
                if (
                    alias in native_settings.marketplaces
                    and previous_alias != source
                ):
                    raise ValueError(f"Reference marketplace alias collides with an authored registration: {alias}")
                if alias in native_settings.marketplaces:
                    item = _plan_item(
                        {"source": source, "enable": item["enable"]}, (repo, native_settings),
                        native_settings.marketplaces, allow_external=True, local_mode="reference",
                    )
                    item["native_local"] = True
                elif reference is not None:
                    definition = reference["definition"]
                    item = {
                        **item, "source": source,
                        "reference": {
                            **reference,
                            "definition": {
                                **definition, "source": {
                                    **definition["source"],
                                    "manifestName": item["catalog"]["name"],
                                },
                            },
                        },
                    }
                else:
                    item = {**item, "source": source}
                item["logical_source"] = logical
                if native_settings.enabled.get(logical) is False:
                    item["enable"] = False
            projected.append(item)
        selected = projected
    payloads = {}
    roots = {}
    enabled = {}
    publications = {}
    for item in selected:
        check_cancelled()
        source = item["source"]
        if source in payloads:
            continue
        _name, marketplace = identity(source)
        if inventory is not None and "marketplace" in item:
            registered = _settings_document(inventory[0] / "settings.json", jsonc_header=True).get(
                "extraKnownMarketplaces", {},
            ).get(marketplace)
            payload = inventory[0] / "installed-plugins" / marketplace / _name
            if registered == item["marketplace"] and payload.is_dir():
                item = {**item, **installed_item(source, *inventory)}
        if item.get("resolve_on_target"):
            if marketplace not in native_settings.marketplaces:
                if inventory is None:
                    raise ValueError(f"Required target marketplace is undeclared: {marketplace}")
                item = {**item, **installed_item(source, *inventory)}
                item.pop("resolve_on_target")
            else:
                item = _plan_item(
                    item, (repo, native_settings), native_settings.marketplaces,
                    allow_external=True, existing_root=staging,
                    local_mode=local_mode,
                )
            if item.get("resolve_on_target"):
                raise ValueError(f"Required target plugin could not be resolved: {source}")
        if "inventory" in item:
            payloads[source] = item["inventory"]["payload"]
            enabled[source] = item["enable"]
            publications[source] = item
            continue
        reference = item.get("reference")
        if reference is not None:
            if local_mode != "reference" and not item.get("target_owned") and not item.get("native_local"):
                raise ValueError("Filesystem references require explicit reference mode")
            payload = Path(reference["payload"]).resolve(strict=True)
            root = Path(reference["marketplace_root"]).resolve(strict=True)
            payload.relative_to(root)
            roots[marketplace] = str(root)
            payloads[source] = str(payload)
            enabled[source] = item["enable"]
            publications[source] = {
                **item, "native_marketplace": native_settings.marketplaces.get(marketplace),
            }
            continue
        root = staging / marketplace
        root.resolve().relative_to(state_dir.resolve())
        roots[marketplace] = str(root)
        root.mkdir(parents=True, exist_ok=True)
        # Per-marketplace install/copy serialization does not hold the checkout
        # registration lock across a network fetch.
        with checkout_lock(root):
            if "local_path" in item:
                payload = Path(item["local_path"]).resolve(strict=True)
                payload.relative_to(root.resolve())
            elif "archive" in item:
                content = base64.b64decode(item["archive"])
                if hashlib.sha256(content).hexdigest() != item["digest"]:
                    raise ValueError("Reflected payload digest mismatch")
                destination = root / "snapshots" / item["digest"]
                destination.resolve().relative_to(root.resolve())
                _unpack(content, destination)
                payload = destination / item["relative"]
            else:
                install_home = _installation_home(staging, source, item["marketplace"])
                payload, item["catalog"], item["entry"] = _install(
                    source, item["marketplace"], install_home, repo,
                    copilot_command,
                    environment=environment, execution_cwd=execution_cwd,
                    check_cancelled=check_cancelled,
                )
            payloads[source] = str(payload)
            enabled[source] = item["enable"]
            publications[source] = {
                **item, "native_marketplace": native_settings.marketplaces.get(marketplace),
            }
        manifest = plugin_manifest(payload)
        item["requirements"] = dependencies(manifest, marketplace)
    # Generated settings are repository-local preferences, not product changes.
    check_cancelled()
    exclude = Path(_git(repo, "rev-parse", "--git-path", "info/exclude", environment=environment))
    if not exclude.is_absolute():
        exclude = repo / exclude
    common = Path(_git(repo, "rev-parse", "--git-common-dir", environment=environment))
    if not common.is_absolute():
        common = repo / common
    exclude.resolve().relative_to(common.resolve())
    with checkout_lock(exclude.parent):
        check_cancelled()
        ignored = "/.github/copilot/settings.local.json"
        previous = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if ignored not in previous.splitlines():
            with exclude.open("a", encoding="utf-8") as stream:
                stream.write(("\n" if previous and not previous.endswith("\n") else "") + ignored + "\n")
    # No settings publication until EVERY required install/copy completed.
    requirements = []
    result = register_prepared(
        repo, payloads, enabled, roots, state_dir=state_dir, publications=publications,
        diagnostics=requirements, initial_owned_plugins=ownership.get("enabledPlugins", {}),
        check_cancelled=check_cancelled,
        inventory=inventory,
        prepared_context=prepared_context,
    )
    if diagnostics is not None:
        diagnostics.extend(requirements)
    else:
        for item in requirements:
            report_diagnostic(item)
    return result

"""Portable vault/group selection; database and runtime custody stay native."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from collections.abc import Mapping
from typing import Any, cast

from configuration_context import context_from_owner, delivered_context, read_owner_context, ready_owner_prefix
from configuration_layers import Composition, ConfigurationError, Layer, compose, read_source

from . import _peer_launch as _peer_launch
from .invocation import CACHE_MAINTENANCE_FIELD, CACHE_TARGET_FIELDS

FIELDS = {"vault", "group", "enabled", CACHE_MAINTENANCE_FIELD, *CACHE_TARGET_FIELDS}


def service_configuration_root(cwd: str | None = None) -> str:
    """Qualify the native discovery directory before a resident changes cwd."""
    from .config import configuration_selection

    selected, native_default = configuration_selection(cwd, strict=True)
    root = Path(selected or os.getcwd())
    if not root.is_absolute() or not root.is_dir():
        raise ConfigurationError("service configuration root must be an existing absolute directory")
    report = owner_report(root, native_default=True) if native_default else owner_report(root)
    if report is not None:
        context_from_owner(report)
    root_text = str(root)
    if any(ord(character) < 32 for character in root_text):
        raise ConfigurationError("service configuration root contains control characters")
    return root_text


def service_configuration_snapshot(cwd: str | None = None) -> dict[str, str]:
    from .config import _global_config_path

    root = service_configuration_root(cwd)
    selected = _global_config_path()
    path = str(Path(root, selected).absolute())
    if any(ord(character) < 32 for character in path):
        raise ConfigurationError("service configuration path contains control characters")
    from .config import configuration_selection

    snapshot = {"root": root, "config": path}
    if configuration_selection(cwd, strict=True)[1]:
        snapshot["native_default_owner"] = "1"
    return snapshot


def _legacy_report(root: Path) -> dict[str, object] | None:
    from .config import home_dir

    own_root = Path(os.environ.get("AGENT_RT_ROOT") or home_dir())
    legacy = Path.home() / ".agent-vault"
    peer = Path.home() / ".agent-worktrees"
    if own_root.resolve() != legacy.resolve() or not peer.is_dir():
        return None
    if (peer / ".installation-ownership.json").exists():
        raise _peer_launch.ContextRefused("legacy configuration owner has installation-cell ownership")
    # Installed wheels have no payload scripts; construction retains this SDK
    # beside the selected interpreter for read-only runtime resolution.
    slot = Path(sys.prefix)
    if slot.name == ".venv":
        slot = slot.parent
    path = slot / ".runtime-bootstrap" / "versioned_runtime.py"
    if not path.is_file() or path.is_symlink():
        path = Path(__file__).resolve().parents[1] / ".runtime-bootstrap" / "versioned_runtime.py"
    if not path.is_file() or path.is_symlink():
        path = own_root / "scripts" / "versioned_runtime.py"
    if not path.is_file():
        path = Path(__file__).resolve().parents[2] / "scripts" / "versioned_runtime.py"
    environment = _peer_launch.legacy_peer_environment(dict(os.environ))
    environment.pop("AGENT_CONFIGURATION_CONTEXT", None)
    if path.is_file():
        from configuration_context.readiness import qualify_resolver

        qualify_resolver(path, "agent-vault", "agent_vault")
        resolver = _peer_launch._load_primitive(path, "_vault_configuration_runtime")
        prefix = ready_owner_prefix(peer, "agent_worktrees", resolver=resolver.resolve_python)
    else:
        from agent_procutil.owned_process import run_caller_owned
        from configuration_context.runtime import deployed_runtime_python

        deployed = own_root / "bin" / (
            "resolve-runtime.ps1" if os.name == "nt" else "resolve-runtime.sh"
        )
        prefix = ready_owner_prefix(
            peer, "agent_worktrees",
            resolver=lambda selected: deployed_runtime_python(
                selected, resolver=deployed, environment=environment, runner=run_caller_owned,
            ),
        )
    if not prefix:
        return None
    return read_owner_context(
        prefix, root,
        environment=environment,
    )


def _checked_report(
    report: dict[str, object] | None, own: Mapping[str, str] | None,
) -> dict[str, object] | None:
    if report is not None and own is not None and (
        context_from_owner(report)["installation_cell"] != str(own["cellRoot"])
    ):
        raise ConfigurationError("Vault configuration belongs to another installation cell")
    return report


def owner_report(root: Path, *, native_default: bool = False) -> dict[str, object] | None:
    from .config import home_dir

    own_root = Path(os.environ.get("AGENT_RT_ROOT") or home_dir())
    raw = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
    if not raw and (
        own_root.name == "agent-vault" and own_root.parent.name == "plugins"
        and own_root.parent.parent.parent.name == "marketplaces"
    ):
        raw = str(own_root / "install.json")
    own = None
    if raw:
        own = _peer_launch.validate_owner(
            "agent-vault", own_root, raw,
        )
    report = delivered_context(repository_root=root)
    if report is None and native_default:
        return None
    if report is not None:
        _checked_report(report, own)
        if own is not None:
            return report
        fresh = _legacy_report(root)
        if fresh is None or any(
            context_from_owner(fresh)[key] != context_from_owner(report)[key]
            for key in ("repository_id", "installation_cell")
        ):
            raise ConfigurationError("delivered configuration has no matching qualified owner")
        return fresh
    if own is None:
        return _legacy_report(root)
    peer = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
    try:
        peer.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ConfigurationError("optional Worktrees installation could not be inspected") from error
    return _checked_report(read_owner_context(
        _peer_launch.launch_prefix("agent-vault", Path(own["pluginRoot"]), raw, "agent-worktrees"),
        root, environment=os.environ.copy(),
    ), own)


def _ordinary(raw: Mapping[str, object], directory: Path | None = None) -> dict[str, object]:
    from .config import _validate_config_data

    values = {key: value for key, value in raw.items() if key in FIELDS}
    _validate_config_data(values, "ordinary vault selection")
    if directory is not None and any(values.get(key) for key in CACHE_TARGET_FIELDS):
        from .cache_targets import preferences

        values.update(preferences(values, directory))
    return values


def compose_preferences(
    cwd: str | None, repo_data: Mapping[str, object],
    global_data: Mapping[str, object], ext_data: Mapping[str, object],
    *, repository_directory: Path | None = None, strict: bool = False,
) -> Composition | None:
    from .config import _global_config_path, configuration_selection

    selected, native_default = configuration_selection(cwd, strict=strict)
    root = Path(selected or os.getcwd()).resolve()
    report = owner_report(root, native_default=True) if native_default else owner_report(root)
    if report is None:
        return None
    context = context_from_owner(report)
    return explicit_preferences(root, context, report.get("repository_machine_root"), repo_data,
                                global_data, ext_data, repository_directory or root, _global_config_path().parent)


def explicit_preferences(
    root: Path, context: dict[str, Any], machine_root: object, repo_data: Mapping[str, object],
    global_data: Mapping[str, object], ext_data: Mapping[str, object],
    repository_directory: Path, machine_directory: Path,
) -> Composition:
    layers: list[Layer] = [{
        "id": "vault-defaults", "tier": "defaults", "origin": "defaults",
        "values": {
            "vault": None, "group": None, "enabled": False,
            "cache_entries": [], "cache_manifests": [],
        },
    }, {
        "id": "vault-native-extension", "tier": "repository", "origin": "base",
        "repository_id": context["repository_id"], "values": _ordinary(ext_data, root),
    }, {
        "id": "vault-selected-repository", "tier": "repository", "origin": "base",
        "repository_id": context["repository_id"],
        "values": _ordinary(repo_data, repository_directory or root),
    }]
    sources = [(context["base_root"], "repository", "base")]
    if context["binding"] == "bound":
        sources.append((context["knowledge_root"], "repository", "knowledge"))
    if isinstance(machine_root, str) and machine_root:
        sources.append((machine_root, "repository_machine", "repository_machine"))
    for root, tier, origin in sources:
        if not Path(root).exists():
            continue
        document = read_source(str(root), ".agent-vault.json")
        if document is None:
            continue
        raw = document["values"]
        if origin != "base" and set(raw) - FIELDS:
            raise ValueError("portable vault selection cannot change database or runtime custody")
        layer: Layer = {
            "id": f"vault-{origin}", "tier": tier, "origin": origin,
            "repository_id": context["repository_id"],
            "values": _ordinary(raw, Path(root)), "source": document["source"],
        }
        if tier == "repository_machine":
            layer["execution_machine"] = context["execution_machine"]
            layer["installation_cell"] = context["installation_cell"]
        layers.append(layer)
    machine = _ordinary(global_data, machine_directory)
    if "default_vault" in global_data:
        machine["vault"] = global_data["default_vault"]
    if "group" not in machine and "vault_group" in global_data:
        machine["group"] = global_data["vault_group"]
    layers.append({
        "id": "vault-native-machine-selection", "tier": "machine", "origin": "local_machine",
        "execution_machine": context["execution_machine"],
        "installation_cell": context["installation_cell"], "values": machine,
    })
    result = compose(context, layers)
    # Source references remain native selectors; composition never opens a vault.
    _ordinary(result["values"])
    return result


def runtime_enabled(cwd: str | None = None) -> bool:
    """Resolve explicit runtime opt-in without loading extensions or a backend."""
    from .config import _discover_repo_config, _validate_config_data, load_global_config

    path, repository = _discover_repo_config(cwd, strict=True)
    machine = load_global_config(strict=True)
    _validate_config_data(repository, "repository vault configuration")
    _validate_config_data(machine, "global vault configuration")
    result = compose_preferences(
        cwd, repository, machine, {},
        repository_directory=path.parent if path is not None else None, strict=True,
    )
    if result is not None:
        return cast(bool, result["values"]["enabled"])
    return cast(bool, machine.get("enabled", repository.get("enabled", False)))

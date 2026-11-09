"""Portable vault/group selection; database and runtime custody stay native."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from configuration_context import context_from_owner, delivered_context, read_owner_context, ready_owner_prefix
from configuration_layers import ConfigurationError, compose, read_source

from . import _peer_launch

FIELDS = {"vault", "group", "enabled"}


def service_configuration_root(cwd: str | None = None) -> str:
    """Qualify the native discovery directory before a resident changes cwd."""
    from .config import configuration_root

    root = Path(configuration_root(cwd))
    if not root.is_absolute() or not root.is_dir():
        raise ConfigurationError("service configuration root must be an existing absolute directory")
    report = owner_report(root)
    if report is not None:
        context_from_owner(report)
    selected = str(root)
    if any(ord(character) < 32 for character in selected):
        raise ConfigurationError("service configuration root contains control characters")
    return selected


def service_configuration_snapshot(cwd: str | None = None) -> dict[str, str]:
    from .config import _global_config_path

    root = service_configuration_root(cwd)
    selected = _global_config_path()
    path = str(Path(root, selected).absolute())
    if any(ord(character) < 32 for character in path):
        raise ConfigurationError("service configuration path contains control characters")
    return {"root": root, "config": path}


def _legacy_report(root: Path) -> dict | None:
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
        [prefix[0], "-I", "-B", "-X", "utf8", *prefix[1:]], root,
        environment=environment,
    )


def _checked_report(report, own):
    if report is not None and own is not None and (
        context_from_owner(report)["installation_cell"] != str(own["cellRoot"])
    ):
        raise ConfigurationError("Vault configuration belongs to another installation cell")
    return report


def owner_report(root: Path) -> dict | None:
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


def _ordinary(raw: dict) -> dict:
    from .config import _validate_config_data

    values = {key: value for key, value in raw.items() if key in FIELDS}
    _validate_config_data(values, "ordinary vault selection")
    return values


def compose_preferences(cwd, repo_data: dict, global_data: dict, ext_data: dict) -> dict | None:
    from .config import configuration_root

    report = owner_report(Path(configuration_root(cwd)).resolve())
    if report is None:
        return None
    context = context_from_owner(report)
    layers = [{
        "id": "vault-defaults", "tier": "defaults", "origin": "defaults",
        "values": {"vault": None, "group": None, "enabled": False},
    }, {
        "id": "vault-native-extension", "tier": "repository", "origin": "base",
        "repository_id": context["repository_id"], "values": _ordinary(ext_data),
    }, {
        "id": "vault-selected-repository", "tier": "repository", "origin": "base",
        "repository_id": context["repository_id"], "values": _ordinary(repo_data),
    }]
    sources = [(context["base_root"], "repository", "base")]
    if context["binding"] == "bound":
        sources.append((context["knowledge_root"], "repository", "knowledge"))
    if report.get("repository_machine_root"):
        sources.append((report["repository_machine_root"], "repository_machine", "repository_machine"))
    for root, tier, origin in sources:
        if not Path(root).exists():
            continue
        document = read_source(root, ".agent-vault.json")
        if document is None:
            continue
        raw = document["values"]
        if origin != "base" and set(raw) - FIELDS:
            raise ValueError("portable vault selection cannot change database or runtime custody")
        layer = {
            "id": f"vault-{origin}", "tier": tier, "origin": origin,
            "repository_id": context["repository_id"],
            "values": _ordinary(raw), "source": document["source"],
        }
        if tier == "repository_machine":
            layer["execution_machine"] = context["execution_machine"]
            layer["installation_cell"] = context["installation_cell"]
        layers.append(layer)
    machine = _ordinary(global_data)
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
    from .config import _discover_repo_config, _validate_config_data, configuration_root, load_global_config

    cwd = configuration_root(cwd)
    _, repository = _discover_repo_config(cwd, strict=True)
    machine = load_global_config(strict=True)
    _validate_config_data(repository, "repository vault configuration")
    _validate_config_data(machine, "global vault configuration")
    result = compose_preferences(cwd, repository, machine, {})
    if result is not None:
        return result["values"]["enabled"]
    return machine.get("enabled", repository.get("enabled", False))

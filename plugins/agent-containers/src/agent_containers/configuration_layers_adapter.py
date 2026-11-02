"""Native container document selection over the canonical preference composer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from configuration_layers import compose, read_source, resolve_path


def _parse_document(text: str):
    value = yaml.safe_load(text)
    return {} if value is None else value


def selected_document(root: Path, *, local: bool = False) -> Path | None:
    filename = "containers.local.yaml" if local else "containers.yaml"
    canonical = root / ".copilot-extensions" / "agent-containers" / filename
    legacy = root / filename
    for path in (canonical, legacy):
        if path.exists() or path.is_symlink():
            return path
    return None


def compose_documents(report: dict[str, Any], machine_root: Path) -> dict[str, Any]:
    """Read selected native sources without touching runtime/resource state."""
    from configuration_context import context_from_owner

    from .config import SECURITY_PROFILES, _strict_bool
    from .config_migrations import migrate_loaded
    from .copilot_auth_policy import validate_auth_mode

    context_from_owner(report)
    context = report["context"]
    layers: list[dict[str, Any]] = [{
        "id": "container-builtins", "tier": "defaults", "origin": "defaults",
        "values": {
            "schema_version": 2, "exec_user": "vscode", "workspace_folder": "/workspace",
            "acp_command": None, "image_prefixes": ["vsc-"],
        },
    }]

    def add(root: Path, path: Path | None, tier: str, origin: str) -> None:
        if path is None:
            return
        source = read_source(
            str(root), path.relative_to(root).as_posix(),
            parser=_parse_document,
        )
        if source is None:
            return
        data = source["values"]
        data = {} if data is None else data
        if not isinstance(data, dict):
            raise RuntimeError(f"{path}: top-level configuration must be a mapping")
        if "copilot_auth_mode" in data:
            validate_auth_mode(data["copilot_auth_mode"])
        fleets = data.get("fleets") or {}
        if not isinstance(fleets, dict):
            fleets = {}
        for name, fleet in fleets.items():
            if isinstance(fleet, dict):
                if (
                    "security_profile" in fleet
                    and str(fleet["security_profile"]).lower() not in SECURITY_PROFILES
                ):
                    raise RuntimeError(f"Fleet '{name}' has an invalid security_profile")
                if "copilot_auth_mode" in fleet:
                    validate_auth_mode(fleet["copilot_auth_mode"])
                for key in ("dispatch_only", "systemd_capable"):
                    if key in fleet:
                        _strict_bool(fleet[key], f"Fleet '{name}' {key}")
        layer = {
            "id": str(path), "tier": tier, "origin": origin,
            "values": migrate_loaded(data), "source": source["source"],
            "installation_cell": context["installation_cell"],
        }
        if tier != "machine":
            layer["repository_id"] = context["repository_id"]
        if tier in {"machine", "repository_machine"}:
            layer["execution_machine"] = context["execution_machine"]
        layers.append(layer)

    base = Path(context["base_root"])
    add(base, selected_document(base), "repository", "base")
    if context["binding"] == "bound":
        knowledge = Path(context["knowledge_root"])
        add(knowledge, selected_document(knowledge), "repository", "knowledge")
    machine = machine_root / "containers.yaml"
    add(
        machine_root,
        machine if machine.exists() or machine.is_symlink() else None,
        "machine", "local_machine",
    )
    local_root = Path(report.get("repository_machine_root") or base)
    add(
        local_root, selected_document(local_root, local=local_root == base),
        "repository_machine", "repository_machine",
    )
    result = compose(context, layers)
    values = result["values"]
    # Remote paths/commands are not host source paths. The devcontainer config
    # retains its native relative-to-devcontainer contract.
    for key in ("dotfiles", "harness"):
        block = values.get(key)
        if isinstance(block, dict) and isinstance(block.get("repo"), str):
            pointer = f"/{key}/repo"
            value = block["repo"]
            if value and not Path(value).is_absolute() and not value.startswith("~"):
                block["repo"] = str(resolve_path(result, pointer))
    fleets = values.get("fleets") or {}
    for name, fleet in (fleets.items() if isinstance(fleets, dict) else ()):
        if not isinstance(fleet, dict):
            continue
        escaped = name.replace("~", "~0").replace("/", "~1")
        for key in ("devcontainer_path", "host_workspace_path", "host_home_path"):
            value = fleet.get(key)
            if isinstance(value, str) and value and not Path(value).is_absolute() and not value.startswith("~"):
                fleet[key] = str(resolve_path(result, f"/fleets/{escaped}/{key}"))
    return result

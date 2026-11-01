"""Selected-context CodeSpace preferences; activation and custody stay native."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from configuration_context import context_from_owner, delivered_context, read_owner_context
from configuration_layers import ConfigurationError, compose, read_source


def configuration_report(root: Path) -> dict[str, Any] | None:
    from . import worktrees
    from ._peer_launch import launch_prefix

    own = worktrees.validate_context()
    delivered = delivered_context()
    if delivered is not None:
        if own is not None and context_from_owner(delivered)["installation_cell"] != str(own["cellRoot"]):
            raise ConfigurationError("CodeSpace configuration belongs to another installation cell")
        if delivered_context(repository_root=root) is not None:
            return delivered
    if own is not None:
        import os

        peer_root = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
        try:
            peer_root.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ConfigurationError("optional Worktrees installation could not be inspected") from error
        prefix = launch_prefix(
            "agent-codespaces", Path(own["pluginRoot"]),
            os.environ["COPILOT_EXTENSIONS_CONTEXT"], "agent-worktrees",
        )
    else:
        # Without a native delivered projection, an optional legacy owner is
        # unavailable to this read. Do not run its lazy/provisioning front door.
        prefix = None
    report = read_owner_context(prefix, str(root))
    if (
        report is not None and own is not None
        and context_from_owner(report)["installation_cell"] != str(own["cellRoot"])
    ):
        raise ConfigurationError("CodeSpace configuration belongs to another installation cell")
    return report


def compose_documents(
    report: dict[str, Any], machine_root: Path,
) -> dict[str, Any]:
    from .config import _repo_config_layers

    context = context_from_owner(report)
    layers: list[dict[str, Any]] = [{
        "id": "codespace-builtins", "tier": "defaults", "origin": "defaults",
        "values": {"defaults": {
            "machine_type": "largePremiumLinux", "location": "EastUs",
            "devcontainer_path": ".devcontainer/devcontainer.json", "ssh_user": "vscode",
            "workspace_folder": None, "acp_command": None,
            "dotfiles_repo": None, "harness_repo": None,
        }},
    }]
    unions: dict[str, list[Any]] = {}

    def add(root: Path, path: Path, tier: str, origin: str) -> None:
        loaded = read_source(
            str(root), path.relative_to(root).as_posix(),
            parser=lambda text: yaml.safe_load(text) or {},
        )
        if loaded is None:
            return
        values = loaded["values"]
        # Native provisioning src is repository-root relative, not config-dir
        # relative. Absolutize at the defining source before atomic list merge.
        blocks = [values.get("provision")]
        repos = values.get("repos") or {}
        if isinstance(repos, dict):
            blocks.extend(
                repo.get("provision") for repo in repos.values() if isinstance(repo, dict)
            )
        for block in blocks:
            if isinstance(block, dict):
                for entry in block.get("files") or []:
                    if isinstance(entry, dict) and isinstance(entry.get("src"), str):
                        source = Path(entry["src"])
                        if not source.is_absolute():
                            entry["src"] = str(root / source)
        creds = values.get("credentials") or {}
        if isinstance(creds, dict):
            for key in ("feed_token_env", "identity_env"):
                if key in creds:
                    items = unions.setdefault(key, [])
                    for item in creds[key] or []:
                        if item not in items:
                            items.append(item)
                    creds[key] = list(items)
            for name, source in (creds.get("sources") or {}).items():
                if not isinstance(source, dict):
                    continue
                for key in ("allowed_hosts", "allowed_resources"):
                    if key in source:
                        items = unions.setdefault(f"sources/{name}/{key}", [])
                        for item in source[key] or []:
                            if item not in items:
                                items.append(item)
                        source[key] = sorted(items)
        layer = {
            "id": str(path), "tier": tier, "origin": origin,
            "values": values, "source": loaded["source"],
            "installation_cell": context["installation_cell"],
        }
        if tier != "machine":
            layer["repository_id"] = context["repository_id"]
        if tier in {"machine", "repository_machine"}:
            layer["execution_machine"] = context["execution_machine"]
        layers.append(layer)

    base = Path(context["base_root"])
    for path in _repo_config_layers(base):
        add(base, path, "repository", "base")
    if context["binding"] == "bound":
        knowledge = Path(context["knowledge_root"])
        for path in _repo_config_layers(knowledge):
            add(knowledge, path, "repository", "knowledge")
    machine = machine_root / "config.yaml"
    if machine.exists() or machine.is_symlink():
        add(machine_root, machine, "machine", "local_machine")
    local_root = Path(report.get("repository_machine_root") or base)
    if local_root == base:
        local = local_root / ".copilot-extensions" / "agent-codespaces" / "config.local.yaml"
        if local.exists() or local.is_symlink():
            add(local_root, local, "repository_machine", "repository_machine")
    else:
        for local in _repo_config_layers(local_root):
            add(local_root, local, "repository_machine", "repository_machine")
    return compose(context, layers)

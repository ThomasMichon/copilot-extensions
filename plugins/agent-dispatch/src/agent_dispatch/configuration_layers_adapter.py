"""Read-only ordinary registrar/identity preference source adaptation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from configuration_context import context_from_owner, delivered_context, read_owner_context
from configuration_layers import ConfigurationError, compose

from . import repo_config
from .install_paths import install_dir


def configuration_report(root: Path) -> dict[str, Any] | None:
    from .procutil import agent_worktrees_environment, agent_worktrees_launch_prefix

    import os

    raw_context = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
    own = None
    if not raw_context:
        from .hosting_policy import selected_marketplace_id

        selected_marketplace_id()
    else:
        from .peer_launch import validate_owner

        own = validate_owner("agent-dispatch", install_dir(), raw_context)
    delivered = delivered_context()
    if delivered is not None:
        if own is not None and context_from_owner(delivered)["installation_cell"] != str(own["cellRoot"]):
            raise ConfigurationError("dispatch configuration belongs to another installation cell")
        if delivered_context(repository_root=root) is not None:
            return delivered
    if own is not None:
        peer_root = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
        try:
            peer_root.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ConfigurationError("optional Worktrees installation could not be inspected") from error
    report = read_owner_context(
        agent_worktrees_launch_prefix() if own is not None else None,
        str(root),
        environment=agent_worktrees_environment(),
    )
    if (
        report is not None and own is not None
        and context_from_owner(report)["installation_cell"] != str(own["cellRoot"])
    ):
        raise ConfigurationError("dispatch configuration belongs to another installation cell")
    return report


def surface_layers(report: dict[str, Any], surface: str):
    context = context_from_owner(report)
    base = Path(context["base_root"])
    roots = [(base, "repository", "base")]
    if context["binding"] == "bound":
        roots.append((Path(context["knowledge_root"]), "repository", "knowledge"))
    roots.append((install_dir() / "config", "machine", "local_machine"))
    local_root = Path(report.get("repository_machine_root") or base)
    roots.append((local_root, "repository_machine", "repository_machine"))
    for root, tier, origin in roots:
        if tier == "machine":
            directories = [root / surface]
        elif tier == "repository_machine" and root == base:
            directories = [root / repo_config.CANONICAL_REPO_CONFIG_DIR / f"{surface}.local"]
        else:
            directories = repo_config.layered_repo_surface_dirs(root, surface)
        for directory in directories:
            yield root, directory, tier, origin


def layer_record(context, root, path, tier, origin, values):
    layer = {
        "id": str(path), "tier": tier, "origin": origin, "values": values,
        "source": {"root": str(root), "path": path.relative_to(root).as_posix()},
        "installation_cell": context["installation_cell"],
    }
    if tier != "machine":
        layer["repository_id"] = context["repository_id"]
    if tier in {"machine", "repository_machine"}:
        layer["execution_machine"] = context["execution_machine"]
    return layer


def read_registrar_layers(report, *, owner=None):
    from .registrar import RegistrarError
    from .registrar_discovery import _decode, _iter_declaration_files, read_declaration_file_set
    from .registrar_document import expand_declaration_document

    context = context_from_owner(report)
    records = {}
    atomic = {}
    origins = {}
    for root, directory, tier, origin in surface_layers(report, "registrar"):
        names = set()
        for path in _iter_declaration_files(directory):
            document = dict(_decode(path.read_text(encoding="utf-8"), path.suffix, where=str(path)))
            # Resource/recipe kinds keep their native whole-document supplier,
            # reference and activation contracts; ordinary lane preferences
            # merge before the existing strict native parser.
            if document.get("kind", "supervised-lane") != "supervised-lane" or "extends" in document:
                for declaration in read_declaration_file_set(path, repo_root=root):
                    if declaration.name in names:
                        raise RegistrarError(f"duplicate profile name {declaration.name!r} under {directory}")
                    names.add(declaration.name)
                    atomic[declaration.name] = declaration.with_owner(owner) if owner else declaration
                    records.pop(declaration.name, None)
                continue
            name = document.get("name")
            if not isinstance(name, str) or not name:
                raise RegistrarError(f"{path}: declaration name is required")
            if name in names:
                raise RegistrarError(f"duplicate profile name {name!r} under {directory}")
            names.add(name)
            if document.get("max_active_processes") is not None:
                if (
                    document.get("concurrency") is not None
                    and document["concurrency"] != document["max_active_processes"]
                ):
                    raise RegistrarError(
                        "concurrency and max_active_processes must agree when both are set"
                    )
                document["concurrency"] = document.pop("max_active_processes")
            records.setdefault(name, [{
                "id": "registrar-builtins", "tier": "defaults", "origin": "defaults",
                "values": {
                    "concurrency": 1, "interval": 30, "max_attempts": 3,
                    "heartbeat": True, "reactive": False, "reactive_interval": 2,
                    "verify_timeout": 0,
                },
            }]).append(
                layer_record(context, root, path, tier, origin, document),
            )
            atomic.pop(name, None)
            origins[name] = (root, directory, path)
            # Validate every admitted prefix: a later layer cannot conceal a
            # malformed protected boolean, unknown key or incompatible body.
            prefix = compose(context, records[name])
            expand_declaration_document(prefix["values"], directory=directory, repo_root=root)
    result = dict(atomic)
    for name, layers in records.items():
        effective = compose(context, layers)
        root, directory, path = origins[name]
        declarations = expand_declaration_document(
            effective["values"], directory=directory, repo_root=root,
        )
        for declaration in declarations:
            result[name] = replace(
                declaration.with_owner(owner) if owner else declaration,
                source_path=str(path),
                configuration_provenance=effective["provenance"],
                configuration_layers=tuple(effective["layers"]),
            )
    return [result[name] for name in sorted(result)]

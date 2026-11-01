"""Select ordinary scalar floors; never alter native desired-state declarations."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from configuration_context import context_from_owner, delivered_context, read_owner_context, ready_owner_prefix
from configuration_layers import ConfigurationError, compose

from . import _peer_launch
from .manifest import ManifestError, resolve_for_machine

FIELDS = {"model", "effortLevel"}


def _legacy_report(root: Path) -> dict | None:
    legacy = Path.home() / ".agent-machines"
    own_root = Path(os.environ.get("AGENT_RT_ROOT") or legacy)
    peer = Path.home() / ".agent-worktrees"
    if own_root.resolve() != legacy.resolve() or not peer.is_dir():
        return None
    if (peer / ".installation-ownership.json").exists():
        raise _peer_launch.ContextRefused("legacy configuration owner has installation-cell ownership")
    path = own_root / "scripts" / "versioned_runtime.py"
    if not path.is_file():
        return None
    resolver = _peer_launch._load_primitive(path, "_machines_configuration_runtime")
    prefix = ready_owner_prefix(peer, "agent_worktrees", resolver=resolver.resolve_python)
    if not prefix:
        return None
    environment = _peer_launch.legacy_peer_environment(dict(os.environ))
    environment.pop("AGENT_CONFIGURATION_CONTEXT", None)
    return read_owner_context(
        [prefix[0], "-I", "-X", "utf8", *prefix[1:]], root,
        environment=environment,
    )


def _checked_report(report, own):
    if report is not None and own is not None and (
        context_from_owner(report)["installation_cell"] != str(own["cellRoot"])
    ):
        raise ConfigurationError("Machines configuration belongs to another installation cell")
    return report


def owner_report(root: Path) -> dict | None:
    own_root = Path(os.environ.get("AGENT_RT_ROOT") or (Path.home() / ".agent-machines"))
    raw = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
    if not raw and (
        own_root.name == "agent-machines" and own_root.parent.name == "plugins"
        and own_root.parent.parent.parent.name == "marketplaces"
    ):
        raw = str(own_root / "install.json")
    own = None
    if raw:
        own = _peer_launch.validate_owner(
            "agent-machines",
            own_root, raw,
        )
    report = delivered_context(repository_root=root)
    if report is not None:
        return _checked_report(report, own)
    if own is None:
        return _legacy_report(root)
    peer = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
    if not peer.exists() and not peer.is_symlink():
        return None
    return _checked_report(read_owner_context(
        _peer_launch.launch_prefix("agent-machines", Path(own["pluginRoot"]), raw, "agent-worktrees"),
        root, required=True, environment=os.environ.copy(),
    ), own)


def eligible_values(package):
    if package.authority_declared:
        return {}, []
    values, contributors = {}, []
    for key, spec in sorted(package.manage.items()):
        if not (key == "copilot.settings" or key.startswith("copilot.settings.")):
            continue
        if spec.get("disposition") != "ensure-present" or {"authority", "owner"} & spec.keys():
            continue
        payload = spec.get("values", spec.get("value"))
        if not isinstance(payload, dict):
            continue
        for field in FIELDS:
            value = payload.get(field)
            if field not in payload or value is not None and not isinstance(value, (str, bool, int, float)):
                continue
            values.setdefault(field, value)
            contributors.append([str(package.source_path), key, field])
    return values, contributors


def prepare_defaults(packages, machine, accepted_machines=None):
    """Read native packages, compose default selection, leave package union intact."""
    from . import discover

    packages = [replace(package, ordinary_defaults=None) for package in packages]
    report = owner_report(Path.cwd())
    if report is None or not packages:
        return packages
    context = context_from_owner(report)
    if context["execution_machine"] != machine:
        # An explicit simulation/target selector is not this execution identity.
        return packages
    base_anchor = Path(context["base_root"]).resolve()
    knowledge = Path(context["knowledge_root"]).resolve() if context["binding"] == "bound" else None
    selected = []
    for package in packages:
        anchor = package.repo_anchor()
        if anchor is None:
            continue
        anchor = anchor.resolve()
        if anchor == base_anchor:
            selected.append((package, "repository", "base", package.repo_root() or anchor))
        elif knowledge is not None and anchor == knowledge:
            selected.append((package, "repository", "knowledge", package.repo_root() or anchor))
    if not selected:
        return packages
    # Native package discovery admitted an active working tree and its stable anchor.
    base_sources = [str(root) for _, _, origin, root in selected if origin == "base"]
    if base_sources:
        if len(set(base_sources)) != 1:
            raise ManifestError("ordinary defaults have ambiguous base source ownership")
        context["base_root"] = base_sources[0]
    selected.extend(
        (package, "machine", "local_machine", discover.user_package_root())
        for package in discover.user_scoped_packages(machine, accepted_machines)
    )
    local_root = report.get("repository_machine_root")
    if local_root and Path(local_root).is_dir():
        local = discover.packages_in_repo(
            Path(local_root), "repository-machine-defaults", machine,
            source_anchor=Path(local_root), accepted_machines=accepted_machines,
        )
        for package in local:
            if (
                package.authority_declared or package.modules or package.resources
                or package.aliases or package.exclude or package.bootstrap_floor
                or any(
                    not key.startswith("copilot.settings")
                    or spec.get("disposition") != "ensure-present"
                    or set(spec) - {"disposition", "values", "value"}
                    or not isinstance(spec.get("values", spec.get("value")), dict)
                    or set(spec.get("values", spec.get("value", {}))) - FIELDS
                    for key, spec in package.manage.items()
                )
            ):
                raise ManifestError("repository-machine defaults cannot change native hard constraints")
            selected.append((package, "repository_machine", "repository_machine", Path(local_root)))
    layers = [{"id": "machines-defaults", "tier": "defaults", "origin": "defaults", "values": {}}]
    contributors = []
    for index, (package, tier, origin, root) in enumerate(selected):
        resolved = resolve_for_machine(package, machine, accepted_machines)
        values, owners = eligible_values(resolved)
        if not values:
            continue
        layer = {
            "id": f"machines-{origin}-{index}", "tier": tier, "origin": origin, "values": values,
            "source": {"root": str(root.resolve()), "path": str(package.source_path.resolve().relative_to(root.resolve()))},
        }
        if tier in {"repository", "repository_machine"}:
            layer["repository_id"] = context["repository_id"]
        if tier in {"machine", "repository_machine"}:
            layer["execution_machine"] = context["execution_machine"]
            layer["installation_cell"] = context["installation_cell"]
        layers.append(layer)
        contributors.extend(owners)
    result = compose(context, layers)
    if result["values"]:
        packages[0].ordinary_defaults = {**result, "contributors": contributors}
    return packages


def selected_defaults(packages):
    packets = [package.ordinary_defaults for package in packages if package.ordinary_defaults]
    if not packets:
        return None
    if any(packet != packets[0] for packet in packets[1:]):
        raise ManifestError("ordinary defaults contain conflicting source contexts")
    return packets[0]

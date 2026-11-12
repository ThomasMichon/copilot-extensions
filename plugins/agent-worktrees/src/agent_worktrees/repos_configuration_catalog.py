"""Read-only native registration, route, binding and tracked-pair projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

import yaml

from . import config as cfg, installer, related, repos, state_root
from .repos_configuration import FUTURE_ONLY, compose_preference, target
from .repos_configuration_sources import ConfigurationError, defaults, fingerprint, identity, mapping, resource_name
from .repos_registry_reader import RepositoryResolutionError


@dataclass
class Inspection:
    routes: dict = field(default_factory=dict)
    projects: dict = field(default_factory=dict)
    binding: dict = field(default_factory=lambda: {"status": "unknown"})
    pair: dict = field(default_factory=lambda: {
        "status": "unsupported", "reason": "No explicit tracked workspace was supplied.",
    })
    findings: list = field(default_factory=list)
    readiness: dict = field(default_factory=lambda: {"ready": None, "code": "unavailable"})


def _validate_routes(raw: dict) -> None:
    def invalid() -> NoReturn:
        raise ConfigurationError("invalid-route-source", "A route source needs owner repair.")

    entries = raw.get("related", {})
    if not isinstance(entries, dict):
        invalid()
    for name, entry in entries.items():
        resource_name(name)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            invalid()
        locus = entry.get("locus")
        if locus is not None and not isinstance(locus, dict):
            invalid()
        preferred = (locus or {}).get("preferred", "")
        if not isinstance(preferred, str):
            invalid()
        kind, machine = related.parse_preferred(preferred)
        if kind and kind not in related.VALID_LOCUS_KINDS:
            invalid()
        if kind == "machine":
            identity(machine)
        delegate = entry.get("delegate")
        via = delegate.get("via") if isinstance(delegate, dict) else delegate
        if via is not None and not isinstance(via, str):
            invalid()
        via = related.normalize_delegate(via)
        if via and via not in related.VALID_DELEGATES:
            invalid()


def inspect_project(project: str, workspace: str) -> Inspection:
    result = Inspection()
    previous = cfg.active_project()
    cfg.set_active_project(project)
    try:
        result.projects = installer.read_projects_registry(strict=True).get("projects", {})
        if any(
            not isinstance(entry, dict)
            or ("base_repo" in entry and not isinstance(entry["base_repo"], bool))
            for entry in result.projects.values()
        ):
            raise ConfigurationError("invalid-project-entry", "A project registration needs owner repair.")
        config = cfg.load_project_config(project, include_control_plane_related_pr=False)
        anchor = config.default_repo.anchor
        checkout = workspace or anchor
        if workspace and not Path(workspace).is_absolute():
            raise ConfigurationError("invalid-workspace", "The workspace must be an explicit absolute path.")
        sources = state_root.config_source_anchors(config, base_anchor=anchor, cwd=checkout)
        anchors = [
            *related.installed_plugin_related_anchors(),
            *[
                related.config_contribution_anchor(
                    s.anchor, s.origin,
                    directory_form=getattr(s, "directory_form", False),
                    source_tier=getattr(s, "tier", None),
                    state_repo=getattr(s, "state_repo", ""),
                )
                for s in sources
            ],
        ]
        for source in sources:
            cfg._load_inrepo_config(str(source.anchor), strict=True)
        for source in anchors:
            paths = (
                (related.legacy_related_path(source),)
                if related._looks_like_plugin_anchor(Path(source)) else
                (related._repo_base_related_path(Path(source)),
                 related._repo_marketplace_overlay_related_path(Path(source)))
            )
            for path in paths:
                if path is None:
                    continue
                _validate_routes(mapping(path))
        result.routes = related.read_related_grafted(anchors).related
        root = state_root.resolve_state_root(config, cwd=checkout)
        result.readiness = state_root.coordination_readiness(config, cwd=checkout).as_dict()
        result.binding = {
            "status": "present" if root.bound and not root.error else "absent",
            "identity": resource_name(config.knowledge_repo, allow_empty=True),
            "state_repo": resource_name(root.repo, allow_empty=True),
            "source": root.source,
            "anchor": root.path,
            "writable_workspace": False,
            "reason": "A resolved state anchor is not permission to edit it.",
        }
        if workspace:
            pair = state_root.resolve_pair(config, cwd=workspace)
            if pair.current is not None and pair.current.repo != project:
                result.pair = {"status": "error", "reason": "Workspace belongs to a different project."}
            else:
                sibling = pair.sibling
                retired = any(
                    checkout is not None and checkout.status in ("finalized", "archived", "abandoned")
                    for checkout in (pair.current, sibling)
                )
                missing = pair.paired and any(
                    checkout is None or not Path(checkout.path).is_dir()
                    for checkout in (pair.current, sibling)
                )
                result.pair = {
                    "status": "stale" if pair.paired and (pair.error or missing) else
                    "present" if pair.paired else "absent",
                    "workspace": sibling.path if sibling else None,
                    "role": sibling.role if sibling else None,
                    "workspace_status": sibling.status if sibling else None,
                    "writable_workspace": bool(
                        pair.paired and not pair.error and not retired and not missing
                        and sibling and sibling.kind == "worktree"
                    ),
                    "reason": "Inspect the native tracked pair before writing personal state.",
                }
    except (ValueError, TypeError, AttributeError, KeyError, OSError, RuntimeError,
            RepositoryResolutionError, yaml.YAMLError):
        result.findings.append({
            "code": "native-inspection-unavailable", "severity": "error",
            "message": "Project composition or native inspection needs owner repair.",
        })
    finally:
        cfg.set_active_project(previous)
    return result


def catalog(project: str, *, workspace: str = "") -> dict:
    from .repos_knowledge_configuration import binding_names_supported, owner_status
    from .repos_knowledge_default import observe

    registry = repos.read_registry(strict=True)
    inspection = inspect_project(project, workspace)
    binding_owner = owner_status(project)
    try:
        preferred = observe(project)
    except (ValueError, TypeError, OSError) as error:
        preferred = {
            "status": "unavailable",
            "code": error.code if isinstance(error, ConfigurationError) else "preference-input-unavailable",
            "message": str(error) if isinstance(error, ConfigurationError) else
            "Accepted preference inputs need owner inspection.",
        }
    composition_error = None
    try:
        fingerprint([repos._repos_yaml_path()])
        inherited = defaults(project)
    except (ValueError, OSError) as exc:
        composition_error = exc
        inherited = None
    names = sorted({project, *registry.repos, *inspection.routes})
    rows = []
    for name in names:
        name_error = None
        try:
            resource_name(name)
        except ConfigurationError as exc:
            # A registered name that native `add_repo()` accepts but this
            # shared validator rejects (e.g. longer than the login length
            # cap) must not abort the whole catalog -- report it as an
            # unsupported row like any other composition failure instead.
            name_error = exc
        entry = registry.repos.get(name)
        binding_names = binding_names_supported(project, name)
        local_path = entry.local_path() if entry else None
        registration = (
            "absent" if entry is None else "unsupported" if not local_path else
            "present" if Path(local_path).is_dir() else "stale"
        )
        findings = list(inspection.findings)
        try:
            if name_error is not None:
                raise name_error
            if composition_error is not None:
                raise composition_error
            if inherited is None:
                raise ConfigurationError("invalid-composition", "Native preference sources are unavailable.")
            state = compose_preference(project, name, registry, inherited)
            editable = True
        except (ValueError, OSError) as exc:
            state = {"target": target(project, name), "effective": "unknown", "sources": []}
            editable = False
            findings.append({
                "code": exc.code if isinstance(exc, ConfigurationError) else "invalid-composition",
                "severity": "error",
                "message": str(exc) if isinstance(exc, ConfigurationError) else
                "Native composition is unavailable; repair it with its owner.",
            })
        route_entry = inspection.routes.get(name)
        route = {"status": "absent", "editable": False}
        if inspection.findings:
            route["status"] = "unknown"
        elif route_entry:
            resolved = related.build_resolution(
                route_entry, current_machine=cfg.detect_machine(),
                repo_class=entry.repo_class if entry else None,
                repo_path=local_path, adopted=name in inspection.projects,
                base_repo=bool(inspection.projects.get(name, {}).get("base_repo", False)),
            )
            route.update(
                status="present", preferred=resolved.locus_kind,
                target_machine=resolved.target_machine,
                available_here=resolved.available_here,
                delegate=resolved.delegate_via, source=route_entry.origin_layer,
            )
        state.update(
            registration={"status": registration, "class": entry.repo_class if entry else None},
            participation="related" if route_entry else "not-declared",
            same_machine_agent="enabled" if entry and entry.agent else "disabled",
            plugin_enablement="unsupported: owned by the plugin activation provider",
            route=route, binding=inspection.binding, pair=inspection.pair, findings=findings,
            knowledge_preference={key: value for key, value in preferred.items() if key != "selection"},
            unsupported_operations={
                "route": "No revisioned local route writer is exposed by the owner.",
                "pair": "Binding and pairing require their canonical owner workflow.",
                "plugin_enablement": "No plugin enablement writer is exposed by this section.",
            },
        )
        body = "\n".join([
            f"Repository: {name}; selected project: {project}",
            f"Registration: {registration}; participation: {state['participation']}",
            f"Same-machine agent: {state['same_machine_agent']}; plugin enablement: owner-specific",
            f"Copilot preference: {state['effective']}; observed running identity: unknown",
            f"Identity-switch policy: {state.get('switch_enabled', 'unknown')} (separate from preference/auth)",
            f"Route: {route}; editing unsupported",
            f"Bound state identity: {inspection.binding}",
            f"Actual tracked pair: {inspection.pair}",
            f"Accepted knowledge default: {state['knowledge_preference']}",
            "Binding identifiers: " + ("supported" if binding_names else
                "unsupported; exact Configuration names starting with an ASCII letter or digit are required."),
            "Sources (higher precedence last): " + str(state["sources"]),
            "Findings: " + str(findings),
            FUTURE_ONLY,
            "All repository/knowledge/plugin/preset sources are read-only.",
        ])
        rows.append({
            "id": name, "title": name, "context_project": project, "repository": name,
            "registration": registration, "participation": state["participation"],
            "effective": state["effective"], "provenance": "local override" if state.get("override") else "inherited",
            "editable": str(editable).lower(), "resettable": str(editable and bool(state.get("override"))).lower(),
            "configuration": state, "card": {"title": f"{name} configuration", "body": body},
            "fields": [{
                "name": "login", "type": "text", "label": "Non-secret Copilot login",
                "default": state.get("override", ""), "required": True,
            }] if editable else [],
            "reset_fields": [],
            "knowledge_bindable": str(
                binding_names and not inspection.findings
                and inspection.readiness.get("ready") is False
                and inspection.binding.get("identity") in ("", name)
                and entry is not None and entry.repo_class in {"knowledge", "worktree"}
                and name != project and registration == "present"
                and binding_owner == "available"
            ).lower(),
            "binding_fields": [],
            "preferred_knowledge_bindable": str(
                preferred.get("status") == "suggested"
                and preferred["resolution"]["knowledge"]["repository"] == name
                and binding_names and not inspection.findings and binding_owner == "available"
                and inspection.readiness.get("ready") is False
            ).lower(),
            "harness_repository_id": preferred.get("resolution", {}).get("harness", {}).get("repository_id", ""),
            "knowledge_repository_id": preferred.get("resolution", {}).get("knowledge", {}).get("repository_id", ""),
        })
    summary = {
        "repositories": len(rows), "knowledge_readiness": inspection.readiness,
        "binding_owner": binding_owner,
        "knowledge_preference": {key: value for key, value in preferred.items() if key != "selection"},
    }
    if binding_owner != "available" and inspection.readiness.get("ready") is False:
        summary.update(
            banner_text="Knowledge binding is unavailable. Enable and prepare one qualified harness-knowledge owner.",
            banner_level="warn",
        )
    from .repos_knowledge_registration import action_row, eligible

    canonical_harness = (
        composition_error is None and project in registry.repos
        and registry.repos[project].repo_class == "worktree"
        and binding_names_supported(project, project)
    )
    if canonical_harness:
        from .repos_knowledge_creation import action_rows

        rows[0:0] = action_rows(project, inspection)
    if (
        canonical_harness and eligible(inspection)
    ):
        from .repos_knowledge_acquisition import action_row as acquisition_row

        rows[0:0] = [acquisition_row(project), action_row(project)]
    return {"entries": rows, "summary": summary}

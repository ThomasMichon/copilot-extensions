"""Resolve portable relationships through native registration and binding owners."""

from __future__ import annotations

from collections import Counter
import hashlib
import hmac
import json
from pathlib import Path
import re

from dropin_registry import ScanAuthority

from . import config as cfg, config_acquisition, project_state, repos, state_root
from .config_sources import KnowledgeBindingError
from .repos_configuration_sources import ConfigurationError, resource_name

SCHEMA = "agent-worktrees.knowledge-preference"


def _binding_config(project: str):
    machine_path = cfg.project_dir(project) / "config.yaml"
    yaml_sources = {str(cfg.global_config_path()), str(machine_path)}
    dropins = machine_path.parent / "config.d"
    try:
        with config_acquisition.capture_config_observations() as observations:
            config = cfg.load_project_config(project, include_control_plane_related_pr=False)
    except KnowledgeBindingError as error:
        raise ConfigurationError("binding-unresolved", "The explicit local binding needs owner repair.") from error
    for observation in observations:
        binding_source = (
            observation.stage == "config-yaml" and observation.source in yaml_sources
            or observation.stage == "config-dropins" and observation.source == str(dropins)
            or observation.stage == "config-dropin-entry" and Path(observation.source).parent == dropins
        )
        if binding_source and observation.authority is ScanAuthority.INDETERMINATE:
            raise ConfigurationError("binding-unresolved", "The local binding sources need owner repair.")
    return config


def identities() -> dict:
    registry = repos.read_registry(strict=True)
    entries = []
    for name, entry in sorted(registry.repos.items()):
        try:
            identity = project_state.canonical_repository_id(name, remote=entry.remote, portable=True)
        except ValueError:
            identity = None
        path = entry.local_path()
        entries.append({
            "repository": name, "repository_id": identity, "class": entry.repo_class,
            "account": repos.resolve_account(entry, registry=registry),
            "default_branch": entry.default_branch,
            "status": "local-only" if identity is None else
                      "registered" if path and Path(path).is_dir() else "unavailable",
        })
    counts = Counter(entry["repository_id"] for entry in entries)
    for entry in entries:
        if entry["repository_id"] is not None and counts[entry["repository_id"]] > 1:
            entry["status"] = "ambiguous"
    return {
        "schema": "agent-worktrees.repository-identities", "version": 1,
        "capabilities": {"knowledge_preference": 1},
        "registry_revision": registry._baseline[1], "entries": entries,
    }


def _selected(catalog: dict, identity: str) -> dict:
    if not isinstance(identity, str) or re.fullmatch(r"repo-[a-f0-9]{24}", identity) is None:
        raise ConfigurationError("invalid-repository-id", "Use an exact native portable repository identity.")
    matches = [entry for entry in catalog["entries"] if entry["repository_id"] == identity]
    if len(matches) != 1:
        raise ConfigurationError(
            "repository-id-unresolved", "The portable identity needs exactly one native registration.",
        )
    entry = matches[0]
    if entry["status"] != "registered" or entry["class"] not in {"worktree", "knowledge"}:
        raise ConfigurationError(
            "repository-not-ready", "The selected registration must be available and worktree-capable.",
        )
    return entry


def resolve(project: str, harness_id: str, knowledge_id: str) -> dict:
    project = resource_name(project)
    catalog = identities()
    harness, knowledge = _selected(catalog, harness_id), _selected(catalog, knowledge_id)
    if harness["repository"] != project or harness["class"] != "worktree":
        raise ConfigurationError("context-mismatch", "The harness identity must match the selected native project.")
    if harness_id == knowledge_id:
        raise ConfigurationError("same-repository", "Harness and knowledge must be distinct native repositories.")
    config = _binding_config(project)
    if not isinstance(config.knowledge_repo, str):
        raise ConfigurationError("binding-unresolved", "The explicit local binding needs owner repair.")
    binding_name = config.knowledge_repo.strip()
    binding = {"status": "absent"}
    if binding_name:
        current = state_root.resolve_state_root(
            config, repo_override=binding_name, cwd=config.default_repo.anchor,
        )
        if not current.bound or not current.path or current.error:
            raise ConfigurationError("binding-unresolved", "The explicit local binding needs owner repair.")
        binding = {
            "status": "present", "repository": binding_name,
            "repository_id": next(
                (entry["repository_id"] for entry in catalog["entries"]
                 if entry["repository"] == binding_name), None,
            ),
        }
    return {
        "schema": SCHEMA, "version": 1, "harness": harness, "knowledge": knowledge,
        "execution_machine": config.machine, "registry_revision": catalog["registry_revision"],
        "binding": binding,
        "status": "already-bound" if binding.get("repository") == knowledge["repository"] else
                  "binding-preserved" if binding["status"] == "present" else "suggested",
        "binding_changed": False,
    }


def preview(
    project: str, harness_id: str, knowledge_id: str, *, require_preference_freshness: bool = False,
) -> dict:
    from .repos_knowledge_configuration import invoke

    resolution = resolve(project, harness_id, knowledge_id)
    if resolution["status"] != "suggested":
        return {**resolution, "token": None, "native_preview": None}
    native = invoke(
        project, resolution["knowledge"]["repository"], "preview",
        **({"require_preference_freshness": True} if require_preference_freshness else {}),
    )
    native_token = native.get("token")
    if not isinstance(native_token, str) or re.fullmatch(r"[a-f0-9]{64}", native_token) is None:
        raise ConfigurationError("binding-owner-unsupported", "The native binding preview needs a supported token.")
    if resolve(project, harness_id, knowledge_id) != resolution:
        raise ConfigurationError("preview-conflict", "Registration or binding changed; preview again.")
    token = hashlib.sha256(json.dumps({
        "schema": SCHEMA, "version": 1, "resolution": resolution, "native_token": native_token,
    }, sort_keys=True).encode()).hexdigest()
    return {**resolution, "token": token, "native_preview": native}


def apply(project: str, harness_id: str, knowledge_id: str, token: str) -> dict:
    from .repos_knowledge_configuration import invoke

    if not isinstance(token, str) or re.fullmatch(r"[a-f0-9]{64}", token) is None:
        raise ConfigurationError("missing-preview", "A fresh portable relationship preview is required.")
    proposed = preview(project, harness_id, knowledge_id)
    if not proposed["token"] or not hmac.compare_digest(token, proposed["token"]):
        raise ConfigurationError("preview-conflict", "Registration or binding changed; preview again.")
    result = invoke(
        project, proposed["knowledge"]["repository"], "apply", proposed["native_preview"]["token"],
        portable_resolution={key: value for key, value in proposed.items() if key not in {"token", "native_preview"}},
    )
    return {
        **result, "preference_schema": SCHEMA, "preference_version": 1,
        "harness_repository_id": harness_id, "knowledge_repository_id": knowledge_id,
    }

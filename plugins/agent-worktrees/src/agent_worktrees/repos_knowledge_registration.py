"""Confirmed registration of an existing local knowledge anchor, without binding."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import urlsplit

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned
import yaml

from . import config as cfg, inrepo_config_source, project_state, repos
from .git_repository_environment import repository_identity_env
from .repos_configuration import target
from .repos_configuration_sources import (
    ConfigurationError, defaults, fingerprint, identity, mapping, resource_name,
)
from .repos_registry_transaction import RegistryConflictError, registry_lock, write_checked


def action_target(project: str) -> dict[str, str]:
    return target(project, "knowledge-registration")


def eligible(inspection) -> bool:
    return (
        not inspection.findings
        and inspection.readiness.get("ready") is False
        and inspection.readiness.get("code") == "knowledge_binding_required"
        and inspection.binding.get("identity") == ""
    )


def _git(path: Path, arguments: list[str], budget: OwnedExecutionBudget, *, absent=False) -> str:
    executable = shutil.which("git")
    if not executable:
        raise ConfigurationError("git-unavailable", "Git is required to inspect the local checkout.")
    try:
        result = run_caller_owned(
            [executable, "-C", str(path), *arguments], timeout=4,
            max_stdout_bytes=16384, max_stderr_bytes=2048,
            env=repository_identity_env(), budget=budget,
        )
    except OwnedCleanupError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise ConfigurationError("checkout-inspection-failed", "Local Git inspection did not complete.") from error
    if result.returncode and not (absent and result.returncode == 1):
        raise ConfigurationError("invalid-checkout", "Select an existing Git anchor and valid branch metadata.")
    return result.stdout.strip()


def _checkout(path: str, branch: str, budget: OwnedExecutionBudget) -> dict:
    supplied = Path(path)
    if not supplied.is_absolute():
        raise ConfigurationError("invalid-checkout", "Select an absolute existing local checkout path.")
    root = supplied.resolve(strict=True)
    if not root.is_dir():
        raise ConfigurationError("invalid-checkout", "Select an existing local checkout directory.")
    if root.name.casefold() == ".git":
        raise ConfigurationError("invalid-checkout", "Select the repository anchor, not its Git metadata directory.")
    values = _git(root, [
        "rev-parse", "--path-format=absolute", "--absolute-git-dir", "--git-common-dir", "--is-bare-repository",
    ], budget).splitlines()
    if len(values) != 3 or values[2] not in {"true", "false"}:
        raise ConfigurationError("invalid-checkout", "The local Git root could not be established.")
    reported = Path(values[0]).resolve()
    metadata = root / ".git" if reported == (root / ".git").resolve() else root
    if (
        not metadata.is_dir() or project_state._is_link_or_reparse(metadata)
        or reported != metadata.resolve() or metadata == root and values[2] != "true"
        or Path(values[1]).resolve() != metadata.resolve()
    ):
        raise ConfigurationError("invalid-checkout", "Select the repository anchor, not a task worktree or descendant.")
    if not branch or branch != branch.strip() or branch.startswith("-"):
        raise ConfigurationError("invalid-branch", "Supply the intended default branch as registration metadata.")
    if _git(root, ["check-ref-format", "--branch", branch], budget) != branch:
        raise ConfigurationError("invalid-branch", "Supply a literal default branch, not a checkout shorthand.")
    origins = _git(root, ["config", "--includes", "--get-all", "remote.origin.url"], budget, absent=True).splitlines()
    if len(origins) > 1:
        raise ConfigurationError("ambiguous-origin", "The checkout has more than one origin URL.")
    remote = _git(root, ["remote", "get-url", "origin"], budget) if origins else ""
    parsed = urlsplit(remote)
    if parsed.password or parsed.query or parsed.fragment or (
        parsed.scheme.casefold() in {"http", "https"} and (parsed.username or parsed.password)
    ):
        raise ConfigurationError("unsafe-origin", "Use an origin without embedded credentials or URL parameters.")
    try:
        repository_id = project_state.canonical_repository_id("", remote=remote) if remote else ""
    except ValueError as error:
        raise ConfigurationError("invalid-origin", "The origin needs a stable native repository identity.") from error
    return {
        "path": str(root), "remote": remote, "repository_id": repository_id,
        "root_identity": [root.stat().st_dev, root.stat().st_ino],
        "git_identity": [metadata.stat().st_dev, metadata.stat().st_ino],
    }


def _selection(project: str, name: str, path: str, branch: str, account: str, budget) -> tuple[dict, repos.ReposRegistry]:
    from .repos_configuration_catalog import inspect_project
    from .repos_knowledge_configuration import binding_names_supported

    if (
        project != resource_name(project) or name != resource_name(name)
        or not binding_names_supported(project, name) or name == project
    ):
        raise ConfigurationError("invalid-registration-name", "Choose distinct exact harness and knowledge names.")
    fingerprint([repos._repos_yaml_path()])
    registry = repos.read_registry(strict=True)
    if project not in registry.repos:
        raise ConfigurationError("context-not-registered", "The selected harness is not canonically registered.")
    if registry.repos[project].repo_class != "worktree":
        raise ConfigurationError("context-not-worktree", "Knowledge registration requires a worktree-class harness.")
    inspection = inspect_project(project, "")
    if not eligible(inspection):
        raise ConfigurationError("knowledge-registration-unavailable", "Registration requires an unbound stateless harness.")
    config = cfg.load_project_config(project, include_control_plane_related_pr=False)
    selected = _checkout(path, branch, budget)
    root = Path(selected["path"])
    checkout_paths = {root, (root / ".git").resolve()}
    if Path(config.default_repo.anchor).resolve() in checkout_paths:
        raise ConfigurationError("registration-conflict", "The harness cannot be its own knowledge repository.")
    for existing_name, entry in registry.repos.items():
        if existing_name.casefold() == name.casefold() or (
            entry.local_path() and Path(entry.local_path()).resolve() in checkout_paths
        ):
            raise ConfigurationError("registration-conflict", "The name or checkout is already registered; inspect its existing entry.")
        if selected["repository_id"] and entry.remote:
            try:
                existing_id = project_state.canonical_repository_id(existing_name, remote=entry.remote)
            except ValueError as error:
                raise ConfigurationError("registry-identity-unavailable", "A registered origin needs owner inspection.") from error
            if existing_id == selected["repository_id"]:
                raise ConfigurationError("registration-conflict", "The repository identity is already registered.")
    policy = cfg._load_inrepo_config(config.default_repo.anchor, strict=True)
    selected.update(
        name=name, default_branch=branch, account=identity(account, allow_empty=True),
        target=action_target(project), registry_revision=registry._baseline,
        configuration_revision=defaults(project).revision,
        policy_revision=hashlib.sha256(yaml.safe_dump(policy, sort_keys=True).encode()).hexdigest(),
    )
    if time.monotonic() >= budget.execution_deadline:
        raise TimeoutError("Local registration inspection budget expired.")
    return selected, registry


def _token(selected: dict) -> str:
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def preview(project: str, name: str, path: str, branch: str, account: str = "") -> dict:
    budget = OwnedExecutionBudget.from_timeout(18, cleanup_reserve=2)
    with inrepo_config_source.resolution_budget(deadline=budget.execution_deadline):
        selected, _ = _selection(project, name, path, branch, account, budget)
    return {
        "version": 1, "token": _token(selected), "target": selected["target"],
        "scope": "machine-local", "operation": "set",
        "summary": "Register the existing local knowledge checkout",
        "effects": [
            f"Register {name} at {selected['path']} as knowledge, without an agent.",
            f"Default-branch metadata: {branch}; account metadata: {selected['account'] or '(native inheritance)'}.",
            f"Origin metadata: {selected['remote'] or '(local-only)'}. Provider access and policy are unobserved.",
            "Write only the new native registry entry; preserve every existing registration.",
            "No clone, repository creation, credential pin, authentication, Git/source write, binding, pair or launch.",
            "Refresh, then use the separate confirmed binding action. Registration does not make New ready.",
        ],
        "findings": [{
            "code": "registration-only", "severity": "info",
            "message": "Provider access, server landing policy and writable pairing are not observed.",
        }],
    }


def apply(project: str, name: str, path: str, branch: str, account: str, token: str) -> dict:
    from . import guarded_anchors

    if not re.fullmatch(r"[a-f0-9]{64}", token):
        raise ConfigurationError("missing-preview", "A fresh local-registration preview is required.")
    budget = OwnedExecutionBudget.from_timeout(26, cleanup_reserve=2)
    attempted = committed = False
    try:
        with (
            inrepo_config_source.resolution_budget(deadline=budget.execution_deadline),
            registry_lock(repos._repos_yaml_path(), budget=budget),
        ):
            selected, registry = _selection(project, name, path, branch, account, budget)
            if not hmac.compare_digest(token, _token(selected)):
                raise ConfigurationError("preview-conflict", "Registration inputs changed; refresh and preview again.")
            raw = mapping(repos._repos_yaml_path())
            raw["repos"] = dict(raw.get("repos", {}))
            registration = {
                "class": "knowledge", "agent": False,
                repos._current_platform(): selected["path"], "default_branch": branch,
            }
            if selected["remote"]:
                registration["remote"] = selected["remote"]
            if selected["account"]:
                registration["account"] = selected["account"]
            raw["repos"][name] = registration

            def write():
                nonlocal attempted, committed
                current, _ = _selection(project, name, path, branch, account, budget)
                if not hmac.compare_digest(token, _token(current)):
                    raise ConfigurationError("preview-conflict", "Registration inputs changed; refresh and preview again.")
                fingerprint([repos._repos_yaml_path()])
                attempted = True
                write_checked(
                    repos._repos_yaml_path(), registry,
                    yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), budget=budget,
                )
                committed = True

            guarded_anchors.managed_write(write, budget=budget)
    except RegistryConflictError:
        raise
    except (guarded_anchors.GuardPublicationError, OSError, RuntimeError) as error:
        if not attempted:
            if isinstance(error, guarded_anchors.GuardPublicationError):
                raise ConfigurationError(
                    "guard-publication-unavailable", "Registration did not start; inspect its guard-publication owner.",
                ) from error
            raise
        return {
            "status": "incomplete", "operation": "set", "target": action_target(project),
            "registration_committed": True if committed else None,
            "guard_publication": "incomplete",
            "message": "Registration or guard publication is incomplete; inspect the catalog before retrying.",
        }
    return {
        "status": "persisted", "operation": "set", "target": selected["target"],
        "registration": {"name": name, **registration}, "binding_changed": False,
        "guard_publication": "complete",
        "message": "Registered only. Refresh and use the separately confirmed knowledge-binding action.",
    }


def action_row(project: str) -> dict:
    return {
        "id": "action:knowledge-registration", "title": "Register a local knowledge checkout",
        "context_project": project, "registration": "setup action", "participation": "not bound",
        "effective": "Choose an existing checkout", "provenance": "native owner",
        "editable": "false", "resettable": "false", "knowledge_bindable": "false",
        "preferred_knowledge_bindable": "false",
        "knowledge_registration_available": "true", "configuration": {"target": action_target(project)},
        "card": {
            "title": "Register, then bind an existing local knowledge checkout",
            "body": "Select an existing Git anchor. Registration is local metadata only; binding is a separate confirmation.",
        },
        "knowledge_registration_fields": [
            {"name": "name", "type": "text", "label": "Knowledge repository name", "required": True},
            {"name": "path", "type": "text", "label": "Absolute existing checkout path", "required": True},
            {"name": "branch", "type": "text", "label": "Intended default branch (metadata)", "required": True},
            {"name": "account", "type": "text", "label": "Optional non-secret repository account", "default": ""},
        ],
    }

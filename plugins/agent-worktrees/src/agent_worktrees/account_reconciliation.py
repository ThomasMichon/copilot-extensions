"""Project portable operator account slots into missing local choices."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from . import repos
from . import account_projection as projection
from .repos_configuration_sources import identity

SCHEMA = "agent-worktrees.account-reconciliation"
POLICY_FILE = Path(".agent-worktrees") / "github-accounts.json"
_LIMIT = 64 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Account policy contains duplicate keys")
        result[key] = value
    return result


def _slot(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", value):
        raise ValueError("Account slot must be a short lowercase name")
    return value


def _repository(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value,
    ) or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("Account policy repository must be owner/name")
    return value.casefold()


def _read(path):
    try:
        with path.open("rb") as stream:
            raw = stream.read(_LIMIT + 1)
    except FileNotFoundError:
        return None
    if len(raw) > _LIMIT:
        raise ValueError("Portable account policy exceeds its byte limit")
    return raw


def _policy(raw):
    if raw is None:
        return {}, {}
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique)
    if (
        not isinstance(value, dict) or set(value) - {"version", "accounts", "repositories"}
        or type(value.get("version")) is not int or value["version"] != 1
    ):
        raise ValueError("Unsupported portable account policy")
    accounts, repositories = value.get("accounts", {}), value.get("repositories", {})
    if (
        not isinstance(accounts, dict) or len(accounts) > 32
        or not isinstance(repositories, dict) or len(repositories) > 128
    ):
        raise ValueError("Invalid portable account policy entries")
    slots = {_slot(slot): identity(login) for slot, login in accounts.items()}
    targets = {}
    for repository, slot in repositories.items():
        target = _repository(repository)
        if target in targets:
            raise ValueError("Account policy repeats a repository")
        targets[target] = _slot(slot)
    return slots, targets


def reconciliation_context(project):
    from . import config, config_acquisition, config_sources

    if not project:
        from .invocation_project import _resolve_active_project

        project = config.active_project() or _resolve_active_project(None)[0]
    if not project:
        raise ValueError("Account reconciliation requires a registered context project")
    with config_acquisition.capture_config_observations() as observations:
        selected = config.load_config(
            config.project_dir(project) / "config.yaml",
            project=project, include_control_plane_related_pr=False,
            include_knowledge_preferences=False, strict_inrepo_config=True,
        )
        if any(
            item.stage in {"config-yaml", "config-dropins", "config-dropin-entry", "config-migration"}
            and item.authority.value == "indeterminate"
            for item in observations
        ):
            raise ValueError("Account binding configuration is unavailable")
        source = config_sources.knowledge_config_source(
            selected, base_anchor=selected.default_repo.anchor,
        )
    return (Path(source.anchor) / POLICY_FILE if source else None), selected.repo_name


def knowledge_policy_path(project):
    return reconciliation_context(project)[0]


def _configured_identity(registry, target):
    """Bootstrap from an existing choice, never the namespace or ambient login."""
    entry = registry.repos.get(target)
    if entry is not None:
        target = repos.github_slug(entry.remote)
        if not target:
            return None
    target = _repository(target)
    if entry is None:
        entry = next((
            candidate for candidate in registry.repos.values()
            if (repos.github_slug(candidate.remote) or "").casefold() == target
        ), None)
    if entry is not None and entry.account:
        return identity(entry.account)
    owner = target.split("/", 1)[0]
    return repos.account_from_map(owner, registry=registry) or None


def reconcile(
    *, default_slot, identity_source, repository_slots, path, apply=False,
    owner="account-reconcile",
):
    registry = repos.read_registry(strict=True)
    raw = _read(path) if path is not None else None
    slots, targets = _policy(raw)
    requested = {}
    for repository, slot in repository_slots:
        target, slot = _repository(repository), _slot(slot)
        if target in requested and requested[target] != slot:
            raise ValueError("Conflicting repository slot arguments")
        requested[target] = slot
        if target in targets and targets[target] != slot:
            raise ValueError("Repository slot argument conflicts with portable policy")
        targets[target] = slot
    default_slot = _slot(default_slot)
    before = projection.snapshot(registry)
    projection.prepare(registry, owner)
    missing, conflicts, observations = set(), [], []
    selected = slots.get(default_slot)
    source = "knowledge"
    if not selected and identity_source:
        selected = _configured_identity(registry, identity_source)
        source = "configured-repository"
    current = registry.default_account
    changed = False
    if current:
        default = {"slot": default_slot, "account": current, "source": "local", "state": "preserved"}
        if selected and current.casefold() != selected.casefold():
            conflicts.append({"target": "default", "slot": default_slot, "state": "local-choice-preserved"})
    elif selected:
        registry.default_account = identity(selected)
        projection.record(registry, owner, "default", registry.default_account)
        default = {"slot": default_slot, "account": selected, "source": source, "state": "projected"}
        changed = True
    else:
        default = {"slot": default_slot, "account": None, "source": None, "state": "missing"}
        missing.add(default_slot)
    entries = {}
    for entry in registry.repos.values():
        slug = repos.github_slug(entry.remote)
        if slug:
            entries.setdefault(slug.casefold(), []).append(entry)
    for target, slot in sorted(targets.items()):
        candidates = entries.get(target, [])
        login = slots.get(slot)
        row = {"repository": target, "slot": slot, "account": login, "state": "missing-slot"}
        if not login:
            missing.add(slot)
        elif not candidates:
            row["state"] = "not-registered"
        else:
            row["state"] = "preserved"
            for entry in candidates:
                existing = entry.account or repos.account_from_map(target.split("/", 1)[0], registry=registry)
                if existing:
                    if existing.casefold() != login.casefold():
                        conflict = {"target": target, "slot": slot, "state": "local-choice-preserved"}
                        if conflict not in conflicts:
                            conflicts.append(conflict)
                else:
                    entry.account = login
                    projection.record(registry, owner, "repo:" + entry.name, login)
                    row["state"] = "projected"
        observations.append(row)
    changed = before != projection.snapshot(registry)
    if apply and changed:
        if path is not None and _read(path) != raw:
            raise ValueError("Portable account policy changed before projection")
        repos.write_registry(registry)
    return {
        "schema": SCHEMA, "version": 1,
        "status": "partial" if missing or conflicts or any(
            row["state"] == "not-registered" for row in observations
        ) else "ready",
        "applied": bool(apply and changed), "changes_pending": bool(changed and not apply),
        "policy": "knowledge" if raw is not None else "absent",
        "default": default, "repositories": observations,
        "missing_slots": sorted(missing), "conflicts": conflicts,
    }


def command(args):
    from . import output

    parser = argparse.ArgumentParser(
        prog="repos account reconcile", allow_abbrev=False,
        description="Project missing local account choices from portable knowledge slots; never log in or switch accounts.",
    )
    parser.add_argument("--capabilities", action="store_true")
    parser.add_argument("--context-project")
    parser.add_argument("--default-slot")
    bootstrap = parser.add_mutually_exclusive_group()
    bootstrap.add_argument("--identity-source")
    bootstrap.add_argument("--bootstrap-from-context", action="store_true")
    parser.add_argument("--repository-slot", action="append", default=[], metavar="OWNER/REPO=SLOT")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(args)
    if options.capabilities:
        if options.context_project or options.default_slot or options.identity_source or options.bootstrap_from_context or options.repository_slot or options.apply:
            parser.error("--capabilities cannot be combined with projection options")
        output._json_output({"schema": SCHEMA, "version": 1, "capabilities": {"portable_account_slots": 1}})
        return 0
    if not options.default_slot:
        parser.error("--default-slot is required; --context-project selects a project explicitly")
    try:
        targets = []
        for value in options.repository_slot:
            if value.count("=") != 1:
                raise ValueError("Repository slot must be owner/name=slot")
            targets.append(tuple(value.split("=", 1)))
        path, project = reconciliation_context(options.context_project)
        result = reconcile(
            default_slot=options.default_slot,
            identity_source=project if options.bootstrap_from_context else options.identity_source,
            repository_slots=targets, path=path, apply=options.apply,
            owner=project,
        )
    except (OSError, RuntimeError, ValueError, UnicodeError) as error:
        if options.json:
            return output._json_error(
                f"Account reconciliation unavailable ({type(error).__name__}); no account repair was confirmed.",
                exit_code=2,
            )
        output.err("Account reconciliation unavailable; inspect the bound knowledge policy and repository registry.")
        return 2
    if options.json:
        output._json_output(result)
    else:
        print(f"Account reconciliation: {result['status']}")
        print(f"Default slot {options.default_slot}: {result['default']['account'] or '(missing)'}")
        if result["missing_slots"]:
            print("Configure missing slots in the bound knowledge repository's .agent-worktrees/github-accounts.json: "
                  + ", ".join(result["missing_slots"]))
        if result["conflicts"]:
            print("Existing local choices were preserved; review conflicts before changing them.")
    return 0

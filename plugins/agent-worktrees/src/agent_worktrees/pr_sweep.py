"""Read-only PR inventories, local claim observations and their pure join."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import yaml

from . import claims_find_cli, claims_rank, claims_transitive_cli
from . import config as cfg, installer, machine_identity, pr_config, providers, repo_accounts, tracking
from . import legacy_wsl_record_locality
from .providers.base import ProviderError

SCHEMA = "copilot-extensions.repository-pr-sweep"
VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pr_identity(url: str) -> tuple[str, str, int] | None:
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme not in ("https", "http") or parsed.username or parsed.password:
        return None
    parts = parsed.path.strip("/").split("/")
    for index, part in enumerate(parts):
        if part.lower() not in ("pull", "pulls", "pullrequest", "pull-requests"):
            continue
        if index < 2 or index + 2 != len(parts) or not parts[-1].isdigit():
            continue
        try:
            number = int(parts[-1])
            slug = claims_find_cli._pr_repo(SimpleNamespace(repo="", url=url))
        except ValueError:
            return None
        azure = index >= 3 and parts[index - 2] == "_git" and part.lower() == "pullrequest"
        prefix = "/".join(parts[:index - (3 if azure else 2)])
        authority = claims_find_cli.canonical_authority(
            parsed.netloc + ("/" + prefix if prefix else "")
        )
        if authority and slug and number > 0 and not parsed.query and not parsed.fragment:
            return authority, slug.lower(), number
    return None


def collect_local(repo: str, config: cfg.Config) -> dict:
    read_started = utc_now()
    registry = installer.read_projects_registry(strict=True)
    projects = registry.get("projects") if isinstance(registry, dict) else None
    if not isinstance(projects, dict):
        raise ValueError("The project registry has no projects mapping")
    claims: list[dict] = []
    roots: list[dict] = []
    errors: list[dict] = []
    records: dict[tuple[str, str, str], tracking.WorktreeRecord] = {}
    record_paths = {}
    local: list[tuple[str, tracking.WorktreeRecord]] = []
    local_machines: dict[tuple[str, str], bool] = {}
    project_configs = {config.repo_name: config}
    authorities = {}
    for project in sorted(projects):
        try:
            directory = cfg.project_dir(project) / "worktrees"
            if not directory.is_dir():
                raise ValueError("its tracking directory is unavailable")
            files = sorted(directory.glob("*.yaml"))
        except (OSError, ValueError, RuntimeError) as error:
            errors.append({"project": project, "error": str(error)})
            continue
        for path in files:
            try:
                record = tracking.load_record(path)
            except (OSError, ValueError, RuntimeError, TypeError, KeyError, yaml.YAMLError) as error:
                errors.append({"project": project, "record": path.name, "error": str(error)})
                continue
            machine = record.execution_machine
            try:
                if project not in project_configs:
                    project_configs[project] = cfg.load_project_config(
                        project, include_control_plane_related_pr=False,
                    )
                receiving = project_configs[project]
                identity = (project, machine)
                if identity not in local_machines:
                    local_machines[identity] = machine_identity.is_local_machine(machine, receiving)
                admitted = record.platform == config.platform and (
                    local_machines[identity] or
                    legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(
                        tracking.parse_claim_ref(tracking.format_claim_ref(machine, project, record.worktree_id)),
                        receiving,
                    ) == path
                )
            except (OSError, ValueError, RuntimeError, TypeError, KeyError, yaml.YAMLError) as error:
                errors.append({"project": project, "record": path.name, "error": str(error)})
                continue
            if not admitted:
                errors.append({
                    "project": project, "worktree_id": record.worktree_id,
                    "error": "record belongs to another environment; not observed here",
                })
                continue
            records[(record.execution_machine, project, record.worktree_id)] = record
            record_paths[(project, record.worktree_id)] = path
            local.append((project, record))

    admitted_records = {(project, record.worktree_id): record for project, record in local}

    def resolve_record(machine: str, project: str, identity: str) -> tracking.WorktreeRecord | None:
        record = admitted_records.get((project, identity))
        if record is None:
            return None
        if machine == record.execution_machine:
            return record
        ref = tracking.parse_claim_ref(tracking.format_claim_ref(machine, project, identity))
        proved_path = legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(
            ref, project_configs[project],
        )
        return record if proved_path == record_paths[(project, identity)] else None

    for project, record in local:
        root = {
            "project": project, "worktree_id": record.worktree_id,
            "machine": record.execution_machine, "environment": record.platform,
            "codename": record.codename, "status": record.status,
        }
        # Rewalking cached descendants must not renew their source freshness.
        checked = read_started
        obligations, unresolved = claims_transitive_cli.transitive_obligations(
            record.worktree_id, project, machine=record.execution_machine,
            _record=record, _records=records, _platform=config.platform, _snapshot_only=True,
            _record_resolver=resolve_record,
        )
        roots.append({**root, "checked_utc": checked, "unresolved": unresolved})
        errors.extend({**root, **issue} for issue in unresolved)
        for obligation in obligations:
            if obligation.get("kind") != "pr":
                continue
            url = claims_rank.claim_url(obligation["kind"], obligation["ref"])
            identity = pr_identity(url)
            if identity is None:
                errors.append({**root, "error": "unsettled PR claim identity is unavailable"})
                continue
            if identity[1] != repo.lower():
                continue
            claims.append({
                **root, "authority": identity[0], "repo": identity[1],
                "number": identity[2], "url": url, "checked_utc": checked,
                "path": list(obligation["path"]), "evidence": "unsettled-transitive-claim",
                "recorded_state": obligation.get("state", ""),
            })
        for pr in record.prs or []:
            try:
                slug = claims_find_cli._pr_repo(pr)
                number = claims_find_cli._pr_number(pr)
            except ValueError as error:
                errors.append({**root, "error": f"tracked PR identity could not be parsed: {error}"})
                continue
            if "/" not in slug:
                errors.append({**root, "error": "tracked PR repository identity is unavailable"})
                continue
            if slug.lower() != repo.lower():
                continue
            if project not in authorities:
                authorities[project] = claims_find_cli._authority_resolver(project)
            authority = authorities[project](slug, pr.provider or "")
            if authority is None or number is None:
                errors.append({**root, "error": "tracked PR authority or number is unavailable"})
            claims.append({
                **root, "authority": authority,
                "repo": slug.lower(), "number": number,
                "url": pr.url, "checked_utc": checked,
                "path": [record.worktree_id], "evidence": "tracked-candidate",
                "recorded_state": pr.state,
            })
    return {
        "schema": SCHEMA, "version": VERSION, "repo": repo,
        "cell": f"{config.machine}/{config.platform}",
        "source_read_started_utc": read_started,
        "checked_utc": utc_now(), "roots": roots, "claims": claims, "errors": errors,
    }


def provider_inventory(repo: str, config: cfg.Config) -> dict:
    resolution = pr_config.resolve_repo_config_for_slug(config, repo)
    if not resolution.resolved or resolution.repo_config is None:
        raise ProviderError(resolution.error or "The target repository has no registered PR binding")
    binding = resolution.repo_config.pr
    owner = providers.get_provider(binding.provider or "github")
    read = getattr(owner, "list_open_pull_records", None)
    if not callable(read):
        raise ProviderError(f"Provider {owner.name!r} does not support repository PR inventories")
    explicit = bool(binding.token_command or binding.token_env)
    authority = owner.authority_endpoint(binding.api_base or "")
    expected = repo_accounts.account_for_remote(
        f"https://{authority}/{repo}",
    ) if owner.name == "github" else None
    if owner.name == "github" and not expected:
        raise ProviderError("The target repository has no scoped GitHub account")
    public_github = owner.name == "github" and authority == "github.com"
    token = providers.resolve_token(binding) if explicit or not public_github else providers.account_token_for_slug(repo, binding)
    if explicit and not token:
        raise ProviderError("The configured repository credential is unavailable")
    if public_github and not token:
        from . import git_ops

        active = git_ops.active_gh_account()
        if not expected or not active or expected.casefold() != active.casefold():
            raise ProviderError("The repository-scoped GitHub credential is unavailable")
    identity = {"expected_login": expected} if owner.name == "github" else {}
    rows = list(read(repo, api_base=binding.api_base or "", token=token, **identity))
    return {
        "status": "ok", "complete": True, "checked_utc": utc_now(),
        "provider": owner.name,
        "authority": claims_find_cli.canonical_authority(owner.authority_endpoint(binding.api_base or "")),
        "prs": rows,
    }


def join_inventory(repo: str, provider: dict, cells: list[dict], author: str | None = None) -> dict:
    known = provider.get("complete") is True
    authority = provider.get("authority")
    inventory = provider.get("prs") if known else None
    open_prs = {row["number"]: row for row in inventory or []}
    associations: list[dict] = []
    seen: set[tuple] = set()
    for cell in cells:
        for claim in (cell.get("observation") or {}).get("claims", []):
            identity = (
                cell["cell"], claim["project"], claim["worktree_id"],
                claim.get("authority"), claim.get("number"), tuple(claim["path"]),
                claim["evidence"],
            )
            if identity in seen:
                continue
            seen.add(identity)
            matched = (
                claim.get("authority") == authority and claim["repo"].lower() == repo.lower()
                and type(claim.get("number")) is int and claim["number"] > 0
            )
            state = (
                "open" if known and matched and claim.get("number") in open_prs
                else "not-in-open-census" if known and matched
                else "unknown"
            )
            associations.append({**claim, "cell": cell["cell"], "provider_state": state})
    prs = None
    if known:
        prs = [
            {
                **row,
                "claimants": [
                    claim for claim in associations
                    if claim["provider_state"] == "open" and claim["number"] == row["number"]
                    and claim["evidence"] == "unsettled-transitive-claim"
                ],
                "candidates": [
                    claim for claim in associations
                    if claim["provider_state"] == "open" and claim["number"] == row["number"]
                    and claim["evidence"] == "tracked-candidate"
                ],
            }
            for row in inventory or []
        ]
    selected = [
        row for row in prs or []
        if author is None or (row.get("author") or "").casefold() == author.casefold()
    ]
    author_complete = not author or all(row.get("author") for row in prs or [])
    complete = known and all(
        cell["status"] == "ok" and not cell["observation"]["errors"] for cell in cells
    ) and author_complete
    oldest = min(
        selected, key=lambda row: datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
    ) if selected and author_complete else None
    return {
        "schema": SCHEMA, "version": VERSION, "repo": repo, "checked_utc": utc_now(),
        "complete": complete, "provider": provider, "cells": cells,
        "prs": prs, "associations": associations,
        "summary": {
            "author_filter": author, "all_author_open_count": len(prs) if known else None,
            "author_filter_complete": author_complete if known else False,
            "known_matching_open_count": len(selected) if known else None,
            "open_count": len(selected) if known and author_complete else None,
            "oldest": {
                key: oldest[key] for key in ("number", "url", "created_at", "author", "head_sha")
            } if oldest else None,
            "unclaimed_open_count": sum(not row["claimants"] for row in selected)
            if known and author_complete else None,
        },
    }

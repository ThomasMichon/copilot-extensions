"""Read-only repository hosting admission, independent of project adoption."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from repository_policy import AgentHostingError, RepositoryPolicyError, require_agent_hosting

from . import registry_paths


def marketplace_id() -> str:
    try:
        context = registry_paths.installation_context()
    except (OSError, ValueError) as exc:
        raise RepositoryPolicyError(f"Cannot resolve selected marketplace policy: {exc}") from exc
    if context is None:
        return ""
    if not isinstance(context, dict):
        raise RepositoryPolicyError("Validated installation context must be a mapping")
    selected = context.get("marketplaceId")
    if not isinstance(selected, str) or not selected:
        raise RepositoryPolicyError("Validated installation context has no marketplace identity")
    return selected


def require(path: str | Path, *, operation: str = "launch") -> None:
    require_agent_hosting(path, marketplace_id=marketplace_id(), operation=operation)


def require_record(record_path: Path) -> None:
    from . import tracking

    require(tracking.load_record(record_path).worktree_path)


def json_refusal(path: str | Path, *, operation: str = "launch") -> int | None:
    """Return the established CLI refusal status without changing target state."""
    from . import output

    try:
        require(path, operation=operation)
    except AgentHostingError as exc:
        return output._json_error(str(exc), exit_code=3)
    return None


def _project_path(project: str | None, worktree_id: str | None) -> Path:
    if not project:
        raise RepositoryPolicyError("Project policy inspection requires an explicit --project owner")
    from . import config as cfg
    from .record_config import configured_repository_paths

    try:
        if worktree_id is None:
            return configured_repository_paths(project, require_project_absolute=True)[project]
        from .record_read import read_exact_record

        previous = cfg.active_project()
        cfg.set_active_project(project)
        try:
            path = read_exact_record(worktree_id)["path"]
            if not isinstance(path, str):
                raise RepositoryPolicyError("Worktree owner returned no execution checkout")
            return Path(path)
        finally:
            cfg.set_active_project(previous)
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as exc:
        raise RepositoryPolicyError(f"Cannot read the project execution checkout: {exc}") from exc


def main(argv: list[str], *, project: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-worktrees agent-policy")
    sub = parser.add_subparsers(dest="action", required=True)
    check = sub.add_parser("check", help="Read fresh hosting policy without adopting a project")
    selection = check.add_mutually_exclusive_group()
    selection.add_argument("--path", help="Actual checkout; no project adoption is required")
    selection.add_argument("--project-root", action="store_true", help="Read the explicit owner's source checkout")
    selection.add_argument("--worktree-id", help="Read one exact worktree from the explicit owner")
    check.add_argument("--operation", default="launch")
    check.add_argument("--json", action="store_true")
    audit = sub.add_parser("audit", help="Preview conflicting registrations without changing state")
    audit.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        selected_marketplace = marketplace_id()
        if args.action == "audit":
            from .agent_policy_audit import preview

            report, status = preview(marketplace_id=selected_marketplace)
            print(json.dumps(report))
            return status
        path = (
            _project_path(project, args.worktree_id)
            if args.project_root or args.worktree_id is not None else args.path or "."
        )
        policy = require_agent_hosting(path, marketplace_id=selected_marketplace, operation=args.operation)
    except AgentHostingError as exc:
        print(json.dumps({"version": 1, "code": exc.code, "error": str(exc)}))
        return 3
    print(json.dumps({**policy.to_dict(), "allowed": True}))
    return 0

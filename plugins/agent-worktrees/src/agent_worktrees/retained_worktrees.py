"""Bounded read-only discovery of known worktree relationships, including settled edges."""

from __future__ import annotations

import argparse
from pathlib import Path
import stat
from typing import Any

import yaml

from . import claims_selectors, config as cfg, output, record_reporting, tracking, worktree_identity
from .claims_transitive_cli import _is_safe_path_component
from .legacy_wsl_record_locality import record_namespace_matches, resolve_legacy_wsl_owner_ref
from .tracking_claims import format_claim_ref, parse_claim_ref

SCHEMA = "agent-worktrees.retained-worktrees"
MAX_CLAIMS = 4096


def _presence(path: str) -> str:
    if not path or not Path(path).is_absolute():
        return "unavailable"
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unavailable"
    if stat.S_ISDIR(info.st_mode) and not getattr(info, "st_reparse_tag", 0):
        return "directory"
    return "non-directory"


def inventory(
    worktree_id: str, config: cfg.Config, *, max_depth: int = 8, max_nodes: int = 256,
) -> dict[str, Any]:
    """Observe local known descendants without implying removal eligibility."""
    if type(max_depth) is not int or not 1 <= max_depth <= 32:
        raise ValueError("max-depth must be between 1 and 32")
    if type(max_nodes) is not int or not 1 <= max_nodes <= 1024:
        raise ValueError("max-nodes must be between 1 and 1024")
    if not _is_safe_path_component(worktree_id):
        raise ValueError("invalid worktree id")
    project = config.repo_name
    if not _is_safe_path_component(project):
        raise ValueError("retained discovery requires a qualified local project")
    machine = record_reporting.current_context(project).execution_machine
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    visited: set[tuple[str, str, str]] = set()
    projects: dict[str, cfg.Config] = {project: config}
    claims_read = 0

    def unknown(ref: str, reason: str) -> None:
        unresolved.append({"ref": ref, "reason": reason})

    def visit(key: tuple[str, str, str], ancestors: tuple[tuple[str, str, str], ...]) -> None:
        nonlocal claims_read
        host, repo, wid = key
        ref = format_claim_ref(host, repo, wid)
        if key in ancestors:
            unknown(ref, "cycle")
            return
        if key in visited:
            return
        if len(ancestors) > max_depth or len(visited) >= max_nodes:
            unknown(ref, "traversal limit")
            return
        visited.add(key)
        try:
            if repo not in projects:
                projects[repo] = cfg.load_project_config(repo, include_control_plane_related_pr=False)
            receiving = projects[repo]
            path = cfg.project_dir(repo) / "worktrees" / f"{wid}.yaml"
            record = tracking.load_record(path)
            if (
                record.worktree_id != wid or record.repo != repo
                or record.platform != config.platform
                or receiving.platform != config.platform
                or getattr(getattr(record, "_reporting_context", None), "unavailable", False)
                or not record_namespace_matches(record, receiving)
            ):
                unknown(ref, "record identity or execution environment mismatch")
                return
            if host != record.execution_machine:
                owner = parse_claim_ref(ref)
                if owner is None or resolve_legacy_wsl_owner_ref(owner, receiving) != path:
                    unknown(ref, "remote owner; inspect through that resource owner")
                    return
            presence = _presence(record.worktree_path)
        except (OSError, ValueError, KeyError, TypeError, OverflowError, RuntimeError, yaml.YAMLError) as exc:
            unknown(ref, f"record unavailable: {type(exc).__name__}")
            return
        nodes.append({
            "ref": ref, "project": repo, "worktree_id": wid,
            "platform": record.platform, "status": record.status,
            "creation_nonce": record.creation_nonce,
            "checkout_path": record.worktree_path,
            "checkout_presence": presence,
        })
        if presence in {"unavailable", "non-directory"}:
            unknown(ref, f"checkout observation {presence}")
        for claim in record.resources:
            claims_read += 1
            if claims_read > MAX_CLAIMS:
                unknown(ref, "claim observation limit")
                return
            if claim.metadata_opaque:
                unknown(ref, "opaque resource metadata; child relationships unqualified")
                continue
            if claim.kind != "worktree":
                continue
            edges.append({"parent": ref, "child": claim.ref, "state": claim.state})
            child = parse_claim_ref(claim.ref)
            if child is None or not child.is_qualified or child.machine is None:
                unknown(claim.ref, "unqualified child")
                continue
            child_project = child.project or repo
            if not all(_is_safe_path_component(value) for value in (child_project, child.worktree_id)):
                unknown(claim.ref, "unsafe child identity")
                continue
            visit((child.machine, child_project, child.worktree_id), (*ancestors, key))
            if claims_read > MAX_CLAIMS:
                return

    visit((machine, project, worktree_id), ())
    return {
        "schema": SCHEMA, "version": 1, "root": format_claim_ref(machine, project, worktree_id),
        "nodes": nodes, "edges": edges, "unresolved": unresolved,
        "complete": not unresolved, "removal_authorized": False,
    }


def cmd_retained(args: argparse.Namespace, target: list[str]) -> int:
    """Render the retained view without querying providers or mutating records."""
    try:
        wid = claims_selectors.worktree_id(args, target, operation="retained")
    except ValueError as exc:
        return output._json_error(str(exc), 2)
    config = cfg.load_config()
    if wid is None:
        try:
            record = worktree_identity.tracked_invocation_record(
                Path.cwd(), project=config.repo_name, anchor=Path(config.default_repo.anchor),
            )
        except (OSError, ValueError, RuntimeError, TypeError, OverflowError, yaml.YAMLError) as exc:
            return output._json_error(f"tracked checkout unavailable: {type(exc).__name__}", 2)
        wid = record.worktree_id if record is not None else None
    if not wid:
        return output._json_error("no existing tracked worktree; pass --worktree-id ID", 2)
    try:
        result = inventory(wid, config, max_depth=args.max_depth, max_nodes=args.max_nodes)
    except ValueError as exc:
        return output._json_error(str(exc), 2)
    if args.json:
        output._json_output(result)
    else:
        print(f"Known retained worktree relationships for {result['root']} (not cleanup eligibility):")
        for node in result["nodes"]:
            print(f"  {node['ref']}: {node['status']}; checkout={node['checkout_presence']}")
        for item in result["unresolved"]:
            print(f"  unresolved {item['ref']}: {item['reason']}")
    return 0

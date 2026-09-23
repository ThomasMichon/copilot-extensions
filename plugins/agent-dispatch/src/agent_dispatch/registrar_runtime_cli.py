"""Registrar/runtime helper implementations re-exported by ``__main__``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .loop_commands import _resolve_cli_module

if TYPE_CHECKING:
    from .registrar import ProfileDeclaration


def _core():
    return _resolve_cli_module()


_WORKTREE_PARENT_SUFFIX = ".worktrees"


def _reject_worktree_checkout_as_repo_root(repo_root: Path) -> None:
    """Refuse when ``repo_root`` looks like a worktree checkout, not a repo anchor."""
    if repo_root.parent.name.endswith(_WORKTREE_PARENT_SUFFIX):
        raise ValueError(
            f"{repo_root} looks like a worktree checkout (its parent "
            f"directory, {repo_root.parent.name!r}, follows this harness's "
            "'<repo>.worktrees' naming convention), not a repo's registered "
            "anchor. Run this reviewer-loop command from the anchor checkout "
            "instead: a worktree's directory name is per-session and must "
            "never be recorded as a repo's stable identity (see "
            "visions/plugin-services -- repo/agent identity resolves by "
            "registered NAME only; a filesystem path is never persisted "
            "outside repos.yaml/projects.yaml)."
        )


def _declaration_summary(decl: ProfileDeclaration) -> dict[str, Any]:
    """Return a JSON-friendly summary of a discovered declaration."""
    ef = decl.effective_filters()
    return {
        "name": decl.name,
        "owner": decl.owner,
        "labels": list(decl.labels),
        "repos": decl.repos,
        "concurrency": decl.concurrency,
        "max_active_processes": decl.concurrency,
        "body": {"type": decl.body.type, "agent": decl.body.agent},
        "filters": {
            "permit": {dim: sorted(vals) for dim, vals in ef.permit.items()},
            "reject": {dim: sorted(vals) for dim, vals in ef.reject.items()},
        },
    }


def _cmd_registrar(args: argparse.Namespace) -> int:
    """Manage registrar discovery pointers and read the declared profile set."""
    from . import registrar_discovery as rd
    from .registrar import RegistrarError
    summarize = getattr(_core(), "_declaration_summary", _declaration_summary)

    try:
        if args.registrar_command == "doctor":
            from .registrar_registry import registrar_dropins_dir

            sources = rd.RegistrarSources()
            report = sources.refresh(emit_warnings=False)
            combined = report.combined
            trusted_names = {declaration.name for declaration in combined.trusted}
            accepted_plugins = [
                contributed
                for contributed in combined.plugins.declarations
                if contributed.declaration.name not in trusted_names
            ]
            plugin_retention_possible = (
                combined.plugins.snapshot.authority.value == "indeterminate"
                or any(finding.status == "indeterminate" for finding in combined.findings)
            )
            payload = {
                "trusted": {
                    "registry": "pointers.json",
                    "path": str(rd.pointers_file()),
                    "authority": report.trusted_authority.value,
                    "error": report.trusted_error,
                    "retention_possible": report.trusted_error is not None,
                    "declarations": [
                        summarize(declaration) for declaration in combined.trusted
                    ],
                },
                "dropins": {
                    "registry": "registrar.d",
                    "path": str(registrar_dropins_dir()),
                    "authority": combined.plugins.snapshot.authority.value,
                    "active": [
                        {
                            **summarize(contributed.declaration),
                            "plugin": contributed.plugin,
                            "entry": contributed.source_path,
                            "manifest": contributed.manifest_path,
                        }
                        for contributed in accepted_plugins
                    ],
                    "findings": [finding.to_dict() for finding in combined.findings],
                    "fix_available": False,
                    "active_basis": "current-evidence-only",
                    "retention_possible": plugin_retention_possible,
                },
                "active": [summarize(declaration) for declaration in combined.declarations],
                "active_basis": "current-evidence-only",
            }
            failed = bool(report.trusted_error or combined.findings)
            if args.json:
                _core()._emit(payload)
            else:
                trusted_label = "[WARN]" if report.trusted_error else "[OK]"
                print(
                    f"{trusted_label} pointers.json is "
                    f"{report.trusted_authority.value}; "
                    f"{len(combined.trusted)} trusted declaration(s) confirmed "
                    "by current evidence."
                )
                if report.trusted_error:
                    print(f"  {report.trusted_error}")
                    print("  A running supervisor may retain its last-known trusted declarations.")
                dropin_label = "[WARN]" if combined.findings else "[OK]"
                print(
                    f"{dropin_label} registrar.d is "
                    f"{combined.plugins.snapshot.authority.value}; "
                    f"{len(accepted_plugins)} plugin declaration(s) confirmed "
                    "active by current evidence."
                )
                if plugin_retention_possible:
                    print(
                        "  A running supervisor may retain matching last-known "
                        "declarations for indeterminate entries."
                    )
                for finding in combined.findings:
                    target = f" -> {finding.target}" if finding.target else ""
                    print(f"  - {finding.reason}: {finding.entry}{target}")
                    if finding.detail:
                        print(f"    {finding.detail}")
                    if finding.remedy:
                        print(f"    {finding.remedy}")
                print("  Cleanup is report-only; no --fix operation is available.")
            return 1 if failed else 0
        if args.registrar_command == "add-pointer":
            pointer = rd.add_pointer(args.name, args.location, kind=args.kind, owner=args.owner)
            return _core()._emit(pointer.to_dict())
        if args.registrar_command == "list":
            return _core()._emit([p.to_dict() for p in rd.load_pointers()])
        if args.registrar_command == "remove":
            return _core()._emit({"removed": rd.remove_pointer(args.name)})
        if args.registrar_command == "discover":
            decls = rd.discover()
            return _core()._emit([summarize(d) for d in decls])
        if args.registrar_command == "discover-repo":
            decls = rd.discover_repo(args.repo_root, owner=args.owner)
            return _core()._emit([summarize(d) for d in decls])
    except RegistrarError as exc:
        print(f"agent-dispatch registrar: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled registrar command {args.registrar_command!r}")

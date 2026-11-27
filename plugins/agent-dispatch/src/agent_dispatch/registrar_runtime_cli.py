"""Registrar/runtime helper implementations re-exported by ``__main__``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .loop_commands import _resolve_cli_module
from .registrations import RegistrationKind

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
    """Return a JSON-friendly summary of a discovered declaration.

    A ``supervised-lane`` declaration summarizes through its pool-profile
    fields (``body``/``concurrency``/``labels``). Any other kind
    (``evaluator``, ``emitter``, ``schedule``, ``plugin-companion``) carries
    its real identity in ``kind``/``spec`` instead, and is summarized through
    those fields plus its effective filters.
    """
    from .pool_instances import filters_to_dict

    filters = filters_to_dict(decl.effective_filters())
    if decl.kind != RegistrationKind.SUPERVISED_LANE:
        return {
            "name": decl.name,
            "owner": decl.owner,
            "kind": decl.kind,
            "spec": dict(decl.spec),
            "filters": filters,
        }
    body: dict[str, Any] = {"type": decl.body.type, "agent": decl.body.agent}
    if decl.body.charter:
        body["charter"] = decl.body.charter
    if decl.body.headless_labels:
        body["headless_labels"] = list(decl.body.headless_labels)
    if decl.body.cli_labels:
        body["cli_labels"] = list(decl.body.cli_labels)
    if decl.body.disposable_cli_labels:
        body["disposable_cli_labels"] = list(decl.body.disposable_cli_labels)
    return {
        "name": decl.name,
        "owner": decl.owner,
        "labels": list(decl.labels),
        "repos": decl.repos,
        "concurrency": decl.concurrency,
        "max_active_processes": decl.concurrency,
        "priority": decl.priority,
        "body": body,
        "filters": filters,
    }


def _withdraw_declared_registrations(args: argparse.Namespace, owner: str) -> dict[str, Any]:
    """Remove coordinator registrations materialized from ``owner``'s declarations.

    The supervisor daemon withdraws dropped declarations only from its in-memory
    publication ledger, so rows published by an earlier daemon generation (or
    while no daemon is running) would otherwise outlive the pointer and keep
    running as ordinary store registrations. Only ``declared:<owner>:*`` ids are
    touched; unreachable coordinators are reported, never fatal.
    """
    from .registrar_reconcile import DECLARED_ID_PREFIX

    prefix = f"{DECLARED_ID_PREFIX}:{owner}:"
    result: dict[str, Any] = {"owner": owner, "removed": [], "failed": []}
    try:
        with _core()._client(args, ensure=False, allow_ssh_failover=False) as client:
            rows = client.list_registrations(include_paused=True)
            for rid in sorted(str(row.get("id") or "") for row in rows):
                if not rid.startswith(prefix):
                    continue
                try:
                    client.remove_registration(rid)
                except Exception as exc:  # noqa: BLE001 - report per-row, keep going
                    result["failed"].append({"id": rid, "error": str(exc)})
                else:
                    result["removed"].append(rid)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - pointer removal already succeeded
        result["status"] = "coordinator_unavailable"
        result["error"] = str(exc) or type(exc).__name__
        return result
    result["status"] = "partial" if result["failed"] else "ok"
    return result


def _remove_pointer_and_registrations(args: argparse.Namespace, rd: Any) -> dict[str, Any]:
    """``registrar remove``: drop the pointer and withdraw what it materialized."""
    pointers = rd.load_pointers()
    target = next((p for p in pointers if p.name == args.name), None)
    owner = getattr(args, "owner", None) or (target.effective_owner() if target else None)
    removed = rd.remove_pointer(args.name)
    payload: dict[str, Any] = {"removed": removed}
    if getattr(args, "keep_registrations", False) or owner is None:
        return payload
    if not removed and not getattr(args, "owner", None):
        return payload
    sharing = sorted(
        p.name for p in pointers if p.name != args.name and p.effective_owner() == owner
    )
    if sharing:
        payload["registrations"] = {
            "owner": owner, "status": "skipped", "removed": [], "failed": [],
            "reason": f"owner still declared by pointer(s): {', '.join(sharing)}",
        }
        return payload
    payload["registrations"] = _withdraw_declared_registrations(args, owner)
    return payload


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
            kwargs: dict[str, Any] = {
                "kind": args.kind,
                "aliases": getattr(args, "aliases", None),
                "repository": getattr(args, "repository", None),
            }
            if args.owner is not None:
                kwargs["owner"] = args.owner
            pointer = rd.add_pointer(args.name, args.location, **kwargs)
            return _core()._emit(pointer.to_dict())
        if args.registrar_command == "list":
            return _core()._emit([p.to_dict() for p in rd.load_pointers()])
        if args.registrar_command == "remove":
            return _core()._emit(_remove_pointer_and_registrations(args, rd))
        if args.registrar_command == "discover":
            decls = rd.discover()
            return _core()._emit([summarize(d) for d in decls])
        if args.registrar_command == "discover-repo":
            decls = rd.discover_repo(args.repo_root, owner=args.owner)
            return _core()._emit([summarize(d) for d in decls])
        if args.registrar_command == "vocabulary":
            from .registrar import filter_vocabulary

            sources = rd.RegistrarSources()
            report = sources.refresh(emit_warnings=False)
            vocab = filter_vocabulary(report.combined.declarations)
            if getattr(args, "dim", None):
                values = vocab.get(args.dim, [])
                if args.json:
                    return _core()._emit(values)
                for value in values:
                    print(value)
                return 0
            if args.json:
                return _core()._emit(vocab)
            for dim in sorted(vocab):
                print(f"{dim}: {', '.join(vocab[dim])}")
            return 0
    except RegistrarError as exc:
        print(f"agent-dispatch registrar: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled registrar command {args.registrar_command!r}")

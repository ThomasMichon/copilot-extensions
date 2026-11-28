"""Registrar/runtime helper implementations re-exported by ``__main__``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast

from .loop_commands import _resolve_cli_module
from .registrations import RegistrationKind

if TYPE_CHECKING:
    from .client import DispatchClient
    from .registrar import ProfileDeclaration


class _RegistrarCLI(ModuleType):
    _emit: Callable[[object], int]
    _client: Callable[..., DispatchClient]


def _core() -> _RegistrarCLI:
    return cast(_RegistrarCLI, _resolve_cli_module())


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


def _norm_machine(machine: object) -> str | None:
    if not isinstance(machine, str):
        return None
    return machine.strip().casefold() or None


def _declared_name_for_owner(row: dict[str, Any], prefix: str, owner: str) -> str | None:
    """The declaration name if ``row`` was materialized from exactly ``owner``.

    Owners contain colons (``pointer:<name>``, ``repo:<root>``) and coordinator
    rows do not persist the owner, so a bare ``declared:<owner>:`` prefix test
    would also match a longer owner such as ``<owner>:<more>``. Declaration names
    are colon-free (letters, digits, ``-``, ``_``), so the id
    ``declared:<owner>:<name>`` matches ``owner`` exactly only when the remainder
    after the prefix is a single colon-free segment. A row that does carry an
    ``owner`` field must also agree with it.
    """
    rid = row.get("id")
    if not isinstance(rid, str) or not rid.startswith(prefix):
        return None
    name = rid[len(prefix):]
    if not name or ":" in name:
        return None
    row_owner = row.get("owner")
    if row_owner is not None and row_owner != owner:
        return None
    return name


def _withdraw_declared_registrations(
    args: argparse.Namespace, owner: str, machine: str | None
) -> dict[str, Any]:
    """Remove coordinator registrations this host materialized from ``owner``.

    The supervisor daemon withdraws dropped declarations only from its in-memory
    publication ledger, so rows published by an earlier daemon generation (or
    while no daemon is running) would otherwise outlive the pointer and keep
    running as ordinary store registrations. Only ``declared:<owner>:<name>`` ids
    of exactly ``owner`` are touched, and only rows scoped to ``machine`` (or
    unscoped rows): another host publishing the same owner into a shared
    coordinator keeps its rows. Unreachable coordinators are reported, never fatal.
    """
    from .registrar_reconcile import DECLARED_ID_PREFIX

    prefix = f"{DECLARED_ID_PREFIX}:{owner}:"
    local = _norm_machine(machine)
    removed: list[str] = []
    failed: list[dict[str, str]] = []
    other_machines: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "owner": owner, "machine": machine, "removed": removed, "failed": failed,
        "other_machines": other_machines,
    }
    try:
        with _core()._client(args, ensure=False, allow_ssh_failover=False) as client:
            rows = client.list_registrations(include_paused=True)
            for row in sorted(rows, key=lambda r: str(r.get("id") or "")):
                if _declared_name_for_owner(row, prefix, owner) is None:
                    continue
                rid = str(row["id"])
                row_machine = _norm_machine(row.get("machine"))
                if row_machine is not None and row_machine != local:
                    other_machines.append({"id": rid, "machine": row.get("machine")})
                    continue
                try:
                    client.remove_registration(rid)
                except Exception as exc:  # noqa: BLE001 - report per-row, keep going
                    failed.append({"id": rid, "error": str(exc)})
                else:
                    removed.append(rid)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - pointer removal already succeeded
        result["status"] = "coordinator_unavailable"
        result["error"] = str(exc) or type(exc).__name__
        return result
    result["status"] = "partial" if failed else "ok"
    return result


def _remove_pointer_and_registrations(args: argparse.Namespace, rd: Any) -> dict[str, Any]:
    """``registrar remove``: drop the pointer and withdraw what it materialized."""
    from . import remote_dispatch

    pointers = rd.load_pointers()
    target = next((p for p in pointers if p.name == args.name), None)
    explicit_owner: str | None = getattr(args, "owner", None)
    owner: str | None = explicit_owner or (target.effective_owner() if target else None)
    removed = bool(rd.remove_pointer(args.name))
    payload: dict[str, Any] = {"removed": removed}
    if getattr(args, "keep_registrations", False) or owner is None:
        return payload
    if not removed and not explicit_owner:
        return payload
    machine = getattr(args, "machine", None) or remote_dispatch.local_machine()
    sharing = sorted(
        p.name for p in pointers if p.name != args.name and p.effective_owner() == owner
    )
    if sharing:
        payload["registrations"] = {
            "owner": owner, "machine": machine, "status": "skipped", "removed": [],
            "failed": [], "other_machines": [],
            "reason": f"owner still declared by pointer(s): {', '.join(sharing)}",
        }
        return payload
    payload["registrations"] = _withdraw_declared_registrations(args, owner, machine)
    return payload


def _report_registration_withdrawal(args: argparse.Namespace, payload: dict[str, Any]) -> int:
    """Warn on stderr about incomplete withdrawal; return the command's exit code.

    A failed per-row withdrawal is an error (exit 1). An unreachable coordinator
    never fails a successful pointer removal (exit 0 with a warning), but fails a
    ``--owner`` cleanup that removed no pointer, since withdrawal was its only job.
    """
    regs = payload.get("registrations")
    if not isinstance(regs, dict):
        return 0
    owner = regs.get("owner")
    rerun = f"agent-dispatch registrar remove {args.name} --owner {owner}"
    if regs.get("machine"):
        rerun += f" --machine {regs['machine']}"
    for other in regs.get("other_machines") or []:
        print(
            f"agent-dispatch registrar: left {other['id']} in place -- it was published "
            f"by machine {other['machine']!r}; run registrar remove there (or pass "
            "--machine) to withdraw it",
            file=sys.stderr,
        )
    status = regs.get("status")
    if status == "partial":
        failed = ", ".join(f["id"] for f in regs.get("failed") or [])
        print(
            f"agent-dispatch registrar: WARNING -- could not withdraw declared "
            f"registration(s) for owner {owner!r}: {failed}. Rerun: {rerun}",
            file=sys.stderr,
        )
        return 1
    if status == "coordinator_unavailable":
        print(
            f"agent-dispatch registrar: WARNING -- coordinator unreachable "
            f"({regs.get('error')}); declared registrations for owner {owner!r} were "
            f"not withdrawn. Rerun once it is reachable: {rerun}",
            file=sys.stderr,
        )
        return 0 if payload.get("removed") else 1
    return 0


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
            payload = _remove_pointer_and_registrations(args, rd)
            rc = _core()._emit(payload)
            return rc or _report_registration_withdrawal(args, payload)
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

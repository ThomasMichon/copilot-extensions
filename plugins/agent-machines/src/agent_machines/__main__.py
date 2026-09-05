"""``agent-machines`` command-line entry point.

Verbs:
* ``version``  -- print the engine version
* ``discover`` -- show this machine's requirement-package set (from repos.yaml)
* ``doctor``   -- diagnose canonical, legacy, mixed, and malformed package layouts
* ``migrate``  -- move one legacy repo layout to the canonical namespace
* ``plan``     -- read-only restore plan (managed surfaces + drift key)
* ``validate`` -- run the conflict validator over the package union
* ``restore``  -- converge the machine (``--dry-run`` prints the plan; apply lands in #4006)
* ``provision-playwright-cli`` -- converge the machine-local Playwright CLI workspace
* ``capture`` / ``prune`` -- harvest / GC verbs (issue #4006)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import discover as _discover
from . import identity as _identity
from . import layout as _layout
from . import playwright_cli as _playwright_cli
from . import reconcile as _reconcile
from . import validator as _validator
from .manifest import ManifestError, RequirementPackage
from .surfaces._common import SurfaceStateError


def _collect_all_packages(
    machine: str,
    accepted_machines: tuple[str, ...] | None = None,
) -> list[RequirementPackage]:
    packages: list[RequirementPackage] = []
    repos = (
        _discover.discover(machine, accepted_machines=accepted_machines)
        if accepted_machines is not None
        else _discover.discover(machine)
    )
    for repo in repos:
        packages.extend(repo.packages)
    return packages


def _collect_reconcile_packages(
    args: argparse.Namespace,
    machine: str,
    accepted_machines: tuple[str, ...] | None = None,
) -> tuple[list[RequirementPackage], str]:
    """Select the package scope shared by plan, validate, and restore."""
    if getattr(args, "all_projects", False):
        packages = (
            _collect_all_packages(machine, accepted_machines)
            if accepted_machines is not None
            else _collect_all_packages(machine)
        )
        return packages, "all-projects"
    selector = getattr(args, "repo", None)
    if selector:
        candidate = _discover.resolve_registered_repo(selector)
        if candidate is not None:
            repo_name, repo_path = candidate
            if not repo_path.is_dir():
                raise ManifestError(
                    f"registered repo {repo_name!r} is unavailable at {repo_path}"
                )
            repo_anchor = repo_path
        else:
            repo_path = Path(selector).expanduser()
            if not repo_path.is_dir():
                raise ManifestError(
                    f"repo {selector!r} is neither a directory nor a registered repo name"
                )
            try:
                repo_name, repo_path, repo_anchor = _layout.resolve_cwd_repo(repo_path)
            except _layout.NotGitRepositoryError as exc:
                raise ManifestError(
                    f"repo path {selector!r} is not a Git repository"
                ) from exc
    else:
        repo_name, repo_path, repo_anchor = _layout.resolve_cwd_repo()
        project_repos = _discover.project_scope_repos(
            repo_name,
            repo_path,
            project_anchor=repo_anchor,
        )
        if project_repos is not None:
            packages: list[RequirementPackage] = []
            for index, candidate in enumerate(project_repos):
                if not candidate.path.is_dir():
                    owners = ", ".join(candidate.required_by) or repo_name
                    raise ManifestError(
                        f"project {owners!r} requires supplemental repo "
                        f"{candidate.name!r}, but it is unavailable at "
                        f"{candidate.path}; pass --repo {str(repo_path)!r} to "
                        "reconcile only the current repository"
                    )
                packages.extend(
                    _discover.packages_in_repo(
                        candidate.path,
                        candidate.name,
                        machine,
                        source_anchor=repo_anchor if index == 0 else candidate.path,
                        accepted_machines=accepted_machines,
                    )
                )
            scope_kind = "project" if len(project_repos) > 1 else "repo"
            return packages, f"{scope_kind}:{repo_name}"
    return (
        _discover.packages_in_repo(
            repo_path,
            repo_name,
            machine,
            source_anchor=repo_anchor,
            accepted_machines=accepted_machines,
        ),
        f"repo:{repo_name}",
    )


def _resolve_machine_identity(args: argparse.Namespace) -> _identity.MachineIdentity:
    topology_repos: list[Path] = []
    selector = getattr(args, "repo", None)
    if selector:
        registered = _discover.resolve_registered_repo(selector)
        if registered is not None:
            topology_repos.append(registered[1])
        else:
            candidate = Path(selector).expanduser()
            if candidate.is_dir():
                topology_repos.append(candidate)
    else:
        topology_repos.extend(
            candidate.path for candidate in _discover.candidate_repos()
        )
        try:
            topology_repos.append(_layout.resolve_cwd_repo()[1])
        except _layout.NotGitRepositoryError:
            pass
    return _identity.resolve_machine(
        getattr(args, "machine", None),
        topology_repos=topology_repos,
    )


def _emit_identity_warnings(identity: _identity.MachineIdentity) -> None:
    for warning in identity.warnings:
        print(f"warning: {warning}", file=sys.stderr)


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"agent-machines {__version__}")
    return 0


def _cmd_discover(args: argparse.Namespace) -> int:
    identity = _resolve_machine_identity(args)
    if args.json:
        out = [
            {
                "repo": r.name,
                "path": str(r.path),
                "enabled": r.enabled,
                "packages": [p.name for p in r.packages],
            }
            for r in _discover.discover(
                identity.canonical,
                accepted_machines=identity.accepted,
            )
        ]
        print(json.dumps({
            "machine": identity.canonical,
            "machine_raw": identity.raw,
            "machine_aliases": list(identity.accepted),
            "machine_identity_warnings": list(identity.warnings),
            "repos": out,
        }, indent=2))
        return 0
    _emit_identity_warnings(identity)
    return _discover._main(
        machine=identity.canonical,
        accepted_machines=identity.accepted,
        raw_machine=identity.raw,
    )


def _cmd_doctor(args: argparse.Namespace) -> int:
    identity = _resolve_machine_identity(args)
    machine = identity.canonical
    reports = _layout.inspect_layouts(
        machine,
        repo=args.repo,
        accepted_machines=identity.accepted,
    )
    ok = all(report.ok for report in reports)
    if args.json:
        print(json.dumps({
            "machine": machine,
            "machine_raw": identity.raw,
            "machine_aliases": list(identity.accepted),
            "machine_identity_warnings": list(identity.warnings),
            "ok": ok,
            "repos": [report.to_dict() for report in reports],
        }, indent=2))
        return 0 if ok else 1

    print(f"doctor for {machine}")
    for warning in identity.warnings:
        print(f"  [advisory] machine-topology: {warning}")
    if not reports:
        print("  no adopted repos found")
        return 0
    for report in reports:
        print(f"  {report.repo} [{report.status}] ({report.path})")
        if report.package_count:
            print(f"      {report.package_count} applicable package(s)")
        for finding in report.findings:
            print(f"      [{finding.level}] {finding.code}: {finding.message}")
    return 0 if ok else 1


def _cmd_migrate(args: argparse.Namespace) -> int:
    repo_name, repo_path = _layout.resolve_repo(args.repo)
    result = _layout.migrate_repo_layout(repo_path, repo_name, apply=args.apply)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"migrate [{mode}] {result.repo} ({result.path}): {result.status}")
    for move in result.moves:
        verb = "moved" if args.apply else "would move"
        print(f"  {verb}: {move.source} -> {move.target}")
    if result.status == "would-migrate":
        print("  re-run with --apply to perform the migration")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    identity = _resolve_machine_identity(args)
    _emit_identity_warnings(identity)
    machine = identity.canonical
    packages, scope = _collect_reconcile_packages(
        args,
        machine,
        identity.accepted,
    )
    plan = _reconcile.plan(
        packages,
        machine,
        accepted_machines=identity.accepted,
    )
    if args.json:
        payload = _reconcile.plan_to_dict(plan)
        payload["scope"] = scope
        payload["machine_raw"] = identity.raw
        payload["machine_aliases"] = list(identity.accepted)
        payload["machine_identity_warnings"] = list(identity.warnings)
        print(json.dumps(payload, indent=2))
        return 0
    print(f"plan for {machine} [{scope}]  (drift-key {plan.drift_key})")
    if not plan.surfaces and not plan.modules and not plan.resources:
        print("  no managed surfaces, resources, or modules (no requirement packages apply)")
        return 0
    for surface in plan.surfaces:
        owners = ", ".join(surface.contributing_packages)
        print(f"  surface {surface.key}  [{surface.disposition}]  <- {owners}")
    for removal in plan.removals:
        owners = ", ".join(removal["contributors"])
        print(f"    - enabledPlugins.{removal['item']}  <- {owners}")
    for mod in plan.modules:
        print(
            f"  module  {mod['name']}  "
            f"[authority {mod['authority']}; {mod['authority_mode']}]  "
            f"<- {mod['source_repo']}:{mod['package']}"
        )
    for res in plan.resources:
        owners = ", ".join(res.get("contributors", []))
        print(f"  resource {res['type']}:{res['id']}  [{res['summary']}]  <- {owners}")
    _print_authority_decisions(plan.authority_decisions)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    identity = _resolve_machine_identity(args)
    _emit_identity_warnings(identity)
    machine = identity.canonical
    packages, scope = _collect_reconcile_packages(
        args,
        machine,
        identity.accepted,
    )
    resolved = _reconcile.resolve_union(
        packages,
        machine,
        identity.accepted,
    )
    findings = _validator.validate(resolved, machine)
    if args.json:
        print(json.dumps([f.__dict__ for f in findings], indent=2))
    else:
        print(f"validator [{scope}]")
        if not findings:
            print("  no findings")
        for f in findings:
            print(f"  [{f.level}] {f.code}: {f.message}")
    return 1 if _validator.has_errors(findings) else 0


def _cmd_installer_readiness(args: argparse.Namespace) -> int:
    from .installer_readiness import emit, evaluate

    identity = _resolve_machine_identity(args)
    _emit_identity_warnings(identity)
    reports = _layout.inspect_layouts(
        identity.canonical,
        accepted_machines=identity.accepted,
    )
    return emit(evaluate(reports))


def _fmt_val(v: object) -> str:
    s = json.dumps(v) if not isinstance(v, str) else v
    return s if len(s) <= 80 else s[:77] + "..."


def _print_authority_decisions(decisions: list[dict[str, object]]) -> None:
    for decision in decisions:
        identity = decision["identity"]
        if isinstance(identity, dict):
            key = ":".join([str(identity.get("type", ""))]
                           + [str(item) for item in identity.get("key", [])])
            label = f"{key}.{identity.get('field', '')}"
        else:
            label = str(identity)
        selected = ", ".join(
            f"{item['source_repo']}:{item['package']}@{item['authority']}"
            for item in decision.get("selected", [])
        )
        superseded = ", ".join(
            f"{item['source_repo']}:{item['package']}@{item['authority']}"
            for item in decision.get("superseded", [])
        )
        print(
            f"  authority {decision['domain']} {label}: "
            f"{selected} supersedes {superseded}"
        )


def _cmd_restore(args: argparse.Namespace) -> int:
    identity = _resolve_machine_identity(args)
    _emit_identity_warnings(identity)
    machine = identity.canonical
    dry_run = not args.apply
    packages, scope = _collect_reconcile_packages(
        args,
        machine,
        identity.accepted,
    )
    resolved = _reconcile.resolve_union(
        packages,
        machine,
        identity.accepted,
    )
    findings = _validator.validate(resolved, machine)
    if _validator.has_errors(findings):
        print("restore refused: validator reported errors:", file=sys.stderr)
        for f in findings:
            if f.level == "error":
                print(f"  {f.code}: {f.message}", file=sys.stderr)
        return 1

    try:
        result = _reconcile.restore(
            packages,
            machine,
            dry_run=dry_run,
            only=args.only,
            accepted_machines=identity.accepted,
        )
    except (_reconcile.RestoreValidationError, SurfaceStateError) as exc:
        print(f"restore refused: {exc}", file=sys.stderr)
        return 1
    if args.json:
        payload = _reconcile.restore_result_to_dict(result)
        payload["scope"] = scope
        payload["machine_raw"] = identity.raw
        payload["machine_aliases"] = list(identity.accepted)
        payload["machine_identity_warnings"] = list(identity.warnings)
        print(json.dumps(payload, indent=2))
        return 0 if result.ok else 2
    header = "DRY-RUN (preview only; re-run with --apply to make changes)" if dry_run else "APPLY"
    print(f"restore [{header}] for {machine} [{scope}]  (drift-key {result.plan.drift_key})")
    _print_authority_decisions(result.plan.authority_decisions)

    if not result.surface_results:
        print("  surfaces: none")
    for s in result.surface_results:
        if s.skipped_reason:
            print(f"  surface {s.surface}: skipped ({s.skipped_reason})")
        elif s.changed:
            verb = "would change" if s.dry_run else "changed"
            backup = f"  (backup {s.backup_path})" if s.backup_path else ""
            print(f"  surface {s.surface} [{s.file}]: {verb}{backup}")
            for ch in s.changes:  # what changes and why
                if ch.get("op") == "remove":
                    for item in ch["items"]:
                        print(f"      - {ch['key']}.{item}")
                elif "added" in ch:
                    for item in ch["added"]:
                        print(f"      + {ch.get('key', ch.get('location'))}: {_fmt_val(item)}")
                else:
                    before, after = _fmt_val(ch["before"]), _fmt_val(ch["after"])
                    print(f"      ~ {ch['key']}: {before} -> {after}")
        else:
            print(f"  surface {s.surface} [{s.file}]: up-to-date")

    if not result.resource_results:
        print("  resources: none")
    for r in result.resource_results:
        label = f"{r.type}:{r.id}"
        if r.status == "error":
            print(f"  resource {label}: ERROR {r.detail}", file=sys.stderr)
        elif r.blocked_reason:
            print(f"  resource {label}: BLOCKED ({r.blocked_reason})", file=sys.stderr)
            if r.detail:
                print(f"      {r.detail}", file=sys.stderr)
        elif r.deferred_reason:
            print(f"  resource {label}: deferred ({r.deferred_reason})")
            if r.detail:
                print(f"      {r.detail}")
            for cmd in r.commands:
                print(f"      $ {' '.join(cmd)}")
        elif r.skipped_reason:
            print(f"  resource {label}: skipped ({r.skipped_reason})")
        elif r.changed:
            verb = "would" if r.dry_run else "did"
            backup = f"  (backup {r.backup_path})" if r.backup_path else ""
            print(f"  resource {label}: {verb} {r.action}{backup}")
            if r.detail:
                print(f"      {r.detail}")
            for cmd in r.commands:
                print(f"      $ {' '.join(cmd)}")
        else:
            print(f"  resource {label}: up-to-date")

    if not result.module_results:
        print("  modules: none")
    # A dry-run *is* a preview, so surface each module's captured output by
    # default; for --apply keep it terse unless --verbose (or a failure).
    show_output = getattr(args, "verbose", False) or dry_run
    for r in result.module_results:
        if r.ran:
            status = "ok" if r.returncode == 0 else f"FAILED rc={r.returncode}"
            owner = f"{r.source_repo}:{r.package}" if r.package else r.source_repo
            print(
                f"  module {r.name} <- {owner}: {status} "
                f"[authority {r.authority}; {r.authority_mode}]"
            )
            if show_output and r.stdout_tail:
                for line in r.stdout_tail.rstrip().splitlines():
                    print(f"      {line}")
            if r.returncode not in (0, None) and r.stderr_tail:
                print(f"      {r.stderr_tail.strip().splitlines()[-1]}", file=sys.stderr)
        else:
            owner = f"{r.source_repo}:{r.package}" if r.package else r.source_repo
            print(
                f"  module {r.name} <- {owner}: skipped ({r.skipped_reason}) "
                f"[authority {r.authority}; {r.authority_mode}]"
            )
    return 0 if result.ok else 2


def _cmd_todo(args: argparse.Namespace) -> int:
    print(f"'{args.verb}' is delivered by the surfaces package (issue #4006)", file=sys.stderr)
    return 2


def _cmd_provision_playwright_cli(args: argparse.Namespace) -> int:
    result = _playwright_cli.provision_playwright_cli(apply=args.apply)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.ok else 2

    label = "APPLY" if args.apply else "DRY-RUN"
    print(f"provision-playwright-cli [{label}] in {result.home}")
    for action in result.actions:
        print(f"  {action.status}: {' '.join(action.argv)}")
    if not result.actions and result.ok:
        print("  up-to-date")
    if result.error is not None:
        print(f"provision-playwright-cli failed: {result.error}", file=sys.stderr)
        for command in result.commands:
            print(
                f"  command (exit {command.returncode}): {' '.join(command.argv)}",
                file=sys.stderr,
            )
            if command.stdout_tail:
                print("  stdout:", file=sys.stderr)
                print(command.stdout_tail, file=sys.stderr)
            if command.stderr_tail:
                print("  stderr:", file=sys.stderr)
                print(command.stderr_tail, file=sys.stderr)
        return 2
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-machines", description=__doc__)
    parser.add_argument("--version", action="version", version=f"agent-machines {__version__}")
    sub = parser.add_subparsers(dest="command")

    def add(name: str, func) -> argparse.ArgumentParser:
        p = sub.add_parser(name)
        p.add_argument("--machine", help="override the target machine name")
        p.add_argument("--json", action="store_true", help="emit JSON")
        p.set_defaults(func=func)
        return p

    add("version", _cmd_version)
    add("discover", _cmd_discover)
    doctor = add("doctor", _cmd_doctor)
    doctor.add_argument(
        "--repo",
        help="inspect one adopted repo name or directory (default: every adopted repo)",
    )
    migrate = sub.add_parser("migrate")
    migrate.add_argument("--json", action="store_true", help="emit JSON")
    migrate.set_defaults(func=_cmd_migrate)
    migrate.add_argument(
        "--repo",
        required=True,
        help="adopted repo name or directory to migrate",
    )
    migrate.add_argument(
        "--apply",
        action="store_true",
        help="perform the migration (default is a dry-run preview)",
    )

    def add_reconcile_scope(command: argparse.ArgumentParser) -> None:
        scope = command.add_mutually_exclusive_group()
        scope.add_argument(
            "--repo",
            help="reconcile exactly one registered repo name or repository path "
                 "(default: adopted project containing CWD plus its required "
                 "supplemental repositories)",
        )
        scope.add_argument(
            "--all-projects",
            action="store_true",
            help="reconcile the full machine-scoped adopted-project union",
        )

    plan = add("plan", _cmd_plan)
    add_reconcile_scope(plan)
    validate = add("validate", _cmd_validate)
    add_reconcile_scope(validate)
    add("installer-readiness", _cmd_installer_readiness)
    restore = add("restore", _cmd_restore)
    add_reconcile_scope(restore)
    restore.add_argument("--apply", action="store_true",
                         help="make changes (default is a dry-run preview)")
    restore.add_argument("--only", action="append", metavar="NAME",
                         help="restrict to named surfaces/modules (repeatable)")
    restore.add_argument("--verbose", "-v", action="store_true",
                         help="show each module's captured output (shown by default in a dry-run)")
    playwright = sub.add_parser("provision-playwright-cli")
    playwright.add_argument("--json", action="store_true", help="emit JSON")
    playwright_mode = playwright.add_mutually_exclusive_group()
    playwright_mode.add_argument(
        "--dry-run",
        dest="apply",
        action="store_false",
        help="query registry latest and preview required package or skill changes (default)",
    )
    playwright_mode.add_argument(
        "--apply",
        action="store_true",
        help="converge the latest package and bundled skill tree",
    )
    playwright.set_defaults(func=_cmd_provision_playwright_cli, apply=False)
    for verb in ("capture", "prune"):
        p = add(verb, _cmd_todo)
        p.set_defaults(verb=verb)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except ManifestError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"manifest error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

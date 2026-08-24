"""``agent-machines`` command-line entry point.

Verbs:
* ``version``  -- print the engine version
* ``discover`` -- show this machine's requirement-package set (from repos.yaml)
* ``plan``     -- read-only restore plan (managed surfaces + drift key)
* ``validate`` -- run the conflict validator over the package union
* ``restore``  -- converge the machine (``--dry-run`` prints the plan; apply lands in #4006)
* ``capture`` / ``prune`` -- harvest / GC verbs (issue #4006)
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from . import discover as _discover
from . import reconcile as _reconcile
from . import validator as _validator
from .manifest import ManifestError, RequirementPackage


def _collect_packages(machine: str) -> list[RequirementPackage]:
    packages: list[RequirementPackage] = []
    for repo in _discover.discover(machine):
        packages.extend(repo.packages)
    return packages


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"agent-machines {__version__}")
    return 0


def _cmd_discover(args: argparse.Namespace) -> int:
    if args.json:
        machine = args.machine or _discover.current_machine()
        out = [
            {
                "repo": r.name,
                "path": str(r.path),
                "enabled": r.enabled,
                "packages": [p.name for p in r.packages],
            }
            for r in _discover.discover(machine)
        ]
        print(json.dumps({"machine": machine, "repos": out}, indent=2))
        return 0
    return _discover._main()


def _cmd_plan(args: argparse.Namespace) -> int:
    machine = args.machine or _discover.current_machine()
    packages = _collect_packages(machine)
    plan = _reconcile.plan(packages, machine)
    if args.json:
        print(json.dumps(_reconcile.plan_to_dict(plan), indent=2))
        return 0
    print(f"plan for {machine}  (drift-key {plan.drift_key})")
    if not plan.surfaces and not plan.modules and not plan.resources:
        print("  no managed surfaces, resources, or modules (no requirement packages apply)")
        return 0
    for surface in plan.surfaces:
        owners = ", ".join(surface.contributing_packages)
        print(f"  surface {surface.key}  [{surface.disposition}]  <- {owners}")
    for mod in plan.modules:
        print(f"  module  {mod['name']}  <- {mod['source_repo']}")
    for res in plan.resources:
        owners = ", ".join(res.get("contributors", []))
        print(f"  resource {res['type']}:{res['id']}  [{res['summary']}]  <- {owners}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    machine = args.machine or _discover.current_machine()
    resolved = _reconcile.resolve_union(_collect_packages(machine), machine)
    findings = _validator.validate(resolved, machine)
    if args.json:
        print(json.dumps([f.__dict__ for f in findings], indent=2))
    else:
        if not findings:
            print("validator: no findings")
        for f in findings:
            print(f"  [{f.level}] {f.code}: {f.message}")
    return 1 if _validator.has_errors(findings) else 0


def _fmt_val(v: object) -> str:
    s = json.dumps(v) if not isinstance(v, str) else v
    return s if len(s) <= 80 else s[:77] + "..."


def _cmd_restore(args: argparse.Namespace) -> int:
    machine = args.machine or _discover.current_machine()
    dry_run = not args.apply
    packages = _collect_packages(machine)
    resolved = _reconcile.resolve_union(packages, machine)
    findings = _validator.validate(resolved, machine)
    if _validator.has_errors(findings):
        print("restore refused: validator reported errors:", file=sys.stderr)
        for f in findings:
            if f.level == "error":
                print(f"  {f.code}: {f.message}", file=sys.stderr)
        return 1

    result = _reconcile.restore(packages, machine, dry_run=dry_run, only=args.only)
    if args.json:
        print(json.dumps(_reconcile.restore_result_to_dict(result), indent=2))
        return 0 if result.ok else 2
    header = "DRY-RUN (preview only; re-run with --apply to make changes)" if dry_run else "APPLY"
    print(f"restore [{header}] for {machine}  (drift-key {result.plan.drift_key})")

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
                if "added" in ch:
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
            print(f"  module {r.name} <- {r.source_repo}: {status}")
            if show_output and r.stdout_tail:
                for line in r.stdout_tail.rstrip().splitlines():
                    print(f"      {line}")
            if r.returncode not in (0, None) and r.stderr_tail:
                print(f"      {r.stderr_tail.strip().splitlines()[-1]}", file=sys.stderr)
        else:
            print(f"  module {r.name} <- {r.source_repo}: skipped ({r.skipped_reason})")
    return 0 if result.ok else 2


def _cmd_todo(args: argparse.Namespace) -> int:
    print(f"'{args.verb}' is delivered by the surfaces package (issue #4006)", file=sys.stderr)
    return 2


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
    add("plan", _cmd_plan)
    add("validate", _cmd_validate)
    restore = add("restore", _cmd_restore)
    restore.add_argument("--apply", action="store_true",
                         help="make changes (default is a dry-run preview)")
    restore.add_argument("--only", action="append", metavar="NAME",
                         help="restrict to named surfaces/modules (repeatable)")
    restore.add_argument("--verbose", "-v", action="store_true",
                         help="show each module's captured output (shown by default in a dry-run)")
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
        print(f"manifest error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

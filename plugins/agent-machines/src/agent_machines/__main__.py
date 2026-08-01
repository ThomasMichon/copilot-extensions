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
    if not plan.surfaces:
        print("  no managed surfaces (no requirement packages apply)")
        return 0
    for surface in plan.surfaces:
        owners = ", ".join(surface.contributing_packages)
        print(f"  {surface.key}  [{surface.disposition}]  <- {owners}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    machine = args.machine or _discover.current_machine()
    resolved = _reconcile.resolve_union(_collect_packages(machine), machine)
    findings = _validator.validate(resolved)
    if args.json:
        print(json.dumps([f.__dict__ for f in findings], indent=2))
    else:
        if not findings:
            print("validator: no findings")
        for f in findings:
            print(f"  [{f.level}] {f.code}: {f.message}")
    return 1 if _validator.has_errors(findings) else 0


def _cmd_restore(args: argparse.Namespace) -> int:
    machine = args.machine or _discover.current_machine()
    packages = _collect_packages(machine)
    resolved = _reconcile.resolve_union(packages, machine)
    findings = _validator.validate(resolved)
    if _validator.has_errors(findings):
        print("restore refused: validator reported errors:", file=sys.stderr)
        for f in findings:
            if f.level == "error":
                print(f"  {f.code}: {f.message}", file=sys.stderr)
        return 1
    try:
        plan = _reconcile.restore(packages, machine, dry_run=args.dry_run)
    except NotImplementedError as exc:
        print(f"restore: {exc}", file=sys.stderr)
        return 2
    print(f"{'DRY-RUN ' if args.dry_run else ''}restore plan for {machine}  "
          f"(drift-key {plan.drift_key}): {len(plan.surfaces)} surface(s)")
    for surface in plan.surfaces:
        print(f"  {surface.key}  [{surface.disposition}]")
    return 0


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
    restore.add_argument("--dry-run", action="store_true", help="print the plan, do not apply")
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

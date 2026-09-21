"""``agent-machines fleet-update`` CLI verbs, split out of ``__main__.py``
(module-size cap) -- registers the ``fleet-update run|install|status|
uninstall`` subcommands and their resolve/dispatch logic.

Deliberately self-contained (does not import ``__main__.py``, avoiding a
circular dependency): the machine-identity resolution helpers below are a
small, intentional duplication of ``__main__._resolve_machine_identity`` /
``__main__._emit_identity_warnings`` rather than a shared import, since those
two are already used by several unrelated commands in ``__main__.py`` and
extracting them would be a separate, higher-risk refactor.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import discover as _discover
from . import fleet_update as _fleet_update
from . import fleet_update_state as _fleet_update_state
from . import identity as _identity
from . import layout as _layout
from . import reconcile as _reconcile
from . import resources as _resources
from . import validator as _validator
from .manifest import RequirementPackage


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
        topology_repos.extend(candidate.path for candidate in _discover.candidate_repos())
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


def _resolve_fleet_update_resources(
    args: argparse.Namespace,
) -> tuple[_identity.MachineIdentity, str, dict[str, object]] | None:
    identity = _resolve_machine_identity(args)
    _emit_identity_warnings(identity)
    machine = identity.canonical
    packages = _collect_all_packages(machine, identity.accepted)
    resolved = _reconcile.resolve_union(packages, machine, identity.accepted)
    findings = _validator.validate(resolved, machine)
    if _validator.has_errors(findings):
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "validator reported errors",
                        "findings": [f.__dict__ for f in findings],
                    },
                    indent=2,
                )
            )
        else:
            print("fleet-update refused: validator reported errors:", file=sys.stderr)
            for finding in findings:
                if finding.level == "error":
                    print(f"  {finding.code}: {finding.message}", file=sys.stderr)
        return None
    resolved_resources, _ = _resources.resolve_resources(
        resolved,
        machine,
        _discover.current_platform(),
    )
    return (
        identity,
        machine,
        _fleet_update_state.selected_tiers_from_resolved(resolved_resources),
    )


def _selected_fleet_update_tiers(args: argparse.Namespace) -> list[str]:
    selected = getattr(args, "tier", None)
    if not selected:
        return sorted(_fleet_update.TIER_SPECS)
    return list(dict.fromkeys(selected))


def cmd_run(args: argparse.Namespace) -> int:
    resolved_state = _resolve_fleet_update_resources(args)
    if resolved_state is None:
        return 1
    _identity_result, _machine, selected_resources = resolved_state
    resource = selected_resources.get(args.tier)
    result = _fleet_update.run_tier(
        args.tier,
        opted_in=_fleet_update_state.tier_enabled(resource),
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(_fleet_update.format_result(result))
    return 0 if result.ok else 2


def cmd_install(args: argparse.Namespace) -> int:
    resolved_state = _resolve_fleet_update_resources(args)
    if resolved_state is None:
        return 1
    _identity_result, _machine, selected_resources = resolved_state
    results = []
    for tier in _selected_fleet_update_tiers(args):
        resource = selected_resources.get(tier)
        if not _fleet_update_state.tier_enabled(resource):
            results.append(
                _fleet_update.ScheduledTaskReconcileResult(
                    tier=tier,
                    desired_state="absent",
                    status="skipped",
                    changed=False,
                    detail=f"tier {tier!r} is not opted in",
                )
            )
            continue
        results.append(
            _fleet_update.reconcile_scheduled_task(
                tier,
                desired_present=True,
            )
        )
    if args.json:
        payload = {
            "ok": all(result.ok for result in results),
            "tiers": [r.to_dict() for r in results],
        }
        print(json.dumps(payload, indent=2))
    else:
        for result in results:
            print(f"{result.tier}: {result.status}")
            print(f"  {result.detail}")
    return 0 if all(result.ok or result.status == "skipped" for result in results) else 2


def cmd_status(args: argparse.Namespace) -> int:
    resolved_state = _resolve_fleet_update_resources(args)
    if resolved_state is None:
        return 1
    _identity_result, _machine, selected_resources = resolved_state
    results = []
    for tier in _selected_fleet_update_tiers(args):
        resource = selected_resources.get(tier)
        results.append(
            _fleet_update.scheduled_task_status(
                tier,
                opted_in=_fleet_update_state.tier_enabled(resource),
            )
        )
    if args.json:
        print(
            json.dumps(
                {"ok": True, "tiers": [result.to_dict() for result in results]},
                indent=2,
            )
        )
    else:
        for result in results:
            print(
                f"{result.tier}: opted-in={'yes' if result.opted_in else 'no'}; "
                f"registered={'yes' if result.registered else 'no'}; "
                f"matching={'yes' if result.matching else 'no'}"
            )
            print(f"  {result.detail}")
            if result.last_attempt:
                print(f"  last-attempt: {result.last_attempt}")
            if result.last_success:
                print(f"  last-success: {result.last_success}")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    results = [
        _fleet_update.reconcile_scheduled_task(
            tier,
            desired_present=False,
        )
        for tier in _selected_fleet_update_tiers(args)
    ]
    if args.json:
        print(
            json.dumps(
                {
                    "ok": all(result.ok for result in results),
                    "tiers": [r.to_dict() for r in results],
                },
                indent=2,
            )
        )
    else:
        for result in results:
            print(f"{result.tier}: {result.status}")
            print(f"  {result.detail}")
    return 0 if all(result.ok or result.status == "skipped" for result in results) else 2


def register_parser(sub: argparse._SubParsersAction) -> None:
    """Wire the ``fleet-update`` subcommand tree onto agent-machines' parser."""
    fleet_update = sub.add_parser(
        "fleet-update",
        help="Run or manage the unattended fleet-update (worktree-manager update) sweep.",
    )
    fleet_update.add_argument("--machine", help="override the target machine name")
    fleet_update.add_argument("--json", action="store_true", help="emit JSON")
    fleet_update_sub = fleet_update.add_subparsers(dest="fleet_update_command")
    fleet_update_run = fleet_update_sub.add_parser(
        "run",
        help="Run one unattended fleet-update tier now.",
    )
    fleet_update_run.add_argument(
        "--tier",
        required=True,
        choices=sorted(_fleet_update.TIER_SPECS),
        help="the unattended fleet-update tier to run",
    )
    fleet_update_run.add_argument("--machine", help="override the target machine name")
    fleet_update_run.add_argument("--json", action="store_true", help="emit JSON")
    fleet_update_run.set_defaults(func=cmd_run)
    for subcommand, func, help_text in (
        ("install", cmd_install, "Register opted-in fleet-update Scheduled Tasks."),
        ("status", cmd_status, "Inspect fleet-update opt-in and Scheduled Task state."),
        ("uninstall", cmd_uninstall, "Remove fleet-update Scheduled Tasks."),
    ):
        current = fleet_update_sub.add_parser(subcommand, help=help_text)
        current.add_argument(
            "--tier",
            action="append",
            choices=sorted(_fleet_update.TIER_SPECS),
            help="restrict to one unattended fleet-update tier (repeatable)",
        )
        current.add_argument("--machine", help="override the target machine name")
        current.add_argument("--json", action="store_true", help="emit JSON")
        current.set_defaults(func=func)

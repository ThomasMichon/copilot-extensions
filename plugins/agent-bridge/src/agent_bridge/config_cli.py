"""Configuration and registry-audit commands for ``agent-bridge``."""

from __future__ import annotations

import argparse
import sys


def _core():
    from . import __main__ as core

    return core


def _cmd_config_show(args: argparse.Namespace) -> None:
    from .config import config_dir, load_config

    core = _core()
    cfg = load_config()
    cfg_path = config_dir() / "config.yaml"
    if args.json:
        core._json_out(cfg.model_dump())
        return
    print(f"Config: {cfg_path}")
    print(f"  port: {cfg.port}")
    print(f"  bind: {cfg.bind}")
    print(f"  db_path: {cfg.db_path}")
    print(f"  log_level: {cfg.log_level}")
    print()
    if cfg.topologies:
        print("Topologies:")
        for name, profile in cfg.topologies.items():
            print(f"  {name}:")
            if profile.machines_yaml:
                print(f"    machines_yaml: {profile.machines_yaml}")
            if profile.agents_config:
                print(f"    agents_config: {profile.agents_config}")
    else:
        print("Topologies: (none)")


def _cmd_config_adopt(args: argparse.Namespace) -> None:
    from .config import adopt_topology

    try:
        cfg = adopt_topology(
            profile_name=args.profile,
            repo_path=args.repo,
            machines_yaml=getattr(args, "machines_yaml", None),
            agents_config=getattr(args, "agents_config", None),
        )
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        sys.exit(1)

    profile = cfg.topologies[args.profile]
    print(f"[OK] Topology profile '{args.profile}' configured")
    if profile.machines_yaml:
        print(f"  machines_yaml: {profile.machines_yaml}")
    if profile.agents_config:
        print(f"  agents_config: {profile.agents_config}")
    print()
    print("[>] Restart agent-bridge to load the new topology")


def _cmd_config_remove(args: argparse.Namespace) -> None:
    from .config import remove_topology

    try:
        remove_topology(args.profile)
    except KeyError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] Topology profile '{args.profile}' removed")


def _cmd_config_validate(args: argparse.Namespace) -> None:
    from .config import validate_config

    issues = validate_config()
    if not issues:
        print("[OK] Configuration is valid")
        return
    print(f"[WARN] {len(issues)} issue(s) found:")
    for issue in issues:
        print(f"  - {issue}")
    sys.exit(1)


def _cmd_config_migrate(args: argparse.Namespace) -> None:
    from . import config_migrations

    if not config_migrations.available():
        print("config-migrate: migration library unavailable; skipping")
        return
    print(config_migrations.summarize(config_migrations.run_migrations()))


def _print_registry_findings(name: str, report, unit: str) -> None:
    if not report.findings:
        print(f"[OK] {name} is {report.snapshot.authority.value}; {len(report.manifests)} {unit} active.")
        return
    print(f"[WARN] {name} has {len(report.findings)} finding(s); valid entries remain available:")
    for finding in report.findings:
        target = f" -> {finding.target}" if finding.target else ""
        print(f"  - {finding.reason}: {finding.entry}{target}")
        if finding.detail:
            print(f"    {finding.detail}")
        if finding.remedy:
            print(f"    {finding.remedy}")


def _cmd_doctor(args: argparse.Namespace) -> None:
    from .cold_store_sources import scan_cold_store_registry
    from .provider_sources import scan_provider_registry

    core = _core()
    report = scan_provider_registry()
    cold_report = scan_cold_store_registry()
    if args.json:
        core._json_out(
            {
                "registry": "providers.d",
                "authority": report.snapshot.authority.value,
                "active": sorted(report.manifests),
                "findings": [f.to_dict() for f in report.findings],
                "cold_store_registry": "cold-store-providers.d",
                "cold_store_authority": cold_report.snapshot.authority.value,
                "cold_store_active": sorted(cold_report.manifests),
                "cold_store_findings": [f.to_dict() for f in cold_report.findings],
            }
        )
    else:
        _print_registry_findings("providers.d", report, "provider namespace(s)")
        _print_registry_findings("cold-store-providers.d", cold_report, "capability/capabilities")
    if report.findings or cold_report.findings:
        sys.exit(1)


def register_config_commands(sub: argparse._SubParsersAction) -> None:
    doctor_p = sub.add_parser("doctor", help="Audit provider drop-ins and report exact stale-entry cleanup")
    doctor_p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit structured provider findings")
    doctor_p.set_defaults(func=_cmd_doctor)

    config_p = sub.add_parser("config", help="Manage configuration and topology profiles")
    config_sub = config_p.add_subparsers(dest="config_command")

    config_show_p = config_sub.add_parser("show", help="Show current config")
    config_show_p.set_defaults(func=_cmd_config_show)

    config_adopt_p = config_sub.add_parser("adopt", help="Add/update a topology profile for a repo")
    config_adopt_p.add_argument("--repo", required=True, help="Path to the repo root (containing machines.yaml)")
    config_adopt_p.add_argument("--profile", required=True, help="Topology profile name (e.g. 'multi-machine system', 'my-control-harness')")
    config_adopt_p.add_argument("--machines-yaml", help="Explicit path to machines.yaml (auto-discovered if omitted)")
    config_adopt_p.add_argument("--agents-config", help="(Deprecated) Explicit path to an acp-agents.json override. The roster is derived from machines.yaml; this is no longer auto-discovered.")
    config_adopt_p.set_defaults(func=_cmd_config_adopt)

    config_remove_p = config_sub.add_parser("remove", help="Remove a topology profile")
    config_remove_p.add_argument("profile", help="Profile name to remove")
    config_remove_p.set_defaults(func=_cmd_config_remove)

    config_validate_p = config_sub.add_parser("validate", help="Validate current configuration")
    config_validate_p.set_defaults(func=_cmd_config_validate)

    config_migrate_p = config_sub.add_parser("migrate", help="Migrate machine-local config.yaml schema (idempotent)")
    config_migrate_p.set_defaults(func=_cmd_config_migrate)

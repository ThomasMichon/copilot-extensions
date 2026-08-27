"""CLI entry point for agent-ssh.

Subcommands:
  doctor         Audit managed OpenSSH fragments without changing them.
  emit-profile   Render/write a managed SSH profile fragment.
  explore        Introspect a reachable SSH target (repos, runtimes, agents).
  mesh-status    Render the calling repo's SSH machine mesh from machines.yaml.
  verify         Probe machine-name SSH reachability using the active profile.
  version        Show package version.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from agent_procutil import no_window_flags

from . import __version__, fragment_registry, ssh_profile
from . import explore as explore_mod
from . import mesh as mesh_mod


def _cmd_emit_profile(args: argparse.Namespace) -> int:
    cfg = ssh_profile.load_file(args.config)
    module = ssh_profile.load_file(args.module)
    if not isinstance(cfg, dict) or not isinstance(module, dict):
        print("[FAIL] registry and module roots must be mappings", file=sys.stderr)
        return 2
    module_name = module.get("module")
    if not isinstance(module_name, str) or not ssh_profile.is_valid_transport(module_name):
        print("[FAIL] module.yaml has an invalid 'module' name", file=sys.stderr)
        return 2
    if cfg.get("transport") != module_name:
        print(
            "[FAIL] registry 'transport' must match module.yaml 'module'",
            file=sys.stderr,
        )
        return 2

    try:
        if args.print:
            sys.stdout.write(
                ssh_profile.render_fragment(
                    cfg,
                    module,
                    registry_path=args.config.resolve(strict=True),
                    module_path=args.module.resolve(strict=True),
                )
            )
            return 0

        frag = ssh_profile.write_fragment(
            cfg,
            module,
            config_d=args.config_d,
            ssh_config=args.ssh_config,
            registry_path=args.config.resolve(strict=True),
            module_path=args.module.resolve(strict=True),
        )
    except (KeyError, TypeError, ValueError) as exc:
        print(f"[FAIL] cannot render managed SSH fragment: {exc}", file=sys.stderr)
        return 2
    fragment_registry.FragmentRegistry(args.config_d).refresh()
    print(f"[OK] wrote {len(cfg.get('machines', []))} host block(s) to {frag}")
    return 0


def _creation_flags() -> int:
    return no_window_flags()


def _cmd_verify(args: argparse.Namespace) -> int:
    if not args.names:
        print("agent-ssh verify: at least one host name is required", file=sys.stderr)
        return 2
    report = fragment_registry.FragmentRegistry(args.config_d).refresh()
    rc = 0
    for name in args.names:
        if not report.permits_probe(name):
            print(
                f"[FAIL] {name} is not permitted by current managed-profile evidence; "
                "run `agent-ssh doctor`"
            )
            rc = 1
            continue
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={args.timeout}",
                "-o",
                "StrictHostKeyChecking=accept-new",
                name,
                "true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
            check=False,
        )
        if proc.returncode == 0:
            print(f"[OK]   {name} reachable")
        else:
            print(f"[FAIL] {name} unreachable")
            rc = 1
    return rc


def _cmd_explore(args: argparse.Namespace) -> int:
    report = fragment_registry.FragmentRegistry(args.config_d).refresh()
    if not report.permits_probe(args.target):
        result = explore_mod.ExploreResult(
            target=args.target,
            reachable=False,
            error=(
                "target is not permitted by current managed-profile evidence; "
                "run `agent-ssh doctor`"
            ),
        )
        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(explore_mod.format_report(result))
        return 1
    result = explore_mod.explore(args.target, timeout=args.timeout)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(explore_mod.format_report(result))
    return 0 if result.reachable else 1


def _cmd_mesh_status(args: argparse.Namespace) -> int:
    fragment_registry.FragmentRegistry(args.config_d).refresh()
    path = args.path
    if path is None:
        path = mesh_mod.find_machines_file()
    if path is None or not Path(path).is_file():
        msg = "no machines.yaml found for this repo"
        if args.json:
            print(json.dumps({"project": "", "source": "", "machines": []}))
        elif args.summary:
            print(msg)
        else:
            print(f"agent-ssh mesh-status: {msg}")
        return 0
    mesh = mesh_mod.load_mesh(Path(path))
    if args.json:
        print(json.dumps(mesh.to_dict(), indent=2))
    elif args.summary:
        print(mesh_mod.summary_line(mesh))
    else:
        print(mesh_mod.format_report(mesh))
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    report = fragment_registry.scan_fragment_registry(args.config_d)
    if args.json:
        print(
            json.dumps(
                fragment_registry.doctor_payload(report, args.config_d),
                indent=2,
            )
        )
    else:
        print(fragment_registry.format_doctor(report, args.config_d))
    return 1 if report.findings else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-ssh",
        description="Emit and verify machine-name SSH profiles for pluggable transports.",
    )
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    sub = parser.add_subparsers(dest="command")

    emit = sub.add_parser(
        "emit-profile",
        help="Emit a managed SSH config.d fragment from a transport module and registry.",
    )
    emit.add_argument("config", type=Path, help="Normalized machine registry (YAML/JSON).")
    emit.add_argument("--module", type=Path, required=True, help="Transport module.yaml recipe.")
    emit.add_argument("--config-d", type=Path, default=None, help="Override ~/.ssh/config.d.")
    emit.add_argument("--ssh-config", type=Path, default=None, help="Override ~/.ssh/config.")
    emit.add_argument("--print", action="store_true", help="Print the fragment; do not write.")
    emit.set_defaults(func=_cmd_emit_profile)

    verify = sub.add_parser("verify", help="Probe SSH reachability by host alias.")
    verify.add_argument("--timeout", type=int, default=8, help="SSH ConnectTimeout seconds.")
    verify.add_argument(
        "--config-d",
        type=Path,
        default=None,
        help="Override the managed-fragment audit directory; SSH still uses its active config.",
    )
    verify.add_argument("names", nargs="*", help="Host aliases to probe.")
    verify.set_defaults(func=_cmd_verify)

    explore = sub.add_parser(
        "explore",
        help="Introspect a reachable SSH target: its checked-out repos + locations, "
        "installed fabric runtimes, and the agents that fall out of them.",
    )
    explore.add_argument("target", help="SSH host alias to introspect (ssh <target>).")
    explore.add_argument(
        "--timeout", type=int, default=10, help="SSH ConnectTimeout seconds."
    )
    explore.add_argument(
        "--json", action="store_true", help="Emit the structured result as JSON."
    )
    explore.add_argument(
        "--config-d",
        type=Path,
        default=None,
        help="Override the managed-fragment audit directory; SSH still uses its active config.",
    )
    explore.set_defaults(func=_cmd_explore)

    mesh = sub.add_parser(
        "mesh-status",
        help="Render the calling repo's SSH machine mesh from machines.yaml "
        "(per-host role, reachability, and aliases). Config-driven, read-only.",
    )
    mesh.add_argument(
        "--path",
        type=Path,
        default=None,
        help="Path to a machines.yaml (default: resolve from the current repo).",
    )
    mesh.add_argument(
        "--json", action="store_true", help="Emit the structured mesh as JSON."
    )
    mesh.add_argument(
        "--summary", action="store_true", help="Print a one-line summary only."
    )
    mesh.add_argument(
        "--config-d",
        type=Path,
        default=None,
        help="Override the managed-fragment audit directory only.",
    )
    mesh.set_defaults(func=_cmd_mesh_status)

    doctor = sub.add_parser(
        "doctor",
        help="Audit managed 50-agent-ssh-* OpenSSH fragments without changing them.",
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        help="Emit exhaustive structured managed-fragment findings.",
    )
    doctor.add_argument("--config-d", type=Path, default=None, help="Override ~/.ssh/config.d.")
    doctor.set_defaults(func=_cmd_doctor)

    sub.add_parser("version", help="Show version")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version or args.command == "version":
        print(f"agent-ssh {__version__}")
        return 0
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

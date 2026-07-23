"""CLI entry point for agent-ssh.

Subcommands:
  emit-profile   Render/write a managed SSH profile fragment.
  explore        Introspect a reachable SSH target (repos, runtimes, agents).
  verify         Probe machine-name SSH reachability using the active profile.
  version        Show package version.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__
from . import explore as explore_mod
from . import ssh_profile


def _cmd_emit_profile(args: argparse.Namespace) -> int:
    cfg = ssh_profile.load_file(args.config)
    module = ssh_profile.load_file(args.module)
    if "module" not in module or not isinstance(module.get("module"), str):
        print("[FAIL] module.yaml missing required 'module' name", file=sys.stderr)
        return 2

    if args.print:
        sys.stdout.write(ssh_profile.render_fragment(cfg, module))
        return 0

    frag = ssh_profile.write_fragment(
        cfg,
        module,
        config_d=args.config_d,
        ssh_config=args.ssh_config,
    )
    print(f"[OK] wrote {len(cfg.get('machines', []))} host block(s) to {frag}")
    return 0


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    if not args.names:
        print("agent-ssh verify: at least one host name is required", file=sys.stderr)
        return 2
    rc = 0
    for name in args.names:
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
    result = explore_mod.explore(args.target, timeout=args.timeout)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(explore_mod.format_report(result))
    return 0 if result.reachable else 1


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
    explore.set_defaults(func=_cmd_explore)

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

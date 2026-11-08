"""``agent-containers drivers`` -- list registered runtime drivers."""

from __future__ import annotations

import argparse
import json

from .external_driver import BUNDLED_DRIVERS, PATH_PREFIX, drivers_dir, scan_drivers


def cmd_runtime_proxy(args: argparse.Namespace) -> int:
    """Docker-shaped ``inspect``/``exec`` for one runtime-driver member.

    agent-bridge's pre-launch hosting check speaks Docker; for a driver-backed
    venue it calls this instead, so the check reaches the member through its
    driver with the same identity pinning. Non-driver targets are refused.
    """
    import sys

    from . import lifecycle, runtime_drivers
    from .config import load_config
    from .routed_exec import _parse_docker_exec

    argv = list(args.argv[1:] if args.argv[:1] == ["--"] else args.argv)
    fleet = getattr(args, "fleet", None)
    # With --fleet (agent-bridge passes the venue's fleet) only that fleet's
    # driver is asked, so another runtime can neither delay nor answer for it.
    runtime_drivers.discover(load_config(), fleet=fleet)
    if len(argv) == 2 and argv[0] == "inspect":
        if runtime_drivers.owner_of(argv[1]) is None:
            raise RuntimeError(f"'{argv[1]}' is not a runtime-driver member")
        print(json.dumps([lifecycle.inspect_container(argv[1])]))
        return 0
    if argv[:1] == ["exec"]:
        parsed = _parse_docker_exec(["docker", *argv])
        payload = sys.stdin.read() if parsed is not None and parsed.interactive else None
        done = runtime_drivers.run_routed(["docker", *argv], input_text=payload, timeout=600)
        if done is None:
            raise RuntimeError("runtime-proxy exec target is not a runtime-driver member")
        sys.stdout.write(done.stdout)
        sys.stderr.write(done.stderr)
        return done.returncode
    raise RuntimeError(
        "usage: runtime-proxy [--fleet NAME] inspect <id> | exec [options] <id> <argv...>"
    )


def cmd_drivers(args: argparse.Namespace) -> int:
    specs, findings = scan_drivers()
    if args.json:
        print(json.dumps({
            "drivers_dir": str(drivers_dir()),
            "drivers": [
                {"name": s.name, "command": list(s.command), "source": s.source}
                for s in specs.values()
            ],
            "findings": findings,
            "bundled_drivers": list(BUNDLED_DRIVERS),
        }, indent=2))
        return 0
    print(f"Driver manifests: {drivers_dir()}")
    print(f"Bundled fallback: {', '.join(BUNDLED_DRIVERS)} "
          "(explicit manifest/PATH registration takes precedence).")
    if not specs:
        print(f"No manifest drivers (PATH fallback: {PATH_PREFIX}<name>).")
    for spec in specs.values():
        print(f"  {spec.name:<20} {' '.join(spec.command)}")
    for finding in findings:
        print(f"  INVALID {finding['path']}: {finding['reason']}")
    return 1 if findings else 0

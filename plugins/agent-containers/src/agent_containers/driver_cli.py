"""``agent-containers drivers`` -- list registered runtime drivers."""

from __future__ import annotations

import argparse
import json


def add_runtime_proxy_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fleet")
    parser.add_argument("--member")
    parser.add_argument("--expected-runtime")
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    parser.set_defaults(func=cmd_runtime_proxy)


def cmd_runtime_proxy(args: argparse.Namespace) -> int:
    """Docker-shaped ``inspect``/``exec`` for one runtime-driver member.

    agent-bridge's pre-launch hosting check speaks Docker; for a driver-backed
    venue it calls this instead, so the check reaches the member through its
    driver with the same identity pinning. Non-driver targets are refused.
    """
    import sys

    from . import lifecycle, runtime_drivers
    from .bound_proxy import bound_proxy_route
    from .config import load_config
    from .routed_exec import _parse_docker_exec, _run_exec, run_routed

    argv = list(args.argv[1:] if args.argv[:1] == ["--"] else args.argv)
    inspect = len(argv) == 2 and argv[0] == "inspect"
    parsed = _parse_docker_exec(["docker", *argv])
    if not inspect and parsed is None:
        raise RuntimeError(
            "usage: runtime-proxy [--fleet NAME [--member NAME --expected-runtime RUNTIME]] "
            "inspect <id> | exec [options] <id> <argv...>"
        )

    fleet = args.fleet
    bound = args.member is not None or args.expected_runtime is not None
    config = load_config(strict=bound)
    route = None
    if bound:
        if not (fleet and args.member and args.expected_runtime):
            raise RuntimeError("Bound runtime proxy requires --fleet, --member and --expected-runtime")
        if inspect:
            target = argv[1]
        else:
            assert parsed is not None
            target = parsed.target
        route = bound_proxy_route(
            config, fleet, args.member, target,
            args.expected_runtime,
        )
    else:
        runtime_drivers.discover(config, fleet=fleet)

    if inspect:
        if route is not None:
            document = route.driver.inspect(route.name, instance_id=route.instance_id)
        else:
            if runtime_drivers.owner_of(argv[1]) is None:
                raise RuntimeError(f"'{argv[1]}' is not a runtime-driver member")
            document = lifecycle.inspect_container(argv[1])
        print(json.dumps([document]))
        return 0
    assert parsed is not None
    payload = sys.stdin.read() if parsed.interactive else None
    done = (
        _run_exec(parsed, route, input_text=payload, timeout=600) if route is not None
        else run_routed(["docker", *argv], input_text=payload, timeout=600)
    )
    if done is None:
        raise RuntimeError("runtime-proxy exec target is not a runtime-driver member")
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    return done.returncode


def cmd_drivers(args: argparse.Namespace) -> int:
    from .external_driver import BUNDLED_DRIVERS, PATH_PREFIX, drivers_dir, scan_drivers

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

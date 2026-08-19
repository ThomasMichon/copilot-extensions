"""CLI entry point for agent-containers.

Subcommands:
  fleet                 List fleet containers + lease status
  up <fleet>            Provision/top-up a fleet to its configured size
  down <fleet>          Stop (keep warm) all containers in a fleet
  start <fleet>         Start all stopped containers in a fleet
  rm <fleet>            Remove all containers in a fleet (destructive)
  borrow <effort>       Lease a free container to an effort
  release <target>      Release a lease (by container or effort name)
  leases                Show active leases
  exec <name>           Run the ACP launch command in a container (testing)
  version               Show version
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys

from . import __version__
from .config import load_config
from .resolver import build_spawn_command, host_gh_token

log = logging.getLogger("agent-containers")


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-containers",
        description="Local Docker dev-container fleet + lease broker",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    sub = parser.add_subparsers(dest="command")

    fleet_p = sub.add_parser("fleet", help="List fleet containers + lease status")
    fleet_p.add_argument(
        "--json", action="store_true", help="Emit the fleet as a JSON array."
    )

    up_p = sub.add_parser("up", help="Provision/top-up a fleet")
    up_p.add_argument("fleet", help="Fleet name (from containers.yaml)")
    up_p.add_argument("--count", type=int, default=None, help="Target size")

    for name, helptext in (
        ("down", "Stop (keep warm) all containers in a fleet"),
        ("start", "Start all stopped containers in a fleet"),
        ("rm", "Remove all containers in a fleet (destructive)"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("fleet", help="Fleet name")
        if name == "rm":
            p.add_argument("--force", action="store_true", help="Force removal")

    borrow_p = sub.add_parser("borrow", help="Lease a free container to an effort")
    borrow_p.add_argument("effort", help="Effort name (lease holder)")
    borrow_p.add_argument("--container", help="Borrow a specific container")
    borrow_p.add_argument("--fleet", help="Restrict to a fleet")

    release_p = sub.add_parser("release", help="Release a lease")
    release_p.add_argument("target", help="Container name or effort name")

    sub.add_parser("leases", help="Show active leases")

    exec_p = sub.add_parser("exec", help="Run the ACP launch command in a container")
    exec_p.add_argument("name", help="Container name")
    exec_p.add_argument(
        "--stdio", action="store_true",
        help="Attach stdio (ACP transport) instead of a one-shot probe",
    )

    sub.add_parser("version", help="Show version")

    sub.add_parser(
        "config-migrate",
        help="Migrate machine-local config schema (~/.agent-containers/containers.yaml)",
    )

    # --- namespace-* (process-boundary resolver seam for agent-bridge, #892 Inc 3b)
    # The `container:` namespace resolver over a process boundary: agent-bridge
    # drives these instead of importing `agent_containers.resolver`. Emit plain
    # JSON (agent_bridge-free); the bridge shim reconstructs SpawnTarget /
    # NamespaceAgentInfo. Containers do not support cross-repo / plugins, so
    # namespace-resolve takes only a name (mirrors the resolver's resolve(name)).
    sub.add_parser(
        "namespace-list",
        help="Print JSON list of container agents for the `container:` namespace.",
    )
    ns_resolve_p = sub.add_parser(
        "namespace-resolve",
        help="Print JSON {type,spawn_command,user} resolving a container name "
        "(not-found -> exit 3).",
    )
    ns_resolve_p.add_argument("name", help="Container name")
    ns_target_p = sub.add_parser(
        "namespace-target-repo",
        help="Print the workspace repo a container hosts (always empty -- "
        "containers do not drive related-repo plugin injection).",
    )
    ns_target_p.add_argument("name", help="Container name")
    ns_ready_p = sub.add_parser(
        "namespace-ensure-ready",
        help="Exit 0 if the container is running/startable, else exit 1.",
    )
    ns_ready_p.add_argument("name", help="Container name")

    # --- relay-profile (declarative credential-relay seam for agent-bridge #892 Inc 2)
    sub.add_parser(
        "relay-profile",
        help="Print JSON credential-relay profile (sources/azure_resources/"
        "gated_actions/token_store) for agent-bridge to apply.",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "fleet":
            return _cmd_fleet(args)
        if args.command == "up":
            return _cmd_up(args)
        if args.command in ("down", "start", "rm"):
            return _cmd_fleet_op(args)
        if args.command == "borrow":
            return _cmd_borrow(args)
        if args.command == "release":
            return _cmd_release(args)
        if args.command == "leases":
            return _cmd_leases()
        if args.command == "exec":
            return _cmd_exec(args)
        if args.command == "version":
            print(f"agent-containers {__version__}")
            return 0
        if args.command == "config-migrate":
            from . import config_migrations

            if not config_migrations.available():
                print("config-migrate: migration library unavailable; skipping")
                return 0
            print(config_migrations.summarize(config_migrations.run_migrations()))
            return 0
        if args.command == "namespace-list":
            return _cmd_namespace_list()
        if args.command == "namespace-resolve":
            return _cmd_namespace_resolve(args)
        if args.command == "namespace-target-repo":
            print("")  # containers do not drive related-repo plugin injection
            return 0
        if args.command == "namespace-ensure-ready":
            return _cmd_namespace_ensure_ready(args)
        if args.command == "relay-profile":
            return _cmd_relay_profile()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


# --- namespace-* resolver seam (#892 Inc 3b) -------------------------------
# Process-boundary form of the `container:` NamespaceResolver: agent-bridge
# shells out to these instead of importing `agent_containers.resolver`. Emit
# plain JSON (agent_bridge-free) via the resolver's `*_spec` cores; the bridge
# shim reconstructs SpawnTarget / NamespaceAgentInfo. not-found -> exit 3 (the
# bridge maps it back to KeyError). Containers have no bad-state distinction
# (ensure_ready starts a stopped one), so there is no exit-4 case here.
_NS_NOT_FOUND_EXIT = 3


def _cmd_namespace_list() -> int:
    """Print a JSON list of `container:` namespace agent specs (#892 Inc 3b)."""
    import asyncio

    from .resolver import ContainerResolver

    print(json.dumps(asyncio.run(ContainerResolver().list_specs())))
    return 0


def _cmd_namespace_resolve(args: argparse.Namespace) -> int:
    """Print a JSON spawn spec resolving a container name (#892 Inc 3b).

    A not-found maps to exit 3 (bridge -> ``KeyError``), preserving the
    resolver's contract across the process boundary.
    """
    import asyncio

    from .resolver import ContainerResolver

    try:
        spec = asyncio.run(ContainerResolver().resolve_spec(args.name))
    except KeyError as e:
        print(str(e).strip("'"), file=sys.stderr)
        return _NS_NOT_FOUND_EXIT
    print(json.dumps(spec))
    return 0


def _cmd_namespace_ensure_ready(args: argparse.Namespace) -> int:
    """Exit 0 if the container is running/startable, else 1 (#892 Inc 3b)."""
    import asyncio

    from .resolver import ContainerResolver

    try:
        asyncio.run(ContainerResolver().ensure_ready(args.name))
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def _cmd_relay_profile() -> int:
    """Print the declarative credential-relay profile as JSON (#892 Inc 2).

    The process-boundary seam agent-bridge applies (with a file-backed token
    validator) instead of importing ``agent_containers.relay_provider`` in the
    bridge venv. Emits the same policy the in-process ``register_relay`` applies.
    """
    from .relay_provider import relay_profile

    print(json.dumps(relay_profile()))
    return 0


def _cmd_fleet(args: argparse.Namespace) -> int:
    from .lease import get_lease
    from .lifecycle import list_containers
    config = load_config()
    containers = list_containers(config)
    if getattr(args, "json", False):
        # Bare JSON array, one object per container, so the picker's Containers
        # pivot (and any other machine-readable consumer) can render the fleet.
        out = []
        for c in containers:
            lease = get_lease(c.name)
            out.append({
                "name": c.name,
                "container_id": c.container_id,
                "image": c.image,
                "state": c.state,
                "status": c.status,
                "fleet": c.fleet,
                "local_folder": c.local_folder,
                "lease": lease.effort if lease else None,
            })
        print(json.dumps(out, indent=2, default=str))
        return 0
    if not containers:
        print("No fleet containers found. Run `agent-containers up <fleet>`.")
        return 0
    print(f"{'CONTAINER':<28} {'STATE':<10} {'FLEET':<12} {'LEASE'}")
    for c in containers:
        lease = get_lease(c.name)
        holder = lease.effort if lease else "-"
        print(f"{c.name:<28} {c.state:<10} {(c.fleet or '-'):<12} {holder}")
    return 0


def _cmd_up(args: argparse.Namespace) -> int:
    from . import fleet as fleet_mod

    config = load_config()
    created = fleet_mod.up(config, args.fleet, count=args.count)
    if created:
        print(f"Created: {', '.join(created)}")
    else:
        print("Fleet already at target size.")
    return 0


def _cmd_fleet_op(args: argparse.Namespace) -> int:
    from . import fleet as fleet_mod

    config = load_config()
    if args.command == "down":
        names = fleet_mod.down(config, args.fleet)
        print(f"Stopped: {', '.join(names) if names else '(none)'}")
    elif args.command == "start":
        names = fleet_mod.start(config, args.fleet)
        print(f"Started: {', '.join(names) if names else '(none)'}")
    elif args.command == "rm":
        names = fleet_mod.rm(config, args.fleet, force=args.force)
        print(f"Removed: {', '.join(names) if names else '(none)'}")
    return 0


def _cmd_borrow(args: argparse.Namespace) -> int:
    from .lease import borrow

    config = load_config()
    lease = borrow(config, args.effort, container=args.container, fleet=args.fleet)
    print(lease.container)
    return 0


def _cmd_release(args: argparse.Namespace) -> int:
    from .lease import release

    if release(args.target):
        print(f"Released: {args.target}")
        return 0
    print(f"No lease found for '{args.target}'", file=sys.stderr)
    return 1


def _cmd_leases() -> int:
    from .lease import list_leases

    leases = list_leases()
    if not leases:
        print("No active leases.")
        return 0
    print(f"{'CONTAINER':<28} {'EFFORT':<24} {'HOST':<16} {'PID'}")
    for lease in leases:
        print(f"{lease.container:<28} {lease.effort:<24} {lease.host:<16} {lease.pid}")
    return 0


def _resolve_relay_port(default: int) -> int:
    """Resolve the credential-relay port, preferring agent-bridge's LIVE port.

    agent-bridge owns and hosts the shared credential relay and publishes the
    port it *actually* bound via ``relay_state`` (the daemon binds an ephemeral
    loopback port by default -- see dotfiles #694/#540 -- rather than a fixed
    well-known one). The SSH transport already rewrites ``LC_GIT_CREDENTIAL_RELAY``
    to this live port (``transport._effective_auth_hooks``); the container path
    must do the same or it injects a stale well-known port and every in-container
    ADO/git + build-cache auth call targets a dead port once the relay moves
    (dotfiles #1631).

    ``get_live_relay_port()`` reads the primary daemon's published
    ``~/.agent-bridge/relay-port`` file, so this works from the separate
    ``agent-containers exec`` wrapper process. Falls back to ``default`` (the
    configured/legacy port) when agent-bridge is unavailable or no live port has
    been published -- preserving prior behavior.
    """
    try:
        from agent_bridge.relay_state import get_live_relay_port
    except ImportError:  # agent-bridge not importable from this wrapper process
        log.debug("agent-bridge unavailable; using configured relay port %s", default)
        return default

    live = get_live_relay_port()
    return int(live) if live else default


def _cmd_exec(args: argparse.Namespace) -> int:
    """Transport wrapper: exec a Copilot ACP agent into a container.

    This is what agent-bridge spawns for a ``container:`` agent. It resolves the
    container's per-fleet settings, fetches the host ``gh`` token at spawn time
    (so it is never persisted in a SpawnTarget), and runs ``docker exec -i``
    with the token injected via the process environment.

    With ``--stdio`` the wrapper explicitly *pumps* bytes between its own
    stdin/stdout and the child's pipes (threaded ``os.read``/``write``). We do
    NOT rely on fd inheritance: under ``CREATE_NO_WINDOW`` on Windows a child
    spawned with inherited pipe handles does not reliably receive them, which
    silently breaks the ACP channel. Without ``--stdio`` the child inherits our
    stdio for interactive/manual use.
    """
    from .lifecycle import get_container

    config = load_config()
    info = None
    try:
        info = get_container(config, args.name)
    except RuntimeError as exc:
        log.warning("Container lookup failed (%s); using global defaults", exc)
    fleet = config.fleets.get(info.fleet or "") if info and info.fleet else None

    user = (fleet.exec_user if fleet else None) or config.exec_user
    workspace = (fleet.workspace_folder if fleet else None) or config.workspace_folder
    acp_command = config.effective_acp_command(
        workspace_folder=workspace,
        acp_command=(fleet.acp_command if fleet else None),
    )

    forward = config.forward_gh_token
    env = os.environ.copy()
    if forward:
        token = host_gh_token()
        if token:
            env["GH_TOKEN"] = token
        else:
            forward = False
            log.warning(
                "forward_gh_token is on but `gh auth token` returned nothing; "
                "the in-container Copilot CLI may be unauthenticated."
            )

    # On-demand credential relay: deploy shims + inject the relay endpoint/token
    # so in-container auth (Azure storage for rush dev-deploy) is fetched from the
    # host relay over host.docker.internal.
    relay_env: list[str] = []
    if config.relay_enabled:
        try:
            from .container_shims import deploy as deploy_shims
            from .relay_provider import token_for

            deploy_shims(args.name, ado=config.relay_deploy_ado)
            env["LC_GIT_CREDENTIAL_RELAY_HOST"] = config.relay_host
            env["LC_GIT_CREDENTIAL_RELAY"] = str(_resolve_relay_port(config.relay_port))
            env["LC_GIT_CREDENTIAL_RELAY_TOKEN"] = token_for(args.name)
            relay_env = [
                "LC_GIT_CREDENTIAL_RELAY_HOST",
                "LC_GIT_CREDENTIAL_RELAY",
                "LC_GIT_CREDENTIAL_RELAY_TOKEN",
            ]
        except Exception as exc:
            log.warning("Credential relay setup failed for %s: %s", args.name, exc)

    spawn_cmd = build_spawn_command(args.name, user, acp_command, forward, relay_env)
    log.info("exec: %s", " ".join(spawn_cmd))

    if not args.stdio:
        # Interactive / manual: inherit our stdio directly.
        proc = subprocess.run(spawn_cmd, env=env, creationflags=_creation_flags())
        return proc.returncode

    return _exec_stdio(spawn_cmd, env)


def _exec_stdio(spawn_cmd: list[str], env: dict[str, str]) -> int:
    """Run the docker exec command, pumping stdio over explicit pipes."""
    import threading

    proc = subprocess.Popen(
        spawn_cmd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_creation_flags(),
    )

    def _forward_in() -> None:
        try:
            in_fd = sys.stdin.buffer.fileno()
            while True:
                data = os.read(in_fd, 65536)
                if not data:
                    break
                proc.stdin.write(data)
                proc.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    def _forward(src, dst) -> None:
        try:
            src_fd = src.fileno()
            while True:
                data = os.read(src_fd, 65536)
                if not data:
                    break
                dst.write(data)
                dst.flush()
        except (OSError, ValueError):
            pass

    threads = [
        threading.Thread(target=_forward_in, daemon=True),
        threading.Thread(target=_forward, args=(proc.stdout, sys.stdout.buffer), daemon=True),
        threading.Thread(target=_forward, args=(proc.stderr, sys.stderr.buffer), daemon=True),
    ]
    for t in threads:
        t.start()
    rc = proc.wait()
    # Let the output pumps drain anything buffered after exit.
    for t in threads[1:]:
        t.join(timeout=2)
    return rc


if __name__ == "__main__":
    sys.exit(main())

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
  exec <name>           Run the ACP launch command through the venue transport
  ssh-stdio <name>      Serve restricted SSH protocol over provider stdio
  ssh-profile <name>    Emit a named restricted provider SSH profile
  source-remove <name>  Remove a project-scoped Picker source
  version               Show version
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from agent_procutil import no_window_flags

from . import __version__
from .config import (
    RESTRICTED_PROFILE,
    SECURITY_PROFILE_LABEL,
    TRUSTED_PROFILE,
    ContainersConfig,
    FleetConfig,
    load_config,
)
from .resolver import (
    build_restricted_spawn_command,
    host_gh_token,
    resolve_live_exec_target,
)
from .ssh_transport import (
    build_remote_command,
    build_ssh_command,
    cleanup_remote_env,
    cleanup_remote_envs,
    container_environment,
    prepare_ssh_config,
    write_remote_env,
)

log = logging.getLogger("agent-containers")
_BUSY_EXIT = 75


def _creation_flags() -> int:
    return no_window_flags()


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
    up_p.add_argument("--json", action="store_true", help="Emit operation result JSON")
    up_p.add_argument(
        "--recreate",
        action="store_true",
        help="Recreate restricted members that drifted from the fleet's current "
        "image/policy (instead of refusing). Removes and re-provisions them on "
        "the current image; active/unknown/leased members are deferred.",
    )
    up_p.add_argument(
        "--force-abandon",
        action="store_true",
        help="Allow an idle restricted member to be recreated when verified "
        "session-evidence rescue fails. Never overrides active or unknown liveness.",
    )

    for name, helptext in (
        ("down", "Stop (keep warm) all containers in a fleet"),
        ("start", "Start all stopped containers in a fleet"),
        ("rm", "Remove all containers in a fleet (destructive)"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("fleet", help="Fleet name")
        if name in {"down", "rm"}:
            p.add_argument("--json", action="store_true", help="Emit operation result JSON")
        if name in {"down", "rm"}:
            p.add_argument(
                "--force-abandon",
                action="store_true",
                help="Accept unavailable/failed restricted session evidence. "
                "Never overrides active or unknown liveness.",
            )
        if name == "rm":
            p.add_argument("--force", action="store_true", help="Force removal")

    borrow_p = sub.add_parser("borrow", help="Lease a free container to an effort")
    borrow_p.add_argument("effort", help="Effort name (lease holder)")
    borrow_p.add_argument("--container", help="Borrow a specific container")
    borrow_p.add_argument("--fleet", help="Restrict to a fleet")

    release_p = sub.add_parser("release", help="Release a lease")
    release_p.add_argument("target", help="Container name or effort name")

    sub.add_parser("leases", help="Show active leases")
    lifecycle_clear = sub.add_parser(
        "lifecycle-clear",
        help="Clear only expired/dead provider holds and session admissions",
    )
    lifecycle_clear.add_argument(
        "name",
        nargs="?",
        help="Optional container name; omitted clears all safely stale records",
    )

    exec_p = sub.add_parser("exec", help="Run the ACP launch command in a container")
    exec_p.add_argument("name", help="Container name")
    exec_p.add_argument(
        "--stdio", action="store_true",
        help="Attach stdio (ACP transport) instead of a one-shot probe",
    )
    exec_p.add_argument(
        "--force", action="store_true",
        help="Terminate a live SSH holder and take over this trusted container",
    )
    ssh_stdio_p = sub.add_parser(
        "ssh-stdio",
        help="Serve an SSH-compatible restricted provider target over stdio",
    )
    ssh_stdio_p.add_argument("name", help="Restricted container name")
    ssh_stdio_p.add_argument("--expected-target-id")
    ssh_stdio_p.add_argument("--expected-instance-id")
    ssh_stdio_p.add_argument(
        "--expected-assignment",
        type=json.loads,
        help="Canonical expected lease-assignment JSON",
    )
    ssh_profile_p = sub.add_parser(
        "ssh-profile",
        help="Emit a named agent-ssh profile for a restricted target",
    )
    ssh_profile_p.add_argument("name", help="Restricted container name")
    ssh_profile_p.add_argument(
        "--alias",
        help="SSH Host alias (defaults to the provider target name)",
    )
    ssh_profile_p.add_argument(
        "--project",
        help="Also register this target as a Worktree Picker source for PROJECT",
    )
    ssh_profile_p.add_argument(
        "--label",
        help="Picker source label (requires --project; defaults to the SSH alias)",
    )
    profile_output = ssh_profile_p.add_mutually_exclusive_group()
    profile_output.add_argument(
        "--json",
        action="store_true",
        help="Print the provider profile specification without publishing it",
    )
    profile_output.add_argument(
        "--print",
        action="store_true",
        help="Render the OpenSSH fragment through agent-ssh without writing it",
    )
    source_remove_p = sub.add_parser(
        "source-remove",
        help="Remove a project-scoped Worktree Picker source registration",
    )
    source_remove_p.add_argument("name", help="Restricted container name")
    source_remove_p.add_argument("--project", required=True)

    host_prepare = sub.add_parser(
        "session-host-prepare",
        help="Prepare trusted-container SSH/auth launch inputs for agent-bridge",
    )
    host_prepare.add_argument("name", help="Container name")
    host_prepare.add_argument(
        "--host-relay-port",
        type=int,
        default=None,
        help="Agent-bridge's live host relay port",
    )
    host_state = sub.add_parser(
        "session-host-state",
        help="Print non-waking JSON lifecycle state for one container",
    )
    host_state.add_argument("name", help="Container name")
    host_cleanup = sub.add_parser(
        "session-host-cleanup",
        help="Remove one launch-only trusted-container environment file",
    )
    host_cleanup.add_argument("name", help="Container name")
    host_cleanup.add_argument("--remote-env", required=True)

    sub.add_parser("version", help="Show version")
    sub.add_parser(
        "installer-readiness",
        help="Emit the plugin-owned installer/readiness contract state as JSON",
    )

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
        help="Print JSON {type,spawn_command,user,workspace_folder,"
        "security_profile,venue} resolving a container name "
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
    ns_recreate_p = sub.add_parser(
        "namespace-recreate",
        help="Identity-check and recreate one trusted fleet member.",
    )
    ns_recreate_p.add_argument("name", help="Container name")
    ns_recreate_p.add_argument("--expected-container-id", required=True)
    ns_recreate_p.add_argument("--timeout", type=float, default=600.0)

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
        if args.command == "lifecycle-clear":
            return _cmd_lifecycle_clear(args)
        if args.command == "exec":
            return _cmd_exec(args)
        if args.command == "ssh-stdio":
            from .provider_ssh import run_ssh_stdio

            return run_ssh_stdio(
                args.name,
                expected_target_id=args.expected_target_id,
                expected_instance_id=args.expected_instance_id,
                expected_assignment=args.expected_assignment,
            )
        if args.command == "ssh-profile":
            from .provider_ssh import emit_ssh_profile, ssh_profile_spec

            if args.label and not args.project:
                raise ValueError("--label requires --project")
            if args.json:
                print(json.dumps(
                    ssh_profile_spec(
                        args.name,
                        args.alias,
                        project=args.project,
                        label=args.label,
                    ),
                    indent=2,
                ))
                return 0
            return emit_ssh_profile(
                args.name,
                args.alias,
                print_only=args.print,
                project=args.project,
                label=args.label,
            )
        if args.command == "source-remove":
            from .provider_ssh import remove_worktree_source

            removed = remove_worktree_source(args.name, args.project)
            print("removed" if removed else "not registered")
            return 0
        if args.command == "session-host-prepare":
            return _cmd_session_host_prepare(args)
        if args.command == "session-host-state":
            return _cmd_session_host_state(args)
        if args.command == "session-host-cleanup":
            return _cmd_session_host_cleanup(args)
        if args.command == "version":
            print(f"agent-containers {__version__}")
            return 0
        if args.command == "installer-readiness":
            return _cmd_installer_readiness()
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
        if args.command == "namespace-recreate":
            return _cmd_namespace_recreate(args)
        if args.command == "relay-profile":
            return _cmd_relay_profile()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


def _cmd_installer_readiness() -> int:
    """Inspect configuration, toolchain, and Docker without provisioning."""
    from .installer_readiness import emit, evaluate, inspect_toolchain
    from .lifecycle import list_containers

    try:
        config = load_config(strict=True)
        failures = list(inspect_toolchain(config))
        containers = []
        if config.fleets and (
            not failures or all("docker CLI" not in item for item in failures)
        ):
            try:
                containers = list_containers(config)
            except (OSError, RuntimeError, ValueError) as exc:
                failures.append(str(exc))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        config = None
        containers = []
        failures = [str(exc)]
    return emit(evaluate(config, containers, failures))


def _trusted_session_host_context(name: str):
    """Resolve a trusted fleet member without launching its Session Host."""
    from .lifecycle import get_container, inspect_container

    config = load_config()
    info = get_container(config, name)
    if info is None:
        raise RuntimeError(
            f"Container '{name}' is not a discovered fleet member"
        )
    if info.state != "running":
        raise RuntimeError(
            f"Container '{name}' is not running (state={info.state!r})"
        )
    fleet = config.fleets.get(info.fleet or "")
    if fleet is None:
        raise RuntimeError(
            f"Container '{name}' has no matching fleet configuration"
        )
    actual_profile = (
        ((inspect_container(name).get("Config") or {}).get("Labels") or {})
        .get(SECURITY_PROFILE_LABEL)
    )
    if (
        fleet.security_profile != TRUSTED_PROFILE
        or actual_profile != TRUSTED_PROFILE
    ):
        raise RuntimeError(
            f"Container '{name}' is not exact trusted/trusted posture "
            f"(configured={fleet.security_profile!r}, live={actual_profile!r}); "
            "Session Host projection is trusted-fleet only"
        )
    user = fleet.exec_user or config.exec_user
    workspace = fleet.workspace_folder or config.workspace_folder
    return config, fleet, user, workspace


def _cmd_session_host_prepare(args: argparse.Namespace) -> int:
    """Prepare endpoint + auth inputs; agent-bridge owns the Host lifecycle."""
    from ._invoke import module_argv
    from .container_shims import (
        deploy as deploy_shims,
    )
    from .container_shims import (
        git_credential_environment,
    )
    from .relay_provider import token_for

    config, fleet, user, workspace = _trusted_session_host_context(args.name)
    ssh_config = prepare_ssh_config(args.name, user)
    cleanup_remote_envs(args.name, user)
    launch_env = container_environment(args.name, user)

    forward, relay_enabled = config.credentials_for(fleet)
    if forward:
        github_token = host_gh_token()
        if not github_token:
            raise RuntimeError(
                "forward_gh_token is enabled but `gh auth token` returned nothing"
            )
        launch_env["GH_TOKEN"] = github_token

    reverse_forwards: list[str] = []
    if relay_enabled:
        if not args.host_relay_port or not 1 <= args.host_relay_port <= 65535:
            raise RuntimeError(
                "credential relay is enabled but no valid --host-relay-port "
                "was supplied"
            )
        if not _relay_healthy(args.host_relay_port):
            raise RuntimeError(
                f"credential relay on 127.0.0.1:{args.host_relay_port} "
                "did not answer the identity probe"
            )
        deploy_shims(args.name, ado=True)
        launch_env["LC_GIT_CREDENTIAL_RELAY_HOST"] = "127.0.0.1"
        launch_env["LC_GIT_CREDENTIAL_RELAY"] = str(config.relay_port)
        launch_env["LC_GIT_CREDENTIAL_RELAY_TOKEN"] = token_for(args.name)
        launch_env.update(git_credential_environment())
        reverse_forwards.append(
            f"{config.relay_port}:127.0.0.1:{args.host_relay_port}"
        )

    remote_env = write_remote_env(args.name, user, launch_env)
    acp_command = config.acp_command_for(fleet)
    remote_command = build_remote_command(
        acp_command,
        remote_env,
    )
    print(json.dumps({
        "name": args.name,
        "workspace_folder": workspace,
        "security_profile": fleet.security_profile,
        "user": user,
        "ssh": asdict(ssh_config),
        "acp_command": acp_command,
        "remote_command": remote_command,
        "remote_env": remote_env,
        "reverse_forwards": reverse_forwards,
        "state_command": [*module_argv(), "session-host-state", args.name],
    }))
    return 0


def _cmd_session_host_state(args: argparse.Namespace) -> int:
    """Emit lifecycle state without starting or attaching to the container."""
    from .lifecycle import inspect_container

    try:
        details = inspect_container(args.name)
    except RuntimeError as exc:
        detail = str(exc)
        if "No such object" not in detail and "No such container" not in detail:
            raise
        details = {}
    state = str((details.get("State") or {}).get("Status") or "").lower()
    print(json.dumps({
        "name": args.name,
        "state": state or "missing",
        "running": state == "running",
        "container_id": details.get("Id") or None,
        "started_at": (details.get("State") or {}).get("StartedAt") or None,
    }))
    return 0


def _cmd_session_host_cleanup(args: argparse.Namespace) -> int:
    """Remove only a provider-created launch env path."""
    _config, _fleet, user, _workspace = _trusted_session_host_context(args.name)
    remote_env = PurePosixPath(args.remote_env)
    if (
        not remote_env.is_absolute()
        or len(remote_env.parts) < 4
        or remote_env.parts[-3:-1] != (".agent-containers", "launch")
        or remote_env.suffix != ".env"
        or not re.fullmatch(r"[0-9a-f]{32}", remote_env.stem)
    ):
        raise RuntimeError(
            f"Refusing unsafe Session Host env cleanup path: {args.remote_env!r}"
        )
    cleanup_remote_env(args.name, user, str(remote_env))
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


def _cmd_namespace_recreate(args: argparse.Namespace) -> int:
    """Recreate one exact trusted container and emit redacted identity data."""
    from .fleet import RecreateMemberError, recreate_member

    try:
        result = recreate_member(
            load_config(),
            args.name,
            expected_container_id=args.expected_container_id,
            timeout=args.timeout,
        )
    except RecreateMemberError as exc:
        print(json.dumps({
            "name": args.name,
            "old_container_removed": exc.old_container_removed,
            "error": str(exc),
        }))
        return 2
    print(json.dumps(result))
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
    from .lease import deploy_hold_status, get_lease
    from .lifecycle import inspect_container, list_containers, restricted_policy_errors
    from .rescue import latest_rescue_status
    config = load_config()
    containers = list_containers(config)
    if getattr(args, "json", False):
        # Bare JSON array, one object per container, so the picker's Containers
        # pivot (and any other machine-readable consumer) can render the fleet.
        out = []
        for c in containers:
            lease = get_lease(c.name)
            hold_status = deploy_hold_status(c.name)
            fleet = config.fleets.get(c.fleet or "")
            posture_errors = []
            actual_profile = c.security_profile
            actual_network = None
            inspected = None
            if (fleet and fleet.restricted) or actual_profile == RESTRICTED_PROFILE:
                try:
                    inspected = inspect_container(c.name)
                    actual_profile = (
                        (inspected.get("Config") or {}).get("Labels") or {}
                    ).get(SECURITY_PROFILE_LABEL)
                    actual_network = (inspected.get("HostConfig") or {}).get(
                        "NetworkMode"
                    )
                except RuntimeError as exc:
                    actual_profile = "unknown"
                    posture_errors = [str(exc)]
            forward, relay = (
                config.credentials_for(fleet) if fleet else (False, False)
            )
            workspace = (
                fleet.workspace_folder if fleet and fleet.workspace_folder else None
            ) or config.workspace_folder
            exec_user = (
                fleet.exec_user if fleet and fleet.exec_user else None
            ) or config.exec_user
            if fleet and fleet.restricted:
                try:
                    posture_errors.extend(restricted_policy_errors(
                        c,
                        fleet,
                        workspace_folder=workspace,
                        exec_user=exec_user,
                        inspected=inspected,
                    ))
                except RuntimeError as exc:
                    posture_errors.append(str(exc))
            elif actual_profile == RESTRICTED_PROFILE:
                posture_errors.append(
                    "restricted container has no matching fleet configuration"
                )
            if actual_profile == RESTRICTED_PROFILE:
                forward, relay = False, False
            out.append({
                "name": c.name,
                "container_id": c.container_id,
                "image": c.image,
                "state": c.state,
                "status": c.status,
                "fleet": c.fleet,
                "local_folder": c.local_folder,
                "lease": lease.effort if lease else None,
                "lifecycle_hold": hold_status,
                "security_profile": actual_profile,
                "configured_security_profile": (
                    fleet.security_profile if fleet else None
                ),
                "security_policy_current": not posture_errors,
                "security_policy_errors": posture_errors,
                "network": actual_network,
                "environment_names": sorted(fleet.environment) if fleet else [],
                "host_credentials": {
                    "github_token": forward,
                    "relay": relay,
                },
                "rescue": latest_rescue_status(c.name),
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
    result = fleet_mod.reconcile_up(
        config,
        args.fleet,
        count=args.count,
        recreate=args.recreate,
        force_abandon=args.force_abandon,
    )
    if args.json:
        print(json.dumps(asdict(result), indent=2))
        return _BUSY_EXIT if result.deferred else 0
    if result.created:
        print(f"Created: {', '.join(result.created)}")
    elif result.deferred:
        print("Created: (none)")
    else:
        print("Fleet already at target size.")
    for name, reason in result.deferred.items():
        print(f"Deferred: {name} ({reason})")
    if result.telemetry_abandoned:
        print(
            "Telemetry abandoned: "
            + ", ".join(result.telemetry_abandoned)
        )
    return _BUSY_EXIT if result.deferred else 0


def _cmd_fleet_op(args: argparse.Namespace) -> int:
    from . import fleet as fleet_mod

    config = load_config()
    deferred = False
    if args.command == "down":
        result = fleet_mod.down_fleet(
            config,
            args.fleet,
            force_abandon=args.force_abandon,
        )
        if args.json:
            print(json.dumps(asdict(result), indent=2))
            return _BUSY_EXIT if result.deferred else 0
        print(
            f"Stopped: {', '.join(result.stopped) if result.stopped else '(none)'}"
        )
        for name, reason in result.deferred.items():
            print(f"Deferred: {name} ({reason})")
        for name, reason in result.unchanged.items():
            print(f"Unchanged: {name} ({reason})")
        if result.telemetry_abandoned:
            print(
                "Telemetry abandoned: "
                + ", ".join(result.telemetry_abandoned)
            )
        deferred = bool(result.deferred)
    elif args.command == "start":
        names = fleet_mod.start(config, args.fleet)
        print(f"Started: {', '.join(names) if names else '(none)'}")
    elif args.command == "rm":
        result = fleet_mod.remove_fleet(
            config,
            args.fleet,
            force=args.force,
            force_abandon=args.force_abandon,
        )
        if args.json:
            print(json.dumps(asdict(result), indent=2))
            return _BUSY_EXIT if result.deferred else 0
        print(
            f"Removed: {', '.join(result.removed) if result.removed else '(none)'}"
        )
        for name, reason in result.deferred.items():
            print(f"Deferred: {name} ({reason})")
        if result.telemetry_abandoned:
            print(
                "Telemetry abandoned: "
                + ", ".join(result.telemetry_abandoned)
            )
        deferred = bool(result.deferred)
    return _BUSY_EXIT if deferred else 0


def _cmd_borrow(args: argparse.Namespace) -> int:
    from .lease import borrow

    config = load_config()
    lease = borrow(config, args.effort, container=args.container, fleet=args.fleet)
    print(lease.container)
    return 0


def _cmd_release(args: argparse.Namespace) -> int:
    from .lease import ProviderAdmissionError, release
    from .provider_ssh import remove_stale_worktree_sources

    try:
        released = release(args.target)
    except ProviderAdmissionError as exc:
        print(f"Release blocked: {exc}", file=sys.stderr)
        return _BUSY_EXIT
    try:
        removed = remove_stale_worktree_sources(args.target)
    except (OSError, RuntimeError) as exc:
        if released:
            print(f"Released: {args.target}")
        print(
            f"Picker source cleanup failed after release: {exc}",
            file=sys.stderr,
        )
        return 1
    if released:
        print(f"Released: {args.target}")
        if removed:
            print(f"Removed Picker source registrations: {removed}")
        return 0
    if removed:
        print(f"Removed stale Picker source registrations: {removed}")
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


def _cmd_lifecycle_clear(args: argparse.Namespace) -> int:
    from .lease import clear_stale_provider_records

    cleared = clear_stale_provider_records(args.name)
    print(
        "Cleared stale provider records: "
        f"deploy_holds={cleared['deploy_holds']}, "
        f"session_admissions={cleared['session_admissions']}"
    )
    return 0


def _live_relay_port_file() -> Path:
    """Path to agent-bridge's published live-relay-port file.

    agent-bridge records the port its credential relay actually bound to in
    ``<config_dir>/relay-port`` (``relay_state.set_live_relay_port``), where
    ``config_dir`` is ``$AGENT_BRIDGE_CONFIG_DIR`` or ``~/.agent-bridge``. An
    elevated sub-daemon uses ``<primary>/elevated`` and republishes to the
    **primary** dir, so a config dir named ``elevated`` resolves to its parent
    (mirrors ``relay_state._primary_config_dir``).

    We read this **file** rather than importing ``agent_bridge`` because the
    ``agent-containers exec`` wrapper runs in agent-containers' *own* venv, which
    does not have ``agent_bridge`` installed (the untangle keeps providers in
    standalone venvs). The file is the cross-process contract agent-bridge
    publishes for exactly this discovery.
    """
    root = Path(os.environ.get("AGENT_BRIDGE_CONFIG_DIR", "~/.agent-bridge")).expanduser()
    if root.name == "elevated":
        root = root.parent
    return root / "relay-port"


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

    Reads agent-bridge's published ``relay-port`` file directly (see
    :func:`_live_relay_port_file`) -- venv-independent, so it works from the
    standalone ``agent-containers exec`` wrapper process where ``agent_bridge``
    is not importable. Falls back to ``default`` (the configured/legacy port)
    when the file is absent, empty, or unreadable -- preserving prior behavior.
    """
    return _published_relay_port() or default


def _published_relay_port() -> int | None:
    """Read agent-bridge's published live relay port, or ``None``."""
    try:
        txt = _live_relay_port_file().read_text(encoding="utf-8").strip()
        port = int(txt)
        return port if 1 <= port <= 65535 else None
    except (OSError, ValueError):
        return None


def _require_live_relay_port() -> int:
    port = _published_relay_port()
    if port is None:
        raise RuntimeError(
            "agent-bridge has not published a live credential-relay port; "
            "start agent-bridge or set relay.enabled: false in containers.yaml"
        )
    return port


def _relay_healthy(port: int, timeout: float = 0.5) -> bool:
    """Verify the published endpoint speaks the credential-relay protocol."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"ping\n\n")
            return sock.recv(32) == b"pong\n\n"
    except OSError:
        return False


def _cmd_exec(args: argparse.Namespace) -> int:
    """Transport wrapper: launch a Copilot ACP agent in a container.

    This is what agent-bridge spawns for a ``container:`` agent. It resolves the
    container's per-fleet settings, fetches the host ``gh`` token at spawn time
    (so it is never persisted in a SpawnTarget), and selects the transport from
    the fleet's trust posture.

    Trusted fleets use OpenSSH, with ``docker exec`` only as the SSH
    ``ProxyCommand`` bootstrap. Restricted fleets retain the direct
    deny-by-construction ``docker exec`` path and receive no SSH key projection.
    With ``--stdio`` the wrapper explicitly pumps bytes between its own stdio
    and the child because inherited pipes are unreliable under
    ``CREATE_NO_WINDOW`` on Windows.
    """
    target = resolve_live_exec_target(args.name, config=load_config())

    if target.actual_profile == RESTRICTED_PROFILE:
        from .lease import ProviderAdmissionError, session_admission

        try:
            with session_admission(args.name):
                # Re-read after admission so a provider hold cannot win between
                # the initial posture check and the protected launch.
                target = resolve_live_exec_target(args.name, config=load_config())
                return _launch_container_agent(
                    args,
                    target.config,
                    target.fleet,
                    target.actual_profile,
                    target.user,
                    target.acp_command,
                    container_id=target.container_id,
                )
        except ProviderAdmissionError as busy:
            print(str(busy), file=sys.stderr)
            return _BUSY_EXIT

    from ssh_manager import TargetBusyError, TargetLock

    op = "stdio" if args.stdio else "exec"
    target_lock = TargetLock(f"container:{args.name}", op=op)
    try:
        target_lock.acquire(force=getattr(args, "force", False))
    except TargetBusyError as busy:
        print(busy.user_message(), file=sys.stderr)
        return _BUSY_EXIT
    try:
        return _launch_container_agent(
            args,
            target.config,
            target.fleet,
            target.actual_profile,
            target.user,
            target.acp_command,
        )
    finally:
        target_lock.release()


def _launch_container_agent(
    args: argparse.Namespace,
    config: ContainersConfig,
    fleet: FleetConfig,
    actual_profile: str,
    user: str,
    acp_command: str,
    *,
    container_id: str | None = None,
) -> int:
    """Launch through the trust-profiled transport while holding its lock."""
    if actual_profile == RESTRICTED_PROFILE:
        spawn_cmd = build_restricted_spawn_command(
            container_id or args.name,
            user,
            acp_command,
        )
        log.info("exec transport=docker target=%s", args.name)
        env = os.environ.copy()
        if not args.stdio:
            proc = subprocess.run(
                spawn_cmd, env=env, creationflags=_creation_flags()
            )
            return proc.returncode
        return _exec_stdio(spawn_cmd, env)

    forward, relay_enabled = config.credentials_for(fleet)
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

    # On-demand credential relay. Trusted SSH carries a reverse forward from the
    # container's loopback relay port to agent-bridge's live host-loopback port;
    # restricted fleets cannot enable this path.
    relay_env: list[str] = []
    reverse_forwards: list[str] = []
    if relay_enabled:
        from .container_shims import (
            deploy as deploy_shims,
        )
        from .container_shims import (
            git_credential_environment,
        )
        from .relay_provider import token_for

        host_relay_port = _require_live_relay_port()
        if not _relay_healthy(host_relay_port):
            raise RuntimeError(
                "Published credential relay at "
                f"127.0.0.1:{host_relay_port} failed its ping probe; "
                "restart agent-bridge or set relay.enabled: false in "
                "containers.yaml"
            )
        deploy_shims(args.name, ado=True)
        env["LC_GIT_CREDENTIAL_RELAY_HOST"] = "127.0.0.1"
        env["LC_GIT_CREDENTIAL_RELAY"] = str(config.relay_port)
        env["LC_GIT_CREDENTIAL_RELAY_TOKEN"] = token_for(args.name)
        env.update(git_credential_environment())
        relay_env = [
            "LC_GIT_CREDENTIAL_RELAY_HOST",
            "LC_GIT_CREDENTIAL_RELAY",
            "LC_GIT_CREDENTIAL_RELAY_TOKEN",
            "GIT_CONFIG_COUNT",
            "GIT_CONFIG_KEY_0",
            "GIT_CONFIG_VALUE_0",
            "GIT_CONFIG_KEY_1",
            "GIT_CONFIG_VALUE_1",
            "GIT_TERMINAL_PROMPT",
        ]
        reverse_forwards = [
            f"127.0.0.1:{config.relay_port}:127.0.0.1:{host_relay_port}"
        ]

    launch_env = container_environment(args.name, user)
    if forward and "GH_TOKEN" in env:
        launch_env["GH_TOKEN"] = env["GH_TOKEN"]
    launch_env.update({
        name: env[name]
        for name in relay_env
        if name in env
    })
    remote_env = write_remote_env(args.name, user, launch_env)
    try:
        ssh_config = prepare_ssh_config(args.name, user)
        remote_command = build_remote_command(acp_command, remote_env)
        spawn_cmd = build_ssh_command(
            ssh_config,
            remote_command,
            reverse_forwards=reverse_forwards,
        )
        log.info("exec transport=ssh target=%s", args.name)
        if not args.stdio:
            proc = subprocess.run(
                spawn_cmd, env=env, creationflags=_creation_flags()
            )
            return proc.returncode
        return _exec_stdio(spawn_cmd, env)
    finally:
        cleanup_remote_env(args.name, user, remote_env)


def _exec_stdio(spawn_cmd: list[str], env: dict[str, str]) -> int:
    """Run a transport command, pumping stdio over explicit pipes."""
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
        except (OSError, ValueError) as exc:
            log.error("transport stdin forwarding failed: %s", exc)
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
        except (OSError, ValueError) as exc:
            log.error("transport output forwarding failed: %s", exc)

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

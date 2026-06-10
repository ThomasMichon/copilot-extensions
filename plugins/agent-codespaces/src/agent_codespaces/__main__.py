"""CLI entry point for agent-codespaces.

Subcommands:
  ssh <name>            SSH into a CodeSpace (interactive or --stdio)
  list                  List active CodeSpaces
  config adopt          Register current repo for config
  config show           Show resolved config
  config validate       Validate config
  status                Show service status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

from .codespace_config import CodespaceSource
from .config import (
    ADOPTED_REPOS_FILE,
    RUNTIME_DIR,
    AdoptedRepo,
    load_adopted_repos,
    load_merged_config,
    save_adopted_repos,
    validate_config,
)
from .lifecycle import (
    cleanup_stale,
    create_codespace,
    delete_codespace,
    list_codespaces,
    wait_for_available,
)

log = logging.getLogger("agent-codespaces")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="agent-codespaces",
        description="GitHub Codespaces lifecycle, SSH, and credential relay",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    sub = parser.add_subparsers(dest="command")

    # --- ssh ---
    ssh_parser = sub.add_parser("ssh", help="SSH into a CodeSpace")
    ssh_parser.add_argument("name", help="CodeSpace name")
    ssh_parser.add_argument(
        "--stdio", action="store_true",
        help="Structured stdio mode for agent-bridge transport",
    )
    ssh_parser.add_argument(
        "--remote-cmd", dest="remote_cmd",
        help="Remote command to execute (non-interactive)",
    )
    ssh_parser.add_argument(
        "--no-relay", action="store_true",
        help="Skip credential relay tunnel setup",
    )
    ssh_parser.add_argument(
        "--repo", dest="repo", default=None,
        help="CodeSpace repository (owner/name) -- selects per-repo "
             "provision hooks without an extra lookup",
    )

    # --- list ---
    list_parser = sub.add_parser("list", help="List active CodeSpaces")
    list_parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Output as JSON",
    )

    # --- config ---
    config_parser = sub.add_parser("config", help="Configuration management")
    config_sub = config_parser.add_subparsers(dest="config_command")

    config_sub.add_parser("adopt", help="Register current repo for config")
    config_sub.add_parser("show", help="Show resolved config")
    config_sub.add_parser("validate", help="Validate config")

    # --- delete ---
    delete_parser = sub.add_parser("delete", help="Delete a CodeSpace")
    delete_parser.add_argument("name", help="CodeSpace name")
    delete_parser.add_argument(
        "--force", action="store_true", help="Force deletion",
    )

    # --- create ---
    create_parser = sub.add_parser(
        "create", help="Create a CodeSpace and run post-create provisioning",
    )
    create_parser.add_argument("repo", help="Repository (owner/name)")
    create_parser.add_argument(
        "--branch", default=None, help="Branch to create the CodeSpace on",
    )
    create_parser.add_argument(
        "--no-wait", action="store_true",
        help="Don't wait for Available / run provisioning",
    )
    create_parser.add_argument(
        "--timeout", type=float, default=300.0,
        help="Seconds to wait for the CodeSpace to become Available",
    )

    # --- bridge ---
    bridge_parser = sub.add_parser(
        "bridge", help="Agent-bridge provider integration",
    )
    bridge_sub = bridge_parser.add_subparsers(dest="bridge_command")
    bridge_reg = bridge_sub.add_parser(
        "register", help="Register codespace agents with agent-bridge",
    )
    bridge_reg.add_argument(
        "--ttl", type=float, default=300.0,
        help="TTL in seconds (0 = no expiry, default: 300)",
    )
    bridge_reg.add_argument(
        "--bridge-url", default="http://127.0.0.1:9280",
        help="Agent-bridge URL (default: http://127.0.0.1:9280)",
    )
    bridge_unreg = bridge_sub.add_parser(
        "unregister", help="Remove codespace agents from agent-bridge",
    )
    bridge_unreg.add_argument(
        "--bridge-url", default="http://127.0.0.1:9280",
        help="Agent-bridge URL",
    )
    bridge_status = bridge_sub.add_parser(
        "status", help="Show provider registration status",
    )
    bridge_status.add_argument(
        "--bridge-url", default="http://127.0.0.1:9280",
        help="Agent-bridge URL",
    )
    bridge_refresh = bridge_sub.add_parser(
        "refresh", help="Re-register with current live codespace state",
    )
    bridge_refresh.add_argument(
        "--ttl", type=float, default=300.0,
        help="TTL in seconds (default: 300)",
    )
    bridge_refresh.add_argument(
        "--bridge-url", default="http://127.0.0.1:9280",
        help="Agent-bridge URL",
    )

    # --- cleanup ---
    cleanup_parser = sub.add_parser(
        "cleanup", help="Remove stale local state (SSH configs, sockets)",
    )
    cleanup_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be removed without removing",
    )

    # --- status ---
    sub.add_parser("status", help="Show service status")

    # --- version ---
    sub.add_parser("version", help="Show version")

    args = parser.parse_args(argv)

    # Logging setup
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )

    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "ssh":
            return _cmd_ssh(args)
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "config":
            return _cmd_config(args)
        if args.command == "delete":
            return _cmd_delete(args)
        if args.command == "create":
            return _cmd_create(args)
        if args.command == "bridge":
            return _cmd_bridge(args)
        if args.command == "cleanup":
            return _cmd_cleanup(args)
        if args.command == "status":
            return _cmd_status()
        if args.command == "version":
            return _cmd_version()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    return 0


def _cmd_ssh(args: argparse.Namespace) -> int:
    """SSH into a CodeSpace using ssh-manager."""
    from ssh_manager import ConnectionManager

    source = CodespaceSource(args.name)
    config = load_merged_config()
    relay_port = config.credentials.relay_port

    # Build port forwards for credential relay
    port_forwards: list[str] = []
    relay_env = ""
    if not args.no_relay:
        port_forwards.append(f"-R {relay_port}:127.0.0.1:{relay_port}")
        relay_env = f"export LC_GIT_CREDENTIAL_RELAY={relay_port}; "

    manager = ConnectionManager()

    # Wrap remote commands in a login shell so the CodeSpace platform
    # environment is loaded (GITHUB_TOKEN, gh auth state, profile.d
    # scripts).  Non-interactive SSH commands skip /etc/profile and
    # ~/.profile by default, which leaves tools like `copilot` and `gh`
    # unauthenticated.
    #
    # Inject LC_GIT_CREDENTIAL_RELAY before the login shell so the
    # credential relay activation script and ado-auth-helper can find
    # the tunnel port.
    remote_cmd = args.remote_cmd
    if remote_cmd:
        remote_cmd = f"bash -l -c {shlex.quote(relay_env + remote_cmd)}"

    async def _run() -> int:
        await manager.ensure_connected(args.name, source, port_forwards)

        # Deploy the CodeSpace-side relay helpers (ado-auth-helper-relay +
        # smart wrapper) so ADO auth resolves over the tunnel. Idempotent;
        # best-effort -- a failure here shouldn't block the SSH command.
        if not args.no_relay:
            await _provision_relay_helpers(manager, args.name)

        # Run repo-declared provision hooks (by-convention extras from the
        # adopted repo's codespaces.yaml). Best-effort, idempotent.
        await _provision_repo_hooks(
            manager, args.name, config, getattr(args, "repo", None),
        )

        if args.stdio and remote_cmd:
            # Structured stdio mode for agent-bridge
            proc = await manager.open_stdio_channel(args.name, remote_cmd)
            # Pipe through to our own stdio
            await _pipe_stdio(proc)
            return proc.returncode if proc.returncode is not None else 1

        if remote_cmd:
            # Non-interactive command execution
            result = await manager.exec_command(args.name, remote_cmd)
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            return result.exit_code

        # Interactive SSH -- fall through to gh codespace ssh
        await manager.disconnect(args.name)
        return _interactive_ssh(
            args.name,
            port_forwards,
            relay_port=relay_port if not args.no_relay else None,
        )

    return asyncio.run(_run())


async def _provision_relay_helpers(manager, name: str) -> None:
    """Deploy the CodeSpace-side relay helper scripts over SSH.

    Installs ``ado-auth-helper-relay`` and the smart ``ado-auth-helper``
    wrapper into the CodeSpace so ADO auth resolves over the credential
    relay tunnel. Idempotent and best-effort: logs a warning on failure
    but never raises, since the SSH command itself should still proceed.
    """
    from .codespace_assets import build_provision_command

    try:
        command = build_provision_command()
        result = await manager.exec_command(name, command, timeout=30.0)
        if result.exit_code == 0:
            log.debug("Relay helpers provisioned on %s", name)
        else:
            log.warning(
                "Relay helper provisioning on %s exited %s: %s",
                name, result.exit_code, result.stderr.strip(),
            )
    except Exception as exc:
        log.warning("Relay helper provisioning on %s failed: %s", name, exc)


def _lookup_codespace_repo(name: str) -> str | None:
    """Best-effort lookup of a CodeSpace's repository (owner/name)."""
    try:
        from .lifecycle import list_codespaces

        for cs in list_codespaces():
            if cs.name == name:
                return cs.repository
    except Exception as exc:
        log.debug("Could not resolve repo for %s: %s", name, exc)
    return None


async def _provision_repo_hooks(
    manager, name: str, config, repo: str | None, *,
    include_on_create: bool = False,
) -> None:
    """Run repo-declared provision hooks for a CodeSpace over SSH.

    Applies the adopted repo's ``provision`` block (global + per-repo,
    selected by the CodeSpace's repository) from ``codespaces.yaml``.
    The repo is taken from ``--repo`` when provided (hot path) and only
    looked up when per-repo hooks actually exist. When
    ``include_on_create`` is set, ``on_create`` commands run too (used
    once during ``agent-codespaces create``). Best-effort and idempotent.
    """
    from .provision import build_provision_command

    try:
        # Only pay for a repo lookup when per-repo hooks are declared.
        if repo is None and any(rc.provision for rc in config.repos.values()):
            repo = _lookup_codespace_repo(name)

        provision = config.provision_for_repo(repo)
        command = build_provision_command(
            provision, include_on_create=include_on_create,
        )
        if not command:
            return

        # on_create hooks (e.g. install scripts) can run long; give them
        # a generous timeout. on_connect-only hooks stay snappy.
        timeout = 900.0 if include_on_create else 30.0
        result = await manager.exec_command(name, command, timeout=timeout)
        if result.exit_code == 0:
            log.debug("Repo provision hooks applied on %s", name)
        else:
            log.warning(
                "Repo provision hooks on %s exited %s: %s",
                name, result.exit_code, result.stderr.strip(),
            )
    except Exception as exc:
        log.warning("Repo provision hooks on %s failed: %s", name, exc)


async def _pipe_stdio(proc) -> None:
    """Pipe a subprocess's stdio through to our own stdin/stdout.

    Uses threads for the stdin/stdout relay instead of asyncio pipe
    transports, because Windows ProactorEventLoop cannot wire
    stdin/stdout via ``connect_read_pipe`` (raises
    ``OSError: [WinError 6] The handle is invalid``).

    Threading is simple and works on all platforms.
    """
    import threading

    def _forward_in() -> None:
        """Read from our stdin, write to subprocess stdin (blocking)."""
        try:
            stdin_fd = sys.stdin.buffer.fileno()
            while True:
                # os.read returns as soon as any data is available (no
                # buffering), unlike sys.stdin.buffer.read(n) which can
                # block until n bytes arrive on a pipe.
                data = os.read(stdin_fd, 4096)
                if not data:
                    break
                if proc.stdin:
                    proc.stdin.write(data)
                    asyncio.run_coroutine_threadsafe(
                        proc.stdin.drain(), loop
                    ).result(timeout=10)
        except (OSError, ValueError):
            pass
        finally:
            if proc.stdin:
                proc.stdin.close()

    def _forward_out() -> None:
        """Read from subprocess stdout, write to our stdout (blocking)."""
        try:
            while True:
                # read1 is not available on asyncio streams; use the
                # loop to schedule the async read from this thread.
                fut = asyncio.run_coroutine_threadsafe(
                    proc.stdout.read(4096), loop
                )
                data = fut.result(timeout=30)
                if not data:
                    break
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
        except (OSError, ValueError):
            pass

    loop = asyncio.get_event_loop()

    in_thread = threading.Thread(target=_forward_in, daemon=True)
    out_thread = threading.Thread(target=_forward_out, daemon=True)
    in_thread.start()
    out_thread.start()

    await proc.wait()

    # Give output thread a moment to flush remaining data
    out_thread.join(timeout=2)


def _interactive_ssh(
    codespace_name: str,
    port_forwards: list[str],
    relay_port: int | None = None,
) -> int:
    """Fall back to ``gh codespace ssh`` for interactive sessions."""
    import subprocess as sp

    env = None
    if relay_port is not None:
        env = {**os.environ, "LC_GIT_CREDENTIAL_RELAY": str(relay_port)}

    args = ["gh", "codespace", "ssh", "-c", codespace_name]
    for fwd in port_forwards:
        # Split "-R port:host:port" into SSH option
        args.extend(["--", fwd])

    return sp.call(args, env=env)


def _cmd_list(args: argparse.Namespace) -> int:
    """List active CodeSpaces."""
    codespaces = list_codespaces()

    if args.json_output:
        data = [
            {
                "name": cs.name,
                "display_name": cs.display_name,
                "repository": cs.repository,
                "branch": cs.branch,
                "state": cs.state,
                "machine": cs.machine,
            }
            for cs in codespaces
        ]
        print(json.dumps(data, indent=2))
        return 0

    if not codespaces:
        print("No active CodeSpaces")
        return 0

    # Table output
    print(f"{'Name':<40} {'Repo':<35} {'Branch':<20} {'State':<12}")
    print("-" * 107)
    for cs in codespaces:
        print(f"{cs.name:<40} {cs.repository:<35} {cs.branch:<20} {cs.state:<12}")

    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    """Configuration subcommands."""
    if args.config_command == "adopt":
        return _config_adopt()
    if args.config_command == "show":
        return _config_show()
    if args.config_command == "validate":
        return _config_validate()
    print("Usage: agent-codespaces config {adopt|show|validate}", file=sys.stderr)
    return 1


def _config_adopt() -> int:
    """Register the current repo for config."""
    import subprocess as sp

    cwd = Path.cwd()

    # Resolve to canonical repo root — worktree paths are ephemeral and
    # will go stale when the worktree is cleaned up.
    try:
        result = sp.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # git-common-dir returns the .git dir (or shared .git for
            # worktrees).  Parent of that is the canonical repo root.
            repo_root = Path(result.stdout.strip()).parent.resolve()
        else:
            repo_root = cwd
    except FileNotFoundError:
        repo_root = cwd

    config_file = repo_root / "codespaces.yaml"

    if not config_file.exists():
        print(f"ERROR: No codespaces.yaml found in {repo_root}", file=sys.stderr)
        print("Create one first, then re-run adopt.", file=sys.stderr)
        return 1

    repos = load_adopted_repos()
    existing_paths = {str(r.path) for r in repos}

    if str(repo_root) in existing_paths:
        print(f"Already adopted: {repo_root}")
        return 0

    repos.append(AdoptedRepo(
        path=repo_root,
        adopted_at=datetime.now(tz=timezone.utc).isoformat(),
    ))
    save_adopted_repos(repos)
    print(f"Adopted: {repo_root}")
    print(f"Manifest: {ADOPTED_REPOS_FILE}")
    return 0


def _config_show() -> int:
    """Show resolved config from all adopted repos."""
    config = load_merged_config()

    print("=== Resolved Configuration ===")
    print(f"Sources: {len(config.source_paths)} adopted repo(s)")
    for p in config.source_paths:
        print(f"  - {p}")

    print("\nDefaults:")
    print(f"  machine_type: {config.default_machine_type}")
    print(f"  location: {config.default_location}")
    if config.dotfiles_repo:
        print(f"  dotfiles_repo: {config.dotfiles_repo}")
    print(f"  ssh_user: {config.ssh_user}")
    if config.workspace_folder:
        print(f"  workspace_folder: {config.workspace_folder}")
    if config.acp_command:
        print(f"  acp_command: {config.acp_command} (explicit override)")
    print(f"  effective_acp_command: {config.effective_acp_command}")

    print(f"\nCredential relay port: {config.credentials.relay_port}")
    for name, source in config.credentials.sources.items():
        status = "enabled" if source.enabled else "disabled"
        print(f"  {name}: {status}")
        if source.allowed_hosts:
            for h in source.allowed_hosts:
                print(f"    - {h}")

    if config.repos:
        print(f"\nTarget repos: {len(config.repos)}")
        for repo_key, repo_cfg in config.repos.items():
            mt = repo_cfg.machine_type or config.default_machine_type
            loc = repo_cfg.location or config.default_location
            print(f"  {repo_key}: {mt} / {loc}")

    return 0


def _config_validate() -> int:
    """Validate config from all adopted repos."""
    config = load_merged_config()
    issues = validate_config(config)

    if not issues:
        print("[OK] Configuration is valid")
        return 0

    for issue in issues:
        print(f"[WARN] {issue}")
    return 1


def _cmd_delete(args: argparse.Namespace) -> int:
    """Delete a CodeSpace."""
    delete_codespace(args.name, force=args.force)
    print(f"Deleted: {args.name}")
    return 0


def _cmd_create(args: argparse.Namespace) -> int:
    """Create a CodeSpace and run post-create provisioning hooks."""
    from ssh_manager import ConnectionManager

    config = load_merged_config()
    print(f"Creating CodeSpace for {args.repo}...")
    info = create_codespace(args.repo, config, branch=args.branch)
    print(f"Created: {info.name}")

    if args.no_wait:
        return 0

    print("Waiting for CodeSpace to become Available...")
    if not wait_for_available(info.name, timeout=args.timeout):
        print(
            f"[WARN] {info.name} did not reach Available within "
            f"{args.timeout:.0f}s -- run provisioning later with "
            f"`agent-codespaces ssh {info.name}`",
            file=sys.stderr,
        )
        return 1

    # Provision over SSH: relay helpers + repo hooks including on_create.
    relay_port = config.credentials.relay_port
    port_forwards = [f"-R {relay_port}:127.0.0.1:{relay_port}"]
    source = CodespaceSource(info.name)
    manager = ConnectionManager()

    async def _run() -> int:
        await manager.ensure_connected(info.name, source, port_forwards)
        await _provision_relay_helpers(manager, info.name)
        await _provision_repo_hooks(
            manager, info.name, config, args.repo, include_on_create=True,
        )
        await manager.disconnect(info.name)
        return 0

    print("Running post-create provisioning...")
    rc = asyncio.run(_run())
    if rc == 0:
        print(f"[OK] {info.name} created and provisioned")
    return rc


def _cmd_bridge(args: argparse.Namespace) -> int:
    """Agent-bridge provider integration subcommands."""
    from .bridge_provider import (
        get_bridge_status,
        register_with_bridge,
        unregister_from_bridge,
    )

    if args.bridge_command == "register":
        result = register_with_bridge(
            bridge_url=args.bridge_url,
            ttl=args.ttl,
        )
        print(
            f"[OK] Registered {result.get('agents', 0)} agent(s) "
            f"with agent-bridge (ttl={result.get('ttl', 0):.0f}s)"
        )
        return 0

    if args.bridge_command == "unregister":
        unregister_from_bridge(bridge_url=args.bridge_url)
        print("[OK] Unregistered codespace agents from agent-bridge")
        return 0

    if args.bridge_command == "status":
        status = get_bridge_status(bridge_url=args.bridge_url)
        if status is None:
            print("[--] Not registered (or agent-bridge not reachable)")
            return 0
        expired = status.get("expired", False)
        state = "EXPIRED" if expired else "ACTIVE"
        print(f"[{state}] Provider '{status.get('name', 'codespaces')}'")
        print(f"  Agents: {status.get('agents', 0)}")
        print(f"  Active: {status.get('active_agents', 0)}")
        print(f"  TTL: {status.get('ttl', 0):.0f}s")
        print(f"  Age: {status.get('age', 0):.0f}s")
        conflicts = status.get("conflicts", [])
        if conflicts:
            print(f"  Conflicts: {', '.join(conflicts)}")
        return 0

    if args.bridge_command == "refresh":
        # Re-register with fresh codespace state (drops stale agents)
        result = register_with_bridge(
            bridge_url=args.bridge_url,
            ttl=args.ttl,
        )
        print(
            f"[OK] Refreshed: {result.get('agents', 0)} agent(s) "
            f"registered (ttl={result.get('ttl', 0):.0f}s)"
        )
        return 0

    print(
        "Usage: agent-codespaces bridge {register|unregister|status|refresh}",
        file=sys.stderr,
    )
    return 1


def _cmd_cleanup(args: argparse.Namespace) -> int:
    """Remove stale local state for deleted/rotated codespaces."""
    mode = "Dry run" if args.dry_run else "Cleanup"
    print(f"=== {mode}: pruning stale codespace state ===")

    removed = cleanup_stale(dry_run=args.dry_run)

    ssh_count = len(removed["ssh_configs"])
    socket_count = len(removed["sockets"])
    total = ssh_count + socket_count

    if ssh_count:
        print(f"\nSSH configs ({ssh_count}):")
        for p in removed["ssh_configs"]:
            print(f"  {'[WOULD REMOVE]' if args.dry_run else '[REMOVED]'} {p}")

    if socket_count:
        print(f"\nSockets ({socket_count}):")
        for p in removed["sockets"]:
            print(f"  {'[WOULD REMOVE]' if args.dry_run else '[REMOVED]'} {p}")

    if total == 0:
        print("No stale state found")
    else:
        verb = "would be removed" if args.dry_run else "removed"
        print(f"\n{total} item(s) {verb}")

    return 0


def _cmd_status() -> int:
    """Show service status overview."""
    print("=== agent-codespaces status ===")
    print(f"Runtime dir: {RUNTIME_DIR}")
    print(f"Adopted repos: {ADOPTED_REPOS_FILE}")

    repos = load_adopted_repos()
    print(f"Adopted repo count: {len(repos)}")
    for r in repos:
        exists = r.path.exists()
        status = "[OK]" if exists else "[MISSING]"
        print(f"  {status} {r.path}")

    config = load_merged_config()
    issues = validate_config(config)
    if issues:
        print(f"\nConfig warnings: {len(issues)}")
        for i in issues:
            print(f"  [WARN] {i}")
    else:
        print("\nConfig: [OK]")

    # Check gh CLI
    import shutil
    gh = shutil.which("gh")
    print(f"\ngh CLI: {'[OK] ' + gh if gh else '[MISSING]'}")

    ssh = shutil.which("ssh")
    print(f"ssh: {'[OK] ' + ssh if ssh else '[MISSING]'}")

    return 0


def _cmd_version() -> int:
    """Show version."""
    try:
        from ._build_info import BUILD_INFO
        ver = BUILD_INFO.get("version", "0.0.0")
        commit = BUILD_INFO.get("commit", "unknown")[:8]
        print(f"agent-codespaces {ver} ({commit})")
    except ImportError:
        print("agent-codespaces 0.1.0-dev2")
    return 0


if __name__ == "__main__":
    sys.exit(main())

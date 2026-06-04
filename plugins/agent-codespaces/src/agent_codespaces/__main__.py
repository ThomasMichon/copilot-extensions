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
from .lifecycle import delete_codespace, list_codespaces

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
        if args.command == "bridge":
            return _cmd_bridge(args)
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
    if not args.no_relay:
        port_forwards.append(f"-R {relay_port}:localhost:{relay_port}")

    manager = ConnectionManager()

    async def _run() -> int:
        await manager.ensure_connected(args.name, source, port_forwards)

        if args.stdio and args.remote_cmd:
            # Structured stdio mode for agent-bridge
            proc = await manager.open_stdio_channel(args.name, args.remote_cmd)
            # Pipe through to our own stdio
            await _pipe_stdio(proc)
            return proc.returncode if proc.returncode is not None else 1

        if args.remote_cmd:
            # Non-interactive command execution
            result = await manager.exec_command(args.name, args.remote_cmd)
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            return result.exit_code

        # Interactive SSH -- fall through to gh codespace ssh
        await manager.disconnect(args.name)
        return _interactive_ssh(args.name, port_forwards)

    return asyncio.run(_run())


async def _pipe_stdio(proc) -> None:
    """Pipe a subprocess's stdio through to our own stdin/stdout."""
    import asyncio

    async def _forward_in() -> None:
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
        while True:
            data = await reader.read(4096)
            if not data:
                if proc.stdin:
                    proc.stdin.close()
                break
            if proc.stdin:
                proc.stdin.write(data)
                await proc.stdin.drain()

    async def _forward_out() -> None:
        while proc.stdout:
            data = await proc.stdout.read(4096)
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    await asyncio.gather(
        _forward_in(),
        _forward_out(),
        proc.wait(),
    )


def _interactive_ssh(codespace_name: str, port_forwards: list[str]) -> int:
    """Fall back to ``gh codespace ssh`` for interactive sessions."""
    import subprocess as sp

    args = ["gh", "codespace", "ssh", "-c", codespace_name]
    for fwd in port_forwards:
        # Split "-R port:host:port" into SSH option
        args.extend(["--", fwd])

    return sp.call(args)


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
    cwd = Path.cwd()
    config_file = cwd / "codespaces.yaml"

    if not config_file.exists():
        print(f"ERROR: No codespaces.yaml found in {cwd}", file=sys.stderr)
        print("Create one first, then re-run adopt.", file=sys.stderr)
        return 1

    repos = load_adopted_repos()
    existing_paths = {str(r.path) for r in repos}

    if str(cwd) in existing_paths:
        print(f"Already adopted: {cwd}")
        return 0

    repos.append(AdoptedRepo(
        path=cwd,
        adopted_at=datetime.now(tz=timezone.utc).isoformat(),
    ))
    save_adopted_repos(repos)
    print(f"Adopted: {cwd}")
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

    print(
        "Usage: agent-codespaces bridge {register|unregister|status}",
        file=sys.stderr,
    )
    return 1


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
    print("agent-codespaces 0.1.0-dev1")
    return 0


if __name__ == "__main__":
    sys.exit(main())

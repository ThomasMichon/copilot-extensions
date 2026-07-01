"""CLI entry point for agent-codespaces.

Subcommands:
  ssh <name>            SSH into a CodeSpace (interactive or --stdio)
  list                  List active CodeSpaces
  config adopt          Register current repo for config
  config init           Scaffold codespaces.yaml from existing CodeSpaces
  config show           Show resolved config
  config validate       Validate config
  delete <name>         Delete a CodeSpace (recovers sessions first)
  finalize <name>       Recover Copilot sessions, then optionally --delete
  borrow <effort> <cs>  Advisory-lease a CodeSpace to an effort (check out)
  release <target>      Release a lease (by CodeSpace or effort name)
  leases                Show active CodeSpace leases
  wait <name>           Patiently wait for Available (fail-fast on dead state)
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
import time
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
from .connect import (
    ConnectStage,
    ConnectTracker,
    breadcrumb_prelude,
)
from .lifecycle import (
    cleanup_stale,
    create_codespace,
    delete_codespace,
    list_codespaces,
    wait_for_available,
)
from .sessions import sync_codespace_sessions

log = logging.getLogger("agent-codespaces")

# Patience budget for the SSH-to-CodeSpace stage -- a Shutdown CodeSpace boots
# on connect, which can take well over a minute. Overridable via env.
_SSH_BOOT_TIMEOUT = float(os.environ.get("AGENT_CODESPACES_BOOT_TIMEOUT", "180"))

# Exit code when an SSH operation is rejected because the target is already in
# use by another live process (see ssh_manager.TargetBusyError). Distinct from
# generic failures (1) and the --remote-cmd timeout (124) so callers can react.
_BUSY_EXIT = 75


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
        help="Remote command to execute (non-interactive, no PTY). Interactive "
             "prompts (e.g. a sudo password) will hang -- use `sudo -n`. A "
             "backgrounded process must fully detach its stdio "
             "(`nohup <cmd> >/tmp/out 2>&1 </dev/null & disown`) or it holds "
             "the channel open until --timeout.",
    )
    ssh_parser.add_argument(
        "--timeout", dest="timeout", type=float, default=60.0, metavar="SECS",
        help="Timeout in seconds for --remote-cmd execution (default: 60). On "
             "expiry the command is terminated and the CLI exits 124.",
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
    ssh_parser.add_argument(
        "--effort", dest="effort", default=None,
        help="Effort/worktree borrowing this CodeSpace. When set, records an "
             "advisory lease (check-out) and refreshes its heartbeat on connect. "
             "A conflicting live lease warns but does not block (use `borrow "
             "--force` to take over explicitly).",
    )
    ssh_parser.add_argument(
        "--force", action="store_true",
        help="Take over the target if another SSH operation is already in "
             "progress against it: terminates the in-flight connection and "
             "reclaims the target (discards its in-progress work). Without "
             "this, a busy target is rejected with an explanatory error.",
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
    config_init_p = config_sub.add_parser(
        "init",
        help="Scaffold codespaces.yaml in the current repo, deriving defaults "
             "from your existing CodeSpaces (gh codespace list)",
    )
    config_init_p.add_argument(
        "--from-codespace", dest="from_codespace", default=None,
        help="Derive defaults from this CodeSpace name (default: auto-pick)",
    )
    config_init_p.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing codespaces.yaml",
    )
    config_init_p.add_argument(
        "--adopt", action="store_true",
        help="Also register the repo (run adopt) after writing the file",
    )

    # --- delete ---
    delete_parser = sub.add_parser("delete", help="Delete a CodeSpace")
    delete_parser.add_argument("name", help="CodeSpace name")
    delete_parser.add_argument(
        "--force", action="store_true", help="Force deletion",
    )
    delete_parser.add_argument(
        "--no-sync", action="store_true",
        help="Skip the pre-delete Copilot session recovery",
    )

    # --- finalize ---
    finalize_parser = sub.add_parser(
        "finalize",
        help="Gracefully close out a CodeSpace: recover Copilot sessions, "
             "then optionally delete",
    )
    finalize_parser.add_argument("name", help="CodeSpace name")
    finalize_parser.add_argument(
        "--delete", action="store_true",
        help="Delete the CodeSpace after a successful session recovery",
    )
    finalize_parser.add_argument(
        "--force", action="store_true",
        help="With --delete: delete even if recovery failed -- diagnose the "
             "failure first, do not use for routine hiccups (destroys "
             "unrecovered sessions)",
    )
    finalize_parser.add_argument(
        "--timeout", type=float, default=300.0,
        help="Seconds for the session pull (default: 300)",
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
        "--display-name", dest="display_name", default=None,
        help="Display name for the CodeSpace",
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

    # --- borrow / release / leases (advisory borrow broker) ---
    borrow_p = sub.add_parser(
        "borrow",
        help="Advisory-lease a CodeSpace to an effort (check it out)",
    )
    borrow_p.add_argument("effort", help="Effort/worktree name (lease holder)")
    borrow_p.add_argument("codespace", help="CodeSpace name to borrow")
    borrow_p.add_argument(
        "--force", action="store_true",
        help="Take over even if leased by another effort (stale/buggy holder)",
    )

    release_p = sub.add_parser(
        "release", help="Release a CodeSpace lease (check it in)",
    )
    release_p.add_argument("target", help="CodeSpace name or effort name")

    sub.add_parser("leases", help="Show active CodeSpace leases")

    # --- wait (patient, fail-fast, backgroundable) ---
    wait_p = sub.add_parser(
        "wait",
        help="Wait for a CodeSpace to become Available (patient; fails fast on "
             "a genuinely-dead state; safe to run as a background task)",
    )
    wait_p.add_argument("name", help="CodeSpace name")
    wait_p.add_argument(
        "--timeout", type=float, default=1200.0,
        help="Max seconds to wait (default: 1200 = 20 min)",
    )
    wait_p.add_argument(
        "--interval", type=float, default=10.0,
        help="Poll interval in seconds (default: 10)",
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
        if args.command == "finalize":
            return _cmd_finalize(args)
        if args.command == "create":
            return _cmd_create(args)
        if args.command == "bridge":
            return _cmd_bridge(args)
        if args.command == "cleanup":
            return _cmd_cleanup(args)
        if args.command == "borrow":
            return _cmd_borrow(args)
        if args.command == "release":
            return _cmd_release(args)
        if args.command == "leases":
            return _cmd_leases()
        if args.command == "wait":
            return _cmd_wait(args)
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
    from ssh_manager import ConnectionManager, TargetBusyError, TargetLock

    source = CodespaceSource(args.name)
    config = load_merged_config()
    relay_port = config.credentials.relay_port

    # Advisory check-out: record/refresh the borrow so a parallel same-machine
    # agent doesn't dispatch to this CodeSpace concurrently. Non-blocking -- a
    # conflicting live lease warns but still connects (use `borrow --force` to
    # take over explicitly). ``ssh --force`` (SSH-lock takeover) also forces the
    # lease takeover for consistency.
    effort = getattr(args, "effort", None)
    if effort:
        from .lease import borrow

        try:
            borrow(effort, args.name, force=getattr(args, "force", False))
        except RuntimeError as exc:
            print(f"[WARN] CodeSpace lease conflict (continuing): {exc}",
                  file=sys.stderr)

    # Build port forwards for credential relay
    port_forwards: list[str] = []
    relay_env = ""
    relay_token: str | None = None
    if not args.no_relay:
        port_forwards.append(f"-R {relay_port}:127.0.0.1:{relay_port}")
        # Per-codespace relay token: the shared relay gates get-azure-token
        # (it also serves network-reachable containers), so the codespace path
        # must present its own secret for the official azure-auth-helper scope
        # broker. Minted/persisted host-side; injected over SSH as LC_* so it
        # survives the login shell into the relay client.
        from .relay_token import token_for

        relay_token = token_for(args.name)
        # GIT_TERMINAL_PROMPT=0 ensures git never blocks on an interactive
        # prompt if a credential cannot be resolved over the relay -- it aborts
        # with a prompt error instead of hanging (belt-and-suspenders alongside
        # the relay's quit=1 fail-fast).
        relay_env = (
            f"export LC_GIT_CREDENTIAL_RELAY={relay_port}; "
            f"export LC_GIT_CREDENTIAL_RELAY_TOKEN={relay_token}; "
            "export GIT_TERMINAL_PROMPT=0; "
        )

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
        # Prepend a device-arrival breadcrumb so a hung/failed launch can be
        # diagnosed as "reached the CodeSpace" via ~/.agent-bridge-connect.log.
        inner = relay_env + breadcrumb_prelude(args.name) + "; " + remote_cmd
        remote_cmd = f"bash -l -c {shlex.quote(inner)}"

    tracker = ConnectTracker(session_id=args.name)

    async def _run() -> int:
        # Stage 3 (ssh-to-target): a Shutdown CodeSpace boots on connect, so be
        # patient -- retry to the boot deadline, then fail fast with a clear,
        # staged message (never an opaque provider death).
        tracker.started(ConnectStage.SSH_TO_TARGET, f"codespace={args.name}")
        deadline = time.monotonic() + _SSH_BOOT_TIMEOUT
        backoff = 3.0
        while True:
            try:
                await manager.ensure_connected(args.name, source, port_forwards)
                tracker.reached(ConnectStage.SSH_TO_TARGET, f"codespace={args.name}")
                break
            except (ConnectionError, TimeoutError) as exc:
                if time.monotonic() + backoff >= deadline:
                    tracker.failed(
                        ConnectStage.SSH_TO_TARGET,
                        f"Failed to reach CodeSpace {args.name}: {exc}",
                        retryable=True,
                    )
                    print(
                        f"[FAIL] Could not establish SSH to CodeSpace "
                        f"'{args.name}' within {_SSH_BOOT_TIMEOUT:.0f}s "
                        f"(stage 3/ssh-to-target): {exc}",
                        file=sys.stderr,
                    )
                    return 1
                log.info(
                    "CodeSpace %s not ready (booting?): %s -- retry in %.0fs",
                    args.name, exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 20.0)

        # Stage 4 (target-auth-env): deploy the CodeSpace-side relay helpers so
        # ADO auth resolves over the tunnel. Idempotent; best-effort -- a
        # failure here shouldn't block the SSH command.
        if not args.no_relay:
            tracker.started(ConnectStage.TARGET_AUTH_ENV, "credential relay")
            await _provision_relay_helpers(manager, args.name)
            tracker.reached(ConnectStage.TARGET_AUTH_ENV)

        # Ensure the account dotfiles repo is cloned + current (universal
        # bootstrap, gated on `dotfiles_repo`). Heals a CodeSpace whose
        # post-start dotfiles clone hasn't run (e.g. first agent-bridge connect)
        # and syncs it forward on reconnect. Needs the relay up for git auth.
        if not args.no_relay:
            await _provision_dotfiles(manager, args.name, config)

        # Run repo-declared provision hooks (by-convention extras from the
        # adopted repo's codespaces.yaml). Best-effort, idempotent.
        await _provision_repo_hooks(
            manager, args.name, config, getattr(args, "repo", None),
        )

        # Verify the host has local auth for every domain the session's git
        # remotes use -- the workspace (ADO) AND the dotfiles repo (GitHub).
        # Surfaces missing auth up front rather than letting it fail mid-fetch.
        # Best-effort, warning-only.
        if not args.no_relay:
            await _verify_remote_auth(manager, args.name, config)

        if args.stdio and remote_cmd:
            # Structured stdio mode for agent-bridge
            proc = await manager.open_stdio_channel(args.name, remote_cmd)
            # Pipe through to our own stdio
            await _pipe_stdio(proc)
            return proc.returncode if proc.returncode is not None else 1

        if remote_cmd:
            # Non-interactive command execution
            result = await manager.exec_command(
                args.name, remote_cmd, timeout=args.timeout
            )
            return _emit_remote_cmd_result(result, args.timeout)

        # Interactive SSH -- fall through to gh codespace ssh
        await manager.disconnect(args.name)
        return _interactive_ssh(
            args.name,
            port_forwards,
            relay_port=relay_port if not args.no_relay else None,
            relay_token=relay_token,
        )

    # Serialize SSH access to this CodeSpace across processes. All access funnels
    # through one credential-relay reverse-forward (one relay port per host), so
    # a second concurrent connection collides on that port and can collapse a
    # live agent-bridge dispatch. Hold a per-target lock for the operation's
    # lifetime; reject a busy target (or take over with --force).
    op = "stdio" if args.stdio else ("remote-cmd" if args.remote_cmd else "interactive")
    target_lock = TargetLock(args.name, op=op)
    try:
        target_lock.acquire(force=getattr(args, "force", False))
    except TargetBusyError as busy:
        print(busy.user_message(), file=sys.stderr)
        return _BUSY_EXIT

    try:
        return asyncio.run(_run())
    finally:
        target_lock.release()


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


async def _provision_dotfiles(manager, name: str, config) -> None:
    """Ensure the configured dotfiles repo is present + current on a CodeSpace.

    Universal bootstrap for every CodeSpace when ``defaults.dotfiles_repo`` is
    set: clone-if-absent (+ run ``install.sh``) and sync-forward on the default
    branch (re-installing only when ``HEAD`` moved); a checkout parked on a
    feature branch / left dirty is never touched. This used to live in a per-repo
    ``on_create`` hook; making it built-in means a CodeSpace created outside
    agent-codespaces (e.g. via the GitHub UI / VS Code, where the post-start
    dotfiles clone may not have completed) is healed on the first connect.
    Best-effort and idempotent: logs a warning on failure but never raises.
    """
    if not config.dotfiles_repo:
        return

    from .provision import build_dotfiles_command

    try:
        command = build_dotfiles_command(
            config.dotfiles_repo, config.credentials.relay_port,
        )
        # Run under a LOGIN shell: the dotfiles clone authenticates to GitHub via
        # the CodeSpace's own credential helper (gitcredential_github.sh), which
        # needs the platform env (GITHUB_TOKEN, profile.d) that only a login
        # shell loads. A non-login `exec_command` would clone unauthenticated and
        # fail silently. Mirrors how `_verify_remote_auth` and the remote_cmd
        # path wrap their commands.
        login_command = f"bash -l -c {shlex.quote(command)}"
        # Clone + install.sh can run long on a first connect; be generous.
        result = await manager.exec_command(name, login_command, timeout=900.0)
        if result.exit_code == 0:
            log.debug("Dotfiles provisioned on %s", name)
        else:
            log.warning(
                "Dotfiles provisioning on %s exited %s: %s",
                name, result.exit_code, result.stderr.strip(),
            )
    except Exception as exc:
        log.warning("Dotfiles provisioning on %s failed: %s", name, exc)


async def _verify_remote_auth(manager, name: str, config) -> None:
    """Verify host-side auth for the CodeSpace's git remote domains.

    Lists the git remotes of both the workspace/product checkout and the
    dotfiles checkout, extracts their domains, and probes the local credential
    store (the same source the relay uses) for each. When ``dotfiles_repo`` is
    configured, its host (e.g. github.com) is verified explicitly too -- even
    on a first connect before the dotfiles clone exists. Missing domains are
    reported as a warning so the user can fix auth (``az login`` / GCM sign-in)
    before work begins. Best-effort: never raises.
    """
    from .auth_preflight import host_from_url, verify_remote_auth

    async def _run_remote(cmd: str) -> str:
        wrapped = f"bash -l -c {shlex.quote(cmd)}"
        result = await manager.exec_command(name, wrapped, timeout=30.0)
        return result.stdout or ""

    # Guarantee the dotfiles repo's host is checked even if its checkout isn't
    # present yet (account dotfiles are always GitHub-hosted).
    extra_hosts: list[str] = []
    if config.dotfiles_repo:
        host = host_from_url(f"https://github.com/{config.dotfiles_repo}")
        if host:
            extra_hosts.append(host)

    try:
        hosts, missing = await verify_remote_auth(
            _run_remote, extra_hosts=extra_hosts,
        )
    except Exception as exc:
        log.debug("Remote auth verification on %s failed: %s", name, exc)
        return

    if not hosts:
        return

    if missing:
        msg = (
            f"Missing local auth for remote domain(s): {', '.join(missing)}. "
            f"Git operations against these in CodeSpace '{name}' will fail "
            f"fast over the relay. Sign in on the host "
            f"(az login / Git Credential Manager) for each domain."
        )
        log.warning(msg)
        print(f"[WARN] {msg}", file=sys.stderr)
    else:
        log.info(
            "Remote auth verified for %s: %s", name, ", ".join(hosts),
        )


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


def _emit_remote_cmd_result(result, timeout: float) -> int:  # noqa: ANN001
    """Print a remote command's output and return its exit code.

    Surfaces partial output and a loud, cause-hinting error when the command
    was terminated for exceeding the timeout, instead of returning a silent
    ``-1`` with swallowed output (#47). The remote command runs without a PTY
    (``-T``), so the usual causes of a hang are a backgrounded process that
    keeps the stdout/stderr channel open, or a command waiting for input the
    session cannot provide (e.g. a ``sudo`` password prompt).
    """
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.timed_out:
        print(
            f"[FAIL] Remote command did not finish within {timeout:.0f}s and "
            f"was terminated (no PTY).\n"
            f"       - Backgrounded work must fully detach its stdio, e.g. "
            f"`nohup <cmd> >/tmp/out 2>&1 </dev/null & disown`.\n"
            f"       - sudo cannot prompt here; use passwordless `sudo -n`.\n"
            f"       - For a legitimately long command, raise `--timeout <secs>`.",
            file=sys.stderr,
        )
        return 124
    return result.exit_code


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
                    # Block until drained -- never time out. A drain timeout
                    # here would close the agent's stdin under backpressure and
                    # wedge the ACP channel (see _forward_out for the symmetric
                    # stdout hazard, #46.6).
                    asyncio.run_coroutine_threadsafe(
                        proc.stdin.drain(), loop
                    ).result()
        except (OSError, ValueError):
            pass
        finally:
            if proc.stdin:
                proc.stdin.close()

    def _forward_out() -> None:
        """Read from subprocess stdout, write to our stdout (blocking).

        Blocks indefinitely on each read -- it must NEVER give up on a merely
        *quiet* channel. A long, output-buffered remote tool call (a multi-
        minute ``rush build``/test, or the agent thinking) emits no ACP stdout
        for well over a minute; a read timeout here would terminate this pump
        thread mid-dispatch and silently collapse the session. On Python 3.11+
        the prior ``fut.result(timeout=30)`` made this worse: the resulting
        ``TimeoutError`` is an ``OSError`` subclass, so it was swallowed by the
        ``except`` below and the relay exited cleanly after 30s of silence --
        the root cause of the ~10-15 min dispatch collapse (#46.6). A genuinely
        dead connection still terminates the relay correctly: SSH's
        ``ServerAliveInterval`` kills the ssh process, closing stdout (EOF),
        which returns empty and breaks the loop.
        """
        try:
            while True:
                # read1 is not available on asyncio streams; use the
                # loop to schedule the async read from this thread.
                fut = asyncio.run_coroutine_threadsafe(
                    proc.stdout.read(4096), loop
                )
                data = fut.result()  # block until data or EOF -- no timeout
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
    relay_token: str | None = None,
) -> int:
    """Fall back to ``gh codespace ssh`` for interactive sessions."""
    import subprocess as sp

    env = None
    if relay_port is not None:
        env = {
            **os.environ,
            "LC_GIT_CREDENTIAL_RELAY": str(relay_port),
            "GIT_TERMINAL_PROMPT": "0",
        }
        if relay_token:
            env["LC_GIT_CREDENTIAL_RELAY_TOKEN"] = relay_token

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
    if args.config_command == "init":
        return _config_init(
            from_codespace=args.from_codespace,
            force=args.force,
            also_adopt=args.adopt,
        )
    print(
        "Usage: agent-codespaces config {init|adopt|show|validate}",
        file=sys.stderr,
    )
    return 1


def _resolve_repo_root() -> Path:
    """Resolve the canonical repo root (worktree-safe), or cwd if not a repo."""
    import subprocess as sp

    cwd = Path.cwd()
    try:
        result = sp.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).parent.resolve()
    except FileNotFoundError:
        pass
    return cwd


def _list_codespaces_for_init() -> list[dict]:
    """Return `gh codespace list` entries, or [] on any failure."""
    import subprocess as sp

    try:
        result = sp.run(
            ["gh", "codespace", "list", "--json",
             "name,repository,machineName,displayName,state,lastUsedAt"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, sp.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _discover_workspace_folder(codespaces: list[dict], repository: str) -> str | None:
    """Best-effort: read $WORKING_DIRECTORY from an already-Available CodeSpace.

    Only targets CodeSpaces already in the ``Available`` state so we never pay
    a cold-start. Returns None on any failure (no Available CodeSpace, SSH
    error, timeout) -- callers must treat workspace_folder as unknown, not
    guess it from the repo name (the CodeSpaces repo name often differs from
    the checked-out workspace, e.g. ``<repo>-codespaces`` vs ``<repo>``).
    """
    import subprocess as sp

    available = [
        c for c in codespaces
        if c.get("repository") == repository and c.get("state") == "Available"
    ]
    for c in available:
        name = c.get("name")
        if not name:
            continue
        try:
            result = sp.run(
                ["gh", "codespace", "ssh", "-c", name, "--",
                 "printf %s \"$WORKING_DIRECTORY\""],
                capture_output=True,
                text=True,
                timeout=45,
            )
        except (FileNotFoundError, sp.TimeoutExpired):
            return None
        if result.returncode == 0:
            wd = (result.stdout or "").strip()
            if wd.startswith("/"):
                return wd
    return None


def _derive_codespaces_defaults(
    codespaces: list[dict], from_codespace: str | None
) -> dict | None:
    """Pick a representative CodeSpace and derive scaffold defaults.

    Returns a dict with keys: repository, machine_type, workspace_folder
    (str or None if it could not be reliably discovered), source_name.
    Returns None if no usable CodeSpace is found.
    """
    if not codespaces:
        return None

    chosen: dict | None = None
    if from_codespace:
        chosen = next(
            (c for c in codespaces if c.get("name") == from_codespace), None
        )
        if chosen is None:
            return None
    else:
        # Prefer the most-recently-used CodeSpace (lastUsedAt is ISO-8601, so
        # lexical max works); fall back to the first.
        chosen = max(
            codespaces,
            key=lambda c: c.get("lastUsedAt") or "",
        )

    repository = chosen.get("repository") or ""

    # Use the most common machine type across CodeSpaces of the chosen repo
    # (more representative than a single CodeSpace's machine).
    same_repo = [c for c in codespaces if c.get("repository") == repository]
    machine_counts: dict[str, int] = {}
    for c in same_repo:
        m = c.get("machineName")
        if m:
            machine_counts[m] = machine_counts.get(m, 0) + 1
    machine_type = (
        max(machine_counts, key=machine_counts.get)
        if machine_counts
        else "largePremiumLinux"
    )

    # Discover workspace_folder from a live CodeSpace -- NOT from the repo name,
    # which is unreliable (the CodeSpaces repo often differs from the checkout).
    workspace_folder = _discover_workspace_folder(codespaces, repository)

    return {
        "repository": repository,
        "machine_type": machine_type,
        "workspace_folder": workspace_folder,
        "source_name": chosen.get("displayName") or chosen.get("name") or "",
    }


def _render_codespaces_yaml(defaults: dict | None) -> str:
    """Render a codespaces.yaml. If defaults is None, emit a generic template."""
    if defaults:
        machine = defaults["machine_type"]
        repo = defaults["repository"]
        ws = defaults.get("workspace_folder")
        if ws:
            workspace_line = f"  workspace_folder: {ws}\n"
        else:
            # Could not discover it cheaply -- emit a TODO, never a wrong guess.
            workspace_line = (
                "  # workspace_folder: /workspaces/<your-checkout>   # TODO: set "
                "to the repo\n  # checkout path INSIDE the CodeSpace. This is the "
                "dir `cd $WORKING_DIRECTORY`\n  # lands in -- often NOT the "
                "CodeSpaces repo name. Find it with:\n"
                "  #   gh codespace ssh -c <name> -- 'echo $WORKING_DIRECTORY'\n"
            )
        repo_block = (
            f"\nrepos:\n  {repo}:\n    machine_type: {machine}\n"
            if repo else ""
        )
        ws_status = ws if ws else "UNKNOWN (left as a TODO -- set it manually)"
        derived_note = (
            f"# Derived from your existing CodeSpace "
            f"'{defaults['source_name']}'.\n"
            f"# workspace_folder: {ws_status}\n"
        )
    else:
        machine = "largePremiumLinux"
        workspace_line = "  workspace_folder: /workspaces/<your-repo>\n"
        repo_block = (
            "\n# repos:\n#   <your-org>/<your-repo>:\n"
            "#     machine_type: largePremiumLinux256gb\n"
        )
        derived_note = (
            "# No existing CodeSpaces found -- this is a generic template. "
            "Fill in the\n# placeholders below.\n"
        )

    return (
        "# codespaces.yaml -- agent-codespaces configuration\n"
        "# All org/account/URL values live HERE, in your own repo -- never in\n"
        "# the copilot-extensions plugin.\n"
        f"{derived_note}\n"
        "defaults:\n"
        f"  machine_type: {machine}\n"
        "  location: EastUs\n"
        "  ssh_user: vscode\n"
        f"{workspace_line}"
        "  # dotfiles_repo: <your-user>/<your-dotfiles>\n"
        "\n"
        "credentials:\n"
        "  relay_port: 9857\n"
        "  # ado_host: <your-org>.visualstudio.com   # for bare get-access-token\n"
        "  sources:\n"
        "    git-credential:\n"
        "      enabled: true\n"
        "      allowed_hosts:\n"
        '        - "github.com"\n'
        '        - "*.github.com"\n'
        '        - "dev.azure.com"\n'
        '        - "*.visualstudio.com"\n'
        "    gh-auth:\n"
        "      enabled: true\n"
        "      allowed_hosts:\n"
        '        - "github.com"\n'
        f"{repo_block}"
    )


def _gh_auth_preflight() -> list[str]:
    """Check gh auth + codespace scope. Returns a list of guidance messages
    (empty if all good)."""
    import subprocess as sp

    msgs: list[str] = []
    try:
        result = sp.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=20,
        )
    except FileNotFoundError:
        return ["gh CLI not found -- install from https://cli.github.com/ "
                "then run: gh auth login"]
    except sp.TimeoutExpired:
        return ["gh auth status timed out -- check your network / gh install."]

    combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "not logged" in combined.lower():
        msgs.append("gh is not authenticated -- run: gh auth login")
        return msgs

    # gh prints "Token scopes: 'gist', 'repo', ..." -- the codespace scope is
    # required for `gh codespace` operations.
    if "codespace" not in combined.lower():
        msgs.append(
            "gh token is missing the 'codespace' scope (needed for CodeSpace "
            "operations) -- run: gh auth refresh -h github.com -s codespace"
        )
    return msgs


def _config_init(
    *, from_codespace: str | None, force: bool, also_adopt: bool
) -> int:
    """Scaffold codespaces.yaml, deriving defaults from existing CodeSpaces."""
    repo_root = _resolve_repo_root()
    config_file = repo_root / "codespaces.yaml"

    if config_file.exists() and not force:
        print(f"codespaces.yaml already exists at {config_file}")
        print("Use --force to overwrite, or edit it directly.")
        return 0

    # Preflight: surface gh auth / scope problems explicitly, so an empty
    # `gh codespace list` (auth failure) isn't mistaken for "no CodeSpaces".
    gh_msgs = _gh_auth_preflight()
    for m in gh_msgs:
        print(f"[gh] {m}", file=sys.stderr)

    codespaces = _list_codespaces_for_init()
    defaults = _derive_codespaces_defaults(codespaces, from_codespace)

    if from_codespace and defaults is None:
        print(
            f"ERROR: CodeSpace '{from_codespace}' not found in "
            "`gh codespace list`.",
            file=sys.stderr,
        )
        return 1

    content = _render_codespaces_yaml(defaults)
    config_file.write_text(content, encoding="utf-8")

    print(f"Wrote {config_file}")
    if defaults:
        print(f"  Derived from CodeSpace: {defaults['source_name']}")
        print(f"  repository:        {defaults['repository']}")
        print(f"  machine_type:      {defaults['machine_type']}")
        ws = defaults.get("workspace_folder")
        if ws:
            print(f"  workspace_folder:  {ws}  (discovered from a live CodeSpace)")
        else:
            print(
                "  workspace_folder:  NOT set -- no Available CodeSpace to read "
                "$WORKING_DIRECTORY from."
            )
            print(
                "                     Left as a TODO in the file. Set it to your "
                "checkout path"
            )
            print(
                "                     (often NOT the CodeSpaces repo name, e.g. "
                "<repo> vs <repo>-codespaces)."
            )
    else:
        print("  No existing CodeSpaces detected -- wrote a generic template.")
        print("  Edit the placeholders before adopting.")

    if also_adopt:
        print()
        return _config_adopt()
    print("\nReview the file, then run: agent-codespaces config adopt")
    return 0


def _config_adopt() -> int:
    """Register the current repo for config."""
    repo_root = _resolve_repo_root()

    config_file = repo_root / "codespaces.yaml"

    if not config_file.exists():
        print(f"ERROR: No codespaces.yaml found in {repo_root}", file=sys.stderr)
        print(
            "Run `agent-codespaces config init` to scaffold one "
            "(it derives defaults from your existing CodeSpaces), "
            "then re-run adopt.",
            file=sys.stderr,
        )
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
    """Delete a CodeSpace, recovering its Copilot sessions first (unless
    --no-sync). The recovery is best-effort: a failure warns but does not block
    deletion (use `finalize` for a sync-gated delete)."""
    if not getattr(args, "no_sync", False):
        res = sync_codespace_sessions(args.name, verbose=args.verbose)
        if res.get("ok"):
            print(f"[OK] Recovered {res.get('session_count', 0)} session(s) "
                  f"before delete: {res.get('detail', '')}")
        else:
            print(f"[WARN] Pre-delete session recovery failed (continuing): "
                  f"{res.get('detail')}", file=sys.stderr)
    delete_codespace(args.name, force=args.force)
    print(f"Deleted: {args.name}")
    _release_lease_quietly(args.name)
    return 0


def _cmd_finalize(args: argparse.Namespace) -> int:
    """Gracefully close out a CodeSpace: recover its Copilot sessions into the
    agent-logger hub, then optionally delete it.

    Without --delete this is a pure recovery. With --delete, the CodeSpace is
    removed only after a successful sync, unless --force overrides a failed one.
    """
    res = sync_codespace_sessions(args.name, timeout=args.timeout, verbose=args.verbose)
    if res.get("ok"):
        print(f"[OK] Recovered {res.get('session_count', 0)} session(s) from "
              f"{args.name}: {res.get('detail', '')}")
    else:
        print(f"[WARN] Session recovery for {args.name} failed: "
              f"{res.get('detail')}", file=sys.stderr)
        if args.delete and not args.force:
            print("Refusing to delete after a failed recovery. Diagnose and "
                  "resolve the error above (often a still-booting CodeSpace or "
                  "an SSH/relay issue), then re-run finalize so the sessions "
                  "are captured.", file=sys.stderr)
            return 1

    if args.delete:
        delete_codespace(args.name, force=args.force)
        print(f"Deleted: {args.name}")
        _release_lease_quietly(args.name)

    return 0 if res.get("ok") else 1


def _release_lease_quietly(codespace: str) -> None:
    """Check a CodeSpace back in on teardown. Best-effort, never raises."""
    try:
        from .lease import release

        if release(codespace):
            print(f"[OK] Released lease on {codespace}")
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("lease release for %s failed: %s", codespace, exc)


def _cmd_create(args: argparse.Namespace) -> int:
    """Create a CodeSpace and run post-create provisioning hooks."""
    from ssh_manager import ConnectionManager

    config = load_merged_config()
    print(f"Creating CodeSpace for {args.repo}...")
    info = create_codespace(
        args.repo, config, branch=args.branch,
        display_name=getattr(args, "display_name", None),
    )
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

    # Provision over SSH: relay helpers + dotfiles bootstrap + repo hooks
    # (including on_create extras).
    relay_port = config.credentials.relay_port
    port_forwards = [f"-R {relay_port}:127.0.0.1:{relay_port}"]
    source = CodespaceSource(info.name)
    manager = ConnectionManager()

    async def _run() -> int:
        await manager.ensure_connected(info.name, source, port_forwards)
        await _provision_relay_helpers(manager, info.name)
        await _provision_dotfiles(manager, info.name, config)
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


def _cmd_borrow(args: argparse.Namespace) -> int:
    """Advisory-lease a CodeSpace to an effort (check it out)."""
    from .lease import borrow

    lease = borrow(args.effort, args.codespace, force=args.force)
    print(lease.codespace)
    return 0


def _cmd_release(args: argparse.Namespace) -> int:
    """Release a CodeSpace lease by CodeSpace name or effort name."""
    from .lease import release

    if release(args.target):
        print(f"Released: {args.target}")
        return 0
    print(f"No lease found for '{args.target}'", file=sys.stderr)
    return 1


def _cmd_leases() -> int:
    """Show active CodeSpace leases."""
    from .lease import list_leases

    leases = list_leases()
    if not leases:
        print("No active leases.")
        return 0
    print(f"{'CODESPACE':<40} {'EFFORT':<24} {'HOST':<16} {'PID'}")
    for lease in leases:
        print(
            f"{lease.codespace:<40} {lease.effort:<24} "
            f"{lease.host:<16} {lease.pid}"
        )
    return 0


def _cmd_wait(args: argparse.Namespace) -> int:
    """Patiently wait for a CodeSpace to become Available.

    Exit codes: 0 Available, 2 genuinely-failed state, 124 timeout -- so a
    background caller can distinguish "still slow" from "dead" and never create
    a redundant CodeSpace just because a boot was slow.
    """
    from .lifecycle import WaitOutcome, wait_for_codespace

    print(f"Waiting for CodeSpace '{args.name}' (up to {args.timeout:.0f}s)...")

    def _progress(state: str, remaining: float) -> None:
        print(f"  ... state={state or '?'} ({remaining:.0f}s left)")

    outcome, last_state = wait_for_codespace(
        args.name, timeout=args.timeout, interval=args.interval,
        on_progress=_progress,
    )
    if outcome == WaitOutcome.AVAILABLE:
        print(f"[OK] {args.name} is Available")
        return 0
    if outcome == WaitOutcome.FAILED:
        print(
            f"[FAIL] {args.name} reached a terminal state '{last_state}' -- it "
            f"will not become Available on its own. Diagnose before recreating.",
            file=sys.stderr,
        )
        return 2
    print(
        f"[TIMEOUT] {args.name} still not Available (last state "
        f"'{last_state or '?'}') after {args.timeout:.0f}s. It may still be "
        f"provisioning -- wait longer rather than declaring it dead.",
        file=sys.stderr,
    )
    return 124


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

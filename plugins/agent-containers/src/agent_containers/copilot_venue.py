"""``agent-containers copilot <name>`` -- the venue counterpart to
``agent-worktrees copilot`` (PR #3126), agent-bridge-cli-mode-sessions Phase 4.

Split out of ``__main__.py`` (module-size guard, ``tools/check-module-size.py``)
rather than left inline.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable


def add_copilot_subparser(sub) -> None:
    """Register the ``copilot`` subcommand on the shared subparsers group."""
    copilot_p = sub.add_parser(
        "copilot",
        help="Deliver a TTY Copilot session in THIS terminal, remotely, via "
             "this trusted container -- the venue counterpart to "
             "`agent-worktrees copilot` (PR #3126): reserves the worktree's "
             "CLI-mode slot and SSHes `-t` in to run `agent-worktrees copilot` "
             "there. Trusted containers only (no SSH key projection on a "
             "restricted fleet).",
    )
    copilot_p.add_argument("name", help="Container name")
    copilot_p.add_argument(
        "--worktree-id", dest="worktree_id", default=None,
        help="The worktree id to reserve/attach on the venue side (forwarded "
             "verbatim to the remote `agent-worktrees copilot --worktree-id`). "
             "Omit for the default: a trusted container is conventionally "
             "anchor-only (its fleet image already clones the repo directly; "
             "no worktree exists unless an operator explicitly created one), "
             "so this delivers a Copilot session directly in the anchor "
             "checkout instead -- matching headless ACP dispatch's own "
             "existing behavior of running straight in the container's "
             "workspace_folder.",
    )
    copilot_p.add_argument(
        "--driver", default="cli-mode",
        help="Forwarded to the remote `agent-worktrees copilot --driver` "
             "(stamps the 'driven by' banner; default 'cli-mode')",
    )
    copilot_p.add_argument(
        "--seed", help="Forwarded to the remote `agent-worktrees copilot --seed`",
    )
    copilot_p.add_argument(
        "--ttl-seconds", type=float, default=300.0,
        help="CLI-mode reservation lifetime before it's reclaimable (default 300)",
    )
    copilot_p.add_argument(
        "--no-ensure-mux", dest="ensure_mux", action="store_false", default=True,
        help="Do not forward --ensure-mux to the remote `agent-worktrees "
             "copilot` (by default it self-heals a missing tmux on the venue).",
    )
    copilot_p.add_argument(
        "--no-relay", action="store_true",
        help="Skip the credential-relay reverse forward",
    )
    copilot_p.add_argument(
        "--force", action="store_true",
        help="Terminate a live SSH holder and take over this trusted container",
    )
    lifecycle = copilot_p.add_mutually_exclusive_group()
    lifecycle.add_argument(
        "--detach", action="store_true",
        help="Start (or rejoin) the trusted container's CLI-mode Copilot "
             "session in the background, wait until it registers with the "
             "host agent-bridge, and print a JSON handle.",
    )
    lifecycle.add_argument(
        "--stop", action="store_true",
        help="Stop the trusted container's detached session, release its "
             "forward keeper, and deregister its live-session row.",
    )
    copilot_p.add_argument(
        "--seed-file", dest="seed_file", default=None, metavar="PATH",
        help="With --detach or attached launch: read the seed from PATH "
             "('-' = stdin) instead of --seed.",
    )
    copilot_p.add_argument(
        "--copilot-arg", dest="copilot_args", action="append", default=[],
        metavar="ARG",
        help="With --detach: extra Copilot argument for the launched session "
             "(repeatable).",
    )
    copilot_p.add_argument(
        "--ref-file", dest="ref_files", action="append", default=[], metavar="PATH",
        help="With --detach: copy PATH (file or directory) into the container "
             "outside the checkout and tell the worker where it is (repeatable).",
    )
    copilot_p.add_argument(
        "--register-timeout", dest="register_timeout", type=float, default=180.0,
        metavar="SECS",
        help="With --detach: how long to wait for the session to register with "
             "the host bridge after it starts (default 180).",
    )
    copilot_p.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="With --detach: print the resolved plan without touching anything.",
    )


def cmd_copilot(
    args: argparse.Namespace,
    *,
    require_live_relay_port: Callable[[], int],
    relay_healthy: Callable[[int], bool],
    busy_exit: int,
    creation_flags: Callable[[], int],
) -> int:
    """Deliver a TTY Copilot session to this terminal, remotely, via a trusted
    container.

    The venue counterpart to ``agent-worktrees copilot`` (PR #3126) --
    ``agent-bridge-cli-mode-sessions`` Phase 4: reserves the worktree's
    CLI-mode Session Host slot on the host ``agent-bridge`` daemon, then SSHes
    ``-t`` into the container (carrying the credential-relay + daemon-port
    reverse forwards) to run ``agent-worktrees copilot`` there -- identical
    contract to the local verb, just dispatched remotely. Trusted containers
    only: a restricted fleet has no SSH key projection (deny-by-construction),
    so this verb refuses it outright rather than degrading silently.

    Defaults to **anchor mode** (no ``--worktree-id``): a trusted container is
    conventionally anchor-only (its fleet image already clones the repo
    directly; no worktree exists unless an operator explicitly created one),
    so forcing ``--worktree-id`` on every caller would be needless ceremony
    for the common case. Mirrors headless ACP dispatch's own existing
    behavior of running straight in the container's ``workspace_folder``.

    Every ``__main__``-private helper is injected rather than imported, to
    avoid a circular import between this module and ``__main__``.
    """
    if getattr(args, "ref_files", None) and not getattr(args, "detach", False):
        print("[FAIL] --ref-file requires --detach", file=sys.stderr)
        return 1
    if getattr(args, "stop", False) or getattr(args, "detach", False):
        from . import copilot_detach

        if getattr(args, "stop", False):
            return copilot_detach.cmd_stop(args)
        return copilot_detach.cmd_detach(
            args,
            require_live_relay_port=require_live_relay_port,
            relay_healthy=relay_healthy,
            busy_exit=busy_exit,
        )

    from venue_copilot import VenueCopilotError, resolve_daemon_port, run_venue_copilot

    from .config import RESTRICTED_PROFILE
    from .resolver import resolve_live_exec_target
    from .config import load_config
    from .ssh_transport import build_ssh_command, prepare_ssh_config

    target = resolve_live_exec_target(args.name, config=load_config())
    if target.actual_profile == RESTRICTED_PROFILE:
        print(
            f"[FAIL] '{args.name}' is a {RESTRICTED_PROFILE} container -- the "
            "`copilot` verb needs SSH key projection, which a restricted "
            "fleet deliberately refuses. Use a trusted fleet.",
            file=sys.stderr,
        )
        return 1

    anchor_mode = not args.worktree_id
    identity = args.worktree_id or (
        f"anchor-{os.path.basename(target.workspace_folder.rstrip('/')) or args.name}"
    )

    from ssh_manager import TargetBusyError, TargetLock

    target_lock = TargetLock(f"container:{args.name}", op="copilot")
    try:
        target_lock.acquire(force=getattr(args, "force", False))
    except TargetBusyError as busy:
        print(busy.user_message(), file=sys.stderr)
        return busy_exit

    try:
        config = target.config
        user = target.user
        reverse_forwards: list[str] = []
        relay_env: dict[str, str] = {}
        if not args.no_relay:
            host_relay_port = require_live_relay_port()
            if not relay_healthy(host_relay_port):
                raise RuntimeError(
                    "Published credential relay at "
                    f"127.0.0.1:{host_relay_port} failed its ping probe; "
                    "restart agent-bridge or set relay.enabled: false in "
                    "containers.yaml"
                )
            reverse_forwards.append(
                f"127.0.0.1:{config.relay_port}:127.0.0.1:{host_relay_port}"
            )
            from .relay_provider import token_for

            relay_env = {
                "LC_GIT_CREDENTIAL_RELAY_HOST": "127.0.0.1",
                "LC_GIT_CREDENTIAL_RELAY": str(config.relay_port),
                "LC_GIT_CREDENTIAL_RELAY_TOKEN": token_for(args.name),
            }
        daemon_port = resolve_daemon_port()
        if daemon_port:
            reverse_forwards.append(f"{daemon_port}:127.0.0.1:{daemon_port}")
        else:
            print(
                "[WARN] Could not resolve the host agent-bridge daemon's live "
                "port; the remote session will not be able to register back "
                "to it (it will still attach locally inside the container).",
                file=sys.stderr,
            )

        ssh_config = prepare_ssh_config(args.name, user)

        def connect(remote_command: str) -> int:
            spawn_cmd = build_ssh_command(
                ssh_config, remote_command,
                reverse_forwards=reverse_forwards, pty=True,
            )
            env = {**os.environ, **relay_env}
            return subprocess.run(
                spawn_cmd, env=env, creationflags=creation_flags()
            ).returncode

        try:
            return run_venue_copilot(
                identity,
                connect=connect,
                anchor=anchor_mode,
                ttl_seconds=args.ttl_seconds,
                driver=args.driver,
                seed=args.seed,
                ensure_mux=args.ensure_mux,
            )
        except VenueCopilotError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 1
    finally:
        target_lock.release()

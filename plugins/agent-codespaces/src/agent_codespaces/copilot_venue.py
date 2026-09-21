"""``agent-codespaces copilot <name>`` -- the venue counterpart to
``agent-worktrees copilot`` (PR #3126), agent-bridge-cli-mode-sessions Phase 4.

Split out of ``__main__.py`` (module-size guard, ``tools/check-module-size.py``)
rather than left inline -- this is a self-contained feature with its own
argparse wiring and command body, exactly like ``connection_owner``/
``relay_launch`` already live in their own modules.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from collections.abc import Callable


def add_copilot_subparser(sub) -> None:
    """Register the ``copilot`` subcommand on the shared subparsers group."""
    copilot_parser = sub.add_parser(
        "copilot",
        help="Deliver a TTY Copilot session in THIS terminal, remotely, via "
             "this CodeSpace -- the venue counterpart to `agent-worktrees "
             "copilot` (PR #3126): reserves the worktree's CLI-mode slot, "
             "holds+heartbeats a Connection Owner tenant for as long as this "
             "process stays attached, and SSHes `-t` in to run "
             "`agent-worktrees copilot` there.",
    )
    copilot_parser.add_argument("name", help="CodeSpace name")
    copilot_parser.add_argument(
        "--worktree-id", dest="worktree_id", required=True,
        help="The worktree id to reserve/attach on the venue side (forwarded "
             "verbatim to the remote `agent-worktrees copilot --worktree-id`).",
    )
    copilot_parser.add_argument(
        "--driver", default="cli-mode",
        help="Forwarded to the remote `agent-worktrees copilot --driver` "
             "(stamps the 'driven by' banner; default 'cli-mode')",
    )
    copilot_parser.add_argument(
        "--seed", help="Forwarded to the remote `agent-worktrees copilot --seed`",
    )
    copilot_parser.add_argument(
        "--ttl-seconds", type=float, default=300.0,
        help="CLI-mode reservation lifetime before it's reclaimable (default 300)",
    )
    copilot_parser.add_argument(
        "--no-ensure-mux", dest="ensure_mux", action="store_false", default=True,
        help="Do not forward --ensure-mux to the remote `agent-worktrees "
             "copilot` (by default it self-heals a missing tmux on the venue).",
    )
    copilot_parser.add_argument(
        "--no-relay", action="store_true",
        help="Skip the credential-relay reverse forward",
    )


def cmd_copilot(
    args: argparse.Namespace,
    *,
    interactive_ssh: Callable[..., int],
) -> int:
    """Deliver a TTY Copilot session to this terminal, remotely, via a CodeSpace.

    The venue counterpart to ``agent-worktrees copilot`` (PR #3126) --
    ``agent-bridge-cli-mode-sessions`` Phase 4: reserves the worktree's
    CLI-mode Session Host slot on the host ``agent-bridge`` daemon, places a
    Connection Owner tenant hold (spinning the Owner up on-demand) so the
    daemon-port + credential-relay reverse forwards ride one durable
    connection, re-heartbeats that hold for as long as this process stays
    attached (self-hosted tenancy -- no agent-bridge daemon involvement), and
    SSHes ``-t`` into the CodeSpace to run ``agent-worktrees copilot`` there --
    identical contract to the local verb, just dispatched remotely.

    ``interactive_ssh`` is injected (``__main__._interactive_ssh``) rather than
    imported, to avoid a circular import between this module and ``__main__``.
    """
    from venue_copilot import VenueCopilotError, resolve_daemon_port, run_venue_copilot

    from . import connection_owner as _owner
    from .config import load_merged_config
    from .relay_launch import effective_relay_port
    from .relay_token import token_for

    config = load_merged_config()
    tenant = f"copilot:{os.getpid()}"

    if not _owner.ensure_owner_running(config):
        print(
            "[WARN] Connection Owner could not be started; the daemon-port "
            "forward is still attempted for this connection only (it will "
            "not survive past this process's own exit).",
            file=sys.stderr,
        )
    _owner.hold(args.name, tenant)

    stop_heartbeat = threading.Event()

    def _heartbeat_loop() -> None:
        # Well inside DEFAULT_TTL (1h) so a scheduling hiccup never reclaims a
        # live tenant; this is the "who renews the hold" answer from the
        # effort's Phase 4 design: the running `copilot` process itself.
        while not stop_heartbeat.wait(60.0):
            _owner.heartbeat(args.name, tenant)

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    reverse_forwards: list[str] = []
    relay_port: int | None = None
    relay_token: str | None = None
    if not args.no_relay:
        relay_port = effective_relay_port(config)
        reverse_forwards.append(f"{relay_port}:127.0.0.1:{relay_port}")
        relay_token = token_for(args.name)
    daemon_port = resolve_daemon_port()
    if daemon_port:
        reverse_forwards.append(f"{daemon_port}:127.0.0.1:{daemon_port}")
    else:
        print(
            "[WARN] Could not resolve the host agent-bridge daemon's live "
            "port; the remote session will not be able to register back to "
            "it (it will still attach locally inside the CodeSpace).",
            file=sys.stderr,
        )

    def connect(remote_command: str) -> int:
        return interactive_ssh(
            args.name, reverse_forwards,
            relay_port=relay_port, relay_token=relay_token,
            remote_command=remote_command,
        )

    try:
        return run_venue_copilot(
            args.worktree_id,
            connect=connect,
            ttl_seconds=args.ttl_seconds,
            driver=args.driver,
            seed=args.seed,
            ensure_mux=args.ensure_mux,
        )
    except VenueCopilotError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        stop_heartbeat.set()
        _owner.release(args.name, tenant)

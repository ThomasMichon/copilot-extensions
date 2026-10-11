"""``agent-containers copilot-login`` -- manual device-code-in-container
Copilot CLI login bootstrap.

Split out of ``__main__.py`` to keep that module within its module-size
budget (the same reason ``copilot_venue.py``/``forward_keeper.py`` own their
own subcommands rather than living inline).
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from agent_procutil import no_window_flags


def add_copilot_login_subparser(sub) -> None:
    p = sub.add_parser(
        "copilot-login",
        help=(
            "Manually bootstrap a host Copilot CLI login by running the "
            "device-code flow INSIDE a running container, then import the "
            "resulting token back into this host's own Copilot CLI config "
            "so normal copilot_cli_account launches find it"
        ),
    )
    p.add_argument("name", help="A running container of the target fleet")
    p.add_argument(
        "--account",
        default=None,
        help=(
            "GitHub account to log in as (defaults to the fleet's own "
            "copilot_cli_account, if configured)"
        ),
    )
    p.set_defaults(func=cmd_copilot_login)


def cmd_copilot_login(args: argparse.Namespace) -> int:
    """Manually bootstrap a Copilot CLI login for ``--account`` (or the
    target fleet's configured ``copilot_cli_account``) when this host has no
    live login for it yet.

    Runs the OAuth device-code flow INTERACTIVELY inside a running container
    of the named fleet (the operator completes it in their own browser, same
    as any device-code flow), then reads the resulting token back out of the
    container's own ``~/.copilot/config.json`` and imports it into THIS
    host's config so future launches resolve it the normal way via
    :func:`agent_containers.resolver.host_copilot_cli_token` -- no repeat
    in-container login needed.

    Manual/on-demand only: never invoked automatically by any launch path.
    Use this as the documented worst-case fallback when
    ``copilot_cli_account`` is configured but no matching host login exists
    yet (the RuntimeError raised by the launch paths names this exact
    command).
    """
    from .config import RESTRICTED_PROFILE, load_config
    from .resolver import (
        copilot_cli_token_from_json_text,
        import_copilot_cli_token,
        resolve_live_exec_target,
    )

    target = resolve_live_exec_target(args.name, config=load_config())
    if target.actual_profile == RESTRICTED_PROFILE:
        raise RuntimeError(
            f"'{args.name}' is a restricted container -- it never receives "
            "host credentials of any kind, so a Copilot CLI login bootstrapped "
            "there would be unreachable from the host. Use a trusted fleet."
        )
    account = args.account or target.config.copilot_cli_account_for(target.fleet)
    if not account:
        raise RuntimeError(
            "no --account given, and the target fleet has no "
            "copilot_cli_account configured -- pass --account explicitly"
        )

    container_id = target.container_id
    user = target.user

    print(
        f"agent-containers: starting the Copilot CLI device-code login for "
        f"'{account}' inside container '{args.name}' -- follow the printed "
        "URL/code to authorize in your browser, then return here.",
        file=sys.stderr,
    )
    login_proc = subprocess.run(
        [
            "docker", "exec", "-it", "-u", user, container_id,
            "copilot", "login", "--device-code", "--host", "https://github.com",
        ],
        creationflags=no_window_flags(),
    )
    if login_proc.returncode != 0:
        raise RuntimeError(
            f"`copilot login` inside '{args.name}' exited "
            f"{login_proc.returncode} -- login did not complete"
        )

    read_proc = subprocess.run(
        [
            "docker", "exec", "-u", user, container_id,
            "bash", "-lc", "cat ~/.copilot/config.json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=no_window_flags(),
    )
    if read_proc.returncode != 0 or not read_proc.stdout:
        raise RuntimeError(
            f"login inside '{args.name}' reported success, but its own "
            "~/.copilot/config.json could not be read back afterward"
        )
    token = copilot_cli_token_from_json_text(account, read_proc.stdout)
    if not token:
        raise RuntimeError(
            f"login inside '{args.name}' reported success, but no token for "
            f"'{account}' was found in its config.json afterward -- was the "
            "device-code flow completed as the expected account?"
        )

    import_copilot_cli_token(account, token)
    print(
        f"agent-containers: imported the '{account}' Copilot CLI token into "
        "this host's own ~/.copilot/config.json -- future launches of this "
        "(and any other) fleet configured with copilot_cli_account: "
        f"{account} will now find it automatically.",
        file=sys.stderr,
    )
    return 0

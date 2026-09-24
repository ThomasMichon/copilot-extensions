"""``agent-codespaces owner`` -- the Connection Owner reconcile daemon entry.

Split out of ``__main__.py`` (module-size guard, ``tools/check-module-size.py``)
exactly like ``copilot_venue``: a self-contained command body.
"""
from __future__ import annotations

import argparse
import sys


def _account_source(codespace: str, gh_env: dict | None = None):
    """SSH config source pinned to the gh account that owns ``codespace``.

    The Owner's forwards must reach CodeSpaces of any mapped account, not only
    the ambient one (``gh_env`` is accepted for the factory seam and ignored).
    """
    from .codespace_config import CodespaceSource
    from .lifecycle import account_for_codespace

    try:
        account = account_for_codespace(codespace)
    except Exception:  # an unresolvable account degrades to ambient auth
        account = None
    return CodespaceSource(codespace, account=account)


def cmd_owner(args: argparse.Namespace) -> int:
    """Run the Connection Owner relay reconcile daemon (config-gated; default on).

    The Owner is the single, persistent per-machine owner of each CodeSpace's
    credential relay, independent of any one agent-bridge dispatch -- so a caller
    disconnect / bridge restart no longer drops the relay mid-task
    (dotfiles#1320/#1333). Config-gated: it refuses to run unless
    ``connection_owner.enabled`` is set (or ``--force`` for validation) --
    default is now on, so this refuses only when a repo/operator has explicitly
    opted out. It is deliberately on-demand rather than a required
    always-resident service: ``ssh``/dispatch spin it up themselves
    (``ensure_owner_running``) when it is not already live, and this loop exits
    cleanly on its own once idle (see ``--idle-shutdown-after`` below) -- a
    login-triggered service registration is a convenience, not a requirement.

    ``--once`` reconciles a single cycle and exits (validation): with no holds it
    is a safe no-op that exercises the wiring without touching a real CodeSpace.

    ``--idle-shutdown-after`` overrides ``connection_owner.idle_shutdown_after``
    (default 300s): once the hold registry has been completely empty (no
    pinned CodeSpace, no live tenant) for that long, the loop exits by itself
    rather than being forced to remain resident indefinitely. A non-positive
    value disables idle shutdown.

    ``--status`` prints the resolved ``connection_owner`` config as JSON
    (``enabled`` / ``reconcile_interval`` / ``idle_shutdown_after``) and exits
    without starting anything -- the install/update scripts call it to decide
    whether to provision the per-machine Owner service (config-gated cutover;
    disabled -> inert).
    """
    import asyncio

    from .config import load_merged_config
    from .connection_owner import (
        ConnectionOwner,
        list_holds,
        make_supervised_relay_factory,
        run_owner_daemon,
    )
    from .session_forwards import (
        SessionForwards,
        make_remote_mux_probe,
        make_supervised_daemon_forward_factory,
    )

    cfg = load_merged_config(include_cwd=False)
    co = getattr(cfg, "connection_owner", None)
    enabled = bool(co and co.enabled)
    default_idle = co.idle_shutdown_after if co else 300.0

    if getattr(args, "status", False):
        import json

        interval = float(co.reconcile_interval) if co else 15.0
        print(json.dumps({
            "enabled": enabled,
            "reconcile_interval": interval,
            "idle_shutdown_after": default_idle,
        }))
        return 0

    if not enabled and not args.force:
        print(
            "connection-owner is disabled (set connection_owner.enabled: true, or "
            "pass --force to validate); not starting.",
            file=sys.stderr,
        )
        return 0

    interval = (
        args.interval
        if args.interval is not None
        else (float(co.reconcile_interval) if co else 15.0)
    )
    if interval <= 0:
        raise RuntimeError(
            f"connection-owner reconcile interval must be > 0 (got {interval}); "
            "check --interval / connection_owner.reconcile_interval."
        )
    idle_shutdown_after = (
        args.idle_shutdown_after
        if getattr(args, "idle_shutdown_after", None) is not None
        else default_idle
    )
    if idle_shutdown_after is not None and idle_shutdown_after <= 0:
        idle_shutdown_after = None
    factory = make_supervised_relay_factory(cfg, config_source_cls=_account_source)
    owner = ConnectionOwner(
        factory,
        sessions=SessionForwards(
            make_supervised_daemon_forward_factory(config_source_cls=_account_source),
            make_remote_mux_probe(),
        ),
    )

    if args.once:
        asyncio.run(owner.reconcile())
        held = sorted(h.codespace for h in list_holds())
        active = sorted(owner.active_codespaces())
        print(f"connection-owner: reconciled once; held={held}; active={active}")
        return 0

    print(
        f"connection-owner: starting reconcile daemon (interval={interval}s; "
        f"idle_shutdown_after={idle_shutdown_after}; Ctrl-C to stop)...",
        file=sys.stderr,
    )
    try:
        asyncio.run(
            run_owner_daemon(
                owner, interval=interval, idle_shutdown_after=idle_shutdown_after,
            )
        )
    except KeyboardInterrupt:
        pass
    return 0

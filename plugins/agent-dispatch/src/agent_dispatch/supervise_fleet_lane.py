"""Build the fleet (``--pool``) body for the transitional ``supervise`` loop.

Moved out of ``supervise_cli.py`` to keep that module under its size ceiling.
Validates the fleet-only flag combinations, resolves the origin alias, loads an
optional trusted placement provider, and constructs the :class:`FleetSpawner`.
"""

from __future__ import annotations

import argparse
import functools
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .fleet import FleetSpawner


@dataclass(frozen=True)
class FleetLane:
    """The constructed fleet spawner plus its headless-agent preflight inputs."""

    spawner: FleetSpawner
    preflight_agent: str | None
    preflight_pool: list[str]


def _refuse(message: str) -> int:
    print(f"agent-dispatch supervise: {message}", file=sys.stderr)
    return 2


def build_fleet_lane(
    args: argparse.Namespace,
    pool: Sequence[str],
    *,
    route: Callable[[argparse.Namespace], str],
    backend: str,
    all_repos: bool,
    headless_labels: Sequence[str],
    cli_labels: Sequence[str],
    script_labels: Sequence[str],
    disposable_cli_labels: Sequence[str],
) -> FleetLane | int:
    """Return the fleet lane, or a CLI exit status after a printed refusal."""
    from . import remote_dispatch
    from .fleet import FleetSpawner
    from .fleet_placement import PlacementConfigError, placement_from_flag

    origin = getattr(args, "origin", None) or remote_dispatch.local_machine()
    if not origin:
        print(
            "agent-dispatch supervise --pool: could not resolve this machine's "
            "alias for fleet bodies to report back to; pass --origin <alias>.",
            file=sys.stderr,
        )
        return 2
    # Fleet bodies are headless by default too (the `--headless` flag remains an
    # explicit force); only `--embody-backend cli` makes fleet bodies CLI.
    fleet_headless = bool(getattr(args, "headless", False)) or backend != "cli"
    charter = getattr(args, "charter", None)
    if charter and not fleet_headless:
        return _refuse(
            "--charter is only supported for a headless fleet lane today -- drop "
            "--embody-backend cli, or drop --charter."
        )
    if disposable_cli_labels:
        return _refuse("--disposable-cli-label is supported only for local worker bodies.")
    if backend == "script" or script_labels:
        return _refuse(
            "the script embodiment is supported only for local (non-pool) worker bodies."
        )
    if getattr(args, "no_pair", False):
        return _refuse("--no-pair is supported only for local worker bodies.")
    try:
        placement = placement_from_flag(getattr(args, "placement_provider", None), pool)
    except PlacementConfigError as exc:
        return _refuse(str(exc))
    agent = getattr(args, "headless_agent", None) or "task-worker"
    make_fleet = functools.partial(
        FleetSpawner,
        list(pool),
        origin=origin,
        route=route(args),
        headless=fleet_headless,
        agent=agent,
        charter=charter,
        all_repos=all_repos,
        verify_timeout=getattr(args, "verify_timeout", 0) or 0,
    )
    # Only a placement lane passes the kwarg; other lanes construct as before.
    fleet = make_fleet(placement=placement) if placement is not None else make_fleet()
    if headless_labels or cli_labels or script_labels:
        print(
            "agent-dispatch supervise: per-label --headless-label/--cli-label/"
            "--script-label are ignored in fleet (--pool) mode; the whole pool is "
            f"{'headless' if fleet_headless else 'CLI'} "
            "(set --embody-backend to change).",
            file=sys.stderr,
        )
    body = "headless agent-bridge ACP" if fleet_headless else "CLI-embodied"
    placement_note = f" placement={placement.declaration.name}" if placement else ""
    print(
        f"agent-dispatch supervise: fleet mode -- pool={','.join(fleet.pool)} "
        f"origin={origin} body={body}{placement_note}",
        file=sys.stderr,
    )
    # Preflight only a headless fleet lane: a CLI-embodied fleet body is a
    # worktree autopilot, not an agent-bridge agent.
    return FleetLane(fleet, agent if fleet_headless else None, list(fleet.pool))

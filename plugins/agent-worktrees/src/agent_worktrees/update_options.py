"""Extra ``update`` CLI options, kept out of ``update_cli`` for its size cap."""

from __future__ import annotations

import argparse
from collections.abc import Collection, Mapping

from . import output, update_events


def add_update_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--force-runtime",
        action="append",
        default=None,
        metavar="NAME",
        help="Re-deploy the named runtime even when the deployed version already "
        "matches the payload (repeatable; --force still forces every runtime)",
    )


def forced_runtimes(args: argparse.Namespace) -> frozenset[str]:
    return frozenset(getattr(args, "force_runtime", None) or ())


def forwarded_flags(args: argparse.Namespace) -> list[str]:
    """The options above, rebuilt for forwarding through the Manager seam."""
    flags: list[str] = []
    for name in getattr(args, "force_runtime", None) or ():
        flags += ["--force-runtime", name]
    return flags


def eligible_runtimes(args: argparse.Namespace, targets: Mapping[str, object],
                      module_names: Collection[str]) -> set[str]:
    """Runtimes this update would deploy: itself, modules and active registrations."""
    skip = getattr(args, "skip_modules", None)
    if skip is not None and len(skip) == 0:
        return {"agent-worktrees"}
    names = set(module_names)
    names |= {name for name, target in targets.items() if getattr(target, "required", True)}
    names -= set(skip or ())
    return names | {"agent-worktrees"}


def check_forced_runtimes(args: argparse.Namespace, targets: Mapping[str, object],
                          module_names: Collection[str]) -> bool:
    """Refuse ``--force-runtime`` names this update would never deploy.

    Records a configuration failure (exit 3) naming them, so a typo or an
    inactive runtime never turns into a silent success.
    """
    unknown = sorted(forced_runtimes(args) - eligible_runtimes(args, targets, module_names))
    if not unknown:
        return True
    names = ", ".join(unknown)
    output.err(f"--force-runtime: no eligible runtime named {names}")
    update_events.record("engine_runtime", "force-runtime",
                         f"--force-runtime target not found: {names}")
    return False

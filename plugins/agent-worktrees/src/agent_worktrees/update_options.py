"""Extra ``update`` CLI options, kept out of ``update_cli`` for its size cap."""

from __future__ import annotations

import argparse
import json
from collections.abc import Collection, Mapping
from pathlib import Path

from . import output, update_events


def add_update_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--auth-report",
        action="store_true",
        help="Report source authentication without updating or changing credentials",
    )
    p.add_argument("--auth-source", action="append", metavar="HTTPS_URL", help=argparse.SUPPRESS)
    p.add_argument("--auth-cwd", metavar="PATH", help=argparse.SUPPRESS)
    p.add_argument("--auth-non-repository", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--auth-environment", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true", help="JSON output for --auth-report only")
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
    if getattr(args, "auth_report", False):
        flags.append("--auth-report")
    for name in getattr(args, "force_runtime", None) or ():
        flags += ["--force-runtime", name]
    return flags


def validate_auth_flags(args: argparse.Namespace) -> None:
    report_only = [
        "--" + name.replace("_", "-")
        for name in ("auth_source", "auth_cwd", "auth_non_repository", "auth_environment", "json")
        if getattr(args, name, False)
    ]
    if not getattr(args, "auth_report", False) and report_only:
        argparse.ArgumentParser(prog="update").error(
            "unrecognized arguments: " + " ".join(report_only) + " (requires --auth-report)"
        )


def _module_dirs(plugin_dir: Path) -> dict[str, Path]:
    """``modules.json`` modules and their payload directories.

    A missing or malformed manifest has no modules; an unreadable one raises
    its ``OSError`` so the caller reports the real cause.
    """
    try:
        text = (plugin_dir / "modules.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text)
        modules = data.get("modules", []) or []
    except Exception:
        return {}
    return {
        str(mod["name"]): plugin_dir.parent / str(mod.get("source", mod["name"]))
        for mod in modules
        if isinstance(mod, dict) and mod.get("name")
    }


def _deployable(name: str, payload_dir: Path | None, context: Path | None) -> bool:
    """Whether the reconcilers would run *name*'s installer (scope + machine gate)."""
    from . import reconcile
    from .update_gate import MachineDetectionError, _machine_gate_blocks

    scope = reconcile.manifest_runtime_scope(payload_dir) if payload_dir is not None else None
    if scope in (None, "none"):
        return False
    try:
        return not _machine_gate_blocks(name, scope, context)
    except MachineDetectionError:
        return True  # the reconciler itself reports the detection failure


def eligible_runtimes(
    args: argparse.Namespace, targets: Mapping[str, object], plugin_dir: Path
) -> set[str]:
    """Runtimes this update would deploy, from the reconcilers' own plan.

    agent-worktrees itself, plus every active target (``modules.json`` module or
    registered plugin) that is not skipped, has a runtime scope other than
    ``none`` and is allowed on this machine.
    """
    eligible = {"agent-worktrees"}
    skip = getattr(args, "skip_modules", None)
    if skip is not None and len(skip) == 0:
        return eligible
    from . import reconcile

    modules = _module_dirs(plugin_dir)
    for name, target in targets.items():
        if name in eligible or name in (skip or ()) or not getattr(target, "required", False):
            continue
        payload = modules[name] if name in modules else reconcile.core_installed_payload_dir(name)
        if _deployable(name, payload, getattr(target, "context", None)):
            eligible.add(name)
    return eligible


def check_forced_runtimes(args: argparse.Namespace, targets: Mapping[str, object],
                          plugin_dir: Path, *, eligible: Collection[str] | None = None,
                          phase: str = "engine_runtime") -> bool:
    """Refuse ``--force-runtime`` names this update would never deploy.

    Records a configuration failure (exit 3) naming them, so a typo or an
    inactive, payload-only or machine-gated runtime never turns into a silent
    success.
    """
    forced = forced_runtimes(args)
    if not forced:
        return True
    try:
        if eligible is None:
            eligible = eligible_runtimes(args, targets, plugin_dir)
    except OSError as error:
        output.err(f"--force-runtime: cannot read modules.json: {error}")
        update_events.record(phase, "force-runtime",
                             f"modules.json unreadable: {error}")
        return False
    unknown = sorted(forced - eligible)
    if not unknown:
        return True
    names = ", ".join(unknown)
    output.err(f"--force-runtime: no eligible runtime named {names}")
    update_events.record(phase, "force-runtime",
                         f"--force-runtime target not found: {names}")
    return False

"""Prepare one service-source portfolio for the exact suite engine being invoked."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass

from . import engine_client as ec
from .runtime_source import acquire
from .runtime_source_config import configured_source
from .runtime_source_prepare import prepare as _prepare, preparer as _preparer
from .self_install import default_root

SCHEMA = "copilot-extensions.prepared-runtime-sources"
CONTRACT = 1


@dataclass(frozen=True)
class PreparedSuite:
    arguments: list[str]
    engine_command: list[str] | None = None


def _targets(inventory: dict, arguments: list[str]) -> list[str]:
    from .installation_updates import _engine_rows

    capability = inventory.get("runtime_sources")
    if (
        not isinstance(capability, dict) or capability.get("schema") != SCHEMA
        or type(capability.get("version")) is not int or capability["version"] != CONTRACT
    ):
        raise ValueError(
            "Selected engine lacks prepared runtime source support. No update was run; "
            "update that engine through its existing marketplace/setup flow first."
        )
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False, exit_on_error=False)
    parser.add_argument("--skip-modules", nargs="*")
    parser.add_argument("--runtime-sources")
    try:
        options, _unknown = parser.parse_known_args(arguments)
    except argparse.ArgumentError as error:
        raise ValueError(f"Invalid source-backed update arguments: {error}") from error
    if options.runtime_sources is not None:
        raise ValueError("Manager owns --runtime-sources; do not supply a second source packet")
    names = []
    for row in _engine_rows(inventory):
        name = row["id"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            raise ValueError("Engine inventory contains an invalid runtime identifier")
        if row["kind"] == "none" or not row["updatable"]:
            continue
        if row["kind"] not in {"universal", "machine-gated"}:
            raise ValueError(f"Engine inventory contains an unsupported runtime scope for {name}")
        if name != "agent-worktrees" and options.skip_modules is not None and (
            not options.skip_modules or name in options.skip_modules
        ):
            continue
        names.append(name)
    if "agent-worktrees" not in names:
        raise ValueError("Selected engine has no eligible engine runtime; repair its inventory first")
    return sorted(names)


def prepare_suite(
    project: str | None, arguments: list[str], *, environment: dict[str, str] | None = None,
) -> PreparedSuite:
    """Default to marketplace; otherwise qualify and pin before either update stage."""
    selection = configured_source()
    if selection.channel == "marketplace":
        return PreparedSuite(arguments)
    command = ec.engine_base_command() if environment is None else ec.installed_engine_command(environment)
    if command is None:
        raise ec.EngineError("the agent-worktrees engine is not installed", install_hint=True)
    command = list(command)
    try:
        inventory = ec.run_json(
            project, ["update", "--inventory", "--json"], engine_command=command,
            **({"environment": environment} if environment is not None else {}),
        )
    except ec.EngineError as error:
        raise ec.EngineError(
            "Cannot establish prepared runtime source support from the selected engine. "
            "No update was run; repair or upgrade that engine through its existing "
            f"marketplace/setup flow first. Provider diagnostic: {error}",
            install_hint=error.install_hint,
        ) from error
    names = _targets(inventory, arguments)
    tool = _preparer()
    output = default_root().absolute() / "runtime-sources/prepared"
    with acquire(selection) as resolved:
        records = {name: _prepare(tool, resolved, name, output) for name in names}
    packet = json.dumps({"schema": SCHEMA, "version": CONTRACT, "sources": records}, sort_keys=True)
    return PreparedSuite([*arguments, "--runtime-sources", packet], command)

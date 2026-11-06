"""Prepare one service-source portfolio for the exact suite engine being invoked."""
from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from collections.abc import Iterator
from pathlib import Path

from . import engine_client as ec
from .runtime_source import ResolvedSource, acquire
from .runtime_source_config import configured_source
from .runtime_source_prepare import prepare_applications, preparer as _preparer
from .self_install import default_root

SCHEMA = "copilot-extensions.prepared-runtime-sources"
CONTRACT = 2


@dataclass(frozen=True)
class PreparedSuite:
    arguments: list[str]
    engine_command: list[str] | None = None


@contextmanager
def repair_selection(arguments: list[str]) -> Iterator[ResolvedSource | None]:
    """Hold one service-source choice through engine repair and suite execution."""
    selection = configured_source()
    if (
        arguments[:1] != ["--repair-engine"] or len(arguments) < 2
        or selection.channel == "marketplace"
    ):
        yield None
        return
    _source_options(arguments[2:])
    with acquire(selection) as resolved:
        yield resolved


def _source_options(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False, exit_on_error=False)
    parser.add_argument("--skip-modules", nargs="*")
    parser.add_argument("--runtime-sources")
    try:
        options, _unknown = parser.parse_known_args(arguments)
    except argparse.ArgumentError as error:
        raise ValueError(f"Invalid source-backed update arguments: {error}") from error
    if options.runtime_sources is not None:
        raise ValueError("Manager owns --runtime-sources; do not supply a second source packet")
    return options


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
    options = _source_options(arguments)
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


def _application_profiles(inventory: dict, names: list[str]) -> dict[str, dict | None]:
    profiles = inventory["runtime_sources"].get("application_profiles")
    if not isinstance(profiles, dict):
        raise ValueError("Selected engine lacks owned application profile inventory; update its tooling")
    result = {}
    for name in names:
        row = profiles.get(name)
        if row is None:
            if name == "agent-containers":
                raise ValueError("Containers application profile selection is unavailable")
            result[name] = None
            continue
        value = row.get("selection") if isinstance(row, dict) else None
        if (
            not isinstance(value, dict)
            or value.get("schema") != "copilot-extensions.runtime-application-profile"
            or type(value.get("version")) is not int or value["version"] != 1
            or value.get("plugin") != name
            or set(value) != {"schema", "version", "plugin", "runtime_root", "profile", "extras"}
            or not isinstance(value["runtime_root"], str) or not Path(value["runtime_root"]).is_absolute()
            or not isinstance(value["profile"], str)
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value["profile"]) is None
            or not isinstance(value.get("extras"), list)
            or len(value["extras"]) > 64
            or any(not isinstance(extra, str)
                   or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", extra) is None
                   for extra in value["extras"])
            or len(set(value["extras"])) != len(value["extras"])
        ):
            raise ValueError(f"Application profile for {name} is unavailable: {row}")
        result[name] = value
    return result


def prepare_suite(
    project: str | None, arguments: list[str], *, environment: dict[str, str] | None = None,
    runtime_source: ResolvedSource | None = None,
) -> PreparedSuite:
    """Default to marketplace; otherwise qualify and pin before either update stage."""
    selection = configured_source()
    if runtime_source is not None and runtime_source.selection != selection:
        raise ValueError("Runtime source policy changed after engine repair selection")
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
    profiles = _application_profiles(inventory, names)
    tool = _preparer()
    output = default_root().absolute() / "runtime-sources" / "applications" / uuid.uuid4().hex
    deadline = time.monotonic() + 600
    with (nullcontext(runtime_source) if runtime_source is not None else acquire(
        selection, environment=environment, deadline=deadline,
    )) as resolved:
        snapshots = prepare_applications(
            tool, resolved, tuple(names), output, deadline=deadline,
            environment=environment,
            extras={name: tuple(profile["extras"]) for name, profile in profiles.items()
                    if profile is not None},
        )
    records = {name: {"snapshot": str(snapshots[name]), "profile": profiles[name]} for name in names}
    packet = json.dumps({
        "schema": SCHEMA, "version": CONTRACT,
        "source": {"commit": resolved.commit, "tree": resolved.tree, "main_seed": resolved.main_head},
        "sources": records,
    }, sort_keys=True)
    return PreparedSuite([*arguments, "--runtime-sources", packet], command)

"""Prepare one service-source portfolio for the exact suite engine being invoked."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from agent_procutil import no_window_kwargs

from . import engine_client as ec
from .runtime_source import ResolvedSource, acquire
from .runtime_source_config import configured_source
from .self_install import _runtime_installer, default_root, running_payload_dir

SCHEMA = "copilot-extensions.prepared-runtime-sources"
CONTRACT = 1


@dataclass(frozen=True)
class PreparedSuite:
    arguments: list[str]
    engine_command: list[str] | None = None


def _preparer() -> Path:
    payload = running_payload_dir()
    packaged = payload / "scripts/runtime-source-tools/prepare_runtime_source.py"
    if packaged.is_file():
        return packaged
    # Source development uses this checkout's canonical tools, never fetched code.
    if (payload.parent / ".git").exists():
        canonical = payload.parent / "tools/prepare_runtime_source.py"
        if canonical.is_file():
            return canonical
    raise ValueError("Manager source preparation assets are unavailable; repair the Manager payload")


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


def _prepare(
    tool: Path, resolved: ResolvedSource, name: str, output: Path,
) -> dict:
    command = [
        sys.executable, "-I", "-B", "-X", "utf8", str(tool),
        "--source", str(resolved.checkout), "--commit", resolved.commit,
        "--branch", resolved.selection.branch, "--plugin", name, "--output", str(output),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, env=ec._engine_environment(),
            timeout=600, check=False, **no_window_kwargs(),
        )
    except subprocess.SubprocessError as error:
        raise ValueError(f"Runtime source preparation for {name} failed: {error}") from error
    if result.returncode:
        raise ValueError(f"Runtime source preparation for {name} failed: {result.stderr.strip()}")
    record = json.loads(result.stdout)
    if (
        not isinstance(record, dict) or not isinstance(record.get("payload"), str)
        or not isinstance(record.get("identity"), dict)
    ):
        raise ValueError(f"Runtime source preparer returned an invalid receipt for {name}")
    payload = Path(record["payload"])
    identity = record["identity"]
    if (
        not payload.is_absolute() or payload.parent != output / name
        or identity.get("plugin") != name
        or not isinstance(identity.get("package_version"), str)
        or identity.get("source_commit") != resolved.commit
        or identity.get("source_tree") != resolved.tree
    ):
        raise ValueError(f"Runtime source preparer returned a different selection for {name}")
    source = _runtime_installer().runtime_source
    observed = source.read_source_identity(payload, identity.get("package_version"), plugin=name)
    if observed is None or observed.document() != identity or payload.name != observed.runtime_version:
        raise ValueError(f"Runtime source preparer returned conflicting bytes for {name}")
    return {
        "payload": str(payload),
        "identity": {"runtime_source": identity, "runtime_version": observed.runtime_version},
    }


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

"""Inert support declarations for an isolated, recursively read-only probe."""

from __future__ import annotations

import hashlib
import json
import os
import re
import runpy
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTRACT_FILE = "readiness-isolation.json"
CONTRACT_SCHEMA = "copilot-extensions.readiness-isolation"
PROBE_ENV = "COPILOT_EXTENSIONS_READONLY_PROBE"
PROBE_CONTRACT = "isolated-imports-v1"
RUNTIME_CONTRACT = Path("share") / "copilot-extensions"
_PLUGIN = re.compile(r"[a-z][a-z0-9-]*\Z")
_MODULE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z", re.ASCII)
REFUSAL = {
    "schema": "copilot-extensions.probe-compatibility", "version": 1,
    "status": "unknown", "requirement": PROBE_CONTRACT,
    "recovery": "update-owning-installation",
}


class ProbeContractError(Exception):
    """Support is unknown; update the owning installation before probing it."""


@dataclass(frozen=True)
class ProbeContract:
    plugin: str
    module: str
    path: Path
    sha256: str


def required(environment: Mapping[str, str] | None = None) -> bool:
    environment = os.environ if environment is None else environment
    value = environment.get(PROBE_ENV)
    if value is not None and value != PROBE_CONTRACT:
        raise ProbeContractError("Unsupported read-only probe contract; update the owning installation.")
    return value == PROBE_CONTRACT


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate contract field")
        result[key] = value
    return result


def _ordinary_file(path: Path) -> os.stat_result:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("noncanonical probe path")
    for entry in (*reversed(path.parents), path):
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked probe path")
        if not (stat.S_ISREG(info.st_mode) if entry == path else stat.S_ISDIR(info.st_mode)):
            raise ValueError("nonordinary probe path")
    return info


def _read(path: Path, *, plugin: str | None = None, module: str | None = None) -> ProbeContract:
    try:
        info = _ordinary_file(path)
        if info.st_size > 4096:
            raise ValueError("oversized contract")
        with path.open("rb") as stream:
            content = stream.read(4097)
        if len(content) > 4096:
            raise ValueError("oversized contract")
        value = json.loads(content, object_pairs_hook=_unique)
        if (
            not isinstance(value, dict)
            or value.get("schema") != CONTRACT_SCHEMA
            or type(value.get("version")) is not int or value["version"] != 1
            or not isinstance(value.get("plugin"), str) or not _PLUGIN.fullmatch(value["plugin"])
            or not isinstance(value.get("module"), str) or not _MODULE.fullmatch(value["module"])
            or plugin is not None and value["plugin"] != plugin
            or module is not None and value["module"] != module
        ):
            raise ValueError("unsupported contract")
    except (OSError, UnicodeError, ValueError) as error:
        raise ProbeContractError(
            "Read-only import isolation is unqualified; update the owning installation."
        ) from error
    return ProbeContract(value["plugin"], value["module"], path, hashlib.sha256(content).hexdigest())


def payload_contract(payload: Path, plugin: str) -> ProbeContract:
    """Qualify the selected payload without importing or invoking its code."""
    return _read(payload / CONTRACT_FILE, plugin=plugin)


def runtime_contract_path(module: str) -> Path:
    if not isinstance(module, str) or not _MODULE.fullmatch(module):
        raise ProbeContractError("Read-only probe requires a qualified provider module.")
    return RUNTIME_CONTRACT / module / CONTRACT_FILE


def runtime_contract(python: Path, module: str, *, plugin: str | None = None) -> ProbeContract:
    """Read the selected environment's wheel-owned descriptor, never a root stamp."""
    if (
        not python.is_absolute() or ".." in python.parts
        or (python.parent.name, python.name) not in {
            ("bin", "python"), ("Scripts", "python.exe"), ("Scripts", "pythonw.exe"),
        }
    ):
        raise ProbeContractError("Read-only probe requires a qualified installed interpreter.")
    return _read(python.parent.parent / runtime_contract_path(module), plugin=plugin, module=module)


def qualify_payload_code(
    path: Path, payload: Path, plugin: str, module: str,
    *, environment: Mapping[str, str] | None = None,
) -> None:
    """Qualify code borrowed from a selected payload before importing it."""
    if not required(environment):
        return
    contract = payload_contract(payload, plugin)
    if contract.module != module:
        raise ProbeContractError("Read-only probe module differs from its payload contract.")
    try:
        path.relative_to(payload)
        _ordinary_file(path)
    except (OSError, ValueError) as error:
        raise ProbeContractError("Probe code must belong to the qualified payload.") from error


def qualify_resolver(
    path: Path, plugin: str, module: str, *, python: Path | None = None,
) -> None:
    """Allow only this interpreter's retained SDK or a qualified payload SDK."""
    if not required():
        return
    python = python if python is not None else Path(sys.executable)
    prefix = python.parent.parent
    retained = [prefix / ".runtime-bootstrap" / "versioned_runtime.py"]
    if prefix.name == ".venv":
        retained.append(prefix.parent / ".runtime-bootstrap" / "versioned_runtime.py")
    if path in retained:
        runtime_contract(python, module, plugin=plugin)
        try:
            _ordinary_file(path)
        except (OSError, ValueError) as error:
            raise ProbeContractError("Retained probe resolver is not an ordinary file.") from error
    elif path.name == "versioned_runtime.py" and path.parent.name == "scripts":
        qualify_payload_code(path, path.parent.parent, plugin, module)
    else:
        raise ProbeContractError("Probe resolver has no qualified provenance; update the owning installation.")


def unqualified_output(content: bytes) -> bool:
    try:
        value = json.loads(content, object_pairs_hook=_unique)
    except (UnicodeError, ValueError):
        return False
    return (
        isinstance(value, dict) and type(value.get("version")) is int
        and all(value.get(key) == expected for key, expected in REFUSAL.items())
    )


def refuse(error: Exception) -> int:
    print(json.dumps(REFUSAL, sort_keys=True))
    print(f"readiness-isolation: {error}", file=sys.stderr)
    return 126


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--module", required=True)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--script", type=Path)
    target.add_argument("--origin", action="store_true")
    parser.add_argument("--payload-only", action="store_true")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        payload_root = Path(__file__).absolute().parent.parent
        payload = payload_contract(payload_root, args.plugin)
        if payload.module != args.module:
            raise ProbeContractError("Read-only probe module differs from its payload contract.")
        if args.payload_only and args.script is None:
            raise ProbeContractError("Payload-only probes require an explicit payload script.")
        if args.script is not None:
            try:
                args.script.relative_to(payload_root)
                _ordinary_file(args.script)
            except (OSError, ValueError) as error:
                raise ProbeContractError("Probe script must belong to the qualified payload.") from error
        if not args.payload_only:
            runtime_contract(Path(sys.executable), args.module, plugin=args.plugin)
    except ProbeContractError as error:
        return refuse(error)
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    os.environ[PROBE_ENV] = PROBE_CONTRACT
    if args.origin:
        import importlib.util

        spec = importlib.util.find_spec(args.module)
        if spec is None or spec.origin is None:
            return 1
        print(Path(spec.origin).resolve())
        return 0
    sys.argv = [str(args.script) if args.script is not None else args.module, *arguments]
    try:
        if args.script is None:
            runpy.run_module(args.module, run_name="__main__", alter_sys=True)
        else:
            runpy.run_path(str(args.script), run_name="__main__")
    except Exception as error:
        # The script copy and installed package have distinct class identities.
        runtime_support = sys.modules.get("configuration_context.readiness")
        runtime_error = getattr(runtime_support, "ProbeContractError", None)
        if isinstance(error, ProbeContractError) or (
            isinstance(runtime_error, type) and isinstance(error, runtime_error)
        ):
            return refuse(error)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

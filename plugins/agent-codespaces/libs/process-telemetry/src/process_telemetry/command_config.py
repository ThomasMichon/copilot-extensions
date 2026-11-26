"""One bounded parser for the consumer-telemetry-command v1 declaration."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import json
import os
from pathlib import Path
from typing import Any, NoReturn

CAPABILITY: dict[str, Any] = {
    "schema_version": 1,
    "contract": "consumer-telemetry-command",
    "versions": [1],
    "transports": ["command-jsonl-v1"],
    "source_schemas": ["bridge.lifecycle.v1"],
}
ENVIRONMENT_KEYS = frozenset({
    "PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
    "HOME", "USERPROFILE", "LANG", "LC_ALL",
})


def _environment(source: Mapping[str, str]) -> dict[str, str]:
    if os.name == "nt":
        canonical = {key.upper(): key for key in ENVIRONMENT_KEYS}
        return {canonical[key.upper()]: value for key, value in source.items()
                if key.upper() in canonical}
    return {key: value for key, value in source.items() if key in ENVIRONMENT_KEYS}


def _executable(name: str, root: Path, env: dict[str, str]) -> str:
    if os.path.dirname(name):
        candidate = root / name
        return str(candidate) if candidate.is_absolute() and candidate.is_file() else ""
    if "PATH" not in env:
        return ""
    extensions = [""]
    if os.name == "nt" and not Path(name).suffix:
        extensions = [extension for extension in env.get("PATHEXT", ".COM;.EXE").split(";")
                      if extension.lower() in (".exe", ".com")]
    for entry in env.get("PATH", "").split(os.pathsep):
        directory = Path(entry)
        if not directory.is_absolute():
            directory = root / directory
        if not directory.is_absolute():
            continue
        for extension in extensions:
            candidate = directory / (name + extension)
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
            except OSError:
                continue
    return ""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _nonfinite(value: str) -> NoReturn:
    raise ValueError("nonfinite JSON")


def read_object(path: Path, limit: int) -> dict[str, Any] | None:
    """Read an opened file with a limit-plus-one check; absence is distinct."""
    try:
        with path.open("rb") as stream:
            encoded = stream.read(limit + 1)
    except FileNotFoundError:
        return None
    if len(encoded) > limit:
        raise ValueError("file limit")
    value = json.loads(
        encoded.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite,
    )
    if type(value) is not dict:
        raise ValueError("object required")
    return value


def _validate(value: dict[str, Any], *, local: bool) -> dict[str, Any]:
    fields = {"schema_version", "mode", "command"}
    if not local:
        fields.add("allow_local_override")
    if set(value) - fields or type(value.get("schema_version")) is not int:
        raise ValueError("invalid declaration")
    if value["schema_version"] != 1 or value.get("mode") not in ("disabled", "command"):
        raise ValueError("unsupported declaration")
    if type(value.get("allow_local_override", False)) is not bool:
        raise ValueError("invalid override")
    if value["mode"] == "disabled":
        if "command" in value:
            raise ValueError("disabled command")
        return value
    command = value.get("command")
    if type(command) is not dict or set(command) != {"argv"}:
        raise ValueError("invalid command")
    argv = command["argv"]
    if (
        type(argv) is not list or not 1 <= len(argv) <= 32
        or any(type(arg) is not str or not arg or len(arg) > 512 or "\0" in arg for arg in argv)
        or sum(len(arg.encode("utf-8")) for arg in argv) > 8192
    ):
        raise ValueError("invalid argv")
    return value


@dataclass(frozen=True)
class Selection:
    """Immutable owner-admitted root and literal command, never record data."""

    state: str
    root: str | None = None
    argv: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ProviderCapabilities:
    """Capabilities declared by one exact selected provider runtime."""

    policy_versions: frozenset[int]
    transports: frozenset[str]
    source_schemas: frozenset[str]


def select(root: str | os.PathLike[str], *, environment: dict[str, str] | None = None) -> Selection:
    """Snapshot an already trusted root; do not discover or authenticate roots."""
    try:
        if not Path(root).is_absolute():
            return Selection("origin_unavailable")
        canonical = Path(root).resolve(strict=True)
        if not canonical.is_dir():
            return Selection("origin_unavailable")
        shared_path = canonical / ".agent-telemetry" / "config.json"
        local_path = shared_path.with_name("config.local.json")
        local = read_object(local_path, 16384)
        if local is not None:
            _validate(local, local=True)
            if local["mode"] == "disabled":
                return Selection("disabled", str(canonical))
        shared = read_object(shared_path, 16384)
        if shared is not None:
            _validate(shared, local=False)
        if shared is None:
            return Selection("override_refused" if local else "unconfigured", str(canonical))
        if shared["mode"] == "disabled":
            return Selection("disabled", str(canonical))
        if local is not None and not shared.get("allow_local_override", False):
            return Selection("override_refused", str(canonical))
        chosen = local or shared
        source = os.environ if environment is None else environment
        env = _environment(source)
        argv = list(chosen["command"]["argv"])
        executable = _executable(argv[0], canonical, env)
        if not executable or Path(executable).suffix.lower() in (".ps1", ".cmd", ".bat"):
            return Selection("start_failed", str(canonical))
        if os.name == "nt" and Path(executable).suffix.lower() not in (".exe", ".com"):
            return Selection("start_failed", str(canonical))
        with open(executable, "rb") as stream:
            if stream.read(2) == b"#!":
                return Selection("start_failed", str(canonical))
        # Interpreter symlinks retain the invoked path's virtual-environment context.
        argv[0] = executable
        return Selection("active", str(canonical), tuple(argv), tuple(env.items()))
    except (OSError, ValueError, UnicodeError, TypeError, RecursionError):
        return Selection("invalid_config")


def supports(path: Path, *, source_schema: str = "bridge.lifecycle.v1") -> bool:
    """Inspect only the selected runtime's exact metadata path."""
    try:
        value = read_object(path / "consumer-telemetry-contract.json", 4096)
        if value is None:
            return False
        if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            return False
        if value.get("contract") != CAPABILITY["contract"]:
            return False
        for key in ("versions", "transports", "source_schemas"):
            choices = value.get(key)
            element_type = int if key == "versions" else str
            if (
                type(choices) is not list
                or any(type(choice) is not element_type for choice in choices)
                or any(required not in choices for required in (
                    [source_schema] if key == "source_schemas" else CAPABILITY[key]
                ))
            ):
                return False
        return True
    except (OSError, ValueError, UnicodeError, TypeError, RecursionError):
        return False


def supports_policy(
    path: Path,
    *,
    policy_version: int,
    source_schema: str,
    transport: str = "command-jsonl-v1",
) -> bool:
    """Require an explicit capability from the exact selected provider."""
    if (
        type(policy_version) is not int
        or type(source_schema) is not str
        or type(transport) is not str
    ):
        return False
    capabilities = read_policy_capabilities(path)
    if capabilities is None:
        return False
    return (
        policy_version in capabilities.policy_versions
        and transport in capabilities.transports
        and source_schema in capabilities.source_schemas
    )


def read_policy_capabilities(path: Path) -> ProviderCapabilities | None:
    """Read policy support only from the selected provider's exact descriptor."""
    try:
        value = read_object(path / "consumer-telemetry-contract.json", 4096)
        if (
            value is None
            or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
            or value.get("contract") != CAPABILITY["contract"]
        ):
            return None
        versions = value.get("versions")
        transports = value.get("transports")
        source_schemas = value.get("source_schemas")
        policy_versions = value.get("policy_versions")
        if (
            type(versions) is not list
            or any(type(version) is not int for version in versions)
            or 1 not in versions
            or type(transports) is not list
            or any(type(transport) is not str for transport in transports)
            or type(source_schemas) is not list
            or any(type(schema) is not str for schema in source_schemas)
            or type(policy_versions) is not list
            or any(type(version) is not int for version in policy_versions)
        ):
            return None
        return ProviderCapabilities(
            frozenset(policy_versions),
            frozenset(transports),
            frozenset(source_schemas),
        )
    except (OSError, ValueError, UnicodeError, TypeError, RecursionError):
        return None

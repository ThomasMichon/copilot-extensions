"""Configuration-only retained telemetry adoption, with producer-owned I/O."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .configured_spool import retained_directory

SCHEMA = "process-telemetry.retained-configuration/v1"
MAX_CONFIG_BYTES = 64 * 1024


def _plain(path: Path, *, directory: bool) -> tuple[int, int]:
    info = path.lstat()
    if (
        path.is_symlink()
        or getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or (not directory and info.st_nlink != 1)
    ):
        raise ValueError("configuration requires exclusive plain paths")
    return info.st_dev, info.st_ino


def _directory(path: Path) -> list[tuple[int, int]]:
    if not path.is_absolute() or "\0" in str(path):
        raise ValueError("directory must be absolute")
    return [_plain(parent, directory=True) for parent in (*reversed(path.parents), path)]


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate telemetry configuration key")
        data[key] = value
    return data


def _constant(value: str) -> None:
    raise ValueError("nonfinite telemetry configuration value")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite telemetry configuration value")
    return number


def preview(
    target: Path, directory: Path, *, service: str, environment_sink: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read a bounded snapshot without provisioning, factories or writer locks."""
    if not target.is_absolute():
        raise ValueError("configuration target must be absolute")
    target_parents = _directory(target.parent)
    if target.parent.resolve(strict=True) != target.parent:
        raise ValueError("configuration target requires a canonical parent")
    namespace = _directory(directory / "retained")
    try:
        identity = _plain(target, directory=False)
    except FileNotFoundError:
        raw = None
        data: dict[str, Any] = {}
        identity = None
    else:
        with target.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino) != identity:
                raise ValueError("telemetry configuration changed while reading")
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if _plain(target, directory=False) != identity:
            raise ValueError("telemetry configuration changed while reading")
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError("telemetry configuration exceeds its read bound")
        data = json.loads(
            raw, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float,
        )
        if type(data) is not dict:
            raise ValueError("telemetry configuration must be an object")

    reason = None
    if environment_sink:
        reason = "environment-sink-preserved"
    elif "sink" in data or "spool" in data:
        reason = "legacy-or-custom-sink-preserved"
    elif "retained_spool" in data:
        if retained_directory(data) != directory:
            reason = "existing-retained-directory-preserved"
    updated = {**data, "retained_spool": {"directory": str(directory)}}
    if len((json.dumps(updated, indent=2) + "\n").encode("utf-8")) > MAX_CONFIG_BYTES:
        raise ValueError("updated telemetry configuration exceeds its read bound")
    snapshot = {
        "schema": SCHEMA, "service": service, "target": str(target),
        "directory": str(directory), "target_parents": target_parents,
        "namespace": namespace, "identity": identity,
        "bytes": None if raw is None else hashlib.sha256(raw).hexdigest(),
        "environment_sink": environment_sink,
    }
    token = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema": SCHEMA, "service": service, "target": str(target),
        "directory": str(directory), "status": "refused" if reason else "preview",
        "reason": reason, "confirmation": None if reason else token,
        "would_change": reason is None and updated != data,
        "namespace_preprovisioned": True,
        "activation": "not-requested", "write_health": "unknown-from-configuration",
    }, updated


def execute(
    args: argparse.Namespace, *, target: Callable[[], Path], service: str,
    sink_env: str, lock_factory: Callable, write_atomic: Callable,
) -> int:
    """Recheck the native target and snapshot under the producer's writer lock."""
    try:
        selected = target()
        result, updated = preview(
            selected, args.directory, service=service, environment_sink=os.environ.get(sink_env),
        )
        if args.apply:
            if result["status"] == "refused":
                print(json.dumps(result, sort_keys=True))
                return 2
            if not args.confirm or args.confirm != result["confirmation"]:
                raise ValueError("current preview confirmation required")
            lock_path = selected.with_name(selected.name + ".lock")
            if lock_path.exists() or lock_path.is_symlink():
                _plain(lock_path, directory=False)
            with lock_factory(lock_path) as lock:
                result, updated = preview(
                    target(), args.directory, service=service,
                    environment_sink=os.environ.get(sink_env),
                )
                if result["status"] == "refused" or result["confirmation"] != args.confirm:
                    raise ValueError("telemetry configuration changed; preview again")
                lock.assert_owned()
                if result["would_change"]:
                    write_atomic(selected, updated)
                result["status"] = "configured"
                result["changed"] = result.pop("would_change")
        elif args.confirm:
            raise ValueError("confirmation is only valid with --apply")
    except (ValueError, OSError, RuntimeError) as error:
        print(json.dumps({
            "schema": SCHEMA, "service": service, "status": "error",
            "reason": str(error), "activation": "not-requested",
        }, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 2 if result["status"] == "refused" else 0


def register_commands(sub: argparse._SubParsersAction, handler: Callable) -> None:
    telemetry = sub.add_parser(
        "telemetry", help="Configuration-only telemetry operations", allow_abbrev=False,
    )
    commands = telemetry.add_subparsers(dest="telemetry_command", required=True)
    capabilities = commands.add_parser("capabilities", allow_abbrev=False)
    capabilities.set_defaults(func=_capabilities)
    retained = commands.add_parser(
        "retained", help="Preview or confirm preprovisioned retained configuration",
        description=SCHEMA, allow_abbrev=False,
    )
    retained.add_argument("--directory", type=Path, required=True)
    retained.add_argument("--apply", action="store_true")
    retained.add_argument("--confirm", help="Exact confirmation from a current preview")
    retained.add_argument("--json", action="store_true", help="Output is always JSON")
    retained.set_defaults(func=handler)


def _capabilities(_args: argparse.Namespace) -> int:
    print(json.dumps({
        "schema": SCHEMA, "operations": ["preview", "apply"],
        "configuration_only": True, "custom_sink_overwrite": False,
    }, sort_keys=True))
    return 0

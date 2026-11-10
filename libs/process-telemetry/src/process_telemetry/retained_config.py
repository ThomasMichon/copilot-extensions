"""Configuration-only retained telemetry adoption, with producer-owned I/O."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any, ContextManager, Protocol

from .configured_spool import retained_directory

SCHEMA = "process-telemetry.retained-configuration/v1"
MAX_CONFIG_BYTES = 64 * 1024
MAX_INPUT_BYTES = 256 * 1024


class _OwnedLock(Protocol):
    def assert_owned(self) -> None: ...


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


def _input_fence(inputs: list[list[str]], *, target: Path) -> list[dict[str, Any]]:
    if len(inputs) > 8:
        raise ValueError("too many configuration inputs")
    snapshots: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for raw_path, expected in inputs:
        path = Path(raw_path)
        if path in seen or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
            raise ValueError("invalid or duplicate configuration input")
        seen.add(path)
        parents = _directory(path.parent)
        if path.parent.resolve(strict=True) != path.parent:
            raise ValueError("configuration input requires a canonical parent")
        identity = _plain(path, directory=False)
        if any(
            path == output or output.exists() and path.samefile(output)
            for output in (target, target.with_name(target.name + ".lock"))
        ):
            raise ValueError("configuration input overlaps native output")
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != identity:
                raise ValueError("configuration input changed")
            raw = stream.read(MAX_INPUT_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (
            len(raw) > MAX_INPUT_BYTES
            or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
            or _plain(path, directory=False) != identity
            or hashlib.sha256(raw).hexdigest() != expected
        ):
            raise ValueError("configuration input changed or exceeds its read bound")
        snapshots.append({
            "path": str(path), "parents": parents, "identity": identity, "sha256": expected,
        })
    return snapshots


def preview(
    target: Path, directory: Path, *, service: str, environment_sink: str | None,
    inputs: list[list[str]] | None = None,
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
            raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=_constant, parse_float=_float,
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
        "inputs": _input_fence(inputs or [], target=target),
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
    sink_env: str, lock_factory: Callable[[Path], ContextManager[_OwnedLock]],
    write_atomic: Callable[[Path, dict[str, Any]], None],
) -> int:
    """Recheck the native target and snapshot under the producer's writer lock."""
    write_started = False
    try:
        selected = target()
        result, updated = preview(
            selected, args.directory, service=service, environment_sink=os.environ.get(sink_env),
            inputs=getattr(args, "if_input", None),
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
                    inputs=getattr(args, "if_input", None),
                )
                if result["status"] == "refused" or result["confirmation"] != args.confirm:
                    raise ValueError("telemetry configuration changed; preview again")
                lock.assert_owned()
                if result["would_change"]:
                    write_started = True
                    write_atomic(selected, updated)
                result["status"] = "configured"
                result["changed"] = result.pop("would_change")
                result["write_outcome"] = "applied" if result["changed"] else "unchanged"
        elif args.confirm:
            raise ValueError("confirmation is only valid with --apply")
    except (ValueError, OSError, RuntimeError) as error:
        print(json.dumps({
            "schema": SCHEMA, "service": service, "status": "error",
            "reason": str(error), "activation": "not-requested",
            "write_outcome": "unknown" if write_started else "not-applied",
        }, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 2 if result["status"] == "refused" else 0


def register_commands(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    handler: Callable[[argparse.Namespace], int],
) -> argparse._SubParsersAction[argparse.ArgumentParser]:
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
    retained.add_argument(
        "--if-input", nargs=2, action="append", metavar=("PATH", "SHA256"),
        help="Require unchanged local input bytes inside the native configuration transaction",
    )
    retained.add_argument("--json", action="store_true", help="Output is always JSON")
    retained.set_defaults(func=handler)
    return commands


def _capabilities(_args: argparse.Namespace) -> int:
    print(json.dumps({
        "schema": SCHEMA, "operations": ["preview", "apply"],
        "configuration_only": True, "custom_sink_overwrite": False,
        "input_fence": 1,
    }, sort_keys=True))
    return 0

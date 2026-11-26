"""Explicit terminal retirement of one source-bound repository test environment."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import cast
import uuid

from _admission_protocol import acquire
from single_instance_lease import AlreadyRunningError as AlreadyRunningError
from cache_directory_paths import ordinary_cache_root
from native_test_paths import native_test_path
from plugin_test_cache import clear_cache_contents as clear_cache_contents
from plugin_test_cache import preparation_generation
from plugin_test_resources import ENVIRONMENT_LAYOUT, resource_path
from plugin_test_selection import resolve_plugin_root
from standalone_consumers import STANDALONE_CONSUMERS

SCHEMA = "copilot-extensions.test-environment-retirement"
MARKER = ".retired-environment.json"
MAX_METADATA = 65536
MAX_ENTRIES = 256


@dataclass(frozen=True)
class Preview:
    schema: str
    version: int
    source: str
    source_device: int
    source_inode: int
    target: str
    platform: str
    environment: str
    device: int
    inode: int
    generation: str
    entries: int


def require_active(environment: Path) -> None:
    """A retired inode cannot become usable again through an admitted waiter."""
    try:
        native_test_path(environment / MARKER).lstat()
    except FileNotFoundError:
        return
    raise ValueError(
        "This test environment is terminally retired. Keep its directory identity; "
        "use another managed source checkout for new tests, not --reinstall."
    )


def _metadata(path: Path) -> dict[str, object]:
    native = native_test_path(path)
    info = native.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_reparse_tag", False) or info.st_size > MAX_METADATA:
        raise ValueError("Cache metadata is linked, non-regular or oversized")
    value = json.loads(native.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Cache metadata must be an object")
    return cast(dict[str, object], value)


def _environment(source: Path, target: str, platform: str) -> Path:
    if platform != sys.platform:
        raise ValueError("Retirement requires the environment's native platform")
    standalone = target in STANDALONE_CONSUMERS
    if not standalone and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", target):
        raise ValueError("Invalid test target")
    source = source.resolve(strict=True)
    support = _metadata(source / "tools" / "test-environment-capabilities.json")
    retirement = support.get("retirement")
    if support != {
        "schema": "copilot-extensions.test-environment-preparation", "version": 1,
        "retirement": {"version": 1, "layout": ENVIRONMENT_LAYOUT},
    } or (
        type(support.get("version")) is not int or not isinstance(retirement, dict)
        or type(retirement.get("version")) is not int
    ):
        raise ValueError("Selected source does not support terminal test-environment retirement")
    project = source / target if standalone else resolve_plugin_root(source, target)
    if not project.is_dir():
        raise ValueError("Test target does not belong to the selected source")
    root = ordinary_cache_root(resource_path(source))
    owner = _metadata(root / "scratch-owner.json")
    if owner.get("source_checkout") != str(source):
        raise ValueError("Retained cache belongs to another source")
    environment = ordinary_cache_root(root / ENVIRONMENT_LAYOUT / platform / target)
    if not native_test_path(environment).is_dir():
        raise ValueError("Selected environment does not exist")
    return environment


def _identity(environment: Path) -> tuple[int, int]:
    info = native_test_path(ordinary_cache_root(environment)).stat()
    if not info.st_ino:
        raise ValueError("Environment has no stable filesystem identity")
    return info.st_dev, info.st_ino


def _observe(source: Path, target: str, platform: str, environment: Path) -> Preview:
    source_info = source.stat()
    if not stat.S_ISDIR(source_info.st_mode) or not source_info.st_ino:
        raise ValueError("Source has no stable directory identity")
    info = native_test_path(environment).stat()
    rows: list[tuple[str, int, int, int, int, int]] = []
    for child in native_test_path(environment).iterdir():
        if len(rows) >= MAX_ENTRIES:
            raise ValueError("Environment observation exceeds the entry limit")
        entry = child.lstat()
        rows.append((child.name, entry.st_dev, entry.st_ino, entry.st_mode, entry.st_size, entry.st_mtime_ns))
    marker = environment / MARKER
    if any(row[0] == MARKER for row in rows):
        value = _metadata(marker)
        if any(value.get(key) != expected for key, expected in (
            ("schema", SCHEMA), ("version", 1), ("source", str(source)),
            ("source_device", source_info.st_dev), ("source_inode", source_info.st_ino),
            ("target", target), ("platform", platform),
            ("device", info.st_dev), ("inode", info.st_ino),
        )) or value.get("state") not in {"retiring", "retired"}:
            raise ValueError("Retirement marker does not describe this environment")
    generation = hashlib.sha256(json.dumps(
        [info.st_mtime_ns, sorted(rows), preparation_generation(environment)], separators=(",", ":"),
    ).encode()).hexdigest()
    return Preview(
        SCHEMA, 1, str(source), source_info.st_dev, source_info.st_ino, target, platform, str(environment),
        info.st_dev, info.st_ino, generation, len(rows),
    )


def preview(source: Path, target: str, platform: str = sys.platform) -> Preview:
    """Observe under the same lease as preparation, use and retirement."""
    source = source.resolve(strict=True)
    environment = _environment(source, target, platform)
    identity = _identity(environment)
    lease = acquire(0, environment)
    try:
        if _identity(_environment(source, target, platform)) != identity:
            raise ValueError("Environment was replaced during admission")
        return _observe(source, target, platform, environment)
    finally:
        lease.release()


def parse_preview(value: object) -> Preview:
    if not isinstance(value, dict) or set(value) != set(Preview.__dataclass_fields__):
        raise ValueError("Unsupported retirement preview")
    for key in ("schema", "source", "target", "platform", "environment", "generation"):
        if not isinstance(value[key], str):
            raise ValueError("Invalid retirement preview text")
    for key in ("version", "source_device", "source_inode", "device", "inode", "entries"):
        if type(value[key]) is not int or value[key] < 0:
            raise ValueError("Invalid retirement preview number")
    if value["schema"] != SCHEMA or value["version"] != 1:
        raise ValueError("Unsupported retirement preview version")
    return Preview(**value)


def _write_marker(environment: Path, plan: Preview, state: str) -> None:
    data = asdict(plan)
    data["state"] = state
    temporary = native_test_path(environment / (MARKER + ".new-" + uuid.uuid4().hex))
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(native_test_path(environment / MARKER))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def apply(plan: Preview) -> dict[str, object]:
    """Retire only this environment's contents, never its leased directory."""
    source = Path(plan.source).resolve(strict=True)
    source_info = source.stat()
    if (source_info.st_dev, source_info.st_ino) != (plan.source_device, plan.source_inode):
        raise ValueError("Retirement preview names a replaced source checkout")
    environment = _environment(source, plan.target, plan.platform)
    identity = _identity(environment)
    if str(environment) != plan.environment or identity != (plan.device, plan.inode):
        raise ValueError("Retirement preview names a replaced environment")
    lease = acquire(0, environment)
    try:
        if any(os.path.lexists(native_test_path(environment / name)) for name in (
            ".dep-fingerprint.pending", ".dep-fingerprint.pending.update",
        )):
            raise ValueError("Unsettled preparation retains this environment; retirement refused")
        current = _environment(source, plan.target, plan.platform)
        if _identity(current) != identity or _observe(source, plan.target, plan.platform, current) != plan:
            raise ValueError("Retirement preview is stale")
        _write_marker(environment, plan, "retiring")
        try:
            clear_cache_contents(environment, preserve=frozenset({MARKER}))
            if _identity(environment) != identity or any(
                child.name != MARKER for child in native_test_path(environment).iterdir()
            ):
                raise ValueError("Environment retirement did not preserve an empty fenced root")
            _write_marker(environment, plan, "retired")
        except (OSError, ValueError) as exc:
            return {
                "schema": SCHEMA, "version": 1, "outcome": "partial-failure",
                "source_retirement_safe": False, "error": str(exc),
                "environment": str(environment), "directory_preserved": True,
            }
        return {
            "schema": SCHEMA, "version": 1, "outcome": "retired",
            "source_retirement_safe": True, "scope": "selected-environment-only",
            "environment": str(environment), "directory_preserved": True,
        }
    finally:
        lease.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    show = commands.add_parser("preview")
    show.add_argument("--source", type=Path, required=True)
    show.add_argument("--target", required=True)
    show.add_argument("--platform", default=sys.platform)
    retire = commands.add_parser("apply")
    retire.add_argument("--preview-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "preview":
            result = asdict(preview(args.source, args.target, args.platform))
        else:
            result = apply(parse_preview(_metadata(args.preview_file)))
    except AlreadyRunningError:
        print(json.dumps({"schema": SCHEMA, "version": 1, "outcome": "busy", "source_retirement_safe": False}))
        return 3
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({
            "schema": SCHEMA, "version": 1, "outcome": "refused",
            "source_retirement_safe": False, "error": str(exc),
        }))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 1 if result.get("outcome") == "partial-failure" else 0


if __name__ == "__main__":
    raise SystemExit(main())

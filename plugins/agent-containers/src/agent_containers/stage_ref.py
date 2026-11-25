"""Stage one verified input file for a borrowed driver-backed member."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from . import resolver, runtime_drivers

_CREATE_DIR = """
set -eu
CDPATH= cd -- "$2"
workspace=$(pwd -P)
CDPATH= cd -- "$HOME"
home=$(pwd -P)
ref=${home%/}/$1
case "$ref/" in "$workspace/"*) exit 1 ;;
esac
[ "$workspace" != / ]
umask 077
mkdir -m 700 -- "$ref"
printf '%s\\n' "$ref"
"""

_VERIFY = """
set -eu
[ ! -L "${1%/*}" ]
[ ! -L "$1" ]
[ -f "$1" ]
size=$(wc -c < "$1")
[ "$size" -eq "$2" ]
if command -v sha256sum >/dev/null 2>&1; then
    digest=$(sha256sum < "$1")
else
    digest=$(shasum -a 256 < "$1")
fi
printf '%s %s\\n' "$2" "${digest%% *}"
"""

_REMOVE = """
set -eu
[ ! -L "$1" ]
[ -d "$1" ]
rm -f -- "$1/input"
rmdir -- "$1"
"""


def _regular(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _snapshot(source: Path, destination: Path) -> tuple[int, str]:
    from venue_copilot.refs import MAX_REF_BYTES

    # Copy a bounded snapshot rather than giving the driver the caller's
    # path, which could be replaced between validation and copy-in.
    before = source.lstat()
    if not _regular(before):
        raise ValueError("Reference input must be a regular, non-symlink file")
    flags = (
        getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(os.open(source, os.O_RDONLY | flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not _regular(opened) or not os.path.samestat(before, opened):
            raise ValueError("Reference input changed while opening")
        if opened.st_size > MAX_REF_BYTES:
            raise ValueError(f"Reference input exceeds the {MAX_REF_BYTES}-byte limit")
        size = 0
        digest = hashlib.sha256()
        with destination.open("xb") as snapshot:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_REF_BYTES:
                    raise ValueError(f"Reference input exceeds the {MAX_REF_BYTES}-byte limit")
                digest.update(chunk)
                snapshot.write(chunk)
        after = os.fstat(stream.fileno())
        if (
            size != opened.st_size or size != after.st_size
            or opened.st_mtime_ns != after.st_mtime_ns or opened.st_ctime_ns != after.st_ctime_ns
        ):
            raise ValueError("Reference input changed while reading")
    # Drivers may copy in as a different user. The containing directories,
    # not this file's mode, restrict access on both sides of the transfer.
    destination.chmod(0o644)
    return size, digest.hexdigest()


def _assignment(name: str, effort: str) -> dict[str, str | float]:
    from . import lease

    current = lease.get_lease(name)
    if current is None or current.effort != effort:
        raise RuntimeError(f"Container '{name}' does not hold the expected lease assignment")
    return {"kind": "lease", "effort": effort, "acquired_at": current.acquired_at}


def _target(
    name: str, expected_instance: str, expected_runtime: str,
) -> tuple[resolver.LiveExecTarget, runtime_drivers.Route]:
    from . import config, resolver, runtime_drivers

    target = resolver.resolve_live_exec_target(name)
    if target.actual_profile != config.TRUSTED_PROFILE or not target.fleet.driver:
        raise RuntimeError("Reference staging requires a trusted driver-backed fleet member")
    handle = runtime_drivers.prepared_handle(name, expected_instance, expected_runtime)
    if handle != target.container_id:
        raise RuntimeError(f"Container '{name}' does not match the expected driver instance")
    route = runtime_drivers.owner_of(handle)
    if route is None or route.name != name or route.instance_id != expected_instance:
        raise RuntimeError(f"Container '{name}' does not match the expected driver instance")
    return target, route


def _stage(
    name: str, effort: str, instance: str, runtime: str, snapshot: Path, size: int, digest: str,
) -> str:
    from . import lease

    assignment = _assignment(name, effort)
    with lease.session_admission(name, expected_assignment=assignment):
        target, route = _target(name, instance, runtime)

        def run(script: str, *args: str) -> str:
            result = route.driver.exec(
                name, ["sh", "-c", script, "stage-ref", *args],
                user=target.user, instance_id=instance,
            )
            if result.exit_code:
                raise RuntimeError("Reference staging could not create or verify the private file")
            return result.stdout.rstrip("\n")

        basename = f".agent-containers-ref-{uuid4().hex}"
        private_dir = run(_CREATE_DIR, basename, target.workspace_folder)
        remote_dir = PurePosixPath(private_dir)
        if (
            remote_dir.root != "/" or str(remote_dir) != private_dir
            or remote_dir.name != basename or ".." in remote_dir.parts
            or remote_dir.is_relative_to(PurePosixPath(target.workspace_folder))
            or any(ord(char) < 32 or ord(char) == 127 for char in private_dir)
        ):
            raise RuntimeError("Reference staging did not return a valid private directory")
        remote_file = str(remote_dir / "input")
        try:
            route.driver.copy_in(name, snapshot, remote_file, instance_id=instance)
            if run(_VERIFY, remote_file, str(size)) != f"{size} {digest}":
                raise RuntimeError("Staged reference did not match the source size and SHA-256")
            current, current_route = _target(name, instance, runtime)
            if (
                _assignment(name, effort) != assignment or current_route != route
                or current.user != target.user or current.workspace_folder != target.workspace_folder
            ):
                raise RuntimeError(f"Container '{name}' assignment or execution target changed")
        except (RuntimeError, ValueError, OSError, KeyboardInterrupt):
            try:
                run(_REMOVE, private_dir)
            except (RuntimeError, ValueError, OSError):
                pass
            raise
        return remote_file


def _command(args: argparse.Namespace) -> int:
    from . import config

    if not args.effort.strip() or not args.expected_instance.strip() or not args.expected_runtime.strip():
        raise ValueError("Reference staging requires a nonempty effort, instance, and runtime")
    try:
        config.ensure_state_dir()
        with TemporaryDirectory(prefix="stage-ref-", dir=config.STATE_DIR) as local:
            snapshot = Path(local) / "input"
            size, digest = _snapshot(args.file.expanduser(), snapshot)
            path = _stage(
                args.name, args.effort, args.expected_instance, args.expected_runtime,
                snapshot, size, digest,
            )
    except OSError as exc:
        raise RuntimeError("Reference staging could not access the host input or staging state") from exc
    if args.json:
        print(json.dumps({
            "schema_version": 1,
            "container": args.name,
            "instance_id": args.expected_instance,
            "path": path,
            "size": size,
            "sha256": digest,
        }))
    else:
        print(path)
    return 0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "stage-ref", help="Stage one verified file on an assigned driver member",
    )
    parser.add_argument("name", help="Borrowed container name")
    parser.add_argument("--effort", required=True, help="Exact current lease holder")
    parser.add_argument("--expected-instance", required=True, help="Fleet container_id for this member")
    parser.add_argument("--expected-runtime", required=True, help="Fleet runtime_identity for this member")
    parser.add_argument("--file", type=Path, required=True, help="Host input file")
    parser.add_argument("--json", action="store_true", help="Emit the verified delivery receipt")
    parser.set_defaults(func=_command)

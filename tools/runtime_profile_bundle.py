"""Reproducible exact-profile wheel transports; never acquisition or approval."""
from __future__ import annotations

import argparse
import json
import os
import stat
import time
import zipfile
from pathlib import Path

from runtime_profile import ProfileError, validate_profile
from runtime_profile_wheels import verify_profile_wheels

# isort: split

from installer_dependency_policy import _private_retained_root, _temporary_inputs
from installer_wheel_inputs import WheelError, WheelLimits, _check_deadline, _ordinary
from installer_wheel_snapshot import (
    _digest,
    _open,
    _unchanged,
    retain_wheel_bundle,
    retain_wheel_inputs,
)


class _BoundedOutput:
    def __init__(self, stream, limit: int, deadline: float):
        self.stream, self.limit, self.deadline = stream, limit, deadline

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def write(self, data):
        _check_deadline(self.deadline)
        if self.stream.tell() + len(data) > self.limit:
            raise WheelError("wheel transport byte budget exceeded")
        return self.stream.write(data)


def _write_bundle(snapshot, path: Path, *, max_bytes: int, deadline: float) -> None:
    _write_archive(
        {"wheels/" + name: stream for name, stream in snapshot.archives.items()},
        path, max_bytes=max_bytes, deadline=deadline,
    )


def _write_archive(archives, path: Path, *, max_bytes: int, deadline: float) -> None:
    with path.open("xb") as output, zipfile.ZipFile(
        _BoundedOutput(output, max_bytes, deadline), "w", compression=zipfile.ZIP_STORED,
    ) as archive:
        for name, stream in sorted(archives.items()):
            _check_deadline(deadline)
            member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.external_attr = (stat.S_IFREG | 0o644) << 16
            stream.seek(0, os.SEEK_END)
            member.file_size = stream.tell()
            stream.seek(0)
            with archive.open(member, "w") as target:
                while chunk := stream.read(1 << 20):
                    _check_deadline(deadline)
                    target.write(chunk)


def build_profile_bundle(
    record: dict, archives, destination: Path, *, limits: WheelLimits,
    max_wheels: int, max_total_bytes: int, max_snapshot_bytes: int,
    max_unpacked_bytes: int, max_bundle_bytes: int, deadline: float,
) -> dict:
    """Produce the existing transport format from a complete, byte-checked set.

    The result binds candidate bytes to an inert profile, not to release/feed
    authority. Source materials and publication remain with their existing owners.
    """
    _check_deadline(deadline)
    if not isinstance(limits, WheelLimits) or any(
        type(value) is not int or value <= 0 for value in (
            max_wheels, max_total_bytes, max_snapshot_bytes,
            max_unpacked_bytes, max_bundle_bytes,
        )
    ):
        raise WheelError("wheel transport limits must be explicit positive integers")
    profile = validate_profile(record, max_tags=limits.max_tags, deadline=deadline)
    expected = {wheel["filename"]: wheel["sha256"] for wheel in profile.record()["wheels"]}
    destination = Path(os.path.abspath(destination))
    if os.path.lexists(destination):
        raise FileExistsError("wheel transport output already exists")
    with _temporary_inputs(destination.parent) as temporary:
        root = _private_retained_root(Path(temporary)).resolve()
        candidate = root / "wheels.zip"
        with retain_wheel_inputs(
            archives, expected, max_wheels=max_wheels,
            max_archive_bytes=limits.max_archive_bytes, max_total_bytes=max_total_bytes,
            max_snapshot_bytes=max_snapshot_bytes, deadline=deadline,
        ) as snapshot:
            verify_profile_wheels(
                profile, snapshot.archives, max_archive_bytes=max_total_bytes,
                max_unpacked_bytes=max_unpacked_bytes, max_metadata_bytes=limits.max_metadata_bytes,
                limits=limits, deadline=deadline,
            )
            snapshot_bytes = sum(os.fstat(stream.fileno()).st_size for stream in snapshot.archives.values())
            _write_bundle(
                snapshot, candidate,
                max_bytes=min(max_bundle_bytes, max_snapshot_bytes - snapshot_bytes),
                deadline=deadline,
            )
            snapshot.verify()
            before = _ordinary(candidate)
            with _open(candidate) as retained:
                opened = _unchanged(candidate, before, retained)
                digest, size = _digest(retained, max_bundle_bytes, deadline)
                with retain_wheel_bundle(
                    candidate, expected, bundle_sha256=digest, max_bundle_bytes=max_bundle_bytes,
                    max_wheels=max_wheels, max_archive_bytes=limits.max_archive_bytes,
                    max_total_bytes=max_total_bytes,
                    max_snapshot_bytes=max_snapshot_bytes - snapshot_bytes - size,
                    deadline=deadline,
                ) as readback:
                    readback.verify()
                _unchanged(candidate, before, retained, opened=opened)
        before = _ordinary(candidate)
        with _open(candidate) as retained:
            opened = _unchanged(candidate, before, retained)
            if _digest(retained, max_bundle_bytes, deadline) != (digest, size):
                raise WheelError("wheel transport changed after input verification")
            _unchanged(candidate, before, retained, opened=opened)
            _check_deadline(deadline)
            # Retained-input exit verification has succeeded. Link publication
            # is atomic on the shared filesystem and refuses an existing output.
            os.link(candidate, destination)
    return {"profile_sha256": profile.lock_id, "sha256": digest, "size": size}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--wheel-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-bytes", type=int, default=512 << 20)
    parser.add_argument("--max-wheels", type=int, default=200)
    args = parser.parse_args(argv)
    deadline = time.monotonic() + args.timeout
    try:
        _check_deadline(deadline)
        before = _ordinary(args.profile)
        with _open(args.profile) as source:
            opened = _unchanged(args.profile, before, source)
            data = source.read((16 << 20) + 1)
            if len(data) > 16 << 20:
                raise ProfileError("profile document byte budget exceeded")
            _unchanged(args.profile, before, source, opened=opened)
        record = json.loads(data)
        profile = validate_profile(record, deadline=deadline)
        paths = {
            wheel["filename"]: args.wheel_dir / wheel["filename"]
            for wheel in profile.record()["wheels"]
        }
        result = build_profile_bundle(
            profile.record(), paths, args.output, deadline=deadline,
            max_wheels=args.max_wheels, max_total_bytes=args.max_bytes,
            max_snapshot_bytes=4 * args.max_bytes, max_bundle_bytes=args.max_bytes,
            max_unpacked_bytes=8 * args.max_bytes,
            limits=WheelLimits(
                args.max_bytes, 8 * args.max_bytes, args.max_bytes, 16 << 20,
                100_000, 10_000, 4 << 20,
            ),
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, UnicodeError) as error:
        parser.exit(1, f"wheel transport refused: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())

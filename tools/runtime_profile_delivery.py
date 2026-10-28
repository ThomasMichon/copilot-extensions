"""Reproducible candidate delivery; material qualification is the caller's job.

An envelope is byte custody, not release approval, license clearance, feed
admission or publication authority. Existing profile and wheel owners remain
authoritative. Materials are never executed, installed or unpacked.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import time
import zipfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from runtime_profile import validate_profile

# isort: split

from installer_dependency_policy import _private_retained_root, _temporary_inputs
from installer_wheel_inputs import (
    WheelError,
    WheelLimits,
    _check_deadline,
    _member_chunks,
    _ordinary,
    _unsafe_windows_component,
    preflight_archive,
)
from installer_wheel_snapshot import _digest, _open, _unchanged, retain_wheel_bundle
from runtime_profile_bundle import _write_archive
from runtime_profile_wheels import verify_profile_wheels

SCHEMA = "copilot-extensions.runtime-profile-delivery"
_MANIFEST_BYTES = 16 << 20
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _json(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WheelError("duplicate delivery JSON property")
        result[key] = value
    return result


def _limits(max_bytes, max_files, max_snapshot_bytes, deadline):
    _check_deadline(deadline)
    if any(type(value) is not int or value <= 0 for value in (max_bytes, max_files, max_snapshot_bytes)):
        raise WheelError("delivery limits must be explicit positive integers")
    return WheelLimits(
        max_bytes, max_bytes, max_bytes, min(max_bytes, _MANIFEST_BYTES),
        max_files, 10_000, min(max_bytes, _MANIFEST_BYTES),
    )


def _file(record, max_bytes):
    if (
        not isinstance(record, dict) or record.keys() != {"sha256", "size"}
        or not isinstance(record["sha256"], str) or not _HASH.fullmatch(record["sha256"])
        or type(record["size"]) is not int or not 0 < record["size"] <= max_bytes
    ):
        raise WheelError("invalid delivery file identity")


def _materials(profile, materials, max_files, max_bytes, deadline):
    if not isinstance(materials, dict) or len(materials) + 3 > max_files:
        raise WheelError("invalid delivery material count")
    wheels = {item["sha256"] for item in profile.record()["wheels"]}
    names = set()
    for name, item in materials.items():
        _check_deadline(deadline)
        if (
            not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name)
            or name.endswith(".") or _unsafe_windows_component(name) or name.casefold() in names
            or not isinstance(item, dict) or item.keys() != {"kind", "wheels", "sha256", "size"}
        ):
            raise WheelError("unsafe or ambiguous delivery material")
        names.add(name.casefold())
        _file({key: item[key] for key in ("sha256", "size")}, max_bytes)
        affected = item["wheels"]
        if (
            item["kind"] not in ("source", "notice") or not isinstance(affected, list)
            or not affected or any(not isinstance(value, str) for value in affected)
            or len(affected) != len(set(affected)) or not set(affected) <= wheels
        ):
            raise WheelError("material must bind selected wheel digests and a source/notice kind")


def _manifest(profile, manifest, max_files, max_bytes, deadline):
    if (
        not isinstance(manifest, dict)
        or manifest.keys() != {"schema", "version", "profile_sha256", "files", "materials"}
        or manifest["schema"] != SCHEMA or type(manifest["version"]) is not int
        or manifest["version"] != 1 or manifest["profile_sha256"] != profile.lock_id
    ):
        raise WheelError("delivery differs from selected profile or schema")
    materials = manifest["materials"]
    _materials(profile, materials, max_files, max_bytes, deadline)
    files = manifest["files"]
    if not isinstance(files, dict) or files.keys() != {
        "wheels.zip", "profile.json", *("materials/" + name for name in materials),
    }:
        raise WheelError("delivery file set differs from selected materials")
    for item in files.values():
        _check_deadline(deadline)
        _file(item, max_bytes)
    for name, item in materials.items():
        if files["materials/" + name] != {key: item[key] for key in ("sha256", "size")}:
            raise WheelError("delivery material/file identity mismatch")
    return files


@contextmanager
def _held(paths, files, deadline):
    with ExitStack() as stack:
        streams, identities = {}, {}
        for name, path in paths.items():
            _check_deadline(deadline)
            before = _ordinary(path)
            stream = stack.enter_context(_open(path))
            opened = _unchanged(path, before, stream)
            if _digest(stream, files[name]["size"], deadline) != (
                files[name]["sha256"], files[name]["size"],
            ):
                raise WheelError("delivery file digest or size mismatch")
            streams[name], identities[name] = stream, (before, opened)
        yield MappingProxyType(streams)
        for name, stream in streams.items():
            before, opened = identities[name]
            _unchanged(paths[name], before, stream, opened=opened)
            if _digest(stream, files[name]["size"], deadline) != (
                files[name]["sha256"], files[name]["size"],
            ):
                raise WheelError("retained delivery file changed")


@dataclass(frozen=True)
class DeliverySnapshot:
    wheels: object
    materials: object
    material_paths: object


@contextmanager
def retain_profile_delivery(
    record, path: Path, *, delivery_sha256: str, max_bytes: int,
    max_files: int, max_snapshot_bytes: int, deadline: float,
):
    """Read independently selected bytes through the original wheel consumer."""
    limits = _limits(max_bytes, max_files, max_snapshot_bytes, deadline)
    profile = validate_profile(record, deadline=deadline)
    if not isinstance(delivery_sha256, str) or not _HASH.fullmatch(delivery_sha256):
        raise WheelError("invalid selected delivery digest")
    path = Path(os.path.abspath(path))
    with _temporary_inputs(None) as temporary:
        root = _private_retained_root(Path(temporary)).resolve()
        before = _ordinary(path)
        with _open(path) as stream:
            opened = _unchanged(path, before, stream)
            if _digest(stream, max_bytes, deadline) != (delivery_sha256, before.st_size):
                raise WheelError("delivery digest mismatch")
            preflight_archive(stream, limits=limits, deadline=deadline)
            with zipfile.ZipFile(stream) as archive:
                members = archive.infolist()
                names = [item.filename for item in members]
                manifest_limit = min(max_bytes, _MANIFEST_BYTES)
                if (
                    len(names) != len(set(names)) or "delivery.json" not in names
                    or archive.getinfo("delivery.json").file_size > manifest_limit
                ):
                    raise WheelError("duplicate delivery member or manifest missing/over byte budget")
                total = sum(item.file_size for item in members)
                if total > max_bytes or total >= max_snapshot_bytes:
                    raise WheelError("delivery total or snapshot byte budget exceeded")
                manifest = json.loads(b"".join(_member_chunks(
                    stream, archive, archive.getinfo("delivery.json"),
                    limit=manifest_limit, deadline=deadline,
                )), object_pairs_hook=_unique)
                files = _manifest(profile, manifest, max_files, max_bytes, deadline)
                if set(names) != {*files, "delivery.json"}:
                    raise WheelError("unexpected or omitted delivery members")
                paths = {}
                for position, member in enumerate(members):
                    _check_deadline(deadline)
                    if (
                        member.orig_filename != member.filename or member.is_dir() or member.flag_bits & 1
                        or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG)
                        or member.external_attr & 0x10
                    ):
                        raise WheelError("unsafe delivery member")
                    if member.filename == "delivery.json":
                        continue
                    selected = files[member.filename]
                    if member.file_size != selected["size"]:
                        raise WheelError("delivery member size mismatch")
                    destination = root / str(position)
                    with destination.open("xb") as output:
                        for chunk in _member_chunks(
                            stream, archive, member, limit=selected["size"], deadline=deadline,
                        ):
                            output.write(chunk)
                    destination.chmod(0o400)
                    paths[member.filename] = destination
            _unchanged(path, before, stream, opened=opened)
        with _held(paths, files, deadline) as streams:
            if streams["profile.json"].read(_MANIFEST_BYTES + 1) != profile.canonical:
                raise WheelError("delivery profile bytes differ from selected profile")
            expected = {item["filename"]: item["sha256"] for item in profile.record()["wheels"]}
            with retain_wheel_bundle(
                paths["wheels.zip"], expected, bundle_sha256=files["wheels.zip"]["sha256"],
                max_bundle_bytes=max_bytes, max_wheels=max_files, max_archive_bytes=max_bytes,
                max_total_bytes=max_bytes, max_snapshot_bytes=max_snapshot_bytes - total,
                deadline=deadline,
            ) as wheels:
                verify_profile_wheels(
                    profile, wheels.archives, max_archive_bytes=max_bytes,
                    max_unpacked_bytes=max_bytes, max_metadata_bytes=_MANIFEST_BYTES,
                    limits=limits, deadline=deadline,
                )
                yield DeliverySnapshot(
                    wheels,
                    MappingProxyType({name: streams["materials/" + name] for name in manifest["materials"]}),
                    MappingProxyType({name: paths["materials/" + name] for name in manifest["materials"]}),
                )


def build_profile_delivery(
    record, transport: Path, transport_sha256: str, materials: dict,
    material_paths: dict, destination: Path, *, max_bytes: int, max_files: int,
    max_snapshot_bytes: int, deadline: float,
) -> dict:
    """Package caller-qualified materials; never discover or approve them."""
    _limits(max_bytes, max_files, max_snapshot_bytes, deadline)
    profile = validate_profile(record, deadline=deadline)
    materials = json.loads(_json(materials))
    material_paths = dict(material_paths)
    _materials(profile, materials, max_files, max_bytes, deadline)
    if set(material_paths) != set(materials):
        raise WheelError("material paths differ from the selected material set")
    destination = Path(os.path.abspath(destination))
    if os.path.lexists(destination):
        raise FileExistsError("delivery output already exists")
    files = {"wheels.zip": {"sha256": transport_sha256, "size": _ordinary(transport).st_size}}
    files.update({"materials/" + name: {
        key: item[key] for key in ("sha256", "size")
    } for name, item in materials.items()})
    for item in files.values():
        _file(item, max_bytes)
    total = sum(item["size"] for item in files.values()) + len(profile.canonical)
    if total > max_bytes or total >= max_snapshot_bytes:
        raise WheelError("delivery input total or snapshot byte budget exceeded")
    with _temporary_inputs(destination.parent) as temporary:
        root = _private_retained_root(Path(temporary)).resolve()
        originals = {"wheels.zip": transport} | {
            "materials/" + name: path for name, path in material_paths.items()
        }
        paths = {}
        with _held(originals, files, deadline) as streams:
            for position, (name, stream) in enumerate(streams.items()):
                target = root / str(position)
                with target.open("xb") as output:
                    _digest(stream, files[name]["size"], deadline, destination=output)
                target.chmod(0o400)
                paths[name] = target
        with _held(paths, files, deadline) as streams:
            files["profile.json"] = {
                "sha256": "sha256:" + hashlib.sha256(profile.canonical).hexdigest(),
                "size": len(profile.canonical),
            }
            manifest = {
                "schema": SCHEMA, "version": 1, "profile_sha256": profile.lock_id,
                "files": files, "materials": materials,
            }
            metadata = _json(manifest)
            if len(metadata) > _MANIFEST_BYTES:
                raise WheelError("delivery manifest byte budget exceeded")
            candidate = root / "delivery.zip"
            _write_archive(
                dict(streams) | {"profile.json": io.BytesIO(profile.canonical), "delivery.json": io.BytesIO(metadata)},
                candidate, max_bytes=min(max_bytes, max_snapshot_bytes - total), deadline=deadline,
            )
            with _open(candidate) as stream:
                sha256, size = _digest(stream, max_bytes, deadline)
            with retain_profile_delivery(
                profile.record(), candidate, delivery_sha256=sha256, max_bytes=max_bytes,
                max_files=max_files, max_snapshot_bytes=max_snapshot_bytes - total - size,
                deadline=deadline,
            ):
                pass
        # Input verification has finished before publication, including exit checks.
        before = _ordinary(candidate)
        with _open(candidate) as stream:
            opened = _unchanged(candidate, before, stream)
            if _digest(stream, max_bytes, deadline) != (sha256, size):
                raise WheelError("delivery changed after readback")
            _unchanged(candidate, before, stream, opened=opened)
            _check_deadline(deadline)
            os.link(candidate, destination)
    return {"profile_sha256": profile.lock_id, "sha256": sha256, "size": size}


def _read_json(path, deadline):
    before = _ordinary(path)
    if before.st_size > _MANIFEST_BYTES:
        raise WheelError("delivery input JSON byte budget exceeded")
    with _open(path) as stream:
        opened = _unchanged(path, before, stream)
        data = stream.read(_MANIFEST_BYTES + 1)
        if len(data) > _MANIFEST_BYTES:
            raise WheelError("delivery input JSON byte budget exceeded")
        _unchanged(path, before, stream, opened=opened)
    _check_deadline(deadline)
    return json.loads(data, object_pairs_hook=_unique)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--transport", required=True, type=Path)
    parser.add_argument("--transport-sha256", required=True)
    parser.add_argument("--materials", required=True, type=Path)
    parser.add_argument("--material-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-bytes", type=int, default=512 << 20)
    parser.add_argument("--max-files", type=int, default=100_000)
    args = parser.parse_args(argv)
    deadline = time.monotonic() + args.timeout
    try:
        record, materials = _read_json(args.profile, deadline), _read_json(args.materials, deadline)
        profile = validate_profile(record, deadline=deadline)
        _materials(profile, materials, args.max_files, args.max_bytes, deadline)
        result = build_profile_delivery(
            record, args.transport, args.transport_sha256, materials,
            {name: args.material_dir / name for name in materials}, args.output,
            max_bytes=args.max_bytes, max_files=args.max_files,
            max_snapshot_bytes=8 * args.max_bytes, deadline=deadline,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, UnicodeError, zipfile.BadZipFile) as error:
        parser.exit(1, f"candidate delivery refused: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
